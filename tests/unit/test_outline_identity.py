"""절 정체성 수술(2026-08-21) — outline 안정 id·builds_on 토큰·plan 병합의 순수 부품.

계약 3개를 못 박는다:
1. outline의 장·절 id는 편집·재플래닝을 살아남는다(정체성의 닻).
2. builds_on은 저작=번호·저장=id 토큰·plan 탑재=현재 번호 라벨 — 절 삽입이
   참조를 어긋나게 하지 않는다.
3. 목차 변경 시 plan은 통째 폐기가 아니라 id 병합 — 브리프 산출(search_queries)과
   실행 계획(_design_plan)이 살아남는다.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from src.api.routers.projects import merge_config_update
from src.core.outline import (
    ensure_outline_ids,
    format_chapter_token,
    format_section_token,
    normalize_outline,
    parse_id_token,
    position_maps,
    token_to_label,
)
from src.core.section_plan import SECTION_PLAN_KEY, dump_section_plan, plan_from_config
from src.services.generation.planner import merge_section_plan, plan_from_outline


def _sec(title: str, sid: str | None = None, builds_on: list[str] | None = None) -> dict:
    out: dict = {
        "title": title,
        "direction": "",
        "key_points": [],
        "analysts": [],
        "builds_on": builds_on or [],
    }
    if sid is not None:
        out["id"] = sid
    return out


def _outline(*chapters: dict) -> dict:
    return {"chapters": list(chapters)}


def _ch(title: str, sections: list[dict], cid: str | None = None) -> dict:
    out: dict = {"title": title, "sections": sections}
    if cid is not None:
        out["id"] = cid
    return out


class TestEnsureOutlineIds:
    def test_fills_missing_and_preserves_existing(self):
        keep = str(uuid4())
        outline, remap = ensure_outline_ids(_outline(_ch("1장", [_sec("가", keep), _sec("나")])))
        secs = outline["chapters"][0]["sections"]
        assert secs[0]["id"] == keep  # 있는 id는 절대 재발급하지 않는다
        assert UUID(secs[1]["id"])  # 없는 id는 발급
        assert outline["chapters"][0]["id"]  # 장도 id를 가진다
        assert remap == {}

    def test_duplicate_ids_reissued(self):
        dup = str(uuid4())
        outline, _ = ensure_outline_ids(_outline(_ch("1장", [_sec("가", dup), _sec("나", dup)])))
        secs = outline["chapters"][0]["sections"]
        assert secs[0]["id"] == dup
        assert secs[1]["id"] != dup  # 중복은 plan PK 충돌이라 뒤쪽만 재발급

    def test_fresh_reissues_all_with_remap(self):
        """생성 경로: sections.id는 전역 PK — 복사된 config도 새 정체성으로 시작."""
        old = str(uuid4())
        outline, remap = ensure_outline_ids(_outline(_ch("1장", [_sec("가", old)])), fresh=True)
        new = outline["chapters"][0]["sections"][0]["id"]
        assert new != old
        assert remap[old] == new


class TestNormalizeOutline:
    def test_numeric_refs_become_id_tokens(self):
        outline, errors = normalize_outline(
            _outline(
                _ch("1장", [_sec("가"), _sec("나")]),
                _ch("2장", [_sec("다", builds_on=["1.2(총사업비)", "1.*"])]),
            )
        )
        assert errors == []
        target = outline["chapters"][0]["sections"][1]["id"]
        ch1 = outline["chapters"][0]["id"]
        assert outline["chapters"][1]["sections"][0]["builds_on"] == [
            format_section_token(target, "총사업비"),
            format_chapter_token(ch1),
        ]

    def test_token_roundtrip_is_stable(self):
        """이미 정규화된 outline을 재제출해도(폼 round-trip) 결과가 같다."""
        first, _ = normalize_outline(
            _outline(_ch("1장", [_sec("가")]), _ch("2장", [_sec("나", builds_on=["1.1"])]))
        )
        second, errors = normalize_outline(first)
        assert errors == []
        assert second == first

    def test_insertion_does_not_shift_reference(self):
        """절 삽입 후에도 토큰은 같은 절을 가리킨다 — 수술의 존재 이유."""
        base, _ = normalize_outline(
            _outline(
                _ch("1장", [_sec("가"), _sec("나")]), _ch("2장", [_sec("다", builds_on=["1.2"])])
            )
        )
        token = base["chapters"][1]["sections"][0]["builds_on"][0]
        target_id = base["chapters"][0]["sections"][1]["id"]
        # "나" 앞에 새 절을 끼운다 — 번호로는 1.2가 1.3이 된다.
        base["chapters"][0]["sections"].insert(1, _sec("삽입"))
        edited, errors = normalize_outline(base)
        assert errors == []
        assert edited["chapters"][1]["sections"][0]["builds_on"] == [token]
        label_by_sec, num_by_ch, _, _ = position_maps(edited)
        assert token_to_label(token, label_by_sec, num_by_ch) == "1.3"
        assert edited["chapters"][0]["sections"][2]["id"] == target_id

    def test_ghost_and_self_refs_error(self):
        _, ghost = normalize_outline(_outline(_ch("1장", [_sec("가", builds_on=["3.9"])])))
        assert ghost and "없는 절" in ghost[0]
        _, selfref = normalize_outline(
            _outline(_ch("1장", [_sec("가"), _sec("나", builds_on=["1.2"])]))
        )
        assert selfref and "자기 자신" in selfref[0]
        _, unreadable = normalize_outline(_outline(_ch("1장", [_sec("가", builds_on=["엉망"])])))
        assert unreadable and "읽을 수 없습니다" in unreadable[0]

    def test_cap_errors(self):
        _, errors = normalize_outline(
            _outline(
                _ch("1장", [_sec("가"), _sec("나"), _sec("다")]),
                _ch("2장", [_sec("라", builds_on=["1.1", "1.2", "1.3"])]),
            )
        )
        assert errors and "상한" in errors[0]

    def test_fresh_ids_remaps_own_tokens(self):
        """복사된 config의 토큰이 자기 목차의 옛 id를 가리키면 새 id로 옮겨 단다."""
        old_target = str(uuid4())
        outline, errors = normalize_outline(
            _outline(
                _ch("1장", [_sec("가", old_target)]),
                _ch("2장", [_sec("나", builds_on=[f"s:{old_target}"])]),
            ),
            fresh_ids=True,
        )
        assert errors == []
        new_target = outline["chapters"][0]["sections"][0]["id"]
        assert new_target != old_target
        assert outline["chapters"][1]["sections"][0]["builds_on"] == [f"s:{new_target}"]


class TestParseToken:
    def test_forms(self):
        sid = str(uuid4())
        assert parse_id_token(f"s:{sid}") == ("section", sid, None)
        assert parse_id_token(f"s:{sid}(지표)") == ("section", sid, "지표")
        assert parse_id_token(f"c:{sid}") == ("chapter", sid, None)
        assert parse_id_token(f"c:{sid}.*") == ("chapter", sid, None)
        assert parse_id_token("4.1") is None
        assert parse_id_token("") is None


class TestPlanFromOutline:
    def test_adopts_outline_ids(self):
        outline, _ = normalize_outline(_outline(_ch("1장", [_sec("가"), _sec("나")])))
        plan = plan_from_outline(outline)
        ids = [str(p.section_id) for p in plan]
        assert ids == [s["id"] for s in outline["chapters"][0]["sections"]]
        # 같은 outline로 재플래닝해도 id가 같다 — 정체성 보존의 핵심.
        again = plan_from_outline(outline)
        assert [p.section_id for p in again] == [p.section_id for p in plan]

    def test_tokens_loaded_as_current_labels(self):
        outline, _ = normalize_outline(
            _outline(
                _ch("1장", [_sec("가"), _sec("나")]),
                _ch("2장", [_sec("다", builds_on=["1.2(지표)", "1.*"])]),
            )
        )
        # 삽입으로 번호를 민다 — plan에는 민 뒤의 번호가 실려야 한다.
        outline["chapters"][0]["sections"].insert(0, _sec("새 절", str(uuid4())))
        plan = plan_from_outline(outline)
        assert plan[-1].builds_on == ["1.3(지표)", "1.*"]

    def test_unresolvable_token_dropped(self):
        outline, _ = normalize_outline(
            _outline(_ch("1장", [_sec("가"), _sec("나", builds_on=["1.1"])]))
        )
        token = outline["chapters"][0]["sections"][1]["builds_on"][0]
        assert token.startswith("s:")
        # 대상 절 삭제 — 의존만 사라지고 실행은 계속돼야 한다.
        del outline["chapters"][0]["sections"][0]
        plan = plan_from_outline(outline)
        assert plan[0].builds_on == []

    def test_legacy_numeric_passthrough(self):
        """마이그레이션 전 config(번호 문자열)는 종전 의미(위치 해석) 그대로."""
        plan = plan_from_outline(_outline(_ch("1장", [_sec("가"), _sec("나", builds_on=["1.1"])])))
        assert plan[1].builds_on == ["1.1"]


class TestMergeSectionPlan:
    def test_search_queries_survive_edit(self):
        outline, _ = normalize_outline(_outline(_ch("1장", [_sec("가"), _sec("나")])))
        old = plan_from_outline(outline)
        old[0] = old[0].model_copy(update={"search_queries": ["브리프 질의"]})
        # 절 하나 삽입 + 제목 수정 — id는 그대로.
        outline["chapters"][0]["sections"].insert(1, _sec("끼움", str(uuid4())))
        outline["chapters"][0]["sections"][0]["title"] = "가(개정)"
        merged = merge_section_plan(old, outline)
        assert merged[0].section_id == old[0].section_id
        assert merged[0].search_queries == ["브리프 질의"]  # 승계
        assert merged[0].title == "가(개정)"  # 내용은 새 목차가 진실
        assert merged[1].search_queries == []  # 새 절은 빈 채로 시작
        assert merged[2].section_id == old[1].section_id
        assert (merged[2].chapter_number, merged[2].section_number) == (1, 3)  # 번호는 민다


class TestMergeConfigUpdatePlan:
    def test_outline_change_merges_plan_and_drops_design(self):
        outline, _ = normalize_outline(_outline(_ch("1장", [_sec("가"), _sec("나")])))
        plan = plan_from_outline(outline)
        plan[1] = plan[1].model_copy(update={"search_queries": ["질의"]})
        keep_id, drop_id = str(plan[1].section_id), str(plan[0].section_id)
        current = {
            "outline": outline,
            SECTION_PLAN_KEY: dump_section_plan(plan),
            "_design_plan": {keep_id: {"goal": "유지"}, drop_id: {"goal": "제거"}},
        }
        # 첫 절 삭제 — 남은 절이 1.1로 승격된다.
        new_outline = {
            "chapters": [
                {**outline["chapters"][0], "sections": [outline["chapters"][0]["sections"][1]]}
            ]
        }
        merged = merge_config_update(current, {"outline": new_outline})
        got = plan_from_config(merged)
        assert [str(p.section_id) for p in got] == [keep_id]
        assert got[0].search_queries == ["질의"]
        assert (got[0].chapter_number, got[0].section_number) == (1, 1)
        # 소재 분담은 **통째로** 버린다. 살아남은 절 것만 남기던 것이 가장 나쁜 중간이었다
        # - 분담 문구는 절 번호를 문자열로 박아 두는데("…는 2.1절 소관") 절이 하나 빠지면
        # 번호가 밀려 그 문구가 엉뚱한 절을 가리키는 지시가 된다(2026-08-27).
        assert "_design_plan" not in merged

    def test_outline_unchanged_keeps_plan_verbatim(self):
        outline, _ = normalize_outline(_outline(_ch("1장", [_sec("가")])))
        plan = plan_from_outline(outline)
        current = {"outline": outline, SECTION_PLAN_KEY: dump_section_plan(plan)}
        merged = merge_config_update(current, {"outline": outline, "model_mode": "premium"})
        assert merged[SECTION_PLAN_KEY] == current[SECTION_PLAN_KEY]

    def test_no_prior_plan_stays_absent(self):
        outline, _ = normalize_outline(_outline(_ch("1장", [_sec("가")])))
        merged = merge_config_update({"outline": {}}, {"outline": outline})
        assert SECTION_PLAN_KEY not in merged


class TestPlanFingerprint:
    def test_content_changes_break_cache_but_renumber_does_not(self):
        from src.services.retrieval.rehearsal import plan_fingerprint

        outline, _ = normalize_outline(_outline(_ch("1장", [_sec("가")])))
        [p] = plan_from_outline(outline)
        base = plan_fingerprint(p)
        renumbered = p.model_copy(update={"chapter_number": 3, "section_number": 2})
        assert plan_fingerprint(renumbered) == base  # 순서만 바뀐 절은 캐시 유지
        retitled = p.model_copy(update={"title": "다른 제목"})
        assert plan_fingerprint(retitled) != base
        requeried = p.model_copy(update={"search_queries": ["새 질의"]})
        assert plan_fingerprint(requeried) != base


class TestAgentCap:
    def test_over_cap_reports_error(self) -> None:
        from src.core.outline import MAX_AGENTS_PER_SECTION, normalize_outline

        outline = {
            "chapters": [
                {
                    "title": "1장",
                    "sections": [
                        {"title": "1.1", "analysts": [f"a{i}" for i in range(6)]},
                        {"title": "1.2", "analysts": ["a1", "a2", "a3"]},
                    ],
                }
            ]
        }
        _, errors = normalize_outline(outline)
        assert any(f"상한({MAX_AGENTS_PER_SECTION}명)" in e for e in errors)
        # 상한 이하는 조용히 통과한다.
        assert not any("1.2" in e for e in errors)
