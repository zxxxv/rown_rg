"""시사점 요약 빌더 — 조립 시 1콜로 2~3쪽 브리핑을 만들어 projects.insights에 저장.

본문의 시사점·제언 절은 그 자체로 3~5쪽이라(프리셋 min/max_chars 4500~7500) 결정권자가
한눈에 훑기엔 길다. 그 절들을 다시 2~3쪽으로 압축한 별도 산출물을 만든다.

**원본 보고서는 건드리지 않는다** — 이 요약은 본문 HWPX에 실리지 않는다(2026-08-25 결정).
그래서 report_blocks 경로에는 아무 배선도 없다: 안 실리는 게 기본이다. 대신 요약만 담은
**별도 한글 파일**을 따로 렌더해 내려받는다(2026-08-27 결정, export_insights).

실패는 비치명 — 요약이 없으면 웹 화면이 "아직 없음"을 보여줄 뿐 렌더·완료를 막지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from src.clients.llm.base import CompletionRequest, LLMClient, Message
from src.clients.llm.factory import get_llm_client
from src.clients.llm.token_tracker import token_context
from src.core.clock import now
from src.core.config import settings
from src.core.state import ProjectState
from src.core.types import SectionPlan
from src.export.hwpx_writer import Block, Heading, Paragraph, build_report
from src.services.generation.planner import _parse_manifest

logger = structlog.get_logger(__name__)

DEFAULT_MAX_TOKENS = 6000
# 토큰 사용 기록에 남는 이름 — 이 호출 기록이 곧 "요약을 언제 만들었나"의 답이라
# 조회 쪽(projects 라우터)이 같은 상수를 본다(문자열 두 벌이면 조용히 어긋난다).
INSIGHTS_OPERATION = "assemble.insights"
# 입력 상한 — 시사점 절만 모아도 프리셋상 최대 2~3절 × 7,500자다. 여유를 두되
# 무한정 밀어 넣지 않는다(pm_verify가 24,000자 상한에 조용히 잘려 본문 47.6%만
# 보고 판정하던 전례가 있다 — 여기선 넘치면 로그를 남긴다).
MAX_INPUT_CHARS = 60_000
# 2~3쪽 목표. 본문 12pt·줄간격 160%·여백 20/20/15/15 기준 A4 1쪽 ≈ 1,500자.
TARGET_MIN_CHARS = 3_000
TARGET_MAX_CHARS = 4_500

# 시사점 성격의 절 제목 — 프리셋 10종에서 실제로 쓰이는 표기를 그대로 담았다
# (…및 제언 / 종합 시사점 / 사례 분석 시사점 / 핵심 제언 및 Next Step / 결론 …).
_INSIGHT_TITLE_RE = re.compile(r"시사점|제언|결론|소결|Next\s*Step", re.IGNORECASE)

# 시사점 개수로 분량을 건다 - 자수로 걸면 3개를 늘려 쓰거나 5개를 욱여넣는다
# (2026-08-27 골든 실측: 개수만 걸었는데 6,081자에 앉았다).
DEFAULT_N_IMPLICATIONS = 4

# 프리셋 무관 공통 템플릿(2026-08-27). 예타 보고서로 한 벌 써 보고 세운 구조다.
#
# 세 덩어리인 이유: 시사점만 늘어놓으면 "무슨 값에 근거했나"가 사라지고(골든 v1에서
# 경제성 지표가 시사점 어디에도 안 걸렸다), 값만 늘어놓으면 앞 절 재요약이 된다.
# 표(값) → 서술(뜻) → 서술(닫음)으로 **형식을 갈라** 같은 사실이 두 번 읽히지 않게 한다.
#
# 규칙을 "새 수치 금지"가 아니라 "있는 값은 그대로 옮기고 없는 값은 만들지 마라"로
# 쓰는 이유: 금지만 말했더니 모델이 안전하게 수치를 통째로 회피해 예타 종결부에서
# B/C 값이 빠졌다(골든 v1 실측). 금지는 회피를 부른다.
_SYSTEM_TEMPLATE = """너는 보고서의 시사점을 결정권자에게 브리핑하는 편집자다.
아래에 주어진 절들만 근거로, 정해진 틀에 맞춰 요약을 쓴다.

## 틀 (이 순서·이 세 덩어리로만 쓴다)

1. `## 한눈에`
   주어진 절에서 뽑은 **핵심 수치 표** 하나. 열은 `지표 | 값 | 근거 절`.
   6~10행. 값이 있는 것만 넣는다 - 서술은 여기 쓰지 마라.

2. `## 시사점`
   시사점 정확히 {n}개. 각각 `### 시사점 N. <한 줄 제목>` 아래 **세 단**으로만 쓰고,
   굵은 말머리를 그대로 쓴다:
   - `**확인된 것**` - 주어진 절에서 확인된 사실 (해석 없음)
   - `**뜻하는 것**` - 그 사실이 의미하는 바
   - `**달라져야 하는 것**` - 그래서 무엇이 바뀌어야 하는가

