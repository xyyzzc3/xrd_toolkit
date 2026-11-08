"""一键分析报告的纯生成器测试（2026-11-08）。

只测 services/analysis_report：快照 → 自包含 HTML。不碰 Qt、不积分；
GUI 侧的收集与弹窗在 test_gui_report.py。
"""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.services import analysis_report as ar  # noqa: E402


def _curve(**overrides):
    x = np.linspace(1.0, 8.0, 3000)
    y = (100.0
         + 800.0 * np.exp(-4.0 * np.log(2.0) * ((x - 3.0) / 0.08) ** 2)
         + 500.0 * np.exp(-4.0 * np.log(2.0) * ((x - 4.5) / 0.07) ** 2))
    curve = ar.ReportCurve(
        name="LMFP_1", tth=x, intensity=y, path="/data/LMFP_1.tif",
        chain="none", config="lmfp1_lab6", wavelength=1.5406)
    curve.peaks = [
        ar.ReportPeak(center=3.0, height=800.0, fwhm=0.08,
                      area=1200.0, prominence=800.0),
        ar.ReportPeak(center=4.5, height=500.0, fwhm=0.07,
                      area=900.0, prominence=500.0),
    ]
    mask = (x > 2.88) & (x < 3.12)
    curve.fits = [ar.ReportFit(
        center0=3.0, half_window=0.12, shape="pseudovoigt",
        center=3.001, fwhm=0.080, fwhm_left=float("nan"),
        fwhm_right=float("nan"), eta=0.55, m=float("nan"),
        height=800.0, area=1200.0, rwp=2.1,
        window_tth=x[mask], background=np.full(int(mask.sum()), 100.0),
        window_intensity=y[mask])]
    curve.wh = ar.ReportWH(
        size_nm=42.0, strain=1.2e-3, intercept=0.001, slope=0.02,
        x=np.array([0.10, 0.15, 0.21]), y=np.array([0.002, 0.003, 0.005]),
        r2=0.98, instrument_note="constant 0.05°", n_used=3)
    for name, value in overrides.items():
        setattr(curve, name, value)
    return curve


def _snapshot(**overrides):
    kwargs = dict(
        title="XRD 分析报告", sample="LMFP_1",
        generated="2026-11-08 12:00:00", version="0.2.0",
        curve=_curve(), sections=ar._ALL_KEYS)
    kwargs.update(overrides)
    return ar.ReportSnapshot(**kwargs)


class TestReportHtml(unittest.TestCase):
    def test_self_contained_html_and_escaped_title(self):
        html = ar.build_report_html(_snapshot(title="测试 <报告> & 1"))
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("测试 &lt;报告&gt; &amp; 1", html)
        self.assertNotIn("<报告>", html)
        self.assertEqual(html.count("data:image/png;base64,"), 3)
        self.assertNotIn("<link", html)
        self.assertNotIn("<script src", html)

    def test_peak_fit_wh_and_phase_tables(self):
        phases = [ar.ReportPhase(
            name="LMFP", score=0.91, coverage=0.88, purity=0.75,
            scale=1.2, tth=np.array([3.0, 4.5]),
            intensity=np.array([1.0, 0.6]))]
        html = ar.build_report_html(_snapshot(phases=phases))
        self.assertIn("LMFP", html)
        self.assertIn("0.910", html)
        self.assertIn("3.0000", html)       # 峰位
        self.assertIn("42.0", html)         # W-H 尺寸
        self.assertIn("2.10", html)         # 拟合 Rwp
        self.assertIn("pseudo-Voigt", html)

    def test_missing_sections_are_marked_not_silent(self):
        empty = ar.ReportCurve(name="empty",
                               tth=np.array([1.0, 2.0]),
                               intensity=np.array([1.0, 1.0]))
        html = ar.build_report_html(_snapshot(curve=empty))
        for reason in ("没有检出峰", "本次未做单峰拟合",
                       "参与 W-H 的拟合峰", "本次未筛相"):
            self.assertIn(reason, html)
        self.assertIn("class=\"status-skip\"", html)
        self.assertIn("结果说明与限制", html)

    def test_unselected_sections_are_absent(self):
        html = ar.build_report_html(_snapshot(sections=("curve",)))
        for title in ("峰表", "单峰拟合", "Williamson–Hall / Scherrer",
                      "物相筛选"):
            self.assertNotIn(f"{title}</h3>", html)
        # 结果说明与限制始终保留（它不是可勾选节）
        self.assertIn("结果说明与限制", html)

    def test_processed_curve_draws_raw_trace(self):
        curve = _curve()
        curve.chain = "bg=auto/win=0.2"
        curve.raw_tth = curve.tth
        curve.raw_intensity = curve.intensity + 10.0
        html = ar.build_report_html(_snapshot(curve=curve))
        self.assertIn("bg=auto/win=0.2", html)
        self.assertIn("灰色细线 = 处理前", html)

    def test_write_report_utf8(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "报告.html"
            got = ar.write_report(_snapshot(), target)
            self.assertEqual(got, target)
            text = target.read_text(encoding="utf-8")
            self.assertIn("XRD Toolkit", text)
            self.assertIn("data:image/png;base64,", text)

    def test_wh_r2(self):
        result = ar.ReportWH(
            size_nm=1.0, strain=0.0, intercept=0.0, slope=2.0,
            x=np.array([1.0, 2.0, 3.0]), y=np.array([2.0, 4.0, 6.0]))
        self.assertAlmostEqual(ar._wh_r2(result), 1.0)

    def test_rwp_quality_thresholds(self):
        self.assertEqual(ar._rwp_quality(1.2), ("良好", "q-ok"))
        self.assertEqual(ar._rwp_quality(3.0), ("可接受", "q-warn"))
        self.assertEqual(ar._rwp_quality(8.0), ("偏差大", "q-bad"))
        self.assertEqual(ar._rwp_quality(float("nan")), ("—", "q-na"))

    def test_poor_fit_is_flagged_in_summary_and_table(self):
        curve = _curve()
        curve.fits[0].rwp = 8.0
        html = ar.build_report_html(_snapshot(curve=curve))
        self.assertIn("Rwp 超过 5%", html)
        self.assertIn("偏差大", html)
        self.assertIn("q-bad", html)


if __name__ == "__main__":
    unittest.main()
