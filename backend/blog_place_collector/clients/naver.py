import html
import logging
import re
import threading
import time

import requests

from blog_place_collector.config import (
    NAVER_BLOG_SEARCH_URL,
    NAVER_LOCAL_SEARCH_URL,
    NAVER_REQUESTS_PER_SECOND,
    TARGET_COUNT,
    naver_open_api_headers,
)

logger = logging.getLogger(__name__)

# 공식 API는 한 번 호출에 최대 100개까지 준다(display 상한, 실측 확인).
BLOG_SEARCH_PAGE_SIZE = 100

# 429를 받았을 때 재시도 횟수와 대기 시간 범위. 네이버는 x-rate-limit-reset(epoch ms)을
# 주고 1초 단위로 리셋되므로(실측) 보통 1초 안쪽만 기다리면 됩니다.
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_RETRY_WAIT = 1.0
MIN_RETRY_WAIT = 0.2
MAX_RETRY_WAIT = 3.0


class RateLimiter:
    """프로세스 전역, 스레드 안전한 최소 간격 제한기.

    호출 시각을 1/rate 초 간격으로 줄 세웁니다. 버스트를 허용하지 않아 서버가 어떤
    1초 창으로 세든 rate를 넘지 않습니다. 429를 받으면 penalize()로 모든 스레드의
    다음 호출을 뒤로 미룹니다."""

    def __init__(self, rate_per_second, clock=time.monotonic, sleep=time.sleep):
        self.interval = 1.0 / rate_per_second
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self):
        with self._lock:
            now = self._clock()
            slot = max(now, self._next_allowed)
            self._next_allowed = slot + self.interval
        wait = slot - now
        if wait > 0:
            self._sleep(wait)

    def penalize(self, seconds):
        with self._lock:
            self._next_allowed = max(self._next_allowed, self._clock() + seconds)


_rate_limiter = RateLimiter(NAVER_REQUESTS_PER_SECOND)


def _retry_wait_seconds(response):
    """Retry-After(초) → x-rate-limit-reset(epoch ms) → 기본 1초 순으로 대기 시간을 정합니다."""
    wait = None
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            wait = float(retry_after)
        except ValueError:
            wait = None
    if wait is None:
        reset = response.headers.get("x-rate-limit-reset")
        if reset:
            try:
                wait = float(reset) / 1000.0 - time.time()
            except ValueError:
                wait = None
    if wait is None:
        wait = DEFAULT_RETRY_WAIT
    return min(max(wait, MIN_RETRY_WAIT), MAX_RETRY_WAIT)


def _naver_get(url, params, timeout):
    """모든 네이버 Open API 호출이 거치는 공통 경로: 전역 rate limiter + 429 재시도."""
    attempt = 0
    while True:
        _rate_limiter.acquire()
        response = requests.get(url, headers=naver_open_api_headers, params=params, timeout=timeout)
        if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
            attempt += 1
            wait = _retry_wait_seconds(response)
            logger.warning(
                "naver 429 %s — %.2fs 후 재시도 (%d/%d) query=%r",
                url.rsplit("/", 1)[-1],
                wait,
                attempt,
                MAX_RATE_LIMIT_RETRIES,
                params.get("query"),
            )
            _rate_limiter.penalize(wait)
            continue
        response.raise_for_status()
        return response


def _strip_tags(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def naver_blog_search(keyword, target_count=TARGET_COUNT, start=1):
    """네이버 블로그 검색 공식 API로 글을 target_count개까지 모읍니다.

    start는 1부터 시작하는 순위 위치입니다. sim 정렬이라 같은 키워드의 앞쪽 결과는
    그대로이므로(실측), 이미 받은 글 뒤부터 이어 받을 때 start=len(posts)+1로 부릅니다."""
    posts = []

    while len(posts) < target_count:
        display = min(BLOG_SEARCH_PAGE_SIZE, target_count - len(posts))
        response = _naver_get(
            NAVER_BLOG_SEARCH_URL,
            params={"query": keyword, "display": display, "start": start, "sort": "sim"},
            timeout=15,
        )
        items = response.json().get("items", [])

        for item in items:
            title = _strip_tags(item.get("title", ""))
            contents = _strip_tags(item.get("description", ""))
            posts.append({"title": title, "contents": contents, "url": item.get("link", "")})

        if len(items) < display:
            break
        start += display

    return posts


def naver_local_search(query, display=5):
    """네이버 지역검색 공식 API로 실제 등록된 업체 후보를 조회합니다
    (블로그 상호명 검증용 — 카카오맵 검증이 실패했을 때 이름을 다시 맞춰보는 용도)."""
    response = _naver_get(
        NAVER_LOCAL_SEARCH_URL,
        params={"query": query, "display": display, "sort": "random"},
        timeout=10,
    )
    data = response.json()

    return [
        {
            "title": _strip_tags(item.get("title", "")),
            "category": item.get("category", ""),
            "road_address": item.get("roadAddress", ""),
            "address": item.get("address", ""),
        }
        for item in data.get("items", [])
    ]
