"""근거 기반 PM 경고 — 저장된 본문과 근거를 대조해 결정적으로 뽑는 경고."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from src.services.qa.evidence_findings import findings_for_section


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

    def test_fabricated_number_is_critical(self):
        cid = uuid4()
        chunk = "국내 생산 능력은 꾸준히 확대되고 있다.\n"
        row = _section("국내 생산 능력은 42.7% 확대된 것으로 나타났음 [1]", [cid])
        rows = findings_for_section(row, {cid: chunk}, renumbered=False)
        assert _categories(rows) == {"무근거 수치"}
        assert rows[0]["severity"] == "critical"
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
