"""근거 동봉 판정 — 의심 문장과 그 근거 원문을 같이 넘겨 LLM에 뒷받침 여부를 묻는다.

어휘 겹침(services/qa/alignment)은 함의를 못 본다. 같은 내용을 다른 말로 쓰면 겹침이
낮게 나오고, 단어를 베낀 채 뜻을 뒤집으면 겹침이 높게 나온다. 그래서 겹침만으로 경고를
띄우면 제언·종합 절처럼 의역이 많은 자리에서 오탐이 쏟아진다 — 오탐이 몇 번 나면 사람은
목록 자체를 안 믿는다.

역할을 나눈다:
- 어휘 겹침(비용 0) = 재현율. 의심스러운 문장을 넉넉히 건져 올린다.
- 여기(LLM) = 정밀도. 근거 원문을 같이 주고 "이 근거로 뒷받침되나"만 묻는다.

비용 캡: 의심 문장이 있는 절에만, 절당 1콜. 근거 원문은 청크 단위로 중복 제거해 한 번만
싣는다(여러 문장이 같은 청크를 인용한다). 실패·타임아웃은 비치명 — 판정을 못 하면
결정적 결과를 그대로 쓴다(경고가 줄지 않을 뿐, 없던 경고가 생기지는 않는다).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.config import settings
from src.prompts import load_workflow_role
from src.services.generation.planner import _parse_manifest
from src.services.qa.alignment import ClaimAlignment

logger = structlog.get_logger(__name__)

DEFAULT_MAX_TOKENS = 2000
# 한 콜에 담는 문장 수 상한 — 넘치면 앞에서 자른다(무한성 캡).
MAX_CLAIMS_PER_CALL = 24
# 근거 원문 상한 — 청크 하나가 길어도 앞부분이면 판정에 충분하다.
_MAX_CHUNK_CHARS = 1_600
_MAX_EVIDENCE_CHARS = 24_000

_VERDICTS = {"supported", "not_supported", "unclear"}

_FORMAT = (
    "아래 형식의 JSON만 출력한다(설명 문장 없이):\n"
    '```json\n{"verdicts": [{"id": 1, "verdict": "supported|not_supported|unclear", '
    '"reason": "..."}]}\n```\n'
    "받은 문장 전부에 대해 한 건씩 출력한다."
)


@dataclass
class ClaimVerdict:
    """문장 하나의 판정 결과."""

    index: int  # 요청에 실은 문장 순번(0부터)
    verdict: str
    reason: str = ""

    @property
    def is_supported(self) -> bool:
        return self.verdict == "supported"


def _cited_ids(claim: ClaimAlignment) -> list[UUID]:
    """그 문장이 인용한 청크 전부 — 없으면 대목의 청크로 폴백(옛 호출부 호환)."""
    if claim.cited_chunk_ids:
        return list(claim.cited_chunk_ids)
    return [claim.span.chunk_id] if claim.span else []


def _evidence_block(claims: list[ClaimAlignment], chunk_texts: dict[UUID, str]) -> str:
    """문장들이 인용한 청크를 중복 없이 모아 번호를 붙인다.

    대목(span) 하나가 아니라 **인용한 청크 전부**를 싣는다. 대목은 어휘 겹침으로
    고른 것이라 한글 청크가 뽑히는데, 한글·영문을 함께 인용한 문장에서는 정작
    그 문장을 받치는 영문 청크가 판정관에게 안 보였다(2026-08-24 COMPA 실측).
    """
    seen: list[UUID] = []
    for claim in claims:
        for cid in _cited_ids(claim):
            if cid not in seen:
                seen.append(cid)
    lines: list[str] = []
    total = 0
    dropped = 0
    for i, cid in enumerate(seen, start=1):
        text = (chunk_texts.get(cid) or "").strip()[:_MAX_CHUNK_CHARS]
        if not text:
            continue
        block = f"[근거 {i}]\n{text}"
        if total + len(block) > _MAX_EVIDENCE_CHARS:
            dropped += 1
            continue
        total += len(block)
        lines.append(block)
    if dropped:
        # 근거가 잘리면 판정관은 못 본 근거를 '없다'고 답한다 — 그 자체가 오탐 공장이라
        # 조용히 넘기지 않는다(2026-08-24: 인용 청크 전부를 싣게 하며 위험이 커졌다).
        logger.warning(
            "claim_verify.evidence_truncated", n_chunks=len(seen), dropped=dropped, chars=total
        )
    return "\n\n".join(lines)


def _claim_block(claims: list[ClaimAlignment], chunk_texts: dict[UUID, str]) -> str:
    """문장마다 자기가 인용한 근거 번호를 달아 나열한다(여러 개면 모두)."""
    order: list[UUID] = []
    for claim in claims:
        for cid in _cited_ids(claim):
            if cid not in order:
                order.append(cid)
    lines: list[str] = []
    for i, claim in enumerate(claims):
        refs = [str(order.index(cid) + 1) for cid in _cited_ids(claim) if cid in order]
        ref = f" (근거 {', '.join(refs)})" if refs else ""
        lines.append(f"{i + 1}.{ref} {claim.claim}")
    return "\n".join(lines)


def build_prompt(claims: list[ClaimAlignment], chunk_texts: dict[UUID, str]) -> str:
    """판정 요청 본문 (순수 함수 — 테스트 대상)."""
    return (
        "다음 문장들이 각자 인용한 근거로 뒷받침되는지 판정하라.\n\n"
        f"[근거 원문]\n{_evidence_block(claims, chunk_texts)}\n\n"
        f"[검증할 문장]\n{_claim_block(claims, chunk_texts)}"
    )


def parse_verdicts(content: str, n_claims: int) -> dict[int, ClaimVerdict]:
    """모델 응답 → {문장 순번: 판정}. 범위 밖·모르는 값은 버린다."""
    manifest = _parse_manifest(content)
    out: dict[int, ClaimVerdict] = {}
    for row in manifest.get("verdicts", []) if isinstance(manifest, dict) else []:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("id", 0)) - 1
        except (TypeError, ValueError):
            continue
        verdict = str(row.get("verdict", "")).strip()
        if not (0 <= index < n_claims) or verdict not in _VERDICTS:
            continue
        out[index] = ClaimVerdict(
            index=index, verdict=verdict, reason=str(row.get("reason", "")).strip()[:200]
        )
    return out


async def verify_claims(
    claims: list[ClaimAlignment],
    chunk_texts: dict[UUID, str],
    *,
    client: LLMClient | None = None,
    model: str | None = None,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
    section_ref: str = "",
) -> dict[int, ClaimVerdict]:
    """의심 문장 묶음을 1콜로 판정한다. 실패하면 빈 dict(=판정 없음)."""
    claims = claims[:MAX_CLAIMS_PER_CALL]
    if not claims:
        return {}
    client = client or get_llm_client()
    model = model or settings.claim_verify_model or settings.verify_model
    system = f"{load_workflow_role('claim_verify_system')}\n\n{_FORMAT}"
    request = CompletionRequest(
        messages=[Message(role="user", content=build_prompt(claims, chunk_texts))],
        model=model,
        system=system,
        temperature=0.0,
        max_tokens=DEFAULT_MAX_TOKENS,
        cache_key=None,
    )
    try:
        with token_context(
            user_id=user_id,
            project_id=project_id,
            operation=f"qa.claim_verify:{section_ref}" if section_ref else "qa.claim_verify",
        ):
            response = await client.complete(request)
        verdicts = parse_verdicts(response.content, len(claims))
    except Exception:
        logger.warning("claim_verify.failed", section=section_ref, exc_info=True)
        return {}
    logger.info(
        "claim_verify.done",
        section=section_ref,
        n_claims=len(claims),
        n_verdicts=len(verdicts),
        supported=sum(1 for v in verdicts.values() if v.is_supported),
    )
    return verdicts
