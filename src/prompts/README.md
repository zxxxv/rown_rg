# prompts — 회사 표준 프롬프트 자산

사내 n8n `seed-settings`(실서비스에서 쓰던 presets/prompts/agents 정리본)를 이관한
프롬프트 패키지. 로딩은 `src.prompts.loader` 단일 관문을 쓴다.

## 구성

| 위치 | 내용 | 형식 |
|---|---|---|
| `components/` | 공용 프롬프트 조각 — 작성 규칙·문체·출처·개조식·시각자료·검색 지침 | `.md` (system 프롬프트에 조합) |
| `workflow_roles/` | 워크플로 역할 시스템 프롬프트 — Tier1 오케스트레이터, Tier2 챕터 PM, 목차 설계, PM 검증 | `.md` |
| `agentic/analysts/` | 분석 에이전트 페르소나 21종 (STEEP·시장·특허·예산 등) | `.json` 원본 유지 |
| `presets/` | 보고서 유형 프리셋 5종 (예비타당성조사·정책기획 등, 챕터/섹션 골격 + 담당 에이전트) | `.json` 원본 유지 |

## 이관 규칙

- `agentic/analysts/`·`presets/`의 JSON과 `_index.json`은 **업스트림 원본 그대로** 둔다.
  회사 쪽 seed-settings가 갱신되면 파일을 통째로 덮어써서 재동기화한다.
- `components/`·`workflow_roles/`의 `.md`는 seed-settings `prompts/*.json`의 `content`를
  추출한 것. 원본에서 "링크" 단어가 위키피디아 URL로 깨져 있던 부분만
  "확인 가능한 링크(URL)"로 복원했다 (옛 3계층 프롬프트 — 2026-08-26 제거, 핵심은 writer_context.py에 흡수).
- **이관 제외**: 모델별 `layer3_*` 프롬프트와 `model_mapping.json` — 모델 분기·카탈로그는
  `src/clients/llm`(라우터·models.py)이 단일 진실이므로 중복 반입하지 않는다.
  `layer3_perplexity`의 검색 지침만 `components/search_guidelines.md`로 일반화해서 가져왔다.
