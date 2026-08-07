"""pause_turn 재전송의 base64 PDF 치환 — 3차 재발 방지 회귀 테스트.

배경: web_fetch가 회수한 100페이지 초과 PDF를 그대로 재전송하면 API가 400으로
거부해 챕터 수집이 통째로 죽는다. 고정 경로(getattr 체인) 방식은 블록 중첩이
달라지면 조용히 통과해 두 번 재발했다(2026-08-03, 2026-08-06) — 그래서 값 기반
재귀 치환으로 바꿨고, 이 테스트가 그 계약을 고정한다.
"""

from __future__ import annotations

from typing import Any

from src.clients.llm.adapters.anthropic import (
    PDF_RESEND_PLACEHOLDER,
    AnthropicAdapter,
    _scrub_base64_sources,
)


class _Block:
    """model_dump()만 흉내내는 SDK 블록 대역."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.type = payload.get("type", "")

    def model_dump(self) -> dict[str, Any]:
        return self._payload


def _pdf_source() -> dict[str, Any]:
    return {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjcK" * 100}


def _known_shape() -> dict[str, Any]:
    """1차 수정이 상정했던 중첩 — web_fetch_tool_result → content → content → source."""
    return {
        "type": "web_fetch_tool_result",
        "content": {
            "type": "web_fetch_result",
            "url": "https://example.go.kr/report.pdf",
            "content": {"type": "document", "source": _pdf_source()},
        },
    }


def _deeper_shape() -> dict[str, Any]:
    """중첩이 한 겹 더 깊고 리스트를 낀 형태 — 고정 경로 방식이 놓치던 케이스."""
    return {
        "type": "web_fetch_tool_result",
        "content": [
            {
                "type": "web_fetch_result",
                "url": "https://example.re.kr/paper.pdf",
                "document": {"source": _pdf_source()},
            }
        ],
    }


def test_scrub_replaces_pdf_in_known_shape() -> None:
    scrubbed, n = _scrub_base64_sources(_known_shape())
    assert n == 1
    source = scrubbed["content"]["content"]["source"]
    assert source["type"] == "text"
    assert source["data"] == PDF_RESEND_PLACEHOLDER


def test_scrub_replaces_pdf_regardless_of_nesting() -> None:
    """경로가 바뀌어도 잡아야 한다 — 이게 3차 재발의 직접 원인이었다."""
    scrubbed, n = _scrub_base64_sources(_deeper_shape())
    assert n == 1
    assert scrubbed["content"][0]["document"]["source"]["data"] == PDF_RESEND_PLACEHOLDER


def test_scrub_leaves_text_documents_untouched() -> None:
    """텍스트로 회수된 본문은 우리 추출 경로가 쓰므로 건드리면 안 된다."""
    payload = {
        "type": "web_fetch_tool_result",
        "content": {
            "type": "web_fetch_result",
            "content": {
                "type": "document",
                "source": {"type": "text", "media_type": "text/plain", "data": "본문입니다"},
            },
        },
    }
    scrubbed, n = _scrub_base64_sources(payload)
    assert n == 0
    assert scrubbed["content"]["content"]["source"]["data"] == "본문입니다"


def test_scrub_counts_multiple_pdfs() -> None:
    payload = {"blocks": [_known_shape(), _deeper_shape(), {"type": "text", "text": "요약"}]}
    _, n = _scrub_base64_sources(payload)
    assert n == 2


def test_sanitize_for_resend_strips_and_preserves_other_blocks() -> None:
    text_block = _Block({"type": "text", "text": "검색을 이어간다"})
    pdf_block = _Block(_deeper_shape())

    out = AnthropicAdapter._sanitize_for_resend([text_block, pdf_block])

    assert len(out) == 2
    # PDF가 없는 블록은 원본 객체 그대로 재전송(직렬화 변형 최소화).
    assert out[0] is text_block
    # PDF 블록만 dict로 치환된다.
    assert isinstance(out[1], dict)
    assert out[1]["content"][0]["document"]["source"]["data"] == PDF_RESEND_PLACEHOLDER
    assert "JVBERi" not in str(out[1])


def test_sanitize_survives_blocks_without_model_dump() -> None:
    """model_dump가 없는 값이 섞여도 죽지 않는다(폴백 경로)."""
    out = AnthropicAdapter._sanitize_for_resend([{"type": "text", "text": "그대로"}])
    assert out == [{"type": "text", "text": "그대로"}]
