import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests

from blog_place_collector.clients.gemini import (
    extract_business_names,
    extract_region_overview,
    extract_regions,
)
from blog_place_collector.clients.kakao import get_area_anchor, get_business_hours, search_place
from blog_place_collector.clients.naver import naver_blog_search, naver_local_search
from blog_place_collector.log_config import describe_http_error

logger = logging.getLogger(__name__)

# 사용자가 직접 고르던 값들을 상수로 고정합니다. 대신 수집 글 수는 결과가
# 빈약하면(distinct 후보가 적거나 반복 언급이 없음) 자동으로 늘려서 재수집합니다.
# 공식 네이버 블로그 검색 API가 한 번에 최대 100개(display)를 주므로 100 단위로
# 잡았습니다(1/2/3번 호출).
COLLECTION_TIERS = [100, 200, 300]
DEFAULT_RADIUS = 5000
MAX_TOP_N = 30
MIN_CANDIDATES = 10
MIN_REPEAT_MENTION = 2
MIN_REPEATED_CANDIDATES = 5
# 카카오 검증(_verified_candidates)이 전체 검색 시간의 절반 이상을 차지하는 게
# 실측으로 확인됐습니다(30페이지 기준 약 30초/56초). I/O 대기가 대부분이라
# 여러 이름을 동시에 검증합니다.
VERIFY_WORKERS = 8


def _enough_repeat_candidates(candidates):
    """상호명 후보가 "확실한 결과"로 보여줄 만큼 모였는지 판단합니다. 단순히 1등
    후보의 언급 횟수만 보면 "부산여행"처럼 넓은 검색도 우연히 통과해버립니다
    (10개 후보를 채웠는데 1등만 2회, 나머지는 다 1회) — 실측 확인. 그래서 "2회 이상
    언급된 후보가 몇 개나 있는지"로 봅니다. 이 기준을 tier 반복 중단과 최종
    결과/개요 판단에 동일하게 씁니다."""
    repeated = sum(1 for c in candidates if c["mention_count"] >= MIN_REPEAT_MENTION)
    return len(candidates) >= MIN_CANDIDATES and repeated >= MIN_REPEATED_CANDIDATES


# 확신 후보가 개수 기준(MIN_REPEATED_CANDIDATES)을 넘겨도, 후보들이 검색 지역
# 기준점에서 너무 멀리 흩어져 있으면(예: "부산여행"이 해운대 호텔·기장 사찰·남포동
# 시장·서면 백화점을 한 목록에 뒤섞어 보여줌) 평평한 결과 목록보다 지역별 개요가
# 낫습니다. 최대 거리(outlier 하나)로 재면 카카오 매칭이 잘못돼 멀리 튄 후보 하나
# 때문에 "강남 맛집"처럼 정상적으로 묶이는 검색까지 오판하는 문제가 있어서(실측),
# "기준점 5km 안에 확신 후보의 몇 %가 있는지"로 판단합니다.
# 실측: "강남 맛집"은 82~97%가 5km 안에 모이고, "부산여행"은 53~63%만 5km 안에
# 있고 나머지는 7~15km 밖(다른 동네)에 흩어짐. 여유를 두고 70%를 기준으로 삼습니다.
SCATTER_RADIUS_KM = 5
SCATTER_MIN_CLUSTER_FRACTION = 0.7


def _is_scattered(candidates, regions):
    confirmed = [c for c in candidates if c["mention_count"] >= MIN_REPEAT_MENTION and c["place"]]
    if len(confirmed) < 2 or not regions:
        return False
    try:
        anchor = get_area_anchor(regions[0])
    except ValueError:
        return False

    within_radius = sum(
        1
        for c in confirmed
        if (_distance_sq(c["place"]["x"], c["place"]["y"], *anchor) ** 0.5) * 100 <= SCATTER_RADIUS_KM
    )
    return within_radius / len(confirmed) < SCATTER_MIN_CLUSTER_FRACTION


