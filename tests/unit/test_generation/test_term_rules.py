"""용어 규칙 주입(generation/term_rules) — 근거팩 필터·정렬·캡·뭉갬 방지."""

from __future__ import annotations

from uuid import uuid4

from src.core.types import RetrievedChunk
from src.services.generation.term_rules import format_term_injection


def _chunk(content: str, *, source_id=None, is_summary: bool = False) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        source_id=source_id or uuid4(),
        content=content,
        score=0.9,
        is_summary=is_summary,
    )


def _def_entry(source_id, **over):
    return {
        "ko": None,
        "en": "operational commencement",
        "abbr": None,
        "definition": "Supply arrangement start year - equivalent to operational commencement",
        "source_title": "RE100 reporting guidance",
        "source_id": str(source_id),
        **over,
    }


class TestFilter:
    def test_term_in_pack_injected_with_definition_and_source(self):
        sid = uuid4()
        chunks = [_chunk("claims with operational commencement before 2024", source_id=sid)]
        block, keys = format_term_injection([_def_entry(sid)], chunks)
        assert "용어 규칙" in block
        assert "RE100 reporting guidance의 정의" in block
        assert keys == ["operational commencement"]
        # 미등재 용어 보수 규칙(꼬리 지시)이 함께 실린다
        assert "원어를 병기" in block

    def test_term_absent_from_pack_not_injected(self):
        chunks = [_chunk("전혀 다른 내용의 청크")]
        block, keys = format_term_injection([_def_entry(uuid4())], chunks)
        assert block == ""
        assert keys == []

    def test_summary_chunks_do_not_count_as_pack(self):
        chunks = [_chunk("operational commencement", is_summary=True)]
        assert format_term_injection([_def_entry(uuid4())], chunks) == ("", [])

    def test_abbr_match_is_case_sensitive(self):
        entry = {
            "ko": "에너지속성인증서",
            "en": None,
            "abbr": "EAC",
            "definition": None,
            "source_id": str(uuid4()),
            "source_title": "",
        }
        hit, _ = format_term_injection([entry], [_chunk("EAC 기반 조달")])
        miss, _ = format_term_injection([entry], [_chunk("each of them")])
        assert "에너지속성인증서" in hit
        assert miss == ""

    def test_ko_pair_line_prescribes_notation(self):
        entry = {
            "ko": "에너지속성인증서",
            "en": "Energy Attribute Certificate",
            "abbr": "EAC",
            "definition": None,
            "source_id": str(uuid4()),
            "source_title": "",
        }
        block, keys = format_term_injection([entry], [_chunk("EAC로 뒷받침되는 재생전력")])
        assert '"에너지속성인증서(Energy Attribute Certificate, EAC)"' in block
        assert keys == ["Energy Attribute Certificate"]

    def test_en_abbr_without_ko_keeps_original(self):
        entry = {
            "ko": None,
            "en": "Clean Competition Act",
            "abbr": "CCA",
            "definition": None,
            "source_id": str(uuid4()),
            "source_title": "",
        }
        block, _ = format_term_injection([entry], [_chunk("the Clean Competition Act imposes")])
        assert "한글 표기를 새로 만들지 마라" in block


class TestRankingAndDedup:
    def test_cap_prefers_pack_source_definitions(self):
        sid = uuid4()
        chunks = [
            _chunk(
                "operational commencement " + " ".join(f"용어{i}" for i in range(20)), source_id=sid
            )
        ]
        # 팩 밖 자료의 병기 항목 20개 + 팩 안 자료의 정의 1개 — 정의가 캡에서 살아남는다
        pairs = [
            {
                "ko": f"용어{i}",
                "en": f"Foreign Term Number {i}",
                "abbr": None,
                "definition": None,
                "source_id": str(uuid4()),
                "source_title": "",
            }
            for i in range(20)
        ]
        block, keys = format_term_injection([*pairs, _def_entry(sid)], chunks)
        assert "operational commencement" in keys
        assert len(keys) <= 12

    def test_identical_definition_from_two_sources_collapses(self):
        sid1, sid2 = uuid4(), uuid4()
        chunks = [_chunk("operational commencement", source_id=sid1)]
        e1, e2 = _def_entry(sid1), _def_entry(sid2)
        block, keys = format_term_injection([e1, e2], chunks)
        assert block.count("RE100 reporting guidance의 정의") == 1
        assert len(keys) == 1

    def test_conflicting_definitions_both_kept_with_attribution(self):
        # 자료 간 병합 금지의 이유 — 같은 용어를 두 문서가 다르게 규정하면 둘 다 싣고
        # 헤더가 "인용하는 그 자료의 정의"로 적용 범위를 못 박는다
        sid1, sid2 = uuid4(), uuid4()
        chunks = [
            _chunk("operational commencement", source_id=sid1),
            _chunk("operational commencement 다른 문서", source_id=sid2),
        ]
        e1 = _def_entry(sid1)
        e2 = _def_entry(sid2, definition="다른 문서의 다른 정의", source_title="다른 가이드")
        block, keys = format_term_injection([e1, e2], chunks)
        assert "RE100 reporting guidance의 정의" in block
        assert "다른 가이드의 정의" in block
        assert "인용하는 그 자료의 정의를 따르라" in block

    def test_no_entries_or_no_chunks(self):
        assert format_term_injection([], [_chunk("본문")]) == ("", [])
        assert format_term_injection([_def_entry(uuid4())], []) == ("", [])


