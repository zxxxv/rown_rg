# Week 4 발표 D-1 체크리스트

> 발표 24시간 전 점검. 실패 항목 발견 시 즉시 PM·개발팀 공유.

## A. 자동 점검 (pnpm preflight)

`pnpm preflight` 한 번에 아래 6개 영역을 점검:

- [ ] **파일 존재** — `public/mockServiceWorker.js`, `public/samples/{hwpx,pdf,md,refs.csv}` 4종
- [ ] **시연용 픽스처** — `proj_demo_w4` 프로젝트 + 모순 3건 + 소스 풀 ≥ 20건
- [ ] **라우트 등록** — 필수 13개 라우트 + MSW 핸들러 9종 모두 등록
- [ ] **디자인 토큰** — `src/{components,features,pages}`에 `#hex` 색상 0건
- [ ] **빌드·타입·린트** — `pnpm typecheck`·`pnpm lint`·`pnpm build` 모두 0 에러
- [ ] **빌드 산출물** — `dist/index.html`에 `__components` 경로 노출 없음 (dev 전용 라우트 격리)

## B. 시연 흐름 1회 리허설 (12분)

발표 환경(해상도·브라우저)에서 직접 실행. URL 직접 입력 + 사이드바 클릭 모두 시도.

1. [ ] `http://localhost:5173/login` → `admin@loweninsight.kr` / `demo1234` → `/projects` 자동 이동
2. [ ] `/projects?demo=1` → "발표 모드" 배지 우상단 확인
3. [ ] 검색창에 "광역교통" 입력 → 카드 필터 0.5초 이내 갱신
4. [ ] 카드 클릭 → `/projects/:id/overview` 진입
5. [ ] [새 프로젝트] 클릭 → `/projects/new` → 프리셋 4종 토글 시연 (영역 1~7 옵션 변경 시 우측 견적 실시간 갱신)
6. [ ] `/projects/proj_demo_w4/overview?demo=1` → 진행 요약·옵션 변경 Accordion 펼침 확인
7. [ ] `/projects/proj_demo_w4/sources` → 자료 카드 채택·제외 → 카운터 실시간 갱신
8. [ ] `/projects/proj_demo_w4/reconcile` → 모순 3건 → 자료 A 채택 → 토스트 + 다음 모순 자동 선택
9. [ ] `/projects/proj_demo_w4/progress?demo=1` → 30초 시나리오 자동 재생 → checkpoint 도래 시 ReviewCheckpoint 전환 → [승인] 클릭 → 진행 패널 복귀
10. [ ] `/projects/proj_demo_w4/preview` → 트리에서 "2.3 인구·고령화 영향" 클릭 → 본문 표시 + `[ref:src_kostat_2024]` 마커 호버 → 카드 부드럽게 표시
11. [ ] `/projects/proj_demo_w4/editor` → E 키 토글 → 마진 막대 표시 → 본문 호버 → 우측 부유 메뉴 → [↻ 재작성] 클릭 → RewriteDialog 열림 → 칩 토글 → 시작
12. [ ] `/projects/proj_001/export` (또는 status=completed 프로젝트) → 카드 3개 + 회사 양식 정보 → [다운로드] → 브라우저 다운로드 트리거
13. [ ] `/admin/dashboard` → KPI 4 카드 + 일별 비용 차트 + 사용자 테이블 → [한도 초과 승인 요청] 박지영 [승인] → 토스트
14. [ ] `/library` → 트리 확장/축소 → 검색 "고령화" → 매칭 노드만 표시 + 하이라이트
15. [ ] 사이드바 토글 → 64px 접힘 상태 확인

## C. 환경 점검

- [ ] **해상도**: 발표 화면이 1920x1080 이상인지 확인. 1440px 이하라면 사이드바 접기.
- [ ] **브라우저**: Chrome/Edge 최신. Safari·Firefox는 검증 안 됨.
- [ ] **네트워크**: MSW가 모든 API를 가로채므로 오프라인에서도 작동. 단 폰트(Pretendard) CDN 미사용 — 자체 호스팅이라 OK.
- [ ] **저장 공간**: 시연 PC에 `pnpm install` 완료 + `node_modules` 약 600MB 확보.
- [ ] **포트 충돌**: 5173 포트 점유 시 자동으로 5174·5175로 fallback. URL을 PM이 직접 확인.

