"""근거 기반 PM 경고 — 저장된 본문과 근거를 대조해 결정적으로 뽑는 경고."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.services.qa.evidence_findings import content_findings, findings_for_section


def _section(content: str, source_ids=None, meta=None, ch=2, sec=1):
    return SimpleNamespace(
        chapter_number=ch,
        section_number=sec,
        content=content,
        source_ids=source_ids or [],
        meta=meta,
    )


def _categories(rows) -> set[str]:
    return {r["category"] for r in rows}


class TestFindingsForSection:
    def test_old_section_makes_no_evidence_findings(self):
        # 되짚을 기록이 없는 옛 절까지 대조 경고를 내면 지난 보고서가 전부 빨간불이 된다.
        content = "\n".join(
            f"ㅇ ({i}) 국내 생산 능력은 42.7% 확대된 것으로 나타났음 [1]" for i in range(4)
        )
        row = _section(content, [uuid4()])
        assert findings_for_section(row, {}, renumbered=True) == []

    def test_grounded_section_is_clean(self):
        cid = uuid4()
        chunk = "국내 반도체 장비 수출액은 3,200억 원으로 집계됐다.\n설비 투자도 함께 늘었다.\n"
        row = _section("국내 반도체 장비 수출액은 3,200억 원으로 집계됐음 [1]", [cid])
        assert findings_for_section(row, {cid: chunk}, renumbered=False) == []

    def test_fabricated_number_warns_without_verdict(self):
        # 어휘 대조만으로는 단위 환산(72억 vs $7.2B)·표기 차이를 못 재서 warning까지만 -
        # critical은 판정(claim_verify)이 '근거 없음'으로 확인한 수치의 몫이다(2026-08-14).
        cid = uuid4()
        chunk = "국내 생산 능력은 꾸준히 확대되고 있다.\n"
        row = _section("국내 생산 능력은 42.7% 확대된 것으로 나타났음 [1]", [cid])
        rows = findings_for_section(row, {cid: chunk}, renumbered=False)
        assert _categories(rows) == {"무근거 수치"}
        assert rows[0]["severity"] == "warning"
        assert "42.7" in rows[0]["detail"]
        assert rows[0]["section_ref"] == "2.1"

    def test_paraphrase_alone_does_not_warn(self):
        # 의역이면 겹침이 낮게 나온다 - 한두 건으로 '근거 불일치'를 띄우면 소음이 된다.
        cid = uuid4()
        chunk = "정부는 관련 산업 육성을 위해 다양한 지원 방안을 마련하고 있다.\n"
        row = _section("당국은 이 분야를 키우려고 여러 방책을 준비하는 중으로 파악됨 [1]", [cid])
        assert "근거 불일치" not in _categories(
            findings_for_section(row, {cid: chunk}, renumbered=False)
        )

    def test_many_unmatched_claims_warn(self):
        cid = uuid4()
        chunk = "유연근무제 도입 기업의 만족도 조사 결과를 정리한 표이다.\n"
        content = "\n".join(
            f"ㅇ ({i}) 차세대 공정에서 미세화 한계가 뚜렷해지고 있다는 분석이 나옴 [1]"
            for i in range(4)
        )
        rows = findings_for_section(_section(content, [cid]), {cid: chunk}, renumbered=False)
        assert "근거 불일치" in _categories(rows)

    def test_uncited_claims_warn(self):
        content = "\n".join(
            [
                "ㅇ 국내 시장은 전년 대비 성장세를 이어갈 것으로 전망된다.",
                "ㅇ 관련 기업들의 설비 투자도 확대되는 흐름이 이어지고 있음",
                "ㅇ 정부 지원 예산 역시 늘어날 필요가 있다고 판단됨",
            ]
        )
        rows = findings_for_section(_section(content), {}, renumbered=False)
        assert "무근거 주장" in _categories(rows)
        assert all(r["severity"] == "warning" for r in rows)

    def test_recorded_mapping_used_when_present(self):
        # meta.citation_chunks가 있으면 조립 후에도(renumbered=True) 되짚는다.
        cid = uuid4()
        chunk = "국내 생산 능력은 꾸준히 확대되고 있다.\n"
        row = _section(
            "국내 생산 능력은 42.7% 확대된 것으로 나타났음 [7]",
            [cid],
            meta={"citation_chunks": {"7": [str(cid)]}},
        )
        rows = findings_for_section(row, {cid: chunk}, renumbered=True)
        assert _categories(rows) == {"무근거 수치"}


class TestContentFindings:
    def test_ghost_source_flagged_only_after_renumber(self):
        # 실측(2026-08-14 탄소규제 런 2.3): 참고문헌 35개인데 출처 48을 가리키는 잔재.
        row = _section("관련 제도가 긍정적으로 평가됨 (출처 48)", ch=2, sec=3)
        rows = content_findings(row, n_sources=35, renumbered=True)
        assert [r["category"] for r in rows] == ["유령 출처"]
        assert "48" in rows[0]["detail"] and rows[0]["severity"] == "warning"
        # 로컬 번호 단계(renumbered=False)에서는 범위 밖이 정상이라 판정하지 않는다.
        assert content_findings(row, n_sources=35, renumbered=False) == []

    def test_leftover_and_truncation_surface_as_findings(self):
        row = _section(
            "ㅇ 활용이 저조함(출처 15의 내용 대신 확인 가능한 범위로 한정 — 삭제)\n"
            "ㅇ 1.5도 시나리오 이행 시 탄소규제 미대응 기업의 국내총생산이 약 2",
            ch=4,
            sec=4,
        )
        rows = content_findings(row, n_sources=None, renumbered=True)
        cats = [r["category"] for r in rows]
        assert "편집 잔재" in cats and "문장 절단" in cats
        severities = {r["category"]: r["severity"] for r in rows}
        assert severities["문장 절단"] == "critical" and severities["편집 잔재"] == "warning"

    def test_arithmetic_mismatch_surfaces_as_warning(self):
        row = _section(
            "ㅇ 참여기업의 재생에너지 사용량은 전년 240TWh에서 289TWh로 30% 증가함 (출처 28)",
            ch=1,
            sec=2,
        )
        rows = content_findings(row, n_sources=None, renumbered=True)
        assert [r["category"] for r in rows] == ["산술 불일치"]
        assert rows[0]["severity"] == "warning"

    def test_clean_section_has_no_content_findings(self):
        row = _section("ㅇ 국내 기업의 대응 수준은 개선되는 흐름을 보이고 있음 (출처 3)")
        assert content_findings(row, n_sources=35, renumbered=True) == []