def _pair(ko, title, definition=None):
    return {
        "ko": ko,
        "en": "Renewable Electricity 100",
        "abbr": "RE100",
        "definition": definition,
        "source_id": str(uuid4()),
        "source_title": title,
    }


class TestPairConflicts:
    """자료 간 표기 상충 보수화 - 2026-08-28 v7 실측(RE100 오염 증폭) 방어."""

    def test_majority_notation_wins(self):
        chunks = [_chunk("RE100 참여기업의 조달 확대")]
        entries = [
            _pair("알이백", "A"),
            _pair("알이백", "B"),
            _pair("재생에너지 사용 확인", "무역협회"),
        ]
        block, keys = format_term_injection(entries, chunks)
        assert '"알이백"' in block
        assert "재생에너지 사용 확인" not in block
        assert keys == ["Renewable Electricity 100"]

    def test_tie_drops_notation_enforcement_entirely(self):
        chunks = [_chunk("RE100 참여기업의 조달 확대")]
        entries = [_pair("알이백", "A"), _pair("재생에너지 사용 확인", "무역협회")]
        block, keys = format_term_injection(entries, chunks)
        assert block == ""
        assert keys == []

    def test_tie_keeps_definition_but_not_notation(self):
        chunks = [_chunk("RE100 참여기업의 조달 확대")]
        entries = [
            _pair("알이백", "A", definition="a global corporate renewable electricity initiative"),
            _pair("재생에너지 사용 확인", "무역협회"),
        ]
        block, keys = format_term_injection(entries, chunks)
        assert "A의 정의" in block
        assert "한글 표기는" not in block
        assert "재생에너지 사용 확인" not in block

    def test_spacing_variants_are_not_a_conflict(self):
        chunks = [_chunk("EAC 기반 조달")]
        entries = [
            {
                "ko": "에너지속성인증서",
                "en": "Energy Attribute Certificate",
                "abbr": "EAC",
                "definition": None,
                "source_id": str(uuid4()),
                "source_title": "A",
            },
            {
                "ko": "에너지 속성 인증서",
                "en": "Energy Attribute Certificate",
                "abbr": "EAC",
                "definition": None,
                "source_id": str(uuid4()),
                "source_title": "B",
            },
        ]
        block, _ = format_term_injection(entries, chunks)
        assert "에너지속성인증서" in block


def _glossary(ko, definition=None):
    return {
        "ko": ko,
        "en": "Renewable Electricity 100",
        "abbr": "RE100",
        "definition": definition,
        "origin": "glossary",
        "source_id": None,
        "source_title": "정본 용어집",
    }


class TestGlossaryPrecedence:
    """정본 우선(2026-09-04 승격 사슬) - 사람이 확정한 표기가 상충과 캡을 이긴다."""

    def test_glossary_resolves_tie_and_forces_notation(self):
        # 채굴만으로는 1:1 동률이라 표기 강제가 빠지던 상황 - 정본이 판가름한다.
        chunks = [_chunk("RE100 참여기업의 조달 확대")]
        entries = [
            _glossary("알이백"),
            _pair("알이백", "A"),
            _pair("재생에너지 사용 확인", "무역협회"),
        ]
        block, keys = format_term_injection(entries, chunks)
        assert '"알이백"' in block
        assert "재생에너지 사용 확인" not in block

    def test_glossary_overrides_mined_majority(self):
        # 채굴 다수결(2표)이 반대여도 정본이 이긴다 - 확정은 다수결 위다.
        chunks = [_chunk("RE100 참여기업의 조달 확대")]
        entries = [
            _pair("재생에너지 사용 확인", "무역협회"),
            _pair("재생에너지 사용 확인", "B"),
            _glossary("알이백"),
        ]
        block, _ = format_term_injection(entries, chunks)
        assert '"알이백"' in block
        assert "재생에너지 사용 확인" not in block

    def test_glossary_survives_injection_cap(self):
        chunks = [_chunk("RE100 " + " ".join(f"용어{i}" for i in range(20)))]
        pairs = [
            {
                "ko": f"용어{i}",
                "en": f"Foreign Term Number {i}",
                "abbr": None,
                "definition": f"정의 {i}",
                "source_id": str(uuid4()),
                "source_title": "",
            }
            for i in range(20)
        ]
        block, keys = format_term_injection([*pairs, _glossary("알이백")], chunks)
        assert keys[0] == "Renewable Electricity 100"
        assert '"알이백"' in block
