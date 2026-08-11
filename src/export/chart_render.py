"""차트 스펙 → PNG — HWPX에 넣을 그림을 서버에서 그린다.

웹 프리뷰는 같은 스펙을 SVG로 그린다(web/src/features/preview/ChartBlock.tsx). 두 렌더러가
따로 놀면 화면에서 본 그래프와 받은 파일의 그래프가 달라 보이므로, 색·계열 순서·값 라벨
규칙은 src/core/charts.py를 단일 진실로 삼는다.

값 라벨을 늘 찍는 이유가 있다. 계열 색 일부는 흰 배경 대비가 3:1 미만이라 색만으로는
구분이 보장되지 않는다(charts.SERIES_COLORS 주석). 라벨과 범례가 그 완화 장치이며,
원본 표(spec.table)도 함께 남는다.

한글 폰트가 없으면 라벨이 네모로 깨진다 — 그건 잘못된 그림이므로 그리지 않고
ChartRenderError를 올려 호출부가 원본 표로 되돌리게 한다.
"""

from __future__ import annotations

import io
from functools import lru_cache

import matplotlib

matplotlib.use("Agg")  # 서버에 디스플레이가 없다 — 반드시 pyplot import 전에.

import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from src.core.charts import SERIES_COLORS, ChartSpec, label_ink  # noqa: E402

# 그림 크기(인치)·해상도 — 본문 폭(A4 좌우 여백 제외 160mm ≈ 6.3in)에 맞춘다.
# 150dpi면 인쇄에서 계단이 보이지 않으면서 파일이 과하게 커지지 않는다.
FIG_WIDTH_IN = 6.3
FIG_HEIGHT_IN = 3.4
DPI = 150

# 찾을 한글 폰트 — 앞에서부터 있는 것을 쓴다(운영 컨테이너=나눔고딕, 윈도우=맑은고딕).
_KOREAN_FONTS = ("NanumGothic", "Nanum Gothic", "Malgun Gothic", "AppleGothic", "Noto Sans CJK KR")

# 눈금·격자는 물러나 있어야 데이터가 읽힌다.
_GRID_COLOR = "#e3e3e0"
_TEXT_PRIMARY = "#0b0b0b"
_TEXT_SECONDARY = "#52514e"


class ChartRenderError(RuntimeError):
    """차트를 그릴 수 없는 상태 — 호출부는 원본 표로 되돌린다."""


@lru_cache(maxsize=1)
def korean_font() -> str | None:
    """설치된 한글 폰트 이름 — 없으면 None. 폰트 목록 스캔이 비싸 한 번만 한다."""
    available = {f.name for f in fm.fontManager.ttflist}
    return next((name for name in _KOREAN_FONTS if name in available), None)


def _apply_style(font: str) -> None:
    plt.rcParams.update(
        {
            "font.family": font,
            "axes.unicode_minus": False,  # 한글 폰트엔 유니코드 마이너스가 없어 네모가 된다
            "axes.edgecolor": _GRID_COLOR,
            "axes.labelcolor": _TEXT_SECONDARY,
            "text.color": _TEXT_PRIMARY,
            "xtick.color": _TEXT_SECONDARY,
            "ytick.color": _TEXT_SECONDARY,
            "font.size": 9,
        }
    )


def _format_value(value: float) -> str:
    """값 라벨 — 정수는 천 단위 쉼표, 소수는 한 자리까지."""
    if abs(value - round(value)) < 0.05:
        return f"{round(value):,}"
    return f"{value:,.1f}"


def _fit_x_labels(labels: tuple[str, ...]) -> list[str]:
    """칸 폭에 안 들어가는 x축 라벨을 줄인다 — 웹 SVG(XLabels)와 같은 규칙.

    실제 보고서의 항목명은 "2023년 글로벌 배터리 수요"처럼 길다. 그대로 두면 이웃
    라벨과 겹쳐 둘 다 못 읽는다. 90도로 돌리는 선택지는 없다 — 세로로 선 한글은
    읽기 나쁘다는 이유로 이미 축 단위 표기에서도 뺐다.
    """
    # 9pt 한글 한 자 ≈ 0.125in. 칸 폭은 그림 너비를 항목 수로 나눈 값.
    max_chars = max(int((FIG_WIDTH_IN / max(len(labels), 1)) / 0.125), 3)
    return [f"{s[: max_chars - 1]}…" if len(s) > max_chars else s for s in labels]