def _collect_candidates(keyword):
    """COLLECTION_TIERS를 시도하며 상호명 후보를 수집·검증합니다. 마지막으로 수집한
    posts와 감지된 지역도 함께 반환합니다 — 개요 전환이나 흩어짐 판단에 재수집/재호출
    없이 그대로 재사용하기 위해서입니다.

    확신 후보(2회 이상 언급)가 최소 기준을 넘겨도, 직전 tier보다 여전히 늘고 있으면
    "더 넓은 지역일 수 있다"고 보고 계속 다음 tier로 진행합니다. 늘지 않거나(정체)
    줄어들면 그 시점에서 멈춥니다 — 좁은 지역명은 tier 1에서 빨리 끝나고, 넓은
    지역명은 신호가 계속 느는 동안 더 많은 글을 모으게 됩니다.

    네이버 블로그 검색은 sim 정렬이라 tier를 올려도 앞쪽 글 목록은 그대로이고 뒤에만
    붙습니다(실측 확인). tier마다 전체 글을 다시 Gemini에 넣으면 이미 추출한 글을
    반복 추출하게 되는데(실측: 상호명 추출이 전체 시간의 ~74%, 그중 절반이 재추출),
    지역 추출은 키워드만 보므로 tier마다 다시 부를 이유가 없습니다. 그래서 지역은
    한 번만 뽑고, 상호명 추출은 새로 늘어난 글에 대해서만 하고 누적합니다."""
    candidates = []
    posts = []
    guesses = []
    regions = extract_regions(keyword)
    logger.info("keyword=%r regions=%s", keyword, regions)
    extracted_count = 0
    previous_confirmed = 0
    # sim 정렬이라 앞쪽 글은 tier가 올라가도 그대로이므로, 이미 받은 글은 재사용하고
    # 모자란 만큼만 이어서 받습니다(tier마다 처음부터 다시 받으면 100/200/300개에
    # 1/2/3번 호출 — 네이버 초당 한도를 불필요하게 소모). 직전 요청에서 결과가 바닥나면
    # 더 요청하지 않습니다(처음부터 다시 받아도 같은 글만 돌아오던 기존 동작과 동일).
    exhausted = False
    # 이름→검증 결과 캐시. tier마다 guesses 전체를 다시 검증하는데, 이전 tier에서 이미
    # 검증한 이름은 결과가 같으므로 재사용합니다(네이버 지역검색 호출이 초당 한도에
    # 걸려 대기하므로 중복 호출이 곧 대기 시간).
    resolved_places = {}
    for target_count in COLLECTION_TIERS:
        tier_started = time.monotonic()
        if not exhausted and len(posts) < target_count:
            requested = target_count - len(posts)
            more = naver_blog_search(keyword=keyword, target_count=requested, start=len(posts) + 1)
            exhausted = len(more) < requested
            posts = posts + more
        if not posts:
            logger.info("keyword=%r tier=%d: 블로그 글 0개 — 중단", keyword, target_count)
            break

        new_posts = posts[extracted_count:]
        extract_started = time.monotonic()
        if new_posts:
            guesses.extend(extract_business_names(new_posts, keyword))
            extracted_count = len(posts)
        verify_started = time.monotonic()
        candidates = _verified_candidates(
            guesses, regions=regions, radius=DEFAULT_RADIUS, cache=resolved_places
        )
        verify_elapsed = time.monotonic() - verify_started

        confirmed = sum(1 for c in candidates if c["mention_count"] >= MIN_REPEAT_MENTION)
        logger.info(
            "keyword=%r tier=%d posts=%d new=%d guesses=%d candidates=%d confirmed=%d "
            "extract=%.1fs verify=%.1fs tier_total=%.1fs",
            keyword,
            target_count,
            len(posts),
            len(new_posts),
            len(guesses),
            len(candidates),
            confirmed,
            verify_started - extract_started,
            verify_elapsed,
            time.monotonic() - tier_started,
        )
        if confirmed >= MIN_REPEATED_CANDIDATES and confirmed <= previous_confirmed:
            break
        previous_confirmed = confirmed

    return len(posts), posts, candidates, regions


