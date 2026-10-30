"""精修工作台的窗口级测试（2026-10-30）。

覆盖三件事：
  1. [精修] 的进出：收起参数/日志坞、按钮两副面孔、退出恢复；
  2. 三种工作台互斥：精修 ↔ Mask ↔ 校准，点分析入口也退出精修；
  3. 真拟合链路：合成 LaB₆ 曲线 → [开始精修] → 结果表/报告/Rwp。
"""
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT), str(ROOT / "tests")]

from PySide6.QtWidgets import QApplication  # noqa: E402
from unittest import mock  # noqa: E402

try:
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403
except ModuleNotFoundError:  # 直接运行本文件
    from tests import _gui_integration_support as _support
    from tests._gui_integration_support import *  # noqa: F401,F403

from xrd_toolkit.simulation import b_iso_from_cif, load_structure  # noqa: E402
from xrd_toolkit.simulation.gsas_profile import (  # noqa: E402
    simulate_pattern_gsas)

LAB6_CIF = ROOT / "data" / "structures" / "LaB6_NIST660c.cif"
AL_CIF = ROOT / "data" / "structures" / "Al_metal.cif"


def setUpModule():
    _support.setUpModule()


def _synth(path, geom, npt):
    """用仓库自带的 LaB₆ CIF 合成一条 1D 曲线（λ 跟随当前几何）。"""
    structure = load_structure(str(LAB6_CIF))
    b_iso = b_iso_from_cif(str(LAB6_CIF)) or None
    tth, y, _peaks, _hex = simulate_pattern_gsas(
        structure, wavelength=float(geom["wavelength_m"]) * 1e10,
        two_theta_range=(1.0, 8.0), step_deg=0.005, b_iso=b_iso,
        normalize=False)
    return tth, y + 50.0


