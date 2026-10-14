import json
import logging
import time
from collections import Counter

import requests

from blog_place_collector.config import GEMINI_API_KEY, GEMINI_API_URL, GEMINI_MODEL

logger = logging.getLogger(__name__)

REGIONS_SCHEMA = {
    "type": "ARRAY",
    "items": {"type": "STRING"},
}

REGIONS_PROMPT_TEMPLATE = """\
아래 검색어 문자열 자체에서만 지역명을 전부 추출하세요 (구/동/역/골목상권 이름 등,
예: 강남, 성수동, 신논현역, 송리단길). 검색어 안에서만 찾고, 다른 지식이나 추측은
쓰지 마세요. 지역명이 전혀 없으면 빈 배열을 반환하세요.

검색어: "{keyword}"
"""

def _business_names_schema(post_count):
    # 응답 배열 길이를 목록 개수로 고정합니다. 예전엔 길이 제한이 없어서, 모델이
    # "같은 가게는 한 번만 출력"이라고 오독하면 항목을 통째로 스킵해도 스키마상
    # 걸리지 않았습니다(실측: 반복 언급 recall이 5/40까지 떨어짐). minItems/maxItems로
    # 개수를 강제하면 스킵이 곧 스키마 위반이 되므로, 프롬프트 워딩에만 기대지 않고
    # 개수 누락을 구조적으로 막습니다.
    return {
        "type": "ARRAY",
        "minItems": post_count,
        "maxItems": post_count,
        "items": {
            "type": "OBJECT",
            "properties": {
                "index": {"type": "INTEGER"},
                "name": {"type": "STRING"},
            },
            "required": ["index", "name"],
        },
    }


PROMPT_TEMPLATE = """\
사용자가 검색한 키워드는 "{keyword}"입니다.
아래는 이 키워드로 찾은 네이버 블로그 검색 결과 목록입니다 (제목 + 본문 일부).
각 항목에서 리뷰 대상으로 언급된 가게, 시설, 업체의 상호명을 추출하세요.

규칙:
- 검색 키워드가 나타내는 장소 유형·주제와 실제로 관련된 상호명만 추출하세요.
  글이 여러 장소를 나열하는 목록형/추천형 글이더라도, 키워드와 무관한 업종·시설은 제외하세요.
  (예: "영화관"을 검색했는데 글이 "실내 놀거리 15선"이라 아쿠아리움·전시공간도 같이 나열돼 있다면,
  그중 영화관에 해당하는 것만 추출하고 나머지는 제외하세요.)
- 상호명으로 보이거나 상호명으로 추측되는 표현이 있으면 확신이 없어도 최대한 추출하세요.
  실제로 존재하는 상호인지는 이후 카카오맵 검색으로 검증하니, 애매하다고 여기서 걸러내지 마세요.
- 상호명은 카카오맵에서 검색할 수 있는 정식 명칭에 가깝게 통일하세요.
- 지점명이 명확히 언급되면 지점명까지 포함하세요.
- 검색 지역명(예: 강남, 이태원)은 실제 지점명의 일부일 때만 포함하고, 단순히 위치를 설명하는 수식어라면 제외하세요.
- 같은 가게가 여러 항목에서 언급되면, 항목마다 각각 결과에 포함하세요(항목을 건너뛰지 마세요).
  다만 표기가 항목마다 다르더라도(예: "메가커피" vs "메가MGC커피") name 값은 동일한 상호명 하나로
  통일해서 쓰세요. 즉 "몇 번 등장하는지"는 그대로 유지하고, "어떻게 표기하는지"만 통일하는 것입니다.
- 제목에 없어도 본문에 구체적인 상호명이 있으면 추출하세요.
- 지역명, 역 이름, 업종처럼 상호명이 전혀 아닌 일반 단어만 있는 경우에만 제외하세요.
- 목록에는 항목이 총 {post_count}개 있습니다. 결과 배열도 정확히 {post_count}개를 반환하고,
  각 항목의 index(1~{post_count})가 정확히 한 번씩 모두 나타나야 합니다. 상호명이 없는
  항목은 건너뛰지 말고 name을 빈 문자열("")로 채워서 포함하세요.

목록:
{items}
"""

OVERVIEW_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "name": {"type": "STRING"},
            "mention_count": {"type": "INTEGER"},
            "categories": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "name": {"type": "STRING"},
                        "mention_count": {"type": "INTEGER"},
                    },
                    "required": ["name", "mention_count"],
                },
            },
        },
        "required": ["name", "mention_count", "categories"],
    },
}

