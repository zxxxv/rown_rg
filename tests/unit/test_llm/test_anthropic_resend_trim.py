"""pause_turn 재전송의 회수 본문 절단 — 입력 상한 초과로 장이 죽던 자리.

배경: pause_turn 재전송은 직전 턴까지의 대화를 통째로 다시 보낸다. base64 PDF는
치환해 왔지만 HTML 본문 텍스트는 그대로 쌓여, 회수가 몇 번 겹치면 모델 입력 상한을
넘겼다(2026-08-19 스모크 실측: prompt is too long 1,136,960 > 200,000 → 1장 수집
전멸, 자료 2건). 잘라도 우리가 잃는 것은 없다 — _collect_sources가 매 턴 돌아 본문을
web_sources로 확보한 뒤에 이 치환기가 불린다.

**모델 자신의 텍스트는 절대 자르지 않는다.** 최종 매니페스트 JSON이 잘리면 출처
매칭이 통째로 소실된다 — 그래서 도구 결과 블록 안에서만 자른다.
"""

from __future__ import annotations

from typing import Any

from src.clients.llm.adapters.anthropic import (
    FETCH_RESEND_KEEP_CHARS,
    FETCH_RESEND_NOTICE,
    AnthropicAdapter,
    _trim_fetched_bodies,
)


class _Block:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.type = payload.get("type", "")

    def model_dump(self) -> dict[str, Any]:
        return self._payload


_LONG = "가" * (FETCH_RESEND_KEEP_CHARS * 3)


def _fetch_block(body: str) -> dict[str, Any]:
    return {
        "type": "web_fetch_tool_result",
        "content": {
            "type": "web_fetch_result",
            "url": "https://example.gov/report",
            "content": {"type": "document", "source": {"type": "text", "data": body}},
        },
    }


def test_long_fetched_body_is_trimmed() -> None:
    trimmed, n = _trim_fetched_bodies(_fetch_block(_LONG))
    assert n == 1
    body = trimmed["content"]["content"]["source"]["data"]
    assert len(body) < len(_LONG)
    assert body.endswith(FETCH_RESEND_NOTICE)


def test_short_body_is_left_alone() -> None:
    """짧은 본문까지 건드리면 모델이 방금 읽은 자료를 잃는다."""
    trimmed, n = _trim_fetched_bodies(_fetch_block("짧은 본문"))
    assert n == 0
    assert trimmed["content"]["content"]["source"]["data"] == "짧은 본문"


def test_url_and_title_survive() -> None:
    """URL은 남아야 모델이 필요할 때 다시 회수할 수 있다."""
    trimmed, _ = _trim_fetched_bodies(_fetch_block(_LONG))
    assert trimmed["content"]["url"] == "https://example.gov/report"


def test_trimming_finds_body_regardless_of_nesting() -> None:
    """중첩 경로를 고정하면 도구 버전이 바뀔 때 조용히 통과한다(3차 재발의 교훈)."""
    block = {"type": "web_search_tool_result", "deep": [{"weird": {"nested": _LONG}}]}
    trimmed, n = _trim_fetched_bodies(block)
    assert n == 1
    assert trimmed["deep"][0]["weird"]["nested"].endswith(FETCH_RESEND_NOTICE)


def test_model_own_text_block_is_never_trimmed() -> None:
    """최종 매니페스트 JSON이 잘리면 출처 매칭이 통째로 소실된다."""
    manifest = {"type": "text", "text": _LONG}
    trimmed, n = _trim_fetched_bodies(manifest)
    assert n == 0
    assert trimmed["text"] == _LONG


def test_thinking_block_is_never_trimmed() -> None:
    trimmed, n = _trim_fetched_bodies({"type": "thinking", "thinking": _LONG})
    assert n == 0


def test_sanitize_trims_and_keeps_other_blocks() -> None:
    out = AnthropicAdapter._sanitize_for_resend(
        [_Block(_fetch_block(_LONG)), _Block({"type": "text", "text": _LONG})]
    )
    assert len(out) == 2
    fetched = out[0]
    assert isinstance(fetched, dict)
    assert fetched["content"]["content"]["source"]["data"].endswith(FETCH_RESEND_NOTICE)
    # 모델 텍스트 블록은 원본 객체 그대로 통과한다(치환 대상이 아니므로).
    assert isinstance(out[1], _Block)
