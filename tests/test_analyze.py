"""自动分析辅助逻辑测试（2026-10-22）。

只测“相筛选/晶胞缩放扫描”和“Al 基片识别”这类纯计算，不跑完整 CLI
（完整 CLI 要两张真实 TIFF + 拟合，放 scripts 里手工跑）。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from pymatgen.core import Lattice, Structure  # noqa: E402

from xrd_toolkit.simulation import core  # noqa: E402
from xrd_toolkit.simulation import phase_search as ps  # noqa: E402
import analyze_pattern as ap  # noqa: E402

CU_KA = 1.5406


class TestAutoAnalysisHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lifepo4 = ap.STRUCT / "LiFePO4_olivine.cif"

    def test_phase_library_scans_directory_and_skips_al_substrate(self):
        names = [p.name for p in ps.phase_library(ap.STRUCT)]
        self.assertIn("LiFePO4_olivine.cif", names)
        self.assertIn("LaB6_NIST660c.cif", names)
        self.assertNotIn("Al_metal.cif", names,
                         "Al 只作第二相，不应进入主相候选")

    def test_phase_candidates_filters_by_allowed_elements(self):
        candidates = ps.phase_candidates(
            ap.STRUCT, ["Li", "Mn", "Fe", "P", "O"])
        names = [path.name for path, _st in candidates]
        self.assertIn("LiFePO4_olivine.cif", names)
        self.assertIn("LiMn0.6Fe0.4PO4_olivine.cif", names)
        self.assertNotIn("LaB6_NIST660c.cif", names)
        self.assertNotIn("Al_metal.cif", names)

    def test_detect_peaks_returns_positions_and_local_heights(self):
        x = np.arange(1.0, 2.0, 0.001)
        bg = np.full(x.size, 100.0)
        y = bg + 300.0 * np.exp(
            -4.0 * np.log(2.0) * ((x - 1.5) / 0.02) ** 2)
        t, h = ap.detect_peaks(x, y, bg)
        self.assertEqual(len(t), 1)
        self.assertAlmostEqual(t[0], 1.5, delta=2e-3)
        self.assertAlmostEqual(h[0], 300.0, delta=10.0)

    def test_scale_scan_finds_scaled_cell(self):
        base = core.load_structure(str(self.lifepo4))
        lat = Lattice.from_parameters(
            base.lattice.a * 1.01, base.lattice.b * 1.01,
            base.lattice.c * 1.01, *base.lattice.angles)
        scaled = Structure(
            lat, [s.species for s in base], [s.frac_coords for s in base])
        _, _, tth, inten = core.reflection_table(
            scaled, wavelength=CU_KA, two_theta_range=(10.0, 80.0), nmax=10)
        inten = inten / inten.max()
        obs = tth[inten >= 0.02]
        score = ps.score_phase(obs, base, CU_KA, 10.0, 80.0, 0.0)
        self.assertIsNotNone(score)
        _sc, scale, _cov, _pur = score
        self.assertAlmostEqual(scale, 1.01, delta=0.012)

    def test_al_substrate_detected(self):
        al_st = core.load_structure(str(ap.AL_CIF))
        al_d0, al_i = ps.reflection_data(al_st, 0.1223, 1.0, 8.0)
        al_t, al_i = ps.scaled_positions(al_d0, al_i, 0.1223, 1.0, 0.0)
        self.assertGreater(len(al_t), 0)
        j = int(np.argmax(al_i))
        self.assertGreater(al_i[j], 0.9)
        ranges = ap.al_substrate_ranges(
            al_t, al_i, 0.1223, 1.0, 8.0, 0.0, self.lifepo4, 1.01)
        self.assertTrue(ranges)
        self.assertTrue(any(lo <= al_t[j] <= hi for lo, hi in ranges))

    def test_estimate_zero_recovers_global_shift(self):
        """观测峰整体平移 0.12° 时估回 +0.12°（物相叠图不再“总偏右”）。"""
        lab6 = core.load_structure(str(ap.STRUCT / "LaB6_NIST660c.cif"))
        d0, inten = ps.reflection_data(lab6, CU_KA, 10.0, 80.0)
        tth, _ = ps.scaled_positions(d0, inten, CU_KA, 1.0, 0.0)
        obs = np.asarray(tth)[np.asarray(inten) >= 0.02] + 0.12
        zero = ps.estimate_zero(obs, lab6, CU_KA, 10.0, 80.0)
        self.assertAlmostEqual(zero, 0.12, delta=1e-3)

    def test_estimate_zero_needs_enough_matches(self):
        lab6 = core.load_structure(str(ap.STRUCT / "LaB6_NIST660c.cif"))
        self.assertEqual(
            ps.estimate_zero([1.0], lab6, CU_KA, 10.0, 80.0), 0.0)

    def test_screen_phases_ranks_lab6_first(self):
        lab6 = core.load_structure(str(ap.STRUCT / "LaB6_NIST660c.cif"))
        _, _, tth, inten = core.reflection_table(
            lab6, wavelength=CU_KA, two_theta_range=(10.0, 80.0), nmax=10)
        inten = inten / inten.max()
        obs = tth[inten >= 0.02]
        matches = ps.screen_phases(obs, ap.STRUCT, CU_KA, 10.0, 80.0, 0.0)
        self.assertTrue(matches)
        # 库里有 LaB6 的两份 CIF（1000055 与 NIST660c），分数相同，
        # 谁是第一取决于文件名；只要求认对物相。
        self.assertTrue(matches[0].path.name.startswith("LaB6"),
                        matches[0].path.name)

    def test_element_filter_reports_near_misses(self):
        """元素表漏 Mn 时，Mn 相不能静默消失，要进 near_miss 并带 extra。"""
        kept, near = ps.phase_candidates_report(
            ap.STRUCT, ["Li", "Fe", "P", "O"])
        kept_names = {path.name for path, _st in kept}
        near_map = {path.name: extra for path, _st, extra in near}
        self.assertIn("LiFePO4_olivine.cif", kept_names)
        self.assertIn("LiMn0.6Fe0.4PO4_olivine.cif", near_map,
                      "漏写 Mn 时 LMFP 相必须出现在相近相里")
        self.assertIn("Mn0.6Fe0.4PO4_heterosite.cif", near_map)
        self.assertEqual(near_map["LiMn0.6Fe0.4PO4_olivine.cif"], ("Mn",))

        kept2, near2 = ps.phase_candidates_report(
            ap.STRUCT, ["Li", "Mn", "Fe", "P", "O", "Al"])
        self.assertEqual(near2, [], "元素表完整时不应再有相近相")
        self.assertIn("LiMn0.6Fe0.4PO4_olivine.cif",
                      {path.name for path, _st in kept2})

    def test_screen_phases_reports_progress(self):
        """2026-11-17：引擎按 CIF 报进度（读取 → 打分候选 → 打分相近相）。"""
        lab6 = core.load_structure(str(ap.STRUCT / "LaB6_NIST660c.cif"))
        _, _, tth, inten = core.reflection_table(
            lab6, wavelength=CU_KA, two_theta_range=(10.0, 80.0), nmax=10)
        inten = inten / inten.max()
        obs = tth[inten >= 0.02]
        steps = []
        ps.screen_phases_report(
            obs, ap.STRUCT, CU_KA, 10.0, 80.0, 0.0,
            elements=["Li", "Fe", "P", "O"],
            on_progress=lambda done, total, label: steps.append(
                (done, total, label)))
        labels = {label for _done, _total, label in steps}
        self.assertTrue({"读取 CIF", "打分候选", "打分相近相"} <= labels)
        # 每个阶段的 done 从 1 开始递增、不超过该阶段的 total
        for label in ("读取 CIF", "打分候选", "打分相近相"):
            stage = [(d, t) for d, t, lab in steps if lab == label]
            self.assertTrue(stage, label)
            self.assertEqual([d for d, _t in stage],
                             list(range(1, len(stage) + 1)))
            self.assertTrue(all(t == stage[0][1] for _d, t in stage))

    def test_screen_phases_report_scores_near_misses(self):
        """相近相照样打分——界面靠这个分数判断"它比当前最佳还高"。"""
        lab6 = core.load_structure(str(ap.STRUCT / "LaB6_NIST660c.cif"))
        _, _, tth, inten = core.reflection_table(
            lab6, wavelength=CU_KA, two_theta_range=(10.0, 80.0), nmax=10)
        inten = inten / inten.max()
        obs = tth[inten >= 0.02]
        matches, near = ps.screen_phases_report(
            obs, ap.STRUCT, CU_KA, 10.0, 80.0, 0.0,
            elements=["Li", "Fe", "P", "O"])
        self.assertTrue(matches)
        self.assertTrue(near, "漏 Mn 时应带回 Mn 相近相")
        by_name = {m.path.stem: m for m in near}
        self.assertEqual(by_name["LiMn0.6Fe0.4PO4_olivine"].extra, ("Mn",))
        self.assertEqual(by_name["Mn0.6Fe0.4PO4_heterosite"].extra, ("Mn",))
        scores = [m.score for m in near]
        self.assertEqual(scores, sorted(scores, reverse=True))


def _gauss(x, center, fwhm, height):
    return height * np.exp(-4.0 * np.log(2.0) * ((x - center) / fwhm) ** 2)


class TestObservedPeaks(unittest.TestCase):
    """筛相用的观测峰：裁剪坏点区间后峰表必须稳定（2026-11-16）。"""

    @staticmethod
    def _curve(with_spike):
        rng = np.random.default_rng(7)
        x = np.arange(1.0, 8.0, 0.002)
        y = 40.0 + 2.0 * rng.standard_normal(x.size)
        strong = (1.42, 1.86, 2.40, 3.25, 3.79, 4.44, 4.88, 5.20,
                  5.72, 5.98)
        for c in strong:
            y = y + _gauss(x, c, 0.02, 300.0)
        # 一批弱峰：旧阈值的全局下限把它们藏起来/放出来，正是问题本身
        for k, c in enumerate(np.linspace(1.3, 7.6, 30)):
            y = y + _gauss(x, c, 0.02, 30.0 + 2.0 * k)
        if with_spike:
            # 探测器坏点：比真峰强 10 倍以上，旧阈值被它抬到 2%×6000
            y = y + _gauss(x, 2.90, 0.008, 6.0e3)
        return x, y

    def test_cutting_detector_spike_keeps_the_same_peak_table(self):
        x, y_ref = self._curve(with_spike=False)
        _, y_spike = self._curve(with_spike=True)
        y_cut = y_spike.copy()
        y_cut[(x >= 2.8) & (x <= 3.1)] = np.nan
        ref = ps.observed_peaks(x, y_ref)
        got = ps.observed_peaks(x, y_cut)
        self.assertLessEqual(got.size, ps.SCREEN_MAX_PEAKS)
        self.assertTrue(np.all(np.abs(got - 2.95) > 0.15),
                        "裁剪区间里不应再报峰")
        np.testing.assert_allclose(got, ref, atol=0.01,
                                   err_msg="挖掉坏点后峰表变了")

    def test_keeps_only_the_strongest_peaks(self):
        x = np.arange(1.0, 2.0, 0.001)
        y = np.full(x.size, 10.0)
        centers = np.linspace(1.05, 1.95, 25)
        for k, c in enumerate(centers):
            y = y + _gauss(x, c, 0.008, 1000.0 - 20.0 * k)
        peaks = ps.observed_peaks(x, y)
        self.assertEqual(peaks.size, ps.SCREEN_MAX_PEAKS)
        # 25 根高度递减，被砍掉的是最后 5 根（最矮）
        self.assertAlmostEqual(peaks.min(), centers[0], delta=0.01)
        self.assertAlmostEqual(peaks.max(),
                               centers[ps.SCREEN_MAX_PEAKS - 1], delta=0.01)


if __name__ == "__main__":
    unittest.main()
