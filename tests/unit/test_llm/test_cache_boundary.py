"""수집 루프의 롤링 캐시 경계 — pause_turn 재전송 비용의 핵심.

실측(7일): research.collect 입력 14.3M 중 캐시 읽기 2.4M(14%)뿐이고 전체 비용의
절반이 여기였다. 경계가 첫 요청 메시지에만 있어, 회수 본문이 쌓인 뒤에도 캐시
프리픽스가 수백 토큰에 머물렀다(Haiku 최소 4,096토큰 미만이라 무시).
"""

from __future__ import annotations

from src.clients.llm.adapters.anthropic import _move_cache_boundary


def _cc(block: dict) -> bool:
    return "cache_control" in block


class TestMoveCacheBoundary:
    def test_marks_last_block_of_last_message(self):
        msgs = [{"role": "user", "content": "질의"}]
        _move_cache_boundary(msgs)
        assert _cc(msgs[0]["content"][-1])

    def test_boundary_moves_and_old_one_is_cleared(self):
        msgs = [{"role": "user", "content": "질의"}]
        _move_cache_boundary(msgs)
        msgs.append({"role": "assistant", "content": [{"type": "text", "text": "회수한 본문 …"}]})
        _move_cache_boundary(msgs)
        # 경계는 하나뿐이어야 한다 - 4개 상한이 있고, 끝이 곧 최장 프리픽스다.
        assert not any(_cc(b) for b in msgs[0]["content"])
        assert _cc(msgs[1]["content"][-1])

    def test_unsafe_block_type_is_skipped(self):
        # thinking 같은 유형에 붙이면 400으로 챕터가 통째로 죽는다.
        msgs = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "..."}]}]
        _move_cache_boundary(msgs)
        assert not _cc(msgs[0]["content"][0])

    def test_prefers_last_cacheable_block(self):
        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "앞"},
                    {"type": "web_fetch_tool_result", "content": {}},
                    {"type": "thinking", "thinking": "..."},
                ],
            }
        ]
        _move_cache_boundary(msgs)
        assert _cc(msgs[0]["content"][1])

    def test_empty_messages_is_noop(self):
        msgs: list = []
        _move_cache_boundary(msgs)
        assert msgs == []
