import logging
import time
from pathlib import Path
from typing import Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from blog_place_collector.config import credential_status
from blog_place_collector.log_config import configure_logging, describe_http_error
from blog_place_collector.service import preview_collection, search_collection

configure_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Placepick API",
    description="네이버 블로그에서 자주 언급된 장소를 찾아 정보를 보여주는 로컬 API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class PreviewRequest(BaseModel):
    keyword: str = Field(min_length=2, max_length=80)


def _friendly_error(error):
    if isinstance(error, requests.Timeout):
        return "외부 서비스 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요."
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else None
        if status in (401, 403):
            return "API 인증 정보가 만료되었거나 올바르지 않습니다."
        if status == 429:
            return "외부 서비스 요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요."
        return f"외부 서비스 요청에 실패했습니다.{f' (HTTP {status})' if status else ''}"
    if isinstance(error, ValueError):
        return str(error)
    return str(error) or "요청 처리 중 알 수 없는 오류가 발생했습니다."


def _log_upstream_error(endpoint, keyword, error, elapsed):
    """502로 바꿔 내려보내기 전에 원본 예외를 남깁니다. 비밀값은 RedactingFormatter가
    traceback까지 지우고, HTTPError 요약은 쿼리스트링 없이 host+path만 씁니다."""
    if isinstance(error, requests.HTTPError):
        logger.error(
            "%s failed: keyword=%r elapsed=%.1fs upstream HTTP error: %s",
            endpoint,
            keyword,
            elapsed,
            describe_http_error(error),
            exc_info=error,
        )
    else:
        logger.error(
            "%s failed: keyword=%r elapsed=%.1fs %s: %s",
            endpoint,
            keyword,
            elapsed,
            type(error).__name__,
            error,
            exc_info=error,
        )


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "credentials": credential_status(),
    }


@app.post("/api/collections/preview")
async def preview(payload: PreviewRequest):
    started = time.monotonic()
    try:
        result = await run_in_threadpool(preview_collection, payload.keyword.strip())
        return {
            "query": payload.keyword.strip(),
            **result,
        }
    except (requests.RequestException, ValueError, KeyError, IndexError) as error:
        _log_upstream_error("preview", payload.keyword.strip(), error, time.monotonic() - started)
        raise HTTPException(status_code=502, detail=_friendly_error(error)) from error


@app.post("/api/collections/search")
async def search(payload: PreviewRequest):
    started = time.monotonic()
    try:
        result = await run_in_threadpool(search_collection, payload.keyword.strip())
        return {
            "query": payload.keyword.strip(),
            **result,
        }
    except (requests.RequestException, ValueError, KeyError, IndexError) as error:
        _log_upstream_error("search", payload.keyword.strip(), error, time.monotonic() - started)
        raise HTTPException(status_code=502, detail=_friendly_error(error)) from error


# API 라우트를 먼저 등록한 뒤 Svelte 빌드 결과를 루트 경로에서 제공합니다.
frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():

    @app.get("/search", include_in_schema=False)
    async def search_page():
        return FileResponse(frontend_dist / "index.html")

    app.mount(
        "/",
        StaticFiles(directory=str(frontend_dist), html=True),
        name="frontend",
    )
