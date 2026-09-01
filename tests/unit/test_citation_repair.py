"""저장 직전 결정층 — 마커 3상태 교정·절 내 소거·재규약 계약(2026-09-01 설계 확정).

핵심 계약:
① 3상태 - 교정은 "인용 청크에 없고 팩의 한 청크에 전부 실재"일 때만. 팩 어디에도
   없으면 보류(마커 불변) - 억지 교정은 경고만 하던 종전보다 나쁘다.
② 치환은 주장 단위 안의 마커만 - 표 이웃 셀을 덮으면 안 된다(2026-08-31 실사고).
③ content와 cited_chunk_ids는 함께 움직인다(첫 등장 순서 재규약) - 안 지키면
   조립 재번호에서 절 전체 인용이 밀린다(v6 사고 경로).
"""

from __future__ import annotations

from uuid import uuid4

from src.services.sections.citation_repair import (
    RepairOutcome,
    _local_chunk_map,
    dedup_intra_section,
    repair_citations,
)

A, B, C = uuid4(), uuid4(), uuid4()


class TestRepair3State:
    def test_교정_인용엔_없고_다른_청크에_실재(self) -> None:
        content = "세계 시장은 2026년 1,594억 달러로 전망됨(출처 1)\n\n다른 서술임(출처 2)"
        pool = {A: "무관한 정책 서술", B: "시장 규모는 1,594억 달러(2026년) 수준"}
        out = repair_citations(content, [A, B], pool)
        assert out.n_fixed == 1
        # 마커가 실재 청크(B)의 로컬 번호 2로 교체된다.
        assert "1,594억 달러로 전망됨(출처 2)" in out.content
        # 재규약: 첫 등장 순서 [2, 2] -> 고유 [2] ... 원 2번 문장의 마커도 2 - cited는 [B]
        assert out.cited_chunk_ids[0] == B

    def test_보류_팩_어디에도_없으면_손대지_않는다(self) -> None:
        content = "인도 PLI로 2조 7,106크로르가 배정됨(출처 1)"
        pool = {A: "무관한 서술", B: "다른 무관한 서술"}
        out = repair_citations(content, [A], pool)
        assert out.n_fixed == 0
        assert out.content == content  # 억지 교정 금지
        assert out.held  # 사람·검출기 몫으로 넘긴다

    def test_유지_인용_청크에_실재(self) -> None:
        content = "생산 비중은 72%로 나타남(출처 1)"
        pool = {A: "고로-전로 경로가 세계 생산의 72%를 차지", B: "72%가 여기도 있음"}
        out = repair_citations(content, [A, B], pool)
        assert out.n_fixed == 0 and out.content == content

    def test_외국어_인용_근거는_판정하지_않는다(self) -> None:
        # 어휘로 '없다'를 선언할 수 없다(단위 환산 - 오귀속 검출기와 같은 원칙).
        content = "연간 700만 톤을 저감함(출처 1)"
        pool = {A: "Stegra eliminates seven million tons of CO2", B: "700만 톤 저감 서술"}
        out = repair_citations(content, [A, B], pool)
        assert out.n_fixed == 0 and out.content == content

    def test_마커가_여럿인_문장은_손대지_않는다(self) -> None:
        content = "두 값 3,200억 원과 45%를 함께 인용함(출처 1)(출처 2)"
        pool = {A: "무관", B: "무관", C: "3,200억 원과 45%가 실재"}
        out = repair_citations(content, [A, B, C], pool)
        assert out.n_fixed == 0 and out.content == content

    def test_새_청크_교정이면_새_로컬_번호를_받고_재규약된다(self) -> None:
        content = "첫 문장 수치 891억 원임(출처 1)\n\n무수치 서술임(출처 1)"
        pool = {A: "무관한 서술", C: "예산 891억 원이 배정"}
        out = repair_citations(content, [A], pool)
        assert out.n_fixed == 1
        assert "(출처 2)" in out.content  # C가 새 번호 2를 받는다
        # 재규약: 첫 등장 [2, 1] 순서로 cited = [C, A]
        assert out.cited_chunk_ids == [C, A]
        mapping = _local_chunk_map(out.content, out.cited_chunk_ids)
        assert mapping == {2: C, 1: A}

    def test_유령_번호가_있으면_통째로_보수적으로_비킨다(self) -> None:
        content = "값 120억임(출처 1) 그리고 딴 값(출처 9)"
        pool = {A: "무관", B: "120억이 실재"}
        out = repair_citations(content, [A], pool)  # 출처 9는 cited 규약 밖
        assert out.content == content and out.n_fixed == 0


