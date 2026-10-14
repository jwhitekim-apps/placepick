"""앱 로깅 설정과 외부 API 에러 요약 도우미.

docker logs에서 "어느 외부 서비스가 어떤 상태코드로 실패했는지"를 바로 볼 수 있게
하는 게 목적입니다. 모든 출력은 RedactingFormatter를 거쳐 API 키·토큰 같은 비밀값이
지워진 뒤 나갑니다 — requests 예외 메시지와 traceback에는 쿼리스트링이 포함된
전체 URL(Gemini는 ?key=...)이 그대로 들어가기 때문입니다.
"""

import logging
import os
import re
import sys
from urllib.parse import urlsplit

from blog_place_collector import config

APP_LOGGER_NAME = "blog_place_collector"
BODY_PREVIEW_CHARS = 1000

# 쿼리스트링에 붙는 비밀값 패턴. 값 자체를 몰라도(예: 환경변수가 바뀐 경우) 지웁니다.
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:key|api_key|apikey|access_token|token|client_secret)=)[^&\s'\"#]+"
)
_AUTH_HEADER_RE = re.compile(r"(?i)(KakaoAK\s+|Bearer\s+)[A-Za-z0-9._\-]+")

_SERVICE_BY_HOST = {
    "generativelanguage.googleapis.com": "gemini",
    "openapi.naver.com": "naver",
    "dapi.kakao.com": "kakao",
    "map.kakao.com": "kakao-map",
}

_RATE_LIMIT_HEADER_HINTS = ("retry-after", "ratelimit", "rate-limit", "quota")


def _secret_values():
    values = [
        config.GEMINI_API_KEY,
        config.KAKAO_REST_API_KEY,
        config.NAVER_CLIENT_ID,
        config.NAVER_CLIENT_SECRET,
    ]
    # 너무 짧은 값은 일반 텍스트를 잘못 지울 수 있어 제외합니다.
    return [value for value in values if value and len(value) >= 6]


def redact(text):
    if not text:
        return text
    for secret in _secret_values():
        text = text.replace(secret, "***")
    text = _QUERY_SECRET_RE.sub(r"\1***", text)
    return _AUTH_HEADER_RE.sub(r"\1***", text)


class RedactingFormatter(logging.Formatter):
    """메시지·traceback 전체를 포맷한 뒤 비밀값을 지웁니다."""

    def format(self, record):
        return redact(super().format(record))


def configure_logging():
    """앱 로거(blog_place_collector.*)를 stderr로 내보냅니다. 여러 번 호출돼도
    핸들러가 중복으로 붙지 않습니다. 레벨은 LOG_LEVEL 환경변수(기본 INFO)."""
    logger = logging.getLogger(APP_LOGGER_NAME)
    if getattr(logger, "_placepick_configured", False):
        return logger

    handler = logging.StreamHandler(sys.stderr)
    # uvicorn 기본 로그("INFO:     ...")와 나란히 읽히도록 레벨을 앞에 둡니다.
    handler.setFormatter(
        RedactingFormatter("%(levelname)-9s %(asctime)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    logger._placepick_configured = True
    return logger


def safe_url(url):
    """scheme+host+path만 남기고 쿼리스트링(API 키 포함 가능)은 버립니다."""
    if not url:
        return "?"
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def service_name(url):
    host = urlsplit(url).hostname if url else None
    return _SERVICE_BY_HOST.get(host or "", host or "unknown")


def describe_http_error(error, include_body=True):
    """requests.HTTPError를 비밀값 없이 한 줄 요약합니다."""
    response = getattr(error, "response", None)
    if response is None:
        return f"HTTPError without response: {redact(str(error))}"

    request = getattr(response, "request", None)
    url = getattr(request, "url", None) or response.url
    method = getattr(request, "method", None) or "?"
    rate_headers = {
        name: value
        for name, value in response.headers.items()
        if any(hint in name.lower() for hint in _RATE_LIMIT_HEADER_HINTS)
    }
    summary = (
        f"service={service_name(url)} {method} {safe_url(url)} "
        f"status={response.status_code} rate_limit_headers={rate_headers or '{}'}"
    )
    if include_body:
        try:
            body = response.text or ""
        except Exception:  # noqa: BLE001 - 본문 디코딩 실패는 요약에 영향 주지 않음
            body = "<undecodable>"
        body = body[:BODY_PREVIEW_CHARS].replace("\n", " ")
        summary += f" body={redact(body)!r}"
    return summary
