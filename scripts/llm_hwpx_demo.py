"""LLM tool_use → 회사양식 HWPX(한글파일) 생성 데모.

핵심 아이디어
-------------
LLM에게 HWPX/XML을 직접 쓰게 하지 않는다. 대신 `emit_document` 도구(tool_use)로
**의미 블록(heading/paragraph/table)** 만 받고, 결정론적 코드가 프로젝트 출력기
(src/export/hwpx_writer.build_report)로 회사 표준 서식 .hwpx를 만든다.
폰트·여백·머리말·쪽번호 같은 서식은 LLM이 손대지 않으며 출력기/템플릿이 고정한다.

  LLM(tool_use) ──emit_document(blocks)──▶ 검증/변환 ──▶ build_report ──▶ .hwpx
                                                                      (한컴 불필요)

두 가지 실행 경로
----------------
1) 키 없이 결정론 파이프라인만 검증(샘플 tool_use 입력 사용):
     JWT_SECRET_KEY=dummy uv run python scripts/llm_hwpx_demo.py --mock --out demo.hwpx
2) 실제 LLM 호출(Anthropic):
     ANTHROPIC_API_KEY=sk-... uv run python scripts/llm_hwpx_demo.py \
         --topic "광역교통망 예비타당성 조사 요약" --out report.hwpx [--model claude-sonnet-4-6]

프로덕션 메모
------------
이 데모는 Anthropic SDK를 직접 쓴다. 제품 파이프라인에서 라우팅·토큰추적·캐셋을 거치려면
src/clients/base.py 의 CompletionRequest 에 `tools`(+tool_choice) 필드를 추가하고
어댑터가 provider 도구 형태로 번역하도록 확장해야 한다(현재는 web_search 서버툴만 지원).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from src.export.hwpx_writer import Block, Heading, Paragraph, Table, build_report

# --- LLM에게 노출할 도구(tool_use) 스키마 — hwpx_writer 의 Block 모델과 1:1 -----------
EMIT_DOCUMENT_TOOL: dict[str, Any] = {
    "name": "emit_document",
    "description": (
        "보고서 본문을 구조화 블록 배열로 제출한다. "
        "블록 타입은 heading/paragraph/table 만 사용한다. "
        "heading.level 은 1=장, 2=절, 3=항(자동 목차 수준). "
        "폰트·색·여백 등 서식은 절대 지정하지 말 것 — 회사 표준 서식이 자동 적용된다. "
        "표는 headers(머리행) + rows(2차원 문자열)로 표현한다."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "blocks": {
                "type": "array",
                "description": "문서를 구성하는 블록들(순서대로 렌더링)",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["heading", "paragraph", "table"]},
                        "level": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 3,
                            "description": "heading 전용. 1=장,2=절,3=항",
                        },
                        "text": {"type": "string", "description": "heading/paragraph 전용"},
                        "headers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "table 전용. 머리행",
                        },
                        "rows": {
                            "type": "array",
                            "items": {"type": "array", "items": {"type": "string"}},
                            "description": "table 전용. 데이터 행들",
                        },
                    },
                    "required": ["type"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["blocks"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = (
    "당신은 주식회사 로운인사이트의 한국어 공공·정책 보고서 작성자다. "
    "주어진 주제로 가독성 좋은 보고서를 구성한다. 장/절/항의 제목 위계를 적절히 쓰고, "
    "수치·비교는 표로 정리한다. "
    "반드시 emit_document 도구로만 답하며 서식(폰트·여백)은 지정하지 않는다."
)


def blocks_from_payload(payload: dict[str, Any]) -> list[Block]:
    """emit_document 도구 입력(JSON)을 hwpx_writer 블록으로 검증·변환한다."""
    raw = payload.get("blocks")
    if not isinstance(raw, list) or not raw:
        raise ValueError("payload.blocks 가 비었거나 배열이 아님")
    out: list[Block] = []
    for i, b in enumerate(raw):
        kind = b.get("type")
        if kind == "heading":
            out.append(Heading(level=int(b.get("level", 1)), text=str(b["text"])))
        elif kind == "paragraph":
            out.append(Paragraph(text=str(b["text"])))
        elif kind == "table":
            out.append(
                Table(
                    headers=[str(h) for h in b["headers"]],
                    rows=[[str(c) for c in row] for row in b["rows"]],
                )
            )
        else:
            raise ValueError(f"blocks[{i}]: 알 수 없는 type={kind!r}")
    return out


def run_live(topic: str, model: str) -> dict[str, Any]:
    """실제 Anthropic 호출 — emit_document 도구를 강제하고 그 입력(blocks)을 반환한다."""
    import anthropic  # lazy: --mock 경로는 SDK 없이도 동작

    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 사용
    msg = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        tools=[EMIT_DOCUMENT_TOOL],
        tool_choice={"type": "tool", "name": "emit_document"},  # 도구 호출 강제
        messages=[{"role": "user", "content": f"다음 주제로 보고서를 작성하라:\n{topic}"}],
    )
    for block in msg.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "emit_document":
            return dict(block.input)
    raise RuntimeError("LLM이 emit_document 도구를 호출하지 않았다")


# --- 키 없이 파이프라인을 검증하기 위한 샘플 tool_use 입력 -------------------------------
# (실제 LLM이 emit_document 로 내보낼 법한 형태를 그대로 모사)
MOCK_PAYLOAD: dict[str, Any] = {
    "blocks": [
        {"type": "heading", "level": 1, "text": "광역교통망 예비타당성 조사 요약"},
        {
            "type": "paragraph",
            "text": (
                "본 보고서는 수도권 광역교통망 확충 사업의 예비타당성을 경제성·정책성 관점에서 "
                "요약한다. 검토 대상은 신규 광역철도 2개 노선과 환승센터 3개소다."
            ),
        },
        {"type": "heading", "level": 2, "text": "1. 사업 개요"},
        {
            "type": "paragraph",
            "text": (
                "총사업비 약 3.2조 원, 사업기간 2027~2034년. 일일 예상 수요는 개통 5년 차 기준 "
                "약 28만 명으로 추정된다."
            ),
        },
        {"type": "heading", "level": 2, "text": "2. 경제성 분석"},
        {"type": "paragraph", "text": "비용편익분석(B/C) 및 내부수익률(IRR)은 다음과 같다."},
        {
            "type": "table",
            "headers": ["지표", "노선 A", "노선 B"],
            "rows": [
                ["B/C", "1.08", "0.94"],
                ["IRR", "6.2%", "5.1%"],
                ["순현재가치(억원)", "4,210", "-870"],
            ],
        },
        {"type": "heading", "level": 2, "text": "3. 종합 의견"},
        {
            "type": "paragraph",
            "text": (
                "노선 A는 경제성(B/C 1.0 이상)을 확보해 추진 타당성이 인정된다. 노선 B는 경제성이 "
                "미흡하나 지역균형 등 정책성 가점을 반영한 종합평가(AHP)가 필요하다."
            ),
        },
    ]
}


def main() -> None:
    ap = argparse.ArgumentParser(description="LLM tool_use → 회사양식 HWPX 데모")
    ap.add_argument(
        "--mock", action="store_true", help="LLM 호출 없이 샘플 tool_use 입력으로 파이프라인만 검증"
    )
    ap.add_argument("--topic", help="실제 LLM 호출 시 보고서 주제")
    ap.add_argument("--model", default="claude-sonnet-4-6", help="Anthropic 모델 ID")
    ap.add_argument("--template", help="한컴 마스터 템플릿 .hwpx 경로(선택)")
    ap.add_argument("--out", default="llm_report.hwpx", help="출력 .hwpx 경로")
    args = ap.parse_args()

    if args.mock:
        payload = MOCK_PAYLOAD
        source = "mock(sample tool_use input)"
    else:
        if not args.topic:
            ap.error("--topic 가 필요합니다(또는 --mock 사용). 실호출엔 ANTHROPIC_API_KEY 도 필요.")
        if not os.getenv("ANTHROPIC_API_KEY"):
            ap.error("ANTHROPIC_API_KEY 환경변수가 없습니다.")
        payload = run_live(args.topic, args.model)
        source = f"live({args.model})"

    blocks = blocks_from_payload(payload)
    out = build_report(
        blocks,
        args.out,
        template_path=args.template,
        apply_chrome=args.template is None,  # 템플릿이 서식을 보유하면 코드 chrome 생략
    )
    print(f"OK [{source}] -> {Path(out)} ({os.path.getsize(out)} bytes, {len(blocks)} blocks)")
    if args.mock:
        print("payload 미리보기:", json.dumps(payload["blocks"][0], ensure_ascii=False))


if __name__ == "__main__":
    main()