def _draw_bar(ax, spec: ChartSpec) -> None:
    n = len(spec.series)
    width = 0.8 / n
    positions = range(len(spec.x))
    for i, s in enumerate(spec.series):
        offset = (i - (n - 1) / 2) * width
        xs = [p + offset for p in positions]
        # 막대 사이 2px 틈 — 이웃한 채움이 서로 번지지 않게 폭을 살짝 줄인다.
        bars = ax.bar(xs, s.values, width=width * 0.92, label=s.name, color=SERIES_COLORS[i])
        if n <= 2:  # 계열이 많으면 라벨이 서로 부딪힌다 — 그때는 범례와 눈금으로 읽는다
            ax.bar_label(bars, labels=[_format_value(v) for v in s.values], padding=2, fontsize=8)
    ax.set_xticks(list(positions))
    ax.set_xticklabels(_fit_x_labels(spec.x))
    ax.set_ylim(bottom=0)


def _draw_line(ax, spec: ChartSpec) -> None:
    labels = _fit_x_labels(spec.x)
    for i, s in enumerate(spec.series):
        ax.plot(
            labels,
            s.values,
            label=s.name,
            color=SERIES_COLORS[i],
            linewidth=2,
            marker="o",
            markersize=4,
        )
        # 마지막 점만 직접 라벨 — 모든 점에 숫자를 찍으면 선이 안 보인다.
        ax.annotate(
            _format_value(s.values[-1]),
            (len(spec.x) - 1, s.values[-1]),
            textcoords="offset points",
            xytext=(4, 4),
            fontsize=8,
            color=_TEXT_SECONDARY,
        )
    # 끝점 라벨이 그림 밖으로 잘리지 않게 오른쪽에 자리를 둔다.
    ax.set_xmargin(0.08)
    # 양수 데이터는 0에서 시작한다. 밑을 잘라내면 증가폭이 실제보다 커 보이는데,
    # 심사받는 문서에서 그건 곧 신뢰 문제다. 음수(증감률 등)가 섞이면 자동에 맡긴다.
    if all(v >= 0 for s in spec.series for v in s.values):
        ax.set_ylim(bottom=0)


def _draw_pie(ax, spec: ChartSpec) -> None:
    values = spec.series[0].values
    colors = SERIES_COLORS[: len(values)]
    _, _, autotexts = ax.pie(
        values,
        labels=spec.x,
        colors=colors,
        autopct=lambda pct: f"{pct:.1f}%",
        startangle=90,
        counterclock=False,
        wedgeprops={"edgecolor": "white", "linewidth": 2},  # 조각 사이 흰 틈
        textprops={"fontsize": 8},
    )
    for text, color in zip(autotexts, colors, strict=False):
        text.set_color(label_ink(color))
    ax.axis("equal")


def render_png(spec: ChartSpec) -> bytes:
    """차트 스펙 → PNG 바이트. 한글 폰트가 없거나 그릴 수 없으면 ChartRenderError."""
    font = korean_font()
    if font is None:
        raise ChartRenderError(
            "한글 폰트를 찾지 못해 차트를 그릴 수 없음 — 라벨이 깨진 그림보다 원본 표가 낫다"
        )
    _apply_style(font)
    fig, ax = plt.subplots(figsize=(FIG_WIDTH_IN, FIG_HEIGHT_IN), dpi=DPI)
    try:
        if spec.type == "bar":
            _draw_bar(ax, spec)
        elif spec.type == "line":
            _draw_line(ax, spec)
        else:
            _draw_pie(ax, spec)

        if spec.type != "pie":
            if spec.unit:
                # 단위는 세로 축 라벨로 두지 않는다 — 90도 돌아간 한글은 읽기 나쁘다.
                # 공공 보고서 관례대로 표 왼쪽 위에 "(단위: …)"로 적는다.
                ax.text(
                    0,
                    1.02,
                    f"(단위: {spec.unit})",
                    transform=ax.transAxes,
                    fontsize=8,
                    color=_TEXT_SECONDARY,
                )
            ax.grid(axis="y", color=_GRID_COLOR, linewidth=0.8)
            ax.set_axisbelow(True)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
        # 계열이 둘 이상이면 범례는 항상 — 색만으로 정체를 알게 두지 않는다.
        # 자리는 축 아래로 고정한다. loc="best"는 빈 곳을 찾는다지만 실제로는 꺾은선 위에
        # 얹혀 데이터를 가렸다(2026-08-11 렌더 확인) — 겹칠 수 없는 자리가 낫다.
        if len(spec.series) > 1:
            ax.legend(
                frameon=False,
                fontsize=8,
                loc="upper center",
                bbox_to_anchor=(0.5, -0.08),
                ncol=len(spec.series),
            )
        # 제목은 그림 안에 넣지 않는다 — 조립 단계가 그림 아래에 "[그림 N-M] 제목" 캡션을
        # 달기 때문에, 여기서도 그리면 같은 제목이 위아래로 두 번 나온다.
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=DPI, bbox_inches="tight", facecolor="white")
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001 — 어떤 실패든 원본 표 폴백이 낫다
        raise ChartRenderError(f"차트 렌더 실패: {exc}") from exc
    finally:
        plt.close(fig)