class TestIntraDedup:
    def test_복사_수준_뒤_문장을_걷는다(self) -> None:
        dup = (
            "범용재 철강은 대량생산에 의한 원가 절감이 핵심 경쟁력이며 제품 간 성능"
            " 차이가 크지 않아 가격이 선택 요인이 되는 경우가 많음(출처 1)"
        )
        near = (
            "범용재 철강은 대량생산에 의한 원가 절감이 핵심 경쟁력이며, 제품 간 성능"
            " 차이가 크지 않아 가격이 선택 요인이 되는 경우가 많은 것으로 나타남(출처 2)"
        )
        content = f"{dup}\n\n중간의 다른 서술로 두 문장을 떼어 놓음(출처 2)\n\n{near}"
        out = dedup_intra_section(content, [A, B])
        assert out.n_removed == 1
        assert near not in out.content and dup in out.content
        # 재규약: 남은 본문 첫 등장 [1, 2] -> [A, B] 유지
        assert out.cited_chunk_ids == [A, B]

    def test_가드레일_전체의_15퍼센트_넘게는_안_걷는다(self) -> None:
        unit = "동일한 문장이 반복되어 등장하는 사례로 근거 검증 대상이 되는 서술임(출처 1)"
        content = "\n\n".join([unit] * 12)
        out = dedup_intra_section(content, [A])
        # 예산(15%) 안에서만 걷고 나머지는 남긴다 - 조용히 얇아지지 않게.
        assert out.content.count(unit[:20]) >= 10

    def test_참조_포인터는_걷지_않는다(self) -> None:
        p = "동 특별법 및 시행령의 제정·시행 경과와 세부 지원 기준은 1.1절 참조"
        content = f"{p}\n\n실내용 서술이 하나 있음 상세한 근거와 함께(출처 1)\n\n{p}"
        out = dedup_intra_section(content, [A])
        assert out.n_removed == 0

    def test_중복이_없으면_원문_그대로(self) -> None:
        content = "서로 완전히 다른 첫 서술임(출처 1)\n\n두 번째는 정책 맥락을 다룸(출처 2)"
        out = dedup_intra_section(content, [A, B])
        assert out.content == content and out.cited_chunk_ids == [A, B]

    def test_숫자가_다르면_골격이_같아도_지우지_않는다(self) -> None:
        # 연도별 추이 문장은 중복이 아니라 정보다(2026-09-01 검토).
        a = "2024년 국내 철강 부문 매출은 3.2조 원으로 집계되어 완만한 회복세를 보임(출처 1)"
        b = "2025년 국내 철강 부문 매출은 4.1조 원으로 집계되어 완만한 회복세를 보임(출처 1)"
        out = dedup_intra_section(f"{a}\n\n{b}", [A])
        assert out.n_removed == 0

    def test_하위_불릿이_딸린_부모는_지우지_않는다(self) -> None:
        dup = "동일한 핵심 서술이 반복되는 문장으로 검사 표적이 되는 사례임(출처 1)"
        content = (
            f"ㅇ {dup}\n\n중간의 무관한 다른 서술이 하나 들어감(출처 1)\n\n"
            f"ㅇ {dup}\n- 하위 근거가 딸려 있음"
        )
        out = dedup_intra_section(content, [A])
        assert out.n_removed == 0  # 지우면 하위가 고아가 된다

    def test_지시어_후속이_있으면_지우지_않는다(self) -> None:
        dup = "동일한 핵심 서술이 반복되는 문장으로 검사 표적이 되는 사례임(출처 1)"
        content = (
            f"{dup}\n\n중간의 무관한 다른 서술이 하나 들어감(출처 1)\n\n"
            f"{dup}\n이는 앞 문장의 결과를 이어받는 서술임"
        )
        out = dedup_intra_section(content, [A])
        assert out.n_removed == 0

    def test_감사_기록과_예산_플래그가_남는다(self) -> None:
        unit = "동일한 문장이 반복되어 등장하는 사례로 근거 검증 대상이 되는 서술임(출처 1)"
        content = "\n\n".join([unit] * 12)
        out = dedup_intra_section(content, [A])
        assert out.removed and out.removed[0]["score"] >= 0.8
        assert out.budget_exhausted  # 소진은 성공이 아니라 경보 - 층4로 올라간다
        assert "budget_exhausted" in out.audit()


class TestOutcome:
    def test_변경_없음_판별(self) -> None:
        assert not RepairOutcome("x", []).changed
        assert RepairOutcome("x", [], n_fixed=1).changed