3. `## 종합 판단`
   서술형 한두 문단. 위 시사점들이 함께 가리키는 결론을 적고 닫는다.
   새 논점을 여기서 꺼내지 마라.

## 규칙 (어기면 그 문장을 통째로 빼라)

- **주어진 절에 있는 값은 그대로 옮겨 쓴다.** 수치·연도·비율·금액·기관명·법령명은
  원문 표기 그대로다. 다만 **주어진 절에 없는 값은 만들지 마라** - 필요한데 값이
  없으면 수치 없이 서술한다. 값을 피하는 것이 아니라, 있는 값을 쓰고 없는 값을 안 쓰는 것이다.
- **주체를 붙일 수 없는 문장은 제언이 아니다.** '달라져야 하는 것'의 각 항목은 누가
  하는 일인지가 문장 안에 있어야 한다(정부·주관부처·수행기관·참여기업 등).
  주체를 못 정하겠으면 그 항목을 빼라.
- 주어진 절의 서술을 그대로 다시 요약하지 마라. 이미 읽은 사람에게 쓰는 글이다.
- 개조식 말머리는 `□`(대) → `ㅇ`(중) → `-`(소) 순서를 지킨다.
- 인용 표식((출처 n)·[n])은 옮기지 않는다.

마지막 메시지에 JSON만 출력한다:
```json
{{"insights": "## 한눈에

| 지표 | 값 | 근거 절 |
…"}}
```"""


def build_system(n_implications: int = DEFAULT_N_IMPLICATIONS) -> str:
    return _SYSTEM_TEMPLATE.format(n=n_implications)


def collect_insight_sections(
    state: ProjectState, section_ids: Sequence[UUID] | None = None
) -> list[tuple[str, str]]:
    """요약의 입력이 될 (절 라벨, 본문) 목록.

    section_ids가 오면 **그 절만** 쓴다(사람이 고른 것이 정답이다, 2026-08-27).
    자동 선택은 제목 규칙에 기대는 추측이라, 장별 시사점이 여러 개거나 제목이 다른
    보고서에서 엉뚱한 절을 물어 왔다. 고른 절이 하나도 안 남으면 자동 선택으로 되돌아간다.

    자동(선택이 없을 때):
    1순위 = 제목이 시사점·제언·결론·소결인 절.
    2순위 = 마지막 장의 모든 절.
    """
    drafts = {d.section_id: d.content for d in state.selected_drafts()}
    plans = [p for p in state.section_plan if p.section_id in drafts]
    if not plans:
        return []

    picked: list[SectionPlan] = []
    if section_ids:
        wanted = {UUID(str(x)) for x in section_ids}
        picked = [p for p in plans if p.section_id in wanted]
    if not picked:
        picked = [p for p in plans if _INSIGHT_TITLE_RE.search(p.title)]
    if not picked:
        last_chapter = max(p.chapter_number for p in plans)
        picked = [p for p in plans if p.chapter_number == last_chapter]
    picked.sort(key=lambda p: (p.chapter_number, p.section_number))
    return [
        (f"{p.chapter_number}.{p.section_number} {p.title}", drafts[p.section_id]) for p in picked
    ]


def _build_input(sections: list[tuple[str, str]]) -> str:
    from src.services.export.report import _strip_citations

    lines: list[str] = []
    for label, content in sections:
        lines.append(f"\n## {label}\n{_strip_citations(content)}")
    text = "\n".join(lines)
    if len(text) > MAX_INPUT_CHARS:
        # 조용히 자르지 않는다 — 무엇이 빠졌는지 로그로 남긴다.
        logger.warning(
            "insights.input_truncated", total_chars=len(text), kept_chars=MAX_INPUT_CHARS
        )
        text = text[:MAX_INPUT_CHARS]
    return text


def _picked_ids(state: ProjectState, sections: list[tuple[str, str]]) -> list[UUID]:
    """고른 절의 안정 id — 라벨(번호 제목)로 계획에서 되짚는다.

    라벨은 번호가 밀리면 흔들린다. 다음에 화면이 선택을 되살릴 정본은 절 id다.
    """
    by_label = {
        f"{p.chapter_number}.{p.section_number} {p.title}": p.section_id for p in state.section_plan
    }
    return [by_label[label] for label, _ in sections if label in by_label]


async def build_insights(
    state: ProjectState,
    *,
    client: LLMClient | None = None,
    model: str | None = None,
    section_ids: Sequence[UUID] | None = None,
    n_implications: int = DEFAULT_N_IMPLICATIONS,
) -> dict[str, Any] | None:
    """시사점 요약 생성 — 입력이 될 절이 없으면 None.

    section_ids가 오면 사람이 고른 그 절만 근거로 삼는다(없으면 자동 선택).
    """
    sections = collect_insight_sections(state, section_ids)
    if not sections:
        return None
    client = client or get_llm_client()
    model = model or settings.insights_model

    request = CompletionRequest(
        messages=[
            Message(
                role="user",
                content=(
                    f"보고서 주제: {state.topic}\n\n"
                    "아래가 근거로 삼을 절 전부다. 여기 없는 사실·값은 쓰지 마라.\n"
                    f"{_build_input(sections)}"
                ),
            )
        ],
        model=model,
        system=build_system(n_implications),
        temperature=0.0,
        max_tokens=DEFAULT_MAX_TOKENS,
        cache_key=None,
    )
    with token_context(
        user_id=state.user_id, project_id=state.project_id, operation=INSIGHTS_OPERATION
    ):
        response = await client.complete(request)

    manifest = _parse_manifest(response.content)
    body = str(manifest.get("insights") or "").strip()
    if not body:
        logger.warning("insights.empty_manifest", project_id=str(state.project_id))
        return None
    logger.info(
        "insights.built",
        project_id=str(state.project_id),
        n_sections=len(sections),
        chars=len(body),
    )
    return {
        "content": body,
        "source_sections": [label for label, _ in sections],
        "source_section_ids": [str(sid) for sid in _picked_ids(state, sections)],
        "n_implications": n_implications,
        "model": model,
        # 만든 시각을 요약과 함께 남긴다 — 온도 0이라 같은 본문이면 같은 요약이 나와
        # '다시 만들기'가 돌아도 화면이 그대로다. 그때 눌린 것이 실제로 돌았는지는
        # 이 값만이 알려준다(projects.updated_at은 다른 편집에도 움직인다).
        "built_at": now().isoformat(),
    }


async def persist_insights(project_id: UUID, insights: dict[str, Any] | None) -> None:
    """projects.insights 저장 — 요청 밖 경로라 자체 세션(opener)으로 커밋."""
    from sqlalchemy import update

    from src.db.models.project import Project
    from src.db.session import async_session_maker

    async with async_session_maker() as session:
        await session.execute(
            update(Project).where(Project.id == project_id).values(insights=insights)
        )
        await session.commit()


# 별도 파일의 첫 줄 제목 — 웹 화면(/insights)과 같은 이름을 써 둘이 같은 산출물임을
# 파일만 받은 사람도 알아보게 한다.
INSIGHTS_HEADING = "시사점 요약"


def insights_blocks(state: ProjectState, content: str) -> list[Block]:
    """요약 마크다운 → HWPX 블록.

    본문 보고서와 **같은 서식**(용지 20/20/15/15·본문 12pt·개조식 계단)을 쓰되
    표지·목차·참고문헌은 없다 — 2~3쪽 브리핑에 겉장 한 장을 앞세우면 읽을 것보다
    겉이 두껍다. 대신 제목 아래 한 줄로 어느 보고서의 요약인지 밝힌다(파일이 따로
    돌아다녀도 출처를 잃지 않게).
    """
    from src.services.export.report import _KST, _strip_citations, markdown_to_blocks

    created = state.created_at
    if created.tzinfo is None:  # 방어: naive면 UTC로 간주(저장은 UTC 규약)
        created = created.replace(tzinfo=UTC)
    origin = "  ·  ".join(
        part
        for part in (
            (state.title or state.topic or "").strip(),
            created.astimezone(_KST).strftime("%Y년 %m월 %d일"),
        )
        if part
    )
    blocks: list[Block] = [Heading(level=1, text=INSIGHTS_HEADING)]
    if origin:
        blocks.append(Paragraph(text=origin, align="LEFT"))
    # (출처 n)은 프롬프트가 이미 옮기지 말라고 못박았지만 새어 나올 때가 있다. 이
    # 파일엔 참고문헌 목록이 없어 번호만 남으면 가리킬 데가 없는 표식이 된다.
    blocks.extend(markdown_to_blocks(_strip_citations(content)))
    return blocks


def export_insights(
    state: ProjectState,
    content: str,
    *,
    output_dir: str | Path | None = None,
) -> Path:
    """시사점 요약을 별도 HWPX로 렌더하고 경로를 반환.

    본문 완성본과 섞이지 않게 `<export_dir>/_insights/`에 project_id로 이름 짓는다.
    다운로드마다 다시 렌더하므로(요약은 3~5천 자, 1초 미만) 신선도 판정이 필요 없다 —
    다시 만들기로 요약이 바뀌면 다음 내려받기가 곧 새 파일이다.
    """
    from src.services.export.report import export_filename

    out_dir = (
        Path(output_dir) if output_dir is not None else Path(settings.export_dir) / "_insights"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    template = Path(settings.export_template_path) if settings.export_template_path else None
    path = out_dir / export_filename(state.project_id)
    build_report(
        insights_blocks(state, content),
        path,
        template_path=template,
        apply_chrome=template is None,
    )
    logger.info(
        "insights.hwpx_written",
        project_id=str(state.project_id),
        path=str(path),
        chars=len(content),
    )
    return path
