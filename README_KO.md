# Placepick

**언어:** [English](README.md) | 한국어

**데모:** <https://plick.2joon.com/> — 개인 배포본입니다. 운영자 개인 API
키/쿼터로 돌아가서 트래픽이 몰리면 느려지거나 다운될 수 있습니다.

어떤 장소를 네이버에서 검색하면 서로 비슷비슷한 SEO용 블로그 글이 수십 개씩
쏟아집니다. 그 안에서 사람들이 진짜로 반복해서 찾는 곳이 어딘지 골라내는
게 실제로 드는 수고입니다. Placepick은 그 글들을 대신 읽고, 신뢰의 기준으로
별점(임의적이고 조작하기 쉬움) 대신 **조작하기 어려운 것 하나** — 서로 다른
사람이 쓴 독립된 글 여러 개에서 같은 곳이 반복 언급되는지 — 를 씁니다.

검색어를 입력하면 관련 네이버 블로그 글을 모으고, Gemini로 상호명 후보를
뽑은 뒤, 후보 하나하나를 카카오맵으로 대조합니다 — 실제로 존재하는 장소로
확인된 것만 남습니다. 남은 결과는 반복 언급 횟수 순으로 정리되고, 각 결과에는
실제로 언급한 블로그 글 링크가 근거로 달립니다.

모든 검색어가 하나의 깔끔한 목록으로 정리되는 건 아닙니다. "부산여행" 같은
검색어는 서로 아무 관련 없는 동네를 넘나듭니다 — 해운대 호텔, 기장 사찰,
남포동 시장. 이걸 억지로 한 목록에 평평하게 펼치면 오히려 정보를 가리게
됩니다. 그래서 검색어가 정말로 넓으면 평평한 목록 대신 지역 → 카테고리
구조로 보여주고, 원하는 동네 하나만 골라 자세히 볼 수 있게 합니다.

수집 범위는 검색어에 따라 자동으로 늘어나거나 줄어듭니다 — 좁고 구체적인
검색어는 신호가 확실해지는 즉시 멈추고, 넓은 검색어는 반복 언급되는 후보가
계속 늘어나는 동안 계속 더 모읍니다. 따로 설정할 게 없고, 검색어만
입력하면 됩니다.

## 어떻게 동작하나요

1. 검색어를 입력합니다(예: "성수동 브런치").
2. 관련 네이버 블로그 글을 자동으로 모읍니다 — 신호가 약하면 더 많이,
   충분하면 적게 모읍니다.
3. Gemini가 글에서 상호명 후보를 뽑고, 각 후보는 카카오맵으로 실제 존재하는
   장소인지 대조합니다 — 실존이 확인된 곳만 남습니다.
4. 결과가 한 지역으로 묶이면 몇 개의 독립된 글에서 언급됐는지와 함께 순위
   목록으로 보여줍니다. 검색어가 여러 동네에 걸칠 만큼 넓으면 대신 지역·
   카테고리별 구조로 보여주고, 카테고리를 누르면 그 지역의 상세 결과로
   넘어갑니다.
5. 카카오맵·네이버지도 링크로 이동해 영업시간·사진·리뷰를 확인하고, 마음에
   들면 그쪽에서 직접 즐겨찾기하세요.

## 시작하기

**준비 사항:** Python 3.9 이상, Node.js 20.19 이상, 그리고
[Gemini](https://ai.google.dev/), [Kakao REST](https://developers.kakao.com/),
[네이버 검색 API](https://developers.naver.com/apps/#/register)(client ID +
secret) 키.

```bash
cp .env.example .env          # API 키 입력

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd frontend && npm install && cd ..

.venv/bin/python backend/main.py
```

브라우저에서 <http://127.0.0.1:8000>을 엽니다. FastAPI 문서는
<http://127.0.0.1:8000/docs>에서 확인할 수 있습니다. `backend/main.py`는
Svelte 소스가 바뀌었거나 빌드 파일이 없으면 자동으로 프론트엔드를 빌드한 뒤,
API와 빌드된 프론트엔드를 같은 포트에서 함께 서비스합니다 — 프론트엔드 개발
서버를 따로 띄울 필요가 없습니다. API 키는 서버에서만 쓰이고 브라우저로는
절대 전달되지 않습니다.

## 명령어

```bash
cd backend && ../.venv/bin/python -m pytest tests/ -q   # 백엔드 테스트
cd frontend && npm run build                              # 프론트엔드 프로덕션 빌드
.venv/bin/python backend/main.py --reload                  # 통합 서버 + 백엔드 자동 새로고침
cd frontend && npm run dev                                  # 프론트엔드 개발 서버만 별도 실행
```

## CI/CD

`main`에 push 또는 PR이 열리면 GitHub Actions(`.github/workflows/ci-cd.yml`)가
백엔드 테스트, 프론트엔드 빌드, Docker 이미지 빌드를 검증합니다. 세 검증이 모두
통과하고 `main`에 직접 push된 경우에만 설정된 서버에 SSH로 접속해 배포합니다.

배포를 쓰려면 저장소 Settings → Secrets and variables → Actions에 아래 값을
등록하세요.

| Secret | 설명 |
| --- | --- |
| `SSH_PRIVATE_KEY` | 배포 서버 접속용 개인키 (OpenSSH 형식) |
| `SSH_HOST` (또는 `SERVER_HOST`) | 배포 서버 호스트/IP |
| `SSH_PORT` | 배포 서버 SSH 포트 |
| `SSH_USER` (또는 `SERVER_USER`) | 배포 서버 SSH 사용자 |

배포 경로는 워크플로우에 고정돼 있습니다 — 경로가 다르면
`.github/workflows/ci-cd.yml`의 `Deploy` 스텝을 직접 수정하세요. 그 경로에
저장소가 이미 `git clone`돼 있고 `git`, `docker`, `docker compose` 플러그인이
설치돼 있어야 합니다.

## 더 읽어보기

- [docs/decisions.md](docs/decisions.md) — 추출·매칭·스케일링 로직을 왜
  지금 방식으로 짰는지, 시도했다가 기각한 접근은 무엇인지 기록한 설계 노트.
- [docs/design-spec.md](docs/design-spec.md) — 시각 디자인 시스템(팔레트·
  타이포그래피·레이아웃 원칙)과 그 근거.