class TestRefineWorkbench(unittest.TestCase):
    def setUp(self):
        self._old_root = _support.stage_cache.CACHE_ROOT
        self.w = create_window()
        self.w.show()
        QApplication.processEvents()

    def tearDown(self):
        self.w.hide()
        self.w.close()
        QApplication.processEvents()
        _support.stage_cache.point_root(self._old_root)

    def _enter(self):
        self.w.refine_btn.click()
        QApplication.processEvents()
        self.assertTrue(self.w._refine_active, "没进入精修模式")

    def _open_panel(self):
        path = "data/fake_refine.tif"
        with mock.patch.object(gui_plot_spawn, "_compute_integration",
                               side_effect=_synth):
            add_checked(self.w, [path])
            _open_view(self.w, "1D")
            ok = _wait_until(
                lambda: f"1D|{path}" in self.w.plot_docks
                and getattr(self.w.plot_docks[f"1D|{path}"], "last_tth", None)
                is not None, timeout_ms=20000)
        self.assertTrue(ok, "1D 图没算完")
        return self.w.plot_docks[f"1D|{path}"]

    def test_enter_exit_restores_docks(self):
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertTrue(self.w.log_dock.isVisible())

        self._enter()
        self.assertEqual(self.w.refine_btn.text(), "退出精修")
        self.assertEqual(self.w.mode_label.text(), "精修模式")
        self.assertTrue(self.w.refine_dock.isVisible())
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertFalse(self.w.log_dock.isVisible())

        self.w.refine_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._refine_active)
        self.assertFalse(self.w.refine_dock.isVisible())
        self.assertEqual(self.w.refine_btn.text(), "精修")
        self.assertEqual(self.w.mode_label.text(), "分析模式")
        self.assertFalse(self.w.param_dock.isVisible())
        self.assertTrue(self.w.log_dock.isVisible())

    def test_mutual_exclusion_with_mask_and_calibration(self):
        self._enter()
        self.w.mask_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._refine_active, "Mask 应把精修退掉")
        self.assertTrue(self.w._mask_active)
        self.assertFalse(self.w.refine_dock.isVisible())
        self.w.mask_btn.click()
        QApplication.processEvents()

        self._enter()
        self.w.calib_btn.click()
        QApplication.processEvents()
        self.assertFalse(self.w._refine_active, "校准应把精修退掉")
        self.assertTrue(self.w.calib_btn.isChecked())
        self.w.calib_btn.click()
        QApplication.processEvents()

        self._enter()
        self.w.entrance_buttons["1D"].click()
        QApplication.processEvents()
        self.assertFalse(self.w._refine_active, "点分析入口应退出精修")
        self.assertTrue(self.w.entrance_buttons["1D"].isChecked())

    def test_cif_list_has_reference_structures(self):
        self._enter()
        combo = self.w.refine_editor.cif_combo
        self.assertGreater(combo.count(), 5)
        labels = [combo.itemText(i) for i in range(combo.count())]
        self.assertIn("LaB6_NIST660c", labels)

    def test_fit_runs_and_fills_result(self):
        self._open_panel()
        self._enter()
        editor = self.w.refine_editor
        idx = editor.cif_combo.findData(str(LAB6_CIF))
        self.assertGreaterEqual(idx, 0, "LaB₆ 结构不在下拉框里")
        editor.cif_combo.setCurrentIndex(idx)
        self.assertTrue(editor.curve_combo.count() >= 1)

        editor.run_btn.click()
        ok = _wait_until(lambda: editor.result is not None, timeout_ms=30000)
        self.assertTrue(ok, "精修没有在 30 秒内完成")

        self.assertLess(editor.result.rwp, 15.0)
        self.assertGreater(editor.param_table.rowCount(), 0)
        self.assertIn("Rwp", editor.report_view.toPlainText())
        self.assertIn("Rwp", editor.rwp_label.text())
        self.assertTrue(editor.save_btn.isEnabled())
        self.assertTrue(editor.copy_btn.isEnabled())
        self.assertGreaterEqual(len(editor.fig.axes), 2)

    def test_adopt_creates_normal_panel(self):
        self._open_panel()
        self._enter()
        editor = self.w.refine_editor
        idx = editor.cif_combo.findData(str(LAB6_CIF))
        editor.cif_combo.setCurrentIndex(idx)
        editor.run_btn.click()
        ok = _wait_until(lambda: editor.result is not None
                         and editor._task is None, timeout_ms=30000)
        self.assertTrue(ok, "精修没有完成")
        editor.adopt_btn.click()
        QApplication.processEvents()
        keys = [k for k in self.w.plot_docks
                if str(k).startswith("1D|fit:")]
        self.assertEqual(len(keys), 1, "应恰好开出一张精修图面板")
        dock = self.w.plot_docks[keys[0]]
        self.assertIn("精修", dock.windowTitle())
        self.assertIsNotNone(getattr(dock, "fit_overlay", None))
        ax = _support.gui_panel_state._content(dock).axes_1d
        labels = [line.get_label() for line in ax.lines]
        self.assertIn("观测", labels)
        self.assertIn("计算", labels)

    def test_extra_phase_scale_and_bragg_row(self):
        self._open_panel()
        self._enter()
        editor = self.w.refine_editor
        idx = editor.cif_combo.findData(str(LAB6_CIF))
        editor.cif_combo.setCurrentIndex(idx)
        self.assertTrue(editor.add_extra_phase(AL_CIF))
        self.assertFalse(editor.add_extra_phase(AL_CIF), "重复加入应被忽略")
        editor.run_btn.click()
        ok = _wait_until(lambda: editor.result is not None
                         and editor._task is None, timeout_ms=30000)
        self.assertTrue(ok, "带额外相的精修没有完成")
        self.assertIn("Al:scale", editor.result.params)
        self.assertIn("Al", editor.result.extra_peak_tth or {})

    def test_instrument_baseline_store_and_fix(self):
        self._open_panel()
        self._enter()
        editor = self.w.refine_editor
        idx = editor.cif_combo.findData(str(LAB6_CIF))
        editor.cif_combo.setCurrentIndex(idx)
        editor.run_btn.click()
        ok = _wait_until(lambda: editor.result is not None
                         and editor._task is None, timeout_ms=30000)
        self.assertTrue(ok, "第一轮精修没有完成")
        self.assertTrue(editor.save_instrument_btn.isEnabled())

        editor.save_instrument_btn.click()
        QApplication.processEvents()
        self.assertIsNotNone(editor._instrument)
        self.assertTrue(editor.chk_fix_instrument.isEnabled())
        self.assertTrue(editor.chk_fix_instrument.isChecked())
        baseline = dict(editor._instrument["params"])
        self.assertFalse(editor.chk_instr.isEnabled(),
                         "固定仪器后不再单独精修仪器峰形")

        editor.run_btn.click()
        ok = _wait_until(lambda: editor.run_btn.isEnabled()
                         and editor._task is None, timeout_ms=30000)
        self.assertTrue(ok, "第二轮精修没有完成")
        for name, value in baseline.items():
            self.assertAlmostEqual(editor.result.initial[name], value,
                                   places=6, msg=name)


if __name__ == "__main__":
    unittest.main()
