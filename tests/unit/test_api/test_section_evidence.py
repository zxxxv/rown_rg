"""근거 추적 — 본문 마커를 청크까지 되짚는 매핑(순수 로직, DB·라우팅 없음).

출처 표기는 자료 단위라 "이 자료 어딘가"까지만 알려준다. 조립 시 renumber가 남긴
meta.citation_chunks가 있으면 청크까지, 없으면(조립 전·옛 절) 등장 순서 대응으로
자료 단위까지 되짚는다.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

from src.api.routers.projects import _marker_chunk_ids
from src.services.sections.renumber import citation_chunk_map


def _section(content: str, source_ids: list[UUID], meta: dict | None = None):
    return SimpleNamespace(content=content, source_ids=source_ids, meta=meta or {})


class TestMarkerChunkIds:
    def test_recorded_map_wins(self):
        chunk = uuid4()
        row = _section(
            "주장이다 [3].",
            [uuid4()],  # 위치 대응으로 풀면 다른 청크가 나온다 — 기록된 매핑이 우선이어야
            {"citation_chunks": {"3": [str(chunk)]}},
        )
        mapping, traceable = _marker_chunk_ids(row)
        assert mapping == {3: [chunk]}
        assert traceable is True

    def test_one_number_can_hold_several_chunks(self):
        # 전역 번호는 자료 단위 — 같은 자료의 다른 대목이 한 번호로 합쳐진다.
        a, b = uuid4(), uuid4()
        row = _section("[1] 문장.", [], {"citation_chunks": {"1": [str(a), str(b)]}})
        mapping, _ = _marker_chunk_ids(row)
        assert mapping == {1: [a, b]}

    def test_falls_back_to_appearance_order(self):
        # 조립 전: [n]은 절-로컬 번호라 등장 순서 ↔ source_ids 위치로 푼다.
        first, second = uuid4(), uuid4()
        row = _section("먼저 [5]. 다음 [2].", [first, second])
        mapping, traceable = _marker_chunk_ids(row)
        assert mapping == {5: [first], 2: [second]}
        assert traceable is True

    def test_assembled_without_record_gives_up(self):
        # 조립 후엔 못 푼 마커가 지워지고 같은 자료의 번호가 합쳐져 위치 대응이 깨진다.
        # 엉뚱한 원문을 근거로 내놓느니 대응을 포기한다.
        mapping, traceable = _marker_chunk_ids(
            _section("먼저 [1]. 다음 [2].", [uuid4(), uuid4()]), renumbered=True
        )
        assert mapping == {}
        assert traceable is False

    def test_no_markers_is_not_traceable(self):
        mapping, traceable = _marker_chunk_ids(_section("인용 없는 본문", []))
        assert mapping == {}
        assert traceable is False

    def test_broken_record_falls_back(self):
        # 숫자 아닌 키·잘못된 uuid가 섞이면 기록을 버리고 위치 대응으로 간다.
        cid = uuid4()
        row = _section("[1] 문장.", [cid], {"citation_chunks": {"x": ["not-a-uuid"]}})
        mapping, _ = _marker_chunk_ids(row)
        assert mapping == {1: [cid]}


class TestCitationChunkMap:
    def test_maps_local_order_to_global_number(self):
        a, b = uuid4(), uuid4()
        # 본문 등장 순서 [2] → cited[0]=a, [1] → cited[1]=b
        content = "먼저 [2]. 다음 [1]."
        got = citation_chunk_map(content, [a, b], {a: 7, b: 7})
        # 두 청크가 같은 자료(전역 7)라 한 번호로 합쳐지되 둘 다 남는다 — 대목 구분용.
        assert got == {7: [a, b]}

    def test_unmapped_chunk_dropped(self):
        a, b = uuid4(), uuid4()
        got = citation_chunk_map("[1] 그리고 [2].", [a, b], {b: 4})
        assert got == {4: [b]}

    def test_more_markers_than_cited_ids(self):
        a = uuid4()
        got = citation_chunk_map("[1] [2] [3]", [a], {a: 1})
        assert got == {1: [a]}
