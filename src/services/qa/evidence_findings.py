"""근거 기반 PM 경고 — 저장된 본문과 근거를 대조해 결정적으로 뽑는 경고.

PM 검증(pm_verify)은 챕터당 1콜로 문서 횡단 문제(수치 충돌·용어 불일치)를 본다. 그건
LLM이라야 보이는 축이다. 반대로 "이 문장이 인용한 근거에 그 내용이 없다"는 코드로 셀 수
있고, 세는 편이 낫다 — 매번 같은 답이 나오고 비용이 0이며, 재검증을 눌러도 흔들리지 않는다.

경고는 절이 아니라 사람에게 보내는 신호다. 그래서 문장 하나하나를 다 올리지 않고 절 단위로
묶어 "몇 건, 예시 하나"로 보낸다 — 35절짜리 보고서에서 문장마다 경고가 뜨면 아무도 안 읽는다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, or_, select

from src.core.citations import numbers_in_order
from src.core.config import settings
from src.db.models.chunk import Chunk
from src.db.models.project import Project
from src.db.models.project_source import ProjectSource
from src.db.models.section import Section
from src.db.session import async_session_maker
from src.services.qa.alignment import ClaimAlignment, align_section
from src.services.qa.claim_verify import verify_claims
from src.services.qa.cross_section import (
    DUPLICATE_THRESHOLD,
    dangling_references,
    duplicate_pairs,
)
from src.services.qa.design_coverage import coverage_terms, judge_covered
from src.services.qa.design_coverage import findings_for_section as coverage_findings
from src.services.qa.gate import (
    arithmetic_suspects,
    claim_coverage,
    claim_years,
    leftover_artifacts,
    locate_probes,
    match_patterns,
    misattributed_numbers,
    normalize_haystack,
    normalize_number,
    truncated_lines,
)
from src.services.qa.table_check import (
    table_prose_mismatches,
    table_share_sum_mismatches,
    table_ungrounded_numbers,
)
from src.services.sections.evidence import marker_chunk_ids

logger = structlog.get_logger(__name__)

# 절 하나에서 이 개수를 넘으면 "많다"고만 알린다 — 목록 나열은 화면 몫이다.
_SAMPLE_CHARS = 40
# 무근거 주장 경고 기준 — 정적 게이트(check_uncited_claims)와 같은 눈금을 쓴다.
_UNCITED_MIN = 3
_UNCITED_RATIO = 0.5
# 근거 불일치 기준 — 의역이면 겹침이 낮게 나오므로 한두 건으로는 경고하지 않는다.
_UNMATCHED_MIN = 3
_UNMATCHED_RATIO = 0.2


def _finding(
    chapter: int, section_ref: str, severity: str, category: str, detail: str
) -> dict[str, Any]:
    return {
        "chapter_number": chapter,
        "severity": severity,
        "category": category,
        "section_ref": section_ref,
        "detail": detail,
    }


def claims_for_section(
    row: Section, chunk_texts: dict[UUID, str], *, renumbered: bool
) -> tuple[list[ClaimAlignment], bool]:
    """절 본문을 주장 단위로 쪼개 근거와 맞춰본다. (주장들, 대조 가능 여부)

    마커는 있는데 청크까지 못 풀면(기록 없는 옛 절) 근거 대조는 성립하지 않는다.
    그때도 "근거 표기가 아예 없는 주장"은 셀 수 있다 — 매핑이 필요 없는 판정이라서다.
    둘을 뭉뚱그려 건너뛰면 인용이 하나도 없는 절이 조용히 통과한다(2026-08-11).
    """
    content = row.content or ""
    mapping, _ = marker_chunk_ids(
        content, list(row.source_ids or []), row.meta, renumbered=renumbered
    )
    return align_section(content, chunk_texts, mapping), bool(mapping)


def suspicious_indices(claims: list[ClaimAlignment]) -> list[int]:
    """LLM 판정으로 넘길 후보 — 겹침이 낮거나 근거에 없는 수치를 문 문장.

    겹침이 낮다는 건 '틀렸다'가 아니라 '어휘로는 확인 못 했다'는 뜻이다. 의역이면
    여기 걸리는 게 정상이라, 이 목록을 그대로 경고로 쓰면 안 되고 판정을 한 번 더 받는다.
    """
    return [
        i
        for i, c in enumerate(claims)
        # crosslingual은 '겹침으로 못 쟀다'는 뜻이라 반드시 판정으로 넘긴다 - 영문 근거가
        # 근거 풀의 78%를 차지하는 보고서에서는 이게 판정 대상의 대부분이 된다.
        if c.numbers and (c.status in ("weak", "unmatched", "crosslingual") or c.ungrounded)
    ]


def ungrounded_token_buckets(
    claims: list[ClaimAlignment],
    *,
    comparable: bool,
    supported: set[int] | None = None,
    refuted: set[int] | None = None,
) -> tuple[list[str], list[str], dict[str, tuple[str, ...]]]:
    """(판정이 '근거 없음' 확인한 수치, 어휘 대조만 실패한 수치, 정규화 수치→명시 연도).

    findings_from_claims와 코퍼스 재검색(_locate_tokens 호출부)이 같은 선별을 써야
    한다 — 자가 어긋나면 재검색 안 한 수치가 critical로 남아 2단 판정이 헛돈다.
    연도 지도는 판정 확인 수치에 대해서만 만든다 — 주입 가드가 '그 연도 곁에
    실재하는가'를 물을 대상이 그들뿐이라서다.
    """
    supported = supported or set()
    refuted = refuted or set()
    confirmed: list[str] = []
    lexical: list[str] = []
    years: dict[str, tuple[str, ...]] = {}
    for i, claim in enumerate(claims) if comparable else []:
        if i in supported:
            continue  # 근거로 뒷받침된다고 판정된 문장의 수치는 창작이 아니다
        if claim.status == "crosslingual" and i not in refuted:
            continue  # 어휘로 잴 수 없는 축 — 판정 없이 세면 전부 오탐이 된다
        bucket = confirmed if i in refuted else lexical
        for token in claim.ungrounded:
            if token not in confirmed and token not in lexical:
                bucket.append(token)
            if i in refuted:
                norm = normalize_number(token)
                stated = claim_years(claim.claim)
                if norm and stated:
                    years[norm] = tuple(dict.fromkeys(years.get(norm, ()) + stated))
    return confirmed, lexical, years


def findings_from_claims(
    row: Section,
    claims: list[ClaimAlignment],
    *,
    comparable: bool,
    supported: set[int] | None = None,
    refuted: set[int] | None = None,
    located: dict[str, str] | None = None,
    injected: set[str] | None = None,
    own_grounded: set[str] | None = None,
) -> list[dict[str, Any]]:
    """주장별 대조 결과 → 절 단위 경고 행 (순수 함수 — 테스트 대상).

    supported/refuted는 근거 동봉 판정(claim_verify)의 결과다. 판정을 받은 문장은
    겹침 점수 대신 그 판정을 따른다 — 뒷받침된다고 나오면 경고에서 빼고, 근거에 없다고
    나오면 겹침이 높아도 경고한다.

    located는 2단 판정(코퍼스 전체 재검색)의 결과 — 정규화 수치 → 실재하는 자료 제목.
    판정이 '근거 없음'이라 해도 수치가 코퍼스 어딘가에 실재하면 창작이 아니라 출처를
    잘못 단 것이다(2026-08-21 v6 실측: critical 18건 표본 32/33이 '수치 실재·출처
    틀림'). None이면 재검색을 안 한 것이므로 종전대로 전부 critical로 본다.

    injected는 3단(주입 가드) — located에서 발견됐지만 본문이 명시한 연도 곁에서는
    코퍼스 어디에도 없는 정규화 수치. 부분문자열 우연 일치가 '실재'로 보일 수 있어
    (v6 실측: 주입 수치 428이 CBAM 문서의 무관한 428과 일치), 연도 대조가 오귀속과
    주입을 가른다. 해당 수치는 오귀속이 아니라 '사전지식 주입 의심'으로 뜬다.
    """
    ref = f"{row.chapter_number}.{row.section_number}"
    if not claims:
        return []
    supported = supported or set()
    refuted = refuted or set()
    verified = bool(supported or refuted)

    out: list[dict[str, Any]] = []
    # crosslingual은 여기 넣지 않는다 - 겹침으로 못 잰 것을 "근거에서 확인되지 않는다"고
    # 알리면 거짓 경고다. 판정을 받았다면 refuted로 들어와 정상적으로 잡힌다.
    unmatched = [
        c
        for i, c in enumerate(claims)
        if i not in supported and (i in refuted or c.status == "unmatched")
    ]
    # 판정을 받았으면 1건도 유효한 신호다. 겹침만으로는 의역이 섞이므로 비중을 본다.
    enough = (
        len(unmatched) >= 1
        if verified
        else (len(unmatched) >= _UNMATCHED_MIN and len(unmatched) / len(claims) >= _UNMATCHED_RATIO)
    )
    if comparable and unmatched and enough:
        sample = unmatched[0].claim[:_SAMPLE_CHARS]
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "근거 불일치",
                f"인용 표기는 있으나 그 근거에서 확인되지 않는 문장 {len(unmatched)}건"
                f' (예: "{sample}…")',
            )
        )

    uncited = [c for c in claims if c.status == "uncited"]
    ratio = len(uncited) / len(claims)
    if len(uncited) >= _UNCITED_MIN and ratio > _UNCITED_RATIO:
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "무근거 주장",
                f"근거 표기가 없는 주장 {len(uncited)}건 ({ratio:.0%})"
                f' (예: "{uncited[0].claim[:_SAMPLE_CHARS]}…")',
            )
        )

    # critical은 판정(claim_verify)이 '근거 없음'으로 확인한 문장의 수치만. 어휘 대조만
    # 실패한 수치는 warning이다 — 부분문자열 매칭은 단위 환산(72억 vs $7.2 billion)·표기
    # 차이(1.8조 vs 1조 8,000억)를 원리적으로 못 재고, 교차언어 근거는 아예 잴 수 없다
    # (2026-08-14 실측: 탄소규제 런 critical 26건 중 표본 22건 오탐 → 전량 강등 원인).
    confirmed, lexical, stated_years = ungrounded_token_buckets(
        claims, comparable=comparable, supported=supported, refuted=refuted
    )
    relocated: list[tuple[str, str]] = []
    suspected: list[tuple[str, str]] = []
    fabricated: list[str] = []
    for token in confirmed:
        norm = normalize_number(token)
        if own_grounded and norm in own_grounded:
            continue  # 인용한 자료 자신의 다른 대목에 있다 — 창작도 오귀속도 아니다
        where = (located or {}).get(norm)
        if where and injected and norm in injected:
            suspected.append((token, "·".join(stated_years.get(norm, ()))))
        elif where:
            relocated.append((token, where))
        else:
            fabricated.append(token)
    if fabricated:
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "critical",
                "무근거 수치",
                f"인용한 근거에 없는 수치 {len(fabricated)}건(판정 확인"
                + ("·코퍼스 재검색 미발견" if located is not None else "")
                + f"): {', '.join(fabricated[:5])}"
                + (" …" if len(fabricated) > 5 else ""),
            )
        )
    if relocated:
        samples = ", ".join(f"{t}(실제: {src})" for t, src in relocated[:3])
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "출처 오귀속",
                f"인용한 출처가 아닌 다른 자료에 실재하는 수치 {len(relocated)}건"
                f"(출처 정정 필요): {samples}" + (" …" if len(relocated) > 3 else ""),
            )
        )
    if suspected:
        samples = ", ".join(f"{t}({y}년 곁에는 없음)" for t, y in suspected[:3])
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "사전지식 주입 의심",
                f"명시한 연도 곁에서는 코퍼스 어디에도 없는 수치 {len(suspected)}건"
                f"(모델 사전지식 의심, 실자료 확인 필요): {samples}"
                + (" …" if len(suspected) > 3 else ""),
            )
        )
    if lexical:
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "무근거 수치",
                f"인용 근거에서 어휘로 확인되지 않는 수치 {len(lexical)}건"
                f"(단위 환산·표기 차이 가능): {', '.join(lexical[:5])}"
                + (" …" if len(lexical) > 5 else ""),
            )
        )
    return out


def content_findings(
    row: Section, *, n_sources: int | None, renumbered: bool
) -> list[dict[str, Any]]:
    """저장된 절 본문만으로 뽑는 결정적 경고 — 편집 잔재·절단 의심·유령 출처 번호.

    유령 출처(참고문헌 범위 밖 번호)는 전역 번호화 이후에만 판정할 수 있다
    (로컬 번호는 절마다 1..k라 범위 밖이 정상). 2026-08-14 실측: 탄소규제 런에
    존재하지 않는 출처 48을 가리키는 편집 메모 잔재가 본문에 남아 있었다.
    """
    ref = f"{row.chapter_number}.{row.section_number}"
    content = row.content or ""
    out: list[dict[str, Any]] = []
    artifacts = leftover_artifacts(content)
    if artifacts:
        out.append(_finding(row.chapter_number, ref, "warning", "편집 잔재", "; ".join(artifacts)))
    cut = truncated_lines(content)
    if cut:
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "critical",
                "문장 절단",
                f'문장 중간에서 끊긴 줄 {len(cut)}건 (예: "{cut[0]}…")',
            )
        )
    math_issues = arithmetic_suspects(content)
    if math_issues:
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "산술 불일치",
                f"본문 스스로의 계산과 안 맞는 서술 {len(math_issues)}건: {math_issues[0]}"
                + (f" 외 {len(math_issues) - 1}건" if len(math_issues) > 1 else ""),
            )
        )
    if renumbered and n_sources:
        ghosts = sorted({n for n in numbers_in_order(content) if n > n_sources})
        if ghosts:
            out.append(
                _finding(
                    row.chapter_number,
                    ref,
                    "warning",
                    "유령 출처",
                    f"참고문헌({n_sources}개) 범위 밖 출처 번호 참조: "
                    f"{', '.join(str(n) for n in ghosts[:5])}" + (" …" if len(ghosts) > 5 else ""),
                )
            )
    return out


def _misattribution_findings(
    row: Section, chunk_texts: dict[UUID, str], *, renumbered: bool
) -> list[dict[str, Any]]:
    """마커 오귀속 전수 검사 — 절 풀 스코프에서 '인용엔 없는데 딴 근거엔 있는' 수치.

    n=2 트리아지(N2O·RGGI)에서 쓴 마커↔청크 결정적 대조의 전수 승격(2026-08-14).
    '엉뚱한 곳' 탐색 범위는 이 절의 검색 풀 — 문장은 그 풀에서 작성됐으므로, 다른
    절 근거까지 넓히면 오탐이 는다.
    """
    content = row.content or ""
    mapping, _ = marker_chunk_ids(
        content, list(row.source_ids or []), row.meta, renumbered=renumbered
    )
    if not mapping:
        return []
    scope: set[UUID] = {cid for ids in mapping.values() for cid in ids}
    for raw in (row.meta or {}).get("pool_chunk_ids") or []:
        try:
            scope.add(UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    section_pool = {cid: t for cid, t in chunk_texts.items() if cid in scope}
    found = misattributed_numbers(content, mapping, section_pool)
    if not found:
        return []
    ref = f"{row.chapter_number}.{row.section_number}"
    return [
        _finding(
            row.chapter_number,
            ref,
            "warning",
            "마커 오귀속",
            f"인용한 출처엔 없고 다른 근거에 있는 수치 {len(found)}건 (예: {found[0]})"
            + (f" 외 {len(found) - 1}건" if len(found) > 1 else ""),
        )
    ]


def _table_findings(
    row: Section, chunk_texts: dict[UUID, str], *, renumbered: bool
) -> list[dict[str, Any]]:
    """표 셀 수치 검사 — 문장 검사망에서 빠져 있던 표를 결정적으로 대조한다.

    B(표-본문 불일치)가 실제 사고 양식이다 — 분할 작성이 검색 풀을 파트별로 쪼개
    같은 지표가 표와 본문에서 다른 값이 된다(탄소규제 런 실측, 백로그 3번의 1차 검출).
    A(근거 대조)는 어휘 매칭이라 단위 환산을 못 재므로 문장 쪽과 같은 warning 눈금.
    """
    content = row.content or ""
    ref = f"{row.chapter_number}.{row.section_number}"
    out: list[dict[str, Any]] = []

    share_gaps = table_share_sum_mismatches(content)
    if share_gaps:
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "표 합계 불일치",
                f"구성비 합이 100%가 아닌 표 {len(share_gaps)}건: {share_gaps[0]}"
                + (f" 외 {len(share_gaps) - 1}건" if len(share_gaps) > 1 else ""),
            )
        )

    mismatches = table_prose_mismatches(content)
    if mismatches:
        out.append(
            _finding(
                row.chapter_number,
                ref,
                "warning",
                "표-본문 수치 불일치",
                f"같은 지표가 표와 본문에서 다른 값 {len(mismatches)}건: {mismatches[0]}"
                + (f" 외 {len(mismatches) - 1}건" if len(mismatches) > 1 else ""),
            )
        )

    mapping, _ = marker_chunk_ids(
        content, list(row.source_ids or []), row.meta, renumbered=renumbered
    )
    if mapping:
        cited = "\n".join(
            chunk_texts[cid] for ids in mapping.values() for cid in ids if cid in chunk_texts
        )
        ungrounded = table_ungrounded_numbers(content, cited)
        if ungrounded:
            out.append(
                _finding(
                    row.chapter_number,
                    ref,
                    "warning",
                    "표 무근거 수치",
                    f"인용 근거에서 어휘로 확인되지 않는 표 수치 {len(ungrounded)}건"
                    f"(단위 환산·표기 차이 가능): {', '.join(ungrounded[:5])}"
                    + (" …" if len(ungrounded) > 5 else ""),
                )
            )
    return out


# 절 하나에서 이만큼 겹치면 사람이 봐야 한다. 개조식 보고서는 한두 문장이 비슷한 게
# 자연스러워(같은 정책을 여러 각도로 다룬다) 건수로 가른다.
_DUP_MIN = 3
_DUP_CRITICAL = 8


def cross_section_findings(sections: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """절 간 중복·떠 있는 참조 → 경고 행. 뒤에 오는 절에 책임을 묻는다.

    절을 병렬로 쓰면 각 절은 자기 근거만 본다. 같은 자료가 여러 절의 상위에 걸리면 같은
    문장이 여러 절에 실린다 - 실측에서 시장 전망 한 문장이 네 절에 거의 그대로 있었다.
    경고를 뒤 절에 붙이는 이유: 앞 절이 원본이고 고칠 곳은 뒤라서다(요약·결론 절이 앞
    본문을 그대로 옮겨 오는 게 전형이다).
    """
    out: list[dict[str, Any]] = []
    by_section: dict[str, list[Any]] = {}
    for pair in duplicate_pairs(sections, threshold=DUPLICATE_THRESHOLD):
        by_section.setdefault(pair.second_ref, []).append(pair)

    for ref, pairs in by_section.items():
        if len(pairs) < _DUP_MIN:
            continue
        sources = sorted({p.first_ref for p in pairs}, key=lambda r: [int(x) for x in r.split(".")])
        out.append(
            _finding(
                _ref_chapter(ref),
                ref,
                "critical" if len(pairs) >= _DUP_CRITICAL else "warning",
                "절 간 중복",
                f"앞 절({', '.join(sources[:4])}{' 외' if len(sources) > 4 else ''})에"
                f" 이미 있는 문장 {len(pairs)}건"
                f' (예: "{pairs[0].second_text[:_SAMPLE_CHARS]}…")',
            )
        )

    dangling: dict[str, list[str]] = {}
    for ref, why in dangling_references(sections):
        dangling.setdefault(ref, []).append(why)
    for ref, whys in dangling.items():
        out.append(
            _finding(
                _ref_chapter(ref),
                ref,
                "warning",
                "떠 있는 참조",
                f"{whys[0]}" + (f" 외 {len(whys) - 1}건" if len(whys) > 1 else ""),
            )
        )
    return out


def _ref_chapter(ref: str) -> int:
    try:
        return int(ref.split(".")[0])
    except ValueError:
        return 0


def findings_for_section(
    row: Section, chunk_texts: dict[UUID, str], *, renumbered: bool
) -> list[dict[str, Any]]:
    """LLM 판정 없이 한 절을 대조한다 — 겹침 판정만 쓰는 경로."""
    claims, comparable = claims_for_section(row, chunk_texts, renumbered=renumbered)
    return findings_from_claims(row, claims, comparable=comparable)


async def _locate_tokens(
    project_id: UUID,
    tokens: list[str],
    cache: dict[str, tuple[UUID | None, str] | None],
    own_sources: set[UUID] | None = None,
) -> tuple[dict[str, str], set[str]]:
    """정규화 수치 → 그 수치가 실재하는 자료 제목. 프로젝트 코퍼스 전체를 재검색한다.

    1단(어휘 대조·근거 동봉 판정)은 '인용한 근거'만 본다. 2단은 절 풀 밖까지 —
    여기서 발견되면 창작이 아니라 오귀속이다. 매칭은 1단과 같은 자(콤마 제거 후
    부분문자열)라야 '인용엔 없다'와 '코퍼스엔 있다'가 같은 눈금이 된다. 토큰은
    숫자·점뿐이라 LIKE 메타문자 걱정이 없고, 같은 수치가 여러 절에 반복되므로
    프로젝트 단위로 캐시한다(못 찾은 것도 None으로 남겨 재검색을 막는다).
    """
    by_norm = {normalize_number(t): t for t in tokens if normalize_number(t)}
    todo = sorted(n for n in by_norm if n not in cache)
    if todo:
        async with async_session_maker() as session:
            for norm in todo:
                # 자릿수 환산 표기까지 함께 찾는다 — 본문 "70.5억 달러"가 코퍼스에는
                # "USD 7.05 billion"으로 적힌다(2026-08-24 COMPA 실측: 이 한 겹이
                # 없어 영문 코퍼스에서 2단 판정이 통째로 뚫렸다).
                haystack = func.replace(Chunk.content, ",", "")
                rows = (
                    await session.execute(
                        select(
                            Chunk.content, Chunk.source_id, ProjectSource.title, ProjectSource.url
                        )
                        .select_from(Chunk)
                        .join(ProjectSource, Chunk.source_id == ProjectSource.id, isouter=True)
                        .where(
                            Chunk.project_id == project_id,
                            or_(*[haystack.like(f"%{v}%") for v in locate_probes(by_norm[norm])]),
                        )
                        .limit(20)
                    )
                ).all()
                # LIKE는 후보 선별까지만 - '실재' 선언은 자릿수 경계로 확인한다.
                # 부분문자열이면 "21"이 "2021" 안에, "0.3"이 "10.3" 안에 걸려
                # 없는 수치가 '오귀속'으로 실렸다(2026-08-27 v6 near_miss 라벨링:
                # 노이즈 4건 중 2건이 이 꼴). number_in_text와 같은 자다.
                patterns = match_patterns(by_norm[norm])
                confirmed = next(
                    (
                        row
                        for row in rows
                        if any(pt.search(normalize_haystack(row[0] or "")) for pt in patterns)
                    ),
                    None,
                )
                cache[norm] = (
                    (confirmed[1], confirmed[2] or confirmed[3] or "제목 없는 자료")
                    if confirmed
                    else None
                )
    # 세 갈래로 가른다. 그 절이 인용한 **자료 자신**에서 나왔으면 오귀속이 아니라
    # 그냥 근거 있음이다 — 청크 단위 인용이라 같은 자료의 다른 대목에 수치가 있는 건
    # 정상이다(2026-08-24 COMPA 재채점에서 오귀속이 1→5로 는 원인).
    other: dict[str, str] = {}
    own: set[str] = set()
    for norm, value in cache.items():
        if value is None:
            continue
        source_id, title = value
        if own_sources and source_id in own_sources:
            own.add(norm)
        else:
            other[norm] = title
    return other, own


# 주입 가드 근접 창 — 수치 발견 지점 양옆에서 연도를 찾는 반경(문자).
# 청크 전체 동시출현은 연도가 흔해 헐겁고, 같은 문장이라기엔 표 행이 길다.
#
# 80→240 (2026-08-27, v6 near_miss 전수 라벨링): 진짜 실재 2건(0.75%=CCA 문서
# 산문, 289TWh=RE100 표)이 모두 연도에서 80~240자 거리였다 — 표는 연도가 머리행에,
# 산문은 기준연도가 문단 앞에 온다. 좁은 창이 실재를 '주입 의심'으로 만들었고,
# v6 채점의 "주입 가족"(424·428·545·289)은 전수가 코퍼스 실재로 판명됐다(진짜
# 병리는 자료 시점 병존+오귀속). 넓혀도 안전한 근거: 매칭이 자릿수 경계를 갖게
# 되어(아래) 우연 일치 자체가 급감했다.
_YEAR_WINDOW = 240


def _year_beside(
    content: str, norm: str, years: tuple[str, ...], window: int = _YEAR_WINDOW
) -> bool:
    """수치의 모든 등장 지점 ±창 안에 명시 연도 중 하나라도 있는가.

    자릿수 환산 표기도 함께 찾는다 — 본문이 "4,610만 달러"라도 코퍼스에는
    "US$ 46.1 million"으로 적혀 있다(2026-08-24 COMPA 실측: 이 한 겹이 없어
    실재하는 수치가 주입 의심으로 샜다).
    """
    text = normalize_haystack(content)
    # 자료는 연도를 축약해 적는다 - "'21년 말 74개사"의 '21이 "2021" 문자열 대조를
    # 비켜 실재 수치가 주입 의심으로 샜다(2026-08-27 v6: K-RE100 74). 뒤 두 자리에
    # 아포스트로피·년을 붙인 표기까지 같은 연도로 본다.
    year_forms = [
        form
        for y in years
        for form in (y, f"'{y[2:]}년", f"’{y[2:]}년", f"`{y[2:]}년")
        if len(y) == 4
    ]
    # find()는 "21"을 "2021" 안에서 찾는다 - 자릿수 경계 패턴으로 진짜 등장만 본다.
    for pattern in match_patterns(norm):
        for m in pattern.finditer(text):
            chunk = text[max(0, m.start() - window) : m.end() + window]
            if any(y in chunk for y in year_forms):
                return True
    return False


async def _injection_suspects(
    project_id: UUID,
    token_years: dict[str, tuple[str, ...]],
    located: dict[str, str],
    cache: dict[tuple[str, tuple[str, ...]], bool],
) -> set[str]:
    """located 수치 중 본문이 명시한 연도 곁에서는 코퍼스 어디에도 없는 것 — 주입 서명.

    '실재'가 부분문자열 우연일 수 있다(v6 실측: 주입 수치 428이 CBAM 문서의 무관한
    428과 일치해 오귀속으로 보였다). 그래서 수치가 등장하는 청크를 다시 읽어 연도가
    수치 곁에 있는 발견만 진짜 실재로 친다. 명시 연도가 없는 수치는 판정하지 않고,
    수치 등장 청크는 50개까지만 읽는다(그 밖에서만 연도가 붙는 경우는 오탐을 감수).
    """
    todo = [
        (norm, years)
        for norm, years in token_years.items()
        if norm in located and years and (norm, years) not in cache
    ]
    if todo:
        async with async_session_maker() as session:
            for norm, years in todo:
                haystack = func.replace(Chunk.content, ",", "")
                # 연도도 사전 선별에 건다 - 묻는 것이 "수치가 연도 곁에 있는 청크가
                # 존재하는가"라서, 연도 없는 청크는 볼 이유가 없다. 두 자리 토큰(74·
                # 10·30)은 LIKE가 수백 청크를 쏟아 50개 캡 안에 진짜 자리가 못 들고
                # 실재가 주입 의심으로 샜다(2026-08-27 v6 라벨링). 축약 표기('21년)는
                # 뒤 두 자리가 어차피 연도의 부분문자열이라 LIKE %21%로 같이 걸린다.
                year_like = [f"%{y}%" for y in years] + [f"%{y[2:]}년%" for y in years]
                texts = (
                    (
                        await session.execute(
                            select(Chunk.content)
                            .where(
                                Chunk.project_id == project_id,
                                or_(*[haystack.like(f"%{v}%") for v in locate_probes(norm)]),
                                or_(*[Chunk.content.like(y) for y in year_like]),
                            )
                            .limit(50)
                        )
                    )
                    .scalars()
                    .all()
                )
                hit = any(_year_beside(t, norm, years) for t in texts)
                cache[(norm, years)] = hit
                # 창 크기 계측 — ±80자엔 없는데 ×3 창엔 있으면 창이 좁아서 놓친
                # 후보다(표 머리 연도 등). 다보고서 회귀가 이 로그로 창을 조정한다.
                if not hit and any(
                    _year_beside(t, norm, years, window=_YEAR_WINDOW * 3) for t in texts
                ):
                    logger.info("injection_guard.year_near_miss", token=norm, years=list(years))
    return {
        norm
        for norm, years in token_years.items()
        if norm in located and years and not cache.get((norm, years), True)
    }


async def evidence_findings(
    project_id: UUID,
    *,
    renumbered: bool = True,
    model: str | None = None,
    user_id: UUID | None = None,
    verify: bool | None = None,
) -> list[dict[str, Any]]:
    """프로젝트 전체 절을 근거와 대조해 경고 행 목록을 만든다(저장은 안 함).

    겹침 판정이 건져 올린 의심 문장은 근거 원문과 함께 LLM에 한 번 더 묻는다
    (절당 1콜, 의심 문장이 있는 절만). 판정이 실패하면 겹침 결과를 그대로 쓴다.
    """
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(Section)
                    .where(Section.project_id == project_id, Section.content != "")
                    .order_by(Section.chapter_number, Section.section_number)
                )
            )
            .scalars()
            .all()
        )
        wanted: set[UUID] = set()
        for row in rows:
            wanted.update(u for u in (row.source_ids or []) if u)
            # 목차 이행 검사는 인용본이 아니라 '실렸던 근거 전체'를 봐야 한다 —
            # 자료가 있었는데 안 쓴 것과 애초에 없던 것을 가르는 게 그 검사의 핵심이다.
            for raw in (row.meta or {}).get("pool_chunk_ids") or []:
                try:
                    wanted.add(UUID(str(raw)))
                except (ValueError, TypeError):
                    continue
        project = await session.get(Project, project_id)
        chapters = ((project.config or {}).get("outline") or {}).get("chapters") or []
        # 유령 출처 판정 기준 — 전역 번호는 채택 자료 순번(renumber._adopted_source_order)
        n_sources = (
            await session.execute(
                select(func.count())
                .select_from(ProjectSource)
                .where(ProjectSource.project_id == project_id, ProjectSource.is_included.is_(True))
            )
        ).scalar_one()
        chunk_texts: dict[UUID, str] = {}
        chunk_sources: dict[UUID, UUID] = {}
        if wanted:
            for cid, text, source_id in (
                await session.execute(
                    select(Chunk.id, Chunk.content, Chunk.source_id).where(Chunk.id.in_(wanted))
                )
            ).all():
                chunk_texts[cid] = text
                if source_id is not None:
                    chunk_sources[cid] = source_id

    use_llm = settings.claim_verify_enabled if verify is None else verify
    out: list[dict[str, Any]] = []
    n_verified = 0
    n_relocated = 0
    n_injected = 0
    locate_cache: dict[str, tuple[UUID | None, str] | None] = {}
    year_cache: dict[tuple[str, tuple[str, ...]], bool] = {}
    # 문장 커버리지 — 검출 지표의 분모. claim_units가 못 집은 문장은 모든 검사에서
    # 증발하고 그 손실은 어떤 지표에도 안 나타난다('남' 꼬리 실사고, 2026-08-14).
    # 미포착 수치 문장은 구조 규칙상 0이어야 한다 — 0이 아니면 회귀다.
    cov_picked, cov_total, cov_missed = 0, 0, []
    for row in rows:
        p, t, missed = claim_coverage(row.content or "")
        cov_picked += p
        cov_total += t
        cov_missed.extend(f"{row.chapter_number}.{row.section_number}: {m}" for m in missed)
        claims, comparable = claims_for_section(row, chunk_texts, renumbered=renumbered)
        supported: set[int] = set()
        refuted: set[int] = set()
        candidates = suspicious_indices(claims) if (use_llm and comparable) else []
        if candidates:
            verdicts = await verify_claims(
                [claims[i] for i in candidates],
                chunk_texts,
                model=model,
                user_id=user_id,
                project_id=project_id,
                section_ref=f"{row.chapter_number}.{row.section_number}",
            )
            n_verified += len(verdicts)
            for pos, verdict in verdicts.items():
                # 판정은 후보 순번으로 오므로 원래 문장 위치로 되돌린다.
                target = candidates[pos]
                if verdict.is_supported:
                    supported.add(target)
                elif verdict.verdict == "not_supported":
                    refuted.add(target)
        # 2단 판정 — 판정이 '근거 없음' 확인한 수치만 코퍼스 전체를 재검색한다.
        # 발견되면 critical(창작)이 아니라 warning(출처 오귀속)으로 갈리고,
        # 발견됐어도 명시 연도 곁에 없으면(3단) '사전지식 주입 의심'으로 갈린다.
        located: dict[str, str] | None = None
        injected: set[str] = set()
        own_grounded: set[str] = set()
        if refuted:
            confirmed_toks, _, tok_years = ungrounded_token_buckets(
                claims, comparable=comparable, supported=supported, refuted=refuted
            )
            if confirmed_toks:
                own = {
                    chunk_sources[cid] for cid in list(row.source_ids or []) if cid in chunk_sources
                }
                located, own_grounded = await _locate_tokens(
                    project_id, confirmed_toks, locate_cache, own
                )
                injected = await _injection_suspects(project_id, tok_years, located, year_cache)
                norms = {normalize_number(t) for t in confirmed_toks}
                n_injected += len(norms & injected)
                n_relocated += len({n for n in norms if n in located} - injected)
        out.extend(
            findings_from_claims(
                row,
                claims,
                comparable=comparable,
                supported=supported,
                refuted=refuted,
                located=located,
                injected=injected,
                own_grounded=own_grounded,
            )
        )
        out.extend(content_findings(row, n_sources=n_sources, renumbered=renumbered))
        out.extend(_misattribution_findings(row, chunk_texts, renumbered=renumbered))
        out.extend(_table_findings(row, chunk_texts, renumbered=renumbered))
        out.extend(
            await _coverage_findings(
                row,
                chapters,
                chunk_texts,
                judge=use_llm,
                model=model,
                user_id=user_id,
                project_id=project_id,
            )
        )
    # 절 간 중복은 절 하나만 봐서는 안 보인다 — 전부 모은 뒤 한 번에 본다.
    out.extend(
        cross_section_findings(
            [(f"{r.chapter_number}.{r.section_number}", r.content or "") for r in rows]
        )
    )
    logger.info(
        "evidence_findings.done",
        project_id=str(project_id),
        n=len(out),
        verified=n_verified,
        relocated=n_relocated,
        injected=n_injected,
        claim_coverage=round(cov_picked / cov_total, 3) if cov_total else None,
        n_claim_candidates=cov_total,
        missed_numeric=len(cov_missed),
        missed_numeric_samples=cov_missed[:5],
    )
    return out


async def _coverage_findings(
    row: Section,
    chapters: list[Any],
    chunk_texts: dict[UUID, str],
    *,
    judge: bool,
    model: str | None,
    user_id: UUID | None,
    project_id: UUID,
) -> list[dict[str, Any]]:
    """목차 지시 이행 검사 — 확정 목차가 없으면(자유 주제) 건너뛴다.

    이행 여부는 LLM이 판정한다. 어휘 겹침은 '단어가 있는가'만 재서 정밀도 25%·
    재현율 33%였다(2026-08-12 실측). 판정이 실패하면 어휘로 폴백한다.
    """
    try:
        spec = chapters[row.chapter_number - 1]["sections"][row.section_number - 1]
    except (IndexError, KeyError, TypeError):
        return []
    pool = [str(x) for x in ((row.meta or {}).get("pool_chunk_ids") or [])] or [
        str(x) for x in (row.source_ids or [])
    ]
    texts: list[str] = []
    for raw in pool:
        try:
            texts.append(chunk_texts.get(UUID(raw), ""))
        except (ValueError, TypeError):
            continue
    key_points = [str(k) for k in (spec.get("key_points") or [])]
    verdict: dict[str, bool] = {}
    if judge:
        terms = coverage_terms(str(spec.get("direction") or ""), key_points)
        verdict = await judge_covered(
            terms,
            row.content or "",
            section_ref=f"{row.chapter_number}.{row.section_number}",
            model=model or settings.claim_verify_model or settings.verify_model,
            user_id=user_id,
            project_id=project_id,
        )
    return coverage_findings(
        row.chapter_number,
        row.section_number,
        row.content or "",
        str(spec.get("direction") or ""),
        key_points,
        "\n".join(t for t in texts if t),
        verdict=verdict,
    )
