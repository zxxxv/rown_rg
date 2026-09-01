# 워크플로 역할 시스템 프롬프트(.md). LLM 호출에 그대로 실린다.
#
# 여기 있는 것은 **런타임에 load_workflow_role()로 실제 로드되는 것만**이다:
#   toc_system          목차 설계(planner)
#   pm_verify_system    문서 횡단 일관성 검증(조립 후, 장별 1콜)
#   claim_verify_system 문장↔근거 대조(절별)
#
# 옛 3계층 실험(Tier1 오케스트레이터 → Tier2 챕터 PM → Tier3 분석가)의 프롬프트
# agent_global_system·tier2_system은 2026-08-26에 지웠다. 지금 파이프라인은 계층이
# 아니라 flat이고(2026-08-06 통제실험: 계층 작성 무근거 +39% 순손해), 두 프롬프트의
# 핵심은 services/generation/writer_context.py에 흡수돼 있다. 카탈로그에 남겨 두면
# 프롬프트 화면에서 "이게 지금 쓰이나"로 읽힌다.
