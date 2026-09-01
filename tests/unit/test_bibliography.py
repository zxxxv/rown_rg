"""서지 조각 추출 - 출처 표기("제목, 발행기관, 2024년 17호")의 재료.

원칙은 표제·발간연도 추출과 같다: 미상이면 안 단다. 넓은 패턴으로 긁으면
"연구원 출신"·"재무부문" 같은 산문이 기관이 된다.
"""

from __future__ import annotations

from uuid import uuid4

from src.core.types import SourceRef, SourceType
from src.services.export.report import _bib_label
from src.services.indexing.bibliography import extract_bibliography


class TestExtract:
    def test_kita_head(self) -> None:
        head = "2024년 17호\n## 제조 수출기업의 RE100 대응 실태와 과제\n## 국제무역통상연구원\n"
        assert extract_bibliography(head) == {
            "issue_label": "2024년 17호",
            "publisher": "국제무역통상연구원",
        }

    def test_label_form(self) -> None:
        got = extract_bibliography("발행처: 에너지경제연구원\n2023년 제5호")
        assert got == {"issue_label": "2023년 5호", "publisher": "에너지경제연구원"}

    def test_prose_not_mistaken(self) -> None:
        # 산문 속 '연구원'·맨 '제3호'는 서지가 아니다.
        assert extract_bibliography("연구원 출신 전문가가 말했다. 규정 제3호에 따라.") == {}


class TestBibLabel:
    def test_full(self) -> None:
        src = SourceRef(
            id=uuid4(),
            source_type=SourceType.UPLOAD,
            title="제조 수출기업의 RE100 대응 실태와 과제.pdf",
            publisher="국제무역통상연구원",
            issue_label="2024년 17호",
            published_year=2024,
        )
        # 호수에 연도가 있으니 연도를 따로 안 단다. 서지 조각은 괄호(2026-08-28 지시).
        assert (
            _bib_label(src)
            == "제조 수출기업의 RE100 대응 실태와 과제 (국제무역통상연구원, 2024년 17호)"
        )

    def test_year_fallback_and_extension_strip(self) -> None:
        src = SourceRef(
            id=uuid4(),
            source_type=SourceType.UPLOAD,
            title="미국 CCA 파급효과 예측.pdf",
            published_year=2024,
        )
        assert _bib_label(src) == "미국 CCA 파급효과 예측 (2024)"

    def test_web_title_only(self) -> None:
        src = SourceRef(id=uuid4(), source_type=SourceType.WEB_SEARCH, title="About RE100 - RE100")
        assert _bib_label(src) == "About RE100 - RE100"


class TestSourceEntry:
    def test_web_entry_carries_search_stamp_kst(self) -> None:
        from datetime import UTC, datetime

        from src.services.export.report import _source_entry

        src = SourceRef(
            id=uuid4(),
            source_type=SourceType.WEB_SEARCH,
            title="철강산업법 시행령 국무회의 통과",
            url="https://example.com/a",
            collected_at=datetime(2026, 8, 27, 3, 5, tzinfo=UTC),  # KST 12:05
        )
        line = _source_entry(3, src)
        assert line.endswith("(https://example.com/a) (2026.08.27 12:05 검색)")

    def test_file_entry_has_no_stamp(self) -> None:
        from datetime import UTC, datetime

        from src.services.export.report import _source_entry

        src = SourceRef(
            id=uuid4(),
            source_type=SourceType.UPLOAD,
            title="발표자료.pdf",
            published_year=2026,
            collected_at=datetime(2026, 8, 27, 3, 5, tzinfo=UTC),
        )
        # 파일은 판이 있는 문서다 - 검색 시각은 웹 전용, 확장자는 표기에서 걷는다.
        assert _source_entry(1, src) == "[1] 발표자료 (2026)"
