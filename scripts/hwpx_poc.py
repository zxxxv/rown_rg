"""HWPX 출력 PoC — 검증된 python-hwpx API로 회사 표준 보고서 한 부를 생성·검증한다.

목적(시나리오 A): 서버에서 한컴 없이 HWPX를 만들 수 있는지, 그리고 자동 목차의 전제인
개요 수준(outline_level)이 문단에 실제로 박히는지 확인한다.

실행:
    .venv/Scripts/python.exe scripts/hwpx_poc.py [--template 마스터.hwpx] [--out 출력.hwpx]

검증되는 것: ② 개요 수준 부여, 서식 적용, 표 채우기, validate, markdown 내보내기.
검증 불가(한컴 필요): ① 자동 목차 페이지번호 갱신, ④ 갱신 방식 — 출력물을 한컴에서 직접 확인.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hwpx import HwpxDocument  # noqa: E402

from src.export.hwpx_writer import Block, Heading, Paragraph, Table, build_report  # noqa: E402

SAMPLE: list[Block] = [
    Heading(1, "Ⅰ. 사업 개요"),
    Paragraph("본 보고서는 OO지역 광역교통망 확충 사업의 예비타당성을 평가한다."),
    Heading(2, "1. 사업의 배경"),
    Paragraph("인구 고령화와 수도권 집중도 변화가 사업 수요에 미치는 영향을 분석한다."),
    Heading(2, "2. 분석 범위"),
    Heading(3, "가. 비용편익 분석"),
    Paragraph("총사업비와 편익을 현재가치로 환산하여 B/C 비율을 산정한다."),
    Table(
        headers=["구분", "금액(억원)", "비고"],
        rows=[
            ["총사업비", "12,400", "2026년 불변가격"],
            ["총편익", "15,800", "30년 누적"],
            ["B/C", "1.27", "1.0 이상 타당"],
        ],
    ),
    Heading(1, "Ⅱ. 종합 결론"),
    Paragraph("비용편익·정책성·지역균형을 종합할 때 사업 추진이 타당한 것으로 판단된다."),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="HWPX 출력 PoC")
    parser.add_argument("--template", type=Path, default=None, help="마스터 템플릿 .hwpx")
    parser.add_argument(
        "--out", type=Path, default=ROOT / "scripts" / "_out_poc.hwpx", help="출력 .hwpx"
    )
    args = parser.parse_args()

    out = build_report(
        SAMPLE,
        args.out,
        template_path=args.template,
        apply_chrome=args.template is None,
    )
    print(f"[저장] {out}  ({out.stat().st_size:,} bytes)")

    # 재오픈 → 스키마 검증 + 구조 확인
    doc = HwpxDocument.open(str(out))
    report = doc.validate()
    ok = getattr(report, "is_valid", None)
    errors = list(getattr(report, "errors", []) or [])
    print(f"[검증] valid={ok}  errors={len(errors)}")
    for err in errors[:5]:
        print(f"   - {err}")
    print(f"[문단] 총 {len(doc.paragraphs)}개")

    md = doc.export_markdown()
    print("[markdown 미리보기]")
    for line in md.splitlines()[:14]:
        print("   " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
