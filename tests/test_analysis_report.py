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


def _profile(title="Whole-pattern refinement — LMFP"):
    x = np.linspace(1.0, 8.0, 400)
    y = (100.0
         + 1200.0 * np.exp(-4.0 * np.log(2.0) * ((x - 2.0) / 0.06) ** 2))
    return ar.ReportProfileFit(
        tth=x, y_obs=y, y_calc=y - 0.5,
        residual=np.full_like(x, 0.5), rwp=2.34, rp=1.82,
        title=title, background=np.full_like(x, 100.0),
        bragg=[("LMFP", np.array([2.0, 3.0, 4.5])),
               ("Al", np.array([3.8]))])


def _refine():
    return ar.ReportRefine(
        phase_label="LMFP", cif="/data/LiMn0.6Fe0.4PO4_olivine.cif",
        rwp=2.34, rp=1.82,
        reduced_chi2=1.2, n_obs=400, n_params=12, success=True,
        message="ok", lebail=False, profile=_profile(),
        params=[("a", 4.70, 4.701, 0.001),
                ("zero", 0.0, 0.01, 0.002)],
        weights=[ar.ReportPhaseWeight("LMFP", 96.2, 123.4, 4.0, 158.0,
                                      289.0),
                 ar.ReportPhaseWeight("Al", 3.8, 8.0, 4.0, 27.0, 66.0,
                                      source="le_bail")],
        weight_note="wt% 只对晶态相",
        site_coords=[("Fe1", "4c", 0.5, 0.5, 0.5)],
        methods=[("结构", "LMFP.cif"), ("模式", "结构精修")],
        note="立方晶胞只精修 a")


def _insitu():
    rows = []
    for i in range(1, 6):
        rows.append({
            "frame": i, "file": f"f{i}.tif",
            "status": "ok_soft" if i == 3 else "ok", "usable": True,
            "rwp": 2.0 + 0.05 * i, "rp": 1.6 + 0.04 * i,
            "f_rich": 0.92 - 0.05 * i, "f_poor": 0.08 + 0.05 * i,
            "wt_rich": 90 - 5 * i, "wt_poor": 10 + 5 * i,
            "rich_a": 10.4, "rich_b": 6.0, "rich_c": 4.7,
            "poor_a": 9.9, "poor_b": 5.8, "poor_c": 4.75,
        })
    mi_rows = [{"frame": i, "doublet_area": 100 + i,
                "centroid": 1.3 + 0.01 * i,
                "f_poor": 0.08 + 0.05 * i, "status": "ok"}
               for i in range(1, 6)]
    return ar.ReportInSitu(
        rows=rows, methods=[("帧数", "5")],
        frame_fit=_profile("In-situ frame 2 — f2.tif"), mi_rows=mi_rows)


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

    def test_refine_section_tables_and_figure(self):
        html = ar.build_report_html(_snapshot(refine=_refine()))
        self.assertIn("全谱精修", html)
        self.assertIn("2.34", html)              # Rwp
        self.assertIn("96.20", html)             # wt%
        self.assertIn("Fe1", html)               # 位点
        self.assertIn("精修参数", html)
        self.assertIn("定量相分数", html)
        self.assertIn("立方晶胞只精修 a", html)
        self.assertIn("whole-pattern refinement", html)   # 图的 alt

    def test_insitu_section_summary_frames_and_figures(self):
        html = ar.build_report_html(_snapshot(insitu=_insitu()))
        self.assertIn("原位三相批量", html)
        self.assertIn("5/5", html)
        self.assertIn("0.870", html)             # f_rich 起点
        self.assertIn("ok_soft=1", html)
        self.assertIn("逐帧结果（完整）", html)
        self.assertIn("模型无关检查", html)
        self.assertIn("in-situ single frame fit", html)

    def test_missing_refine_and_insitu_are_marked(self):
        html = ar.build_report_html(_snapshot(
            sections=("curve", "refine", "insitu")))
        self.assertIn("本次未做全谱精修", html)
        self.assertIn("本次未跑原位批量", html)
        self.assertIn("本次未做：全谱精修、原位三相批量", html)

    def test_insitu_all_error_batch_still_renders(self):
        insitu = ar.ReportInSitu(rows=[{
            "frame": 1, "status": "error: boom", "usable": False,
            "rwp": "nan", "f_rich": "nan"}])
        html = ar.build_report_html(_snapshot(insitu=insitu))
        self.assertIn("error=1", html)
        self.assertIn("Rwp —", html)
        self.assertIn("0/1", html)

    def test_insitu_stats_accepts_csv_strings(self):
        rows = [{"frame": "1", "status": "ok", "usable": "True",
                 "rwp": "2.1", "f_rich": "0.9"},
                {"frame": "2", "status": "error", "usable": "False",
                 "rwp": "bad", "f_rich": "nan"}]
        stats = ar._insitu_stats(rows)
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["usable"], 1)
        self.assertAlmostEqual(stats["rwp_max"], 2.1)
        self.assertAlmostEqual(stats["f_start"], 0.9)

    def test_unselected_sections_are_absent(self):
        html = ar.build_report_html(_snapshot(sections=("curve",)))
        for title in ("峰表", "单峰拟合", "Williamson–Hall / Scherrer",
                      "物相筛选", "全谱精修", "原位三相批量"):
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
