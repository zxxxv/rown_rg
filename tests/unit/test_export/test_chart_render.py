"""차트 PNG 렌더 — 글자가 읽히는지, 못 그릴 때 표로 되돌릴 신호를 주는지."""

from __future__ import annotations

import pytest

from src.core.charts import SERIES_COLORS, label_ink, parse_chart_spec
from src.export.chart_render import ChartRenderError, korean_font, render_png

_PIE = "type: pie\nx: 태양광 | 풍력 | 수력\nseries: 비중 = 46.2 | 21.5 | 32.3\n"


def _luminance(color: str) -> float:
    if color == "white":
        return 1.0

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: str, b: str) -> float:
    lo, hi = sorted((_luminance(a), _luminance(b)))
    return (hi + 0.05) / (lo + 0.05)


class TestLabelInk:
    @pytest.mark.parametrize("color", SERIES_COLORS)
    def test_every_series_color_gets_readable_ink(self, color: str):
        # 조각 위 숫자는 색 대비를 메우는 장치다 — 그 글자가 안 읽히면 장치가 아니다.
        # 3:1은 굵은 큰 글자의 WCAG 기준선이다(라벨은 굵게 그린다).
        assert _contrast(color, label_ink(color)) >= 3.0

    def test_yellow_takes_dark_ink(self):
        # 밝기 임계로 가르면 노랑이 흰 글자 쪽에 걸려 대비 2.2:1로 남았다(2026-08-11 렌더 확인).
        assert label_ink("#eda100") != "white"

    def test_dark_blue_takes_white(self):
        assert label_ink("#2a78d6") == "white"


@pytest.mark.skipif(korean_font() is None, reason="한글 폰트가 없는 환경")
class TestRenderPng:
    def test_renders_png_bytes(self):
        png = render_png(parse_chart_spec(_PIE))
        assert png.startswith(b"\x89PNG")

    def test_title_is_not_drawn_in_the_image(self, monkeypatch):
        # 제목은 조립 단계가 그림 아래에 "[그림 N-M] 제목"으로 단다 — 그림에도 그리면 두 번 나온다.
        titles: list[str] = []
        from matplotlib.axes import Axes

        monkeypatch.setattr(Axes, "set_title", lambda self, *a, **k: titles.append(str(a)))
        render_png(parse_chart_spec("type: bar\ntitle: 제목\nx: 가 | 나\nseries: 값 = 1 | 2\n"))
        assert titles == []


def test_missing_korean_font_refuses_to_draw(monkeypatch):
    # 라벨이 네모로 깨진 그림보다 원본 표가 낫다 — 호출부가 되돌릴 수 있게 예외로 알린다.
    monkeypatch.setattr("src.export.chart_render.korean_font", lambda: None)
    with pytest.raises(ChartRenderError, match="한글 폰트"):
        render_png(parse_chart_spec(_PIE))
