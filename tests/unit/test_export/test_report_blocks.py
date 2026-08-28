class TestChartFallback:
    """차트를 못 그릴 때 원본 표로 되돌아가는가 — 그림 하나가 보고서를 죽이면 안 된다."""

    _MD = """## 시험

```chart
type: bar
title: 폴백 확인
x: 가 | 나 | 다
series: 값 = 1 | 2 | 3
table: |
  표: 폴백 확인
  | 항목 | 값 |
  |---|---|
  | 가 | 1 |
  | 나 | 2 |
  | 다 | 3 |
```
"""

    def test_차트_블록이_원본_표를_들고_다닌다(self):
        from src.export.hwpx_writer import Chart
        from src.services.export.report import markdown_to_blocks

        chart = next(b for b in markdown_to_blocks(self._MD) if isinstance(b, Chart))
        assert chart.fallback is not None
        assert chart.fallback.headers == ["항목", "값"]
        assert chart.fallback.rows == [["가", "1"], ["나", "2"], ["다", "3"]]

    def test_못_그리면_표가_대신_실린다(self, tmp_path, monkeypatch):
        """한글 폰트가 없는 운영 컨테이너 — 예전에는 여기서 내보내기 전체가 죽었다."""
        import zipfile

        from src.export import chart_render
        from src.export.hwpx_writer import Chart, Heading, build_report
        from src.services.export.report import markdown_to_blocks

        chart_render.korean_font.cache_clear()
        monkeypatch.setattr(chart_render, "korean_font", lambda: None)
        chart = next(b for b in markdown_to_blocks(self._MD) if isinstance(b, Chart))

        out = tmp_path / "fallback.hwpx"
        build_report([Heading(level=1, text="시험"), chart], out)

        with zipfile.ZipFile(out) as z:
            assert not [n for n in z.namelist() if n.startswith("BinData")]  # 그림은 없고
            body = z.read("Contents/section0.xml").decode("utf-8")
        assert all(cell in body for cell in ("항목", "가", "나", "다"))  # 내용은 남았다

    def test_펜스는_분량으로_두_번_세지_않는다(self):
        """펜스가 품은 원본 표는 읽는 사람에게 안 보인다 — 자리표시자 계산에서 뺀다."""
        from src.core.charts import chart_fences_as_tables

        measured = chart_fences_as_tables(self._MD)
        assert "```chart" not in measured
        assert "| 가 | 1 |" in measured
        assert len(measured) < len(self._MD)