def _finalize_candidates(candidates):
    """확신 후보(2회 이상 언급)를 최대 MAX_TOP_N개까지 모두 보여줍니다. 확신 후보가
    MIN_REPEATED_CANDIDATES에 못 미치면(좁은 검색) 결과가 텅 비어 보이지 않도록
    1회 언급 후보까지 포함해 상위 5개를 채웁니다."""
    confirmed = [c for c in candidates if c["mention_count"] >= MIN_REPEAT_MENTION]
    if len(confirmed) >= MIN_REPEATED_CANDIDATES:
        top_candidates = confirmed[:MAX_TOP_N]
    else:
        top_candidates = candidates[:MIN_REPEATED_CANDIDATES]

    for candidate in top_candidates:
        candidate["naver_search_url"] = f"https://map.naver.com/p/search/{quote(candidate['name'])}"
    _attach_business_hours(top_candidates)
    return top_candidates


def preview_collection(keyword):
    """수집부터 장소 매칭까지 실행하되 즐겨찾기는 변경하지 않습니다.
    지역+카테고리를 이미 확정한 드릴다운 검색에 씁니다(자동판단 없이 곧장 상세 결과)."""
    started = time.monotonic()
    logger.info("preview start keyword=%r", keyword)
    post_count, _posts, candidates, _regions = _collect_candidates(keyword)
    result = {"post_count": post_count, "candidates": _finalize_candidates(candidates)}
    logger.info(
        "preview done keyword=%r posts=%d shown=%d total=%.1fs",
        keyword,
        post_count,
        len(result["candidates"]),
        time.monotonic() - started,
    )
    return result


def search_collection(keyword):
    """검색어 하나로 상세 결과와 지역 개요 중 뭘 보여줄지 자동으로 판단합니다.
    상세 검색(카카오 검증까지)을 먼저 시도하고, 결과가 확실하고 한 지역에 묶여 있으면
    그대로 보여주고, 빈약하거나 여러 지역에 흩어져 있으면 이미 수집해둔 글을
    재사용해 지역별 개요로 전환합니다."""
    started = time.monotonic()
    logger.info("search start keyword=%r", keyword)
    post_count, posts, candidates, regions = _collect_candidates(keyword)

    if not posts or (_enough_repeat_candidates(candidates) and not _is_scattered(candidates, regions)):
        result = {
            "mode": "results",
            "post_count": post_count,
            "candidates": _finalize_candidates(candidates),
        }
        logger.info(
            "search done keyword=%r mode=results posts=%d shown=%d total=%.1fs",
            keyword,
            post_count,
            len(result["candidates"]),
            time.monotonic() - started,
        )
        return result

    overview_regions = extract_region_overview(posts, keyword)
    logger.info(
        "search done keyword=%r mode=overview posts=%d regions=%d total=%.1fs",
        keyword,
        post_count,
        len(overview_regions),
        time.monotonic() - started,
    )
    return {"mode": "overview", "post_count": post_count, "regions": overview_regions}


def _verified_candidates(guesses, regions, radius, cache=None):
    """LLM이 느슨하게 추출한 후보를 카카오맵 검색으로 검증합니다.
    실제로 존재하는 장소만 남기고, 카카오맵 장소 ID 기준으로 묶어 언급 횟수 순으로 정렬합니다.

    같은 이름이 여러 글에서 반복 추출돼도 카카오 검증은 이름당 한 번만 하고(중복
    호출 제거), 남은 서로 다른 이름들은 동시에 검증합니다 — 순차 호출이 전체
    검색 시간의 절반 이상을 차지하는 게 실측으로 확인됐습니다."""
    posts_by_name = {}
    order_names = []
    for guess in guesses:
        name = guess["name"]
        if name not in posts_by_name:
            posts_by_name[name] = []
            order_names.append(name)
        posts_by_name[name].append(guess["post"])

    local_search_failures = []

    def resolve(name):
        return name, _resolve_place(
            name, regions=regions, radius=radius, failures=local_search_failures
        )

    if cache is None:
        cache = {}
    unresolved = [name for name in order_names if name not in cache]
    with ThreadPoolExecutor(max_workers=VERIFY_WORKERS) as executor:
        cache.update(executor.map(resolve, unresolved))
    places_by_name = cache

    if local_search_failures:
        # 이름마다 찍으면 한 검색에 수십 줄이 나와서, 검증 1회당 한 줄로 요약합니다.
        logger.warning(
            "naver_local_search 실패 %d/%d건(무시하고 진행). 첫 실패: %s",
            len(local_search_failures),
            len(unresolved),
            local_search_failures[0],
        )

    grouped = {}
    order = []
    for name in order_names:
        place = places_by_name[name]
        if not place:
            continue

        key = place["key"]
        if key not in grouped:
            grouped[key] = {
                "name": place["display1"],
                "mention_count": 0,
                "sources": [],
                "place": place,
            }
            order.append(key)

        entry = grouped[key]
        for source in posts_by_name[name]:
            entry["mention_count"] += 1
            if not any(existing["url"] == source["url"] for existing in entry["sources"]):
                entry["sources"].append({"title": source["title"], "url": source["url"]})

    candidates = [grouped[key] for key in order]
    return _sort_candidates(candidates, regions)


