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

    async def test_sections_only_create_composes_content(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        """칸만 채운 저장 — content는 서버가 조합한다. min_length에 잘리던 결함 회귀 방지
        (2026-08-12 QA: "저장 실패, 입력을 확인해주세요"만 뜨고 원인 표시 불가)."""
        resp = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={
                "kind": "agent",
                "name": "칸입력 에이전트",
                "content": "",
                "spec": {"sections": {"mission": "규제 동향을 추적한다", "method": "1. 수집"}},
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "## 임무" in body["content"]
        assert "규제 동향을 추적한다" in body["content"]
        # 칸 값은 재편집용으로 spec에 남는다
        assert body["spec"]["sections"]["mission"] == "규제 동향을 추적한다"


class TestValidationErrorEnvelope:
    """요청 검증 실패(422)가 우리 에러 봉투로, 한국어 필드 경로·이유와 함께 나가는지.

    FastAPI 기본 {detail:[...]}로 새면 프론트 공통 클라이언트가 못 읽어
    "요청을 처리할 수 없습니다"라는 정체불명 문구가 된다(2026-08-12 QA).
    """

    async def test_empty_body_names_reason_in_korean(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        resp = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={"kind": "agent", "name": "빈 에이전트", "content": ""},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" not in body
        assert body["error"]["code"] == "VALIDATION_ERROR"
        assert "본문 또는 칸" in body["error"]["message"]

    async def test_volume_range_names_field_path(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        resp = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={
                "kind": "agent",
                "name": "분량 오류",
                "content": "본문",
                "spec": {"min_chars": 500, "max_chars": 800},
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        msg = body["error"]["message"]
        # 어느 필드가 왜 틀렸는지 문장에 실린다
        assert "spec.min_chars" in msg
        assert "1000 이상" in msg
        fields = body["error"]["details"]["fields"]
        assert {f["field"] for f in fields} == {"spec.min_chars", "spec.max_chars"}

    async def test_min_over_max_uses_custom_korean_message(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        resp = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={
                "kind": "agent",
                "name": "역전 분량",
                "content": "본문",
                "spec": {"min_chars": 20000, "max_chars": 15000},
            },
        )
        assert resp.status_code == 422
        msg = resp.json()["error"]["message"]
        assert "최소는 최대보다 작아야" in msg
        assert "Value error" not in msg


class TestSharedAgents:
    """공개 토글 — 잘 만든 개인 에이전트를 사내가 함께 쓴다(2026-08-19).

    그전엔 owner_id 스코프라 같은 에이전트를 계정마다 손으로 심어야 했다.
    """

    async def _create_public(self, client: AsyncClient, token: str, name: str) -> str:
        created = await client.post(
            "/api/v1/prompts/personal",
            headers=_auth(token),
            json={
                "kind": "agent",
                "name": name,
                "content": "너는 공개된 분석가다.",
                "is_public": True,
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["is_public"] is True
        return created.json()["id"]

    async def test_public_agent_appears_for_other_user(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        await self._create_public(test_client, worker_token, "공개 탄소규제 분석가")
        analysts = await test_client.get("/api/v1/analysts", headers=_auth(super_admin_token))
        hit = next(a for a in analysts.json() if a["name"] == "공개 탄소규제 분석가")
        assert hit["shared"] is True
        assert hit["owner_name"]  # 누구 것인지 보여야 같은 이름 둘을 가릴 수 있다

    async def test_private_agent_stays_invisible(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={"kind": "agent", "name": "비공개 분석가", "content": "..."},
        )
        analysts = await test_client.get("/api/v1/analysts", headers=_auth(super_admin_token))
        assert "비공개 분석가" not in [a["name"] for a in analysts.json()]

    async def test_owner_sees_own_agent_once_not_twice(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        """공개 층에서 자기 것을 또 넣으면 목록에 두 벌 뜬다 - 개인 층에서만 나와야 한다."""
        await self._create_public(test_client, worker_token, "내가 공개한 분석가")
        analysts = await test_client.get("/api/v1/analysts", headers=_auth(worker_token))
        names = [a["name"] for a in analysts.json()]
        assert names.count("내가 공개한 분석가") == 1
        mine = next(a for a in analysts.json() if a["name"] == "내가 공개한 분석가")
        assert mine["shared"] is False

    async def test_toggle_off_removes_from_others(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        pid = await self._create_public(test_client, worker_token, "잠깐 공개한 분석가")
        patched = await test_client.patch(
            f"/api/v1/prompts/personal/{pid}",
            headers=_auth(worker_token),
            json={"is_public": False},
        )
        assert patched.status_code == 200
        assert patched.json()["is_public"] is False
        analysts = await test_client.get("/api/v1/analysts", headers=_auth(super_admin_token))
        assert "잠깐 공개한 분석가" not in [a["name"] for a in analysts.json()]

    async def test_others_cannot_toggle_my_agent(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        pid = await self._create_public(test_client, worker_token, "남이 못 건드릴 분석가")
        resp = await test_client.patch(
            f"/api/v1/prompts/personal/{pid}",
            headers=_auth(super_admin_token),
            json={"is_public": False},
        )
        assert resp.status_code == 404

    async def test_rule_cannot_be_public(self, test_client: AsyncClient, worker_token: str) -> None:
        resp = await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(worker_token),
            json={"kind": "rule", "name": "공개 규칙", "content": "간결하게.", "is_public": True},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "PROMPT_NOT_SHAREABLE"

    async def test_name_collision_is_disambiguated_by_owner(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        """같은 이름이 둘이면 배정이 어느 쪽인지 갈린다 - 겹칠 때만 소유자를 덧붙인다."""
        await self._create_public(test_client, worker_token, "겹치는 이름")
        await test_client.post(
            "/api/v1/prompts/personal",
            headers=_auth(super_admin_token),
            json={"kind": "agent", "name": "겹치는 이름", "content": "내 것."},
        )
        analysts = await test_client.get("/api/v1/analysts", headers=_auth(super_admin_token))
        names = [a["name"] for a in analysts.json()]
        assert "겹치는 이름" in names  # 내 것이 원래 이름을 지킨다
        assert any(n.startswith("겹치는 이름 (") for n in names)  # 공개분은 소유자로 갈린다
        assert len(names) == len(set(names))

    async def test_public_agent_is_assignable_in_outline(
        self, test_client: AsyncClient, worker_token: str, super_admin_token: str
    ) -> None:
        """UNKNOWN_ANALYST 422가 나던 자리 - 공개 에이전트는 남의 목차에서도 통과해야 한다."""
        await self._create_public(test_client, worker_token, "목차에 배정할 공개 분석가")
        resp = await test_client.post(
            "/api/v1/projects",
            headers=_auth(super_admin_token),
            json={
                "title": "공유 에이전트 배정",
                "topic": "공개 에이전트를 남의 계정 목차에 배정한다",
                "config": {
                    "outline": {
                        "chapters": [
                            {
                                "title": "1장",
                                "sections": [
                                    {
                                        "title": "1.1",
                                        "analysts": ["목차에 배정할 공개 분석가"],
                                    }
                                ],
                            }
                        ]
                    }
                },
            },
        )
        assert resp.status_code == 201, resp.text


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


class TestPromptPreview:
    """조합 미리보기 - 작성 경로와 같은 함수로 조립해 보여준다.

    "만든 것이 반영됐는지" 볼 눈이 없어 거짓 스위치를 두 번 늦게 발견했다
    (2026-08-09 다중 배정·2026-08-10 개인 작성 규칙).
    """

    async def test_assembles_persona_rules_and_volume(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        resp = await test_client.post(
            "/api/v1/prompts/preview",
            json={
                "analysts": ["예산산출", "비용편익분석"],
                "title": "예산 산출",
                "direction": "총사업비 산출 근거",
                "key_points": ["단가 기준"],
            },
            headers=_auth(worker_token),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 배정 2개면 페르소나가 둘 다 실린다(첫 개만 쓰이던 거짓 스위치 회귀 방지).
        labels = [b["label"] for b in body["blocks"]]
        assert sum(1 for x in labels if x.startswith("페르소나")) == 2
        # 작성 규칙 3종도 함께.
        assert sum(1 for x in labels if x.startswith("작성 규칙")) == 3
        # 분량은 volume_target에서 생성돼 지시 블록에 실린다.
        assert body["min_chars"] and body["n_parts"] > 1
        assert "목표 분량:" in body["guidance"]
        assert "총사업비 산출 근거" in body["guidance"]

    async def test_unknown_analyst_is_reported_not_silent(
        self, test_client: AsyncClient, worker_token: str
    ) -> None:
        resp = await test_client.post(
            "/api/v1/prompts/preview",
            json={"analysts": ["존재하지않는에이전트"], "title": "x"},
            headers=_auth(worker_token),
        )
        assert resp.status_code == 200
        assert resp.json()["unknown_analysts"] == ["존재하지않는에이전트"]
