---
name: backend-work
description: Placepick 백엔드(backend/blog_place_collector) 작업 시 반드시 사용한다 — service.py의 수집/추출/검증 파이프라인, api.py 라우트, clients/{gemini,kakao,naver}.py 수정, 설정(config.py 상수, .env) 변경, 추출 정확도·검색 속도·API 연동 버그 디버깅 전부 해당. "백엔드 고쳐줘", "검색 결과가 이상해", "추출이 부정확해", "API 바꿔줘" 같은 요청에서 트리거.
---

# Placepick 백엔드 작업

## 시작하기 전에

`docs/decisions.md`를 먼저 확인한다 — 13개 섹션에 걸쳐 이미 시도했다가 틀렸다고 확인된 접근들이 기록돼 있다. 특히:
- 배치를 나눠 추출하면 정확도가 오를 거라는 가정은 **틀렸다** — 진짜 원인은 프롬프트의 "동일 상호명 통일" 규칙이 모델에게 "한 번만 출력해라"로 오독된 것이었다(9장).
- 결과가 부족하면 페이지를 더 모으면 될 거라는 가정도 **틀렸다** — 210개 글을 한 번에 넣으면 오히려 LLM이 더 부정확해질 수 있다(같은 9장, 프롬프트 수정이 답이었음).
- "확신 후보 개수"만으로 넓은 지역을 판단하면 **부산여행 같은 다지역 검색이 오판된다** — 지역 기준점 근처 클러스터 비율로 봐야 한다(11장).

## 핵심 규칙

1. **Gemini 호출은 항상 `temperature=0`.** 재현성 문제(같은 검색이 매번 다른 결과)의 첫 번째 의심 대상.
2. **`minItems`/`maxItems` 스키마 제약은 140개 근처에서 API가 400을 반환한다(실측).** 큰 배치는 청크로 나눈다.
3. **외부 API는 이름/키 기준으로 중복 제거 후 병렬 호출.** 카카오 검증에서 9배 속도 개선 사례(`ThreadPoolExecutor`, `service.py`의 `_verified_candidates` 참고).
4. **설정은 `config.py` 대문자 상수다(settings YAML은 제거됨).** 설정 변경도 git push로 배포된다. git으로 안 가는 건 루트 `.env`(API 키)뿐 — 새 환경변수를 추가하면 `.env.example`도 고치고, 배포 서버 `.env`를 수동으로 갱신해야 한다고 명확히 알린다.

## 작업 방식

가정하지 말고 실측한다. 실제 Gemini/Kakao/Naver API를 호출해서 원인을 확인하고 고친 뒤 재확인한다. 완료 보고 전 `pytest backend/tests`를 돌리고 결과를 그대로 첨부한다 — "될 것 같다"로 끝내지 않는다.

전체 설계 히스토리와 임계값 근거(왜 `MIN_REPEATED_CANDIDATES=5`인지, 왜 `SCATTER_RADIUS_KM=5`인지 등)는 `docs/decisions.md`에서 확인한다.
