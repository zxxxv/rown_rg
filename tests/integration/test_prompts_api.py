"""개인 프롬프트 레이어 API 통합 테스트 — CRUD·층화(개인→시스템)·시스템 카탈로그·트리."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _find(nodes: list[dict[str, Any]], node_id: str) -> dict[str, Any] | None:
    for n in nodes:
        if n["id"] == node_id:
            return n
        if n["type"] == "folder":
            hit = _find(n["children"], node_id)
            if hit is not None:
                return hit
    return None


class TestPersonalPrompts:
    async def test_crud_roundtrip(self, test_client: AsyncClient, worker_token: str) -> None:
        # 새 개인 에이전트 생성(base_ref 없음)
        created = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={
                "kind": "agent",
                "name": "우리회사 전용 분석가",
                "content": "너는 우리 회사 맥락에 특화된 분석가다.",
                "cat": "커스텀",
                "description": "사내 전용",
            },
        )
        assert created.status_code == 201, created.text
        pid = created.json()["id"]

        # 목록에 뜬다
        listed = await test_client.get("/api/v1/prompts/personal", headers=_auth(worker_token))
        assert pid in [p["id"] for p in listed.json()]

        # 단건 조회(본문 포함)
        got = await test_client.get(f"/api/v1/prompts/personal/{pid}", headers=_auth(worker_token))
        assert got.json()["content"] == "너는 우리 회사 맥락에 특화된 분석가다."

        # 수정
        patched = await test_client.patch(
            f"/api/v1/prompts/personal/{pid}",
            headers=_auth(worker_token),
            json={"content": "개정된 지침."},
        )
        assert patched.status_code == 200
        assert patched.json()["content"] == "개정된 지침."

        # 삭제
        deleted = await test_client.delete(
            f"/api/v1/prompts/personal/{pid}", headers=_auth(worker_token)
        )
        assert deleted.status_code == 204
        gone = await test_client.get(f"/api/v1/prompts/personal/{pid}", headers=_auth(worker_token))
        assert gone.status_code == 404

    async def test_owner_isolation(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        created = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={"kind": "rule", "name": "내 문체", "content": "간결하게."},
        )
        pid = created.json()["id"]
        # 다른 사용자는 접근 불가(404)
        other = await test_client.get(
            f"/api/v1/prompts/personal/{pid}", headers=_auth(super_admin_token)
        )
        assert other.status_code == 404

    async def test_new_personal_agent_appears_in_analysts(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={"kind": "agent", "name": "신규 개인 분석가", "content": "..."},
        )
        analysts = await test_client.get("/api/v1/analysts", headers=_auth(worker_token))
        assert "신규 개인 분석가" in [a["name"] for a in analysts.json()]

    async def test_override_system_agent(self, test_client: AsyncClient, worker_token: str) -> None:
        # a01(STEEP분석)을 개인 프롬프트로 덮어쓴다
        await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={
                "kind": "agent",
                "name": "내 STEEP",
                "content": "덮어쓴 프롬프트 본문",
                "base_ref": "a01",
            },
        )
        analysts = await test_client.get("/api/v1/analysts", headers=_auth(worker_token))
        names = [a["name"] for a in analysts.json()]
        # 시스템 a01은 그대로 이름 유지(id/name 보존, 프롬프트만 교체) — 새 항목이 아님
        assert names.count("STEEP분석") == 1

    async def test_unknown_base_ref_rejected(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        resp = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={
                "kind": "agent",
                "name": "x",
                "content": "y",
                "base_ref": "존재하지않는에이전트",
            },
        )
        assert resp.status_code == 422


class TestSystemCatalog:
    async def test_list_and_get(self, test_client: AsyncClient, worker_token: str) -> None:
        agents = await test_client.get(
            "/api/v1/prompts/system?kind=agent", headers=_auth(worker_token)
        )
        assert agents.status_code == 200
        refs = [a["ref"] for a in agents.json()]
        assert "a01" in refs

        one = await test_client.get("/api/v1/prompts/system/agent/a01", headers=_auth(worker_token))
        assert one.status_code == 200
        assert one.json()["name"] == "STEEP분석"
        assert one.json()["content"]  # 본문 존재

    async def test_get_unknown_404(self, test_client: AsyncClient, worker_token: str) -> None:
        resp = await test_client.get(
            "/api/v1/prompts/system/agent/nope", headers=_auth(worker_token)
        )
        assert resp.status_code == 404


class TestPromptsInTree:
    async def test_personal_and_system_prompts_in_tree(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        created = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={"kind": "agent", "name": "트리 표시 에이전트", "content": "..."},
        )
        pid = created.json()["id"]

        tree = await test_client.get("/api/v1/library/tree", headers=_auth(worker_token))
        roots = tree.json()["tree"]

        # 개인 루트 > 프롬프트 > 내 에이전트 에 개인 프롬프트 노드가 있다
        my_agents = _find(roots, "me-agents")
        assert my_agents is not None
        node = next((c for c in my_agents["children"] if c["id"] == f"uprompt-{pid}"), None)
        assert node is not None
        assert node["prompt"]["scope"] == "personal"
        assert node["prompt"]["editable"] is True
        assert node["prompt"]["ref"] == pid

        # 회사 공유 > 시스템 프롬프트 > 에이전트 에 시스템 a01 노드가 있다(읽기전용)
        sys_agents = _find(roots, "sys-agents")
        assert sys_agents is not None
        sys_node = _find(sys_agents["children"], "sysagent-a01")
        assert sys_node is not None
        assert sys_node["prompt"]["editable"] is False