def _sort_candidates(candidates, regions):
    """언급 횟수가 많은 순으로 정렬합니다. 언급 횟수가 같으면, 검색 지역 기준점(첫
    번째로 감지된 지역)에서 가까운 곳을 우선합니다. 지역 정보가 없으면 동점은
    먼저 검증된 순서 그대로 둡니다."""
    anchor = None
    if regions:
        try:
            anchor = get_area_anchor(regions[0])
        except ValueError:
            anchor = None

    def sort_key(candidate):
        if anchor is None:
            return (-candidate["mention_count"], 0)
        distance = _distance_sq(candidate["place"]["x"], candidate["place"]["y"], *anchor)
        return (-candidate["mention_count"], distance)

    candidates.sort(key=sort_key)
    return candidates


def _distance_sq(x1, y1, x2, y2):
    return (x1 - x2) ** 2 + (y1 - y2) ** 2


def _resolve_place(name, regions, radius, failures=None):
    """검색어에서 추출된 지역 각각으로 카카오맵 검증을 시도합니다 (지역이 없으면 이 단계는
    건너뜁니다). 실패하면 네이버 지역검색으로 실제 등록명을 얻어 지역별로 재시도하고,
    그래도 안 되면 지역 제한 없이 전국에서 한 번 더 찾아봅니다."""
    for region in regions:
        place = _search_place_safe(name, area_keyword=region, radius=radius)
        if place:
            return place

    try:
        hits = naver_local_search(name, display=5)
    except requests.RequestException as error:
        # 동작은 그대로(빈 결과로 진행)지만, 한도 초과 등이 조용히 묻히지 않게
        # 호출자(_verified_candidates)가 요약해서 남기도록 모읍니다.
        detail = (
            describe_http_error(error, include_body=False)
            if isinstance(error, requests.HTTPError)
            else type(error).__name__
        )
        logger.debug("naver_local_search 실패 name=%r: %s", name, detail)
        if failures is not None:
            failures.append(f"name={name!r} {detail}")
        hits = []

    for hit in hits:
        if not _loosely_related(hit["title"], name):
            continue
        for region in regions:
            place = _search_place_safe(hit["title"], area_keyword=region, radius=radius)
            if place:
                return place

    return search_place(name, require_match=True, nationwide=True, regions=regions)


def _search_place_safe(name, area_keyword, radius):
    """지역명이 카카오맵에서 기준 좌표를 못 찾는 경우(예: 없는 지명)에도 전체 검증이
    죽지 않도록 감싸서 호출합니다."""
    try:
        return search_place(name, area_keyword=area_keyword, radius=radius, require_match=True)
    except ValueError:
        return None


def _loosely_related(a, b):
    normalized_a = re.sub(r"\s+", "", a)
    normalized_b = re.sub(r"\s+", "", b)
    return normalized_a in normalized_b or normalized_b in normalized_a


def _attach_business_hours(results):
    """영업시간은 비공식 API라 실패해도 나머지 결과에 영향을 주지 않습니다."""
    place_ids = [r["place"]["key"] for r in results if r["place"]]
    if not place_ids:
        return

    try:
        hours_by_id = get_business_hours(place_ids)
    except requests.RequestException as error:
        logger.warning(
            "get_business_hours 실패(영업시간 없이 진행): %s",
            describe_http_error(error)
            if isinstance(error, requests.HTTPError)
            else type(error).__name__,
        )
        return

    for result in results:
        if result["place"]:
            result["place"]["business_hours"] = hours_by_id.get(result["place"]["key"])