OVERVIEW_PROMPT_TEMPLATE = """\
당신은 여행 블로그 게시글들을 분석해 지역과 카테고리 구조로 정리하는 어시스턴트입니다.
사용자가 검색한 키워드는 "{keyword}"입니다.
아래는 이 키워드로 찾은 네이버 블로그 검색 결과 목록입니다 (제목 + 본문 일부).

작업:
1. 게시글 전체에서 언급된 지역(동/거리 단위)을 추출하세요. 예: 서면, 전포, 해운대, 광안리, 남포동
2. 각 지역별로 언급된 장소 카테고리를 추출하세요. 예: 카페, 맛집, 야경/포토스팟, 술집, 관광지, 숙소
3. 같은 지역·카테고리 조합이 여러 게시글에서 반복 언급되면 언급 빈도로 집계하세요.
4. 실제 상호명은 이 단계에서 추출하지 마세요. 지역/카테고리 구조만 만드세요.
   (상호명 추출은 사용자가 지역+카테고리를 고른 다음 단계에서 별도로 처리합니다)
5. 검색 키워드("{keyword}")와 관련 없는 지역/카테고리는 제외하세요.

주의사항:
- mention_count는 실제 등장 횟수에 근거해 추정하세요. 임의로 만들지 마세요.
- 지역명은 행정동보다 실제 블로거들이 쓰는 명칭(예: "전포동" 대신 "전포")을 우선하세요.

목록:
{items}
"""


def _call_gemini(prompt, response_schema, label="gemini"):
    # temperature=0: 같은 입력이면 같은 결과가 나오게 합니다. 기본 temperature(샘플링
    # 랜덤성)를 쓰면 70개 글처럼 큰 배치를 넣었을 때 호출마다 추출 결과가 크게 달라지는
    # 문제가 있었습니다 (예: 같은 업체 언급이 1회로도, 41회로도 잡힘).
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
            "temperature": 0,
        },
    }
    started = time.monotonic()
    try:
        response = requests.post(
            GEMINI_API_URL,
            params={"key": GEMINI_API_KEY},
            json=payload,
            timeout=60,
        )
    except requests.RequestException as error:
        logger.warning(
            "gemini call %s failed after %.1fs: %s",
            label,
            time.monotonic() - started,
            type(error).__name__,
        )
        raise
    elapsed = time.monotonic() - started
    logger.info(
        "gemini call %s model=%s status=%s prompt_chars=%d elapsed=%.1fs",
        label,
        GEMINI_MODEL,
        response.status_code,
        len(prompt),
        elapsed,
    )
    response.raise_for_status()
    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def extract_regions(keyword):
    """검색 키워드 문자열 자체에서 지역명을 추출합니다. 블로그 본문은 보지 않고
    키워드만 보므로, 여러 지역이 섞여 있거나 순서가 달라도 안정적으로 동작합니다."""
    regions = _call_gemini(
        REGIONS_PROMPT_TEMPLATE.format(keyword=keyword), REGIONS_SCHEMA, label="regions"
    )
    return [region.strip() for region in regions if region.strip()]


def _numbered_posts(posts):
    return "\n".join(
        f"{i}. 제목: {post['title']}\n   본문: {post['contents']}"
        for i, post in enumerate(posts, start=1)
    )


def _build_prompt(posts, keyword):
    return PROMPT_TEMPLATE.format(keyword=keyword, items=_numbered_posts(posts), post_count=len(posts))


# minItems/maxItems로 응답 길이를 고정하면 Gemini API가 요청 자체를 400으로 거부하는
# 지점이 있습니다(실측: 135개는 성공, 140개부터 실패). 정확도 문제와는 무관하고 순전히
# API 쪽 제약이라, 여유를 두고 이 크기 이하로만 나눠 호출합니다.
MAX_ITEMS_PER_CALL = 100


def extract_business_names(posts, keyword):
    names = []
    for start in range(0, len(posts), MAX_ITEMS_PER_CALL):
        chunk = posts[start : start + MAX_ITEMS_PER_CALL]
        schema = _business_names_schema(len(chunk))
        entries = _call_gemini(
            _build_prompt(chunk, keyword),
            schema,
            label=f"business_names[{start + 1}-{start + len(chunk)}]",
        )
        if len(entries) != len(chunk):
            # minItems/maxItems로 강제했음에도 길이가 다르면 API가 스키마를 못 지킨
            # 드문 경우입니다 — 있는 만큼은 그대로 쓰되, 놓친 항목이 있을 수 있다는
            # 신호로 남겨둡니다.
            logger.warning(
                "extract_business_names: 응답 %d개, 글 %d개 — 길이가 다릅니다.",
                len(entries),
                len(chunk),
            )

        for entry in entries:
            index = entry["index"] - 1
            name = entry["name"].strip()
            if 0 <= index < len(chunk) and name:
                names.append({"name": name, "post": chunk[index]})
    return names


def top_business_names(posts, keyword, top_n=5):
    results = extract_business_names(posts, keyword)
    counter = Counter(result["name"] for result in results)
    return counter.most_common(top_n)


def extract_region_overview(posts, keyword):
    """블로그 글에서 지역별 카테고리 구조(언급 빈도 포함)를 추출합니다. 상호명은
    이 단계에서 뽑지 않고, 사용자가 지역+카테고리를 고른 뒤 extract_business_names로
    별도 처리합니다."""
    prompt = OVERVIEW_PROMPT_TEMPLATE.format(keyword=keyword, items=_numbered_posts(posts))
    return _call_gemini(prompt, OVERVIEW_SCHEMA, label=f"region_overview[{len(posts)} posts]")
