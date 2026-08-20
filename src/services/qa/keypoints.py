"""핵심 포인트 커버리지 — 결정적 대조(목차 지시 미반영의 게이트 쪽 방어).

PM의 "목차 지시 미반영"(LLM 판정)은 사후 경고고, 이 검사는 생성 직후에 결정적으로
재어 미반영이 크면 1회 환송한다. 판정은 가중 토큰 겹침 — 키포인트의 드문 토큰이
본문 어딘가에 충분히 나타나면 반영으로 본다(의역 허용, 보수적 임계로 오환송 방지).
"""

from __future__ import annotations

from src.services.qa.alignment import _weighted_tokens

# 반영 판정 임계 — 키포인트 가중 토큰의 이 비율이 본문에 있어야 한다. 보수적으로
# 낮게(0.45) 둔다: 환송 1회가 절 재생성 전체($0.7)라 오환송이 미반영 방치보다 비싸다.
COVERED_THRESHOLD = 0.45
# 환송 발동 하한 — 미반영이 이만큼 쌓여야 재생성한다(1건은 지시 강화만으로 못 잡는
# 수준이 아니라 판정 노이즈일 수 있다).
RETRY_MIN_MISSED = 2


def missed_keypoints(content: str, key_points: list[str]) -> list[str]:
    """본문에 반영되지 않은 키포인트 목록(원문 순서 유지)."""
    if not content or not key_points:
        return []
    body = _weighted_tokens(content)
    missed: list[str] = []
    for kp in key_points:
        kp = (kp or "").strip()
        if not kp:
            continue
        tokens = _weighted_tokens(kp)
        if not tokens:
            continue
        total = sum(tokens.values())
        hit = sum(w for t, w in tokens.items() if t in body)
        if total > 0 and hit / total < COVERED_THRESHOLD:
            missed.append(kp)
    return missed


def keypoint_findings(
    sections: list[tuple[object, str, list[str]]],
) -> list[dict[str, object]]:
    """(절 계획, 본문, 키포인트) → 미반영 경고 행(pm_verify 행 모양, 웹 전용).

    사용자 결정(2026-08-20): 키포인트가 본문에 안 실렸으면 웹에서 알 수 있게 한다 —
    자료를 다시 넣어 재작성할 수 있는 신호다(HWPX 최종 출력에는 안 나간다).
    판정은 결정적(가중 토큰 겹침) — LLM 판정 축(목차 지시 미반영)은 노이즈가 커서
    (v5c-2 실측: LLM 14건 vs 결정적 3건) 이 축을 병기한다.
    """
    rows: list[dict[str, object]] = []
    for plan, content, key_points in sections:
        missed = missed_keypoints(content, key_points)
        if not missed:
            continue
        rows.append(
            {
                "chapter_number": plan.chapter_number,
                "severity": "warning",
                "category": "핵심 포인트 미반영(결정적)",
                "section_ref": f"{plan.chapter_number}.{plan.section_number}",
                "detail": (
                    f"목차에 적힌 핵심 포인트 {len(missed)}건이 본문에서 확인되지 않음: "
                    + " / ".join(m[:40] for m in missed[:5])
                    + " — 자료에 근거가 있으면 절 재작성으로, 없으면 자료 보강 후 재작성으로 해결"
                ),
            }
        )
    return rows
