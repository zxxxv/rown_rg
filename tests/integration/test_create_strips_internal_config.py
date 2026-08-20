"""생성 시 서버 내부 config 키 스트립 — 복제 생성이 남의 절 정체성을 물려받지 않는다.

2026-08-21 6차 검증런 실사고: 검증런 복제 스크립트가 이전 프로젝트 config를
_section_plan째 복사해 생성 → 새 프로젝트가 남의 절 id로 실행 → sections.id
전역 PK 충돌로 증분·조립 저장이 전부 침묵 실패(본문 유실). 계획·스냅샷 내부
키는 생성 입력에서 걷어내고, 절 id는 fresh_ids로 항상 새로 발급한다.
"""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestCreateStripsInternalConfig:
    async def test_copied_plan_and_snapshots_dropped(
        self, test_client: AsyncClient, worker_token: str, test_session: AsyncSession
    ) -> None:
        foreign_sid = str(uuid4())
        payload = {
            "title": "복제 생성",
            "topic": "내부 키 스트립 검증",
            "preset": None,
            "config": {
                "model_mode": "standard",
                "outline": {
                    "chapters": [
                        {
                            "id": str(uuid4()),
                            "title": "1장",
                            "sections": [
                                {
                                    "id": foreign_sid,
                                    "title": "절",
                                    "direction": "",
                                    "key_points": [],
                                    "analysts": [],
                                    "builds_on": [],
                                }
                            ],
                        }
                    ]
                },
                # 복제 스크립트가 실어 보내는 서버 내부 키들 — 전부 걷어내야 한다.
                "_section_plan": [
                    {
                        "section_id": foreign_sid,
                        "chapter_number": 1,
                        "section_number": 1,
                        "title": "절",
                    }
                ],
                "_design_plan": {foreign_sid: {"goal": "남의 계획"}},
                "models": {"write": "stale-model"},
                "analysts": [{"name": "얼린 페르소나"}],
                "verify_resolved": ["k1"],
            },
        }
        resp = await test_client.post("/api/v1/projects", json=payload, headers=_auth(worker_token))
        assert resp.status_code == 201, resp.text
        config = resp.json()["config"]
        for key in ("_section_plan", "_design_plan", "models", "analysts", "verify_resolved"):
            assert key not in config, key
        # 절 id도 새 정체성 — 복사해 온 id가 그대로 살아남으면 전역 PK 충돌의 씨앗.
        new_sid = config["outline"]["chapters"][0]["sections"][0]["id"]
        assert new_sid != foreign_sid
        assert config["model_mode"] == "standard"  # 사용자 옵션은 그대로
