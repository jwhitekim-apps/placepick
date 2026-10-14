import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT.parent / ".env")

# 검색 기본값. 예전엔 gitignore된 settings.yaml에서 읽었지만, 배포 서버 파일이
# git push로 갱신되지 않아 키 불일치 사고가 났으므로 코드 상수로 둔다.
TARGET_COUNT = 300  # naver_blog_search 기본 수집 글 수
KAKAO_SEARCH_RADIUS = 5000  # 카카오 키워드 검색 반경(m)

# 네이버 Open API 한도는 클라이언트 ID 기준 초당 10건(응답 헤더 x-rate-limit: 10,
# blog/local 공유 — 실측). 여유를 두고 8건/초.
NAVER_REQUESTS_PER_SECOND = 8

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY")
KAKAO_LOCAL_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
kakao_auth_headers = {"Authorization": f"KakaoAK {KAKAO_REST_API_KEY}"}

NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
NAVER_LOCAL_SEARCH_URL = "https://openapi.naver.com/v1/search/local.json"
NAVER_BLOG_SEARCH_URL = "https://openapi.naver.com/v1/search/blog.json"
naver_open_api_headers = {
    "X-Naver-Client-Id": NAVER_CLIENT_ID or "",
    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET or "",
}


def credential_status():
    """비밀값을 노출하지 않고 로컬 설정 준비 상태만 반환합니다."""
    return {
        "gemini": bool(GEMINI_API_KEY),
        "kakao_rest": bool(KAKAO_REST_API_KEY),
        "naver_local": bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET),
    }