## D. 백업 영상

PM 작업 — 발표 D-2 완료:

- [ ] Playwright로 위 시연 흐름 1·11 핵심 5분 클립 녹화
- [ ] 영상 URL을 발표 슬라이드 후반에 비공개 백업으로 포함
- [ ] 라이브 데모 실패 시 즉시 영상으로 전환 가능한 단축키 / 슬라이드 준비

## E. 데이터 인덱싱 (백엔드 PM 작업)

이 항목은 프론트엔드 mock과 무관하지만, **발표 후 Q&A에서 "실제 백엔드는 어떻게 되나"**
질문이 나올 가능성에 대비:

- [ ] BGE-M3 ONNX 임베딩 모델이 로드되는지 확인
- [ ] pgroonga 인덱스가 시연 자료 100건에 대해 정상 생성됐는지 확인
- [ ] HWPX 파싱 샘플 1건이 실제 페이지·문단 단위로 분할되는지 확인

## F. Q&A 예상 질문 (답변 초안 포함)

1. **Q: "지금 화면이 모킹인 건 알고 있는데, 실제 백엔드는 언제 붙나요?"**
   A: Phase 3(Week 5~7)부터 단계적으로 실 백엔드 연동. 현재는 MSW로 모든 API를 가로채는 구조라
   백엔드 엔드포인트만 같으면 mock 핸들러 한 줄씩 제거하며 점진 전환 가능. WebSocket·snapshot 패턴도
   완성돼 있어 진짜 작성 작업이 시작되면 그대로 흐름 작동.

2. **Q: "200~300페이지 보고서를 진짜로 만들 수 있나요? 시간·비용이 어떻게 됩니까?"**
   A: 견적 화면에 표시되는 5~10시간 / $130~$430 범위는 spec §3.0.4 기반 추정치. 실 비용은
   사용한 LLM 모델·청크 분할·재시도 횟수에 따라 변동. 실 백엔드 통합 후 Phase 5 Critic Agent까지
   모두 켠 풀세트 시나리오에서 실측 예정.

3. **Q: "한컴 HWPX 양식은 정확히 어떻게 적용되나요?"**
   A: 백엔드 python-hwpx 라이브러리로 회사 표준 양식(함초롬바탕 11pt / 함초롬돋움 16·14·12pt /
   160% 줄간격 / 상하 20mm·좌 30mm·우 20mm 여백)을 템플릿화. Phase 4에서 양식 관리 UI(/admin/hwpx-style)
   도입 후 양식 변경도 가능.

4. **Q: "사용자가 작성한 보고서가 다른 회사로 유출되지 않나요?"**
   A: 모든 데이터는 회사 사내 인프라에 격리. LLM 호출 시 OpenAI/Anthropic 본 API가 아닌
   Azure OpenAI 또는 자체 호스팅 모델 사용 예정 (Phase 4 보안 검토 완료 후). IP 화이트리스트(/admin/ip)도
   Phase 4에서 본격 작동.

5. **Q: "Critic Agent가 정말 의미 있는 검토를 해주나요? GPT가 흉내내는 게 아니라?"**
   A: Critic Agent는 일관성 그래프(Phase 3) 결과를 입력으로 받아 "이 주장은 이 자료에 근거하지만
   상충하는 자료 B가 있다"는 식의 구조화된 검토를 수행. 일반 LLM 검토와 달리 출처 추적이 1-클릭으로
   가능. 시연에서 본 Critic 사고 과정 typewriter가 그 출력 일부.

## G. 발표 직후 즉시 작업

- [ ] 발표 영상 녹화본을 회사 내부 저장소에 업로드
- [ ] 발표 Q&A 답변을 정리해 회의록으로 추가
- [ ] Phase 3 진입 일정 PM 공유 (다음 주 월요일 킥오프)
