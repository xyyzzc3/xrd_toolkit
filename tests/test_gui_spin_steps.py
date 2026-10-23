"""数字框步长守卫（2026-10-24）。

用户报寻峰滚轮/箭头"很无效"：QDoubleSpinBox 默认 singleStep = 1.0，
对 0.008° 这种量程等于废掉。顺手清点全界面：校准几何、坞顶 2θ/点数、
对比/热图视图 2θ 也都有同类问题。这里把该细的步长钉住。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
from PySide6.QtWidgets import QDoubleSpinBox  # noqa: E402

from xrd_toolkit.gui import calib_form as gui_calib_form  # noqa: E402
from xrd_toolkit.gui.app import create_window  # noqa: E402
from xrd_toolkit.services import stage_cache  # noqa: E402

_app = QApplication.instance() or QApplication([])


def setUpModule():
    stage_cache.CACHE_ROOT = Path(tempfile.mkdtemp(prefix="xrd_steps_test_"))


class TestSpinSteps(unittest.TestCase):
    def test_data_row_and_view_ranges(self):
        w = create_window()
        try:
            self.assertAlmostEqual(
                w.params["2θ 下限 (°)"].singleStep(), 0.1)
            self.assertAlmostEqual(
                w.params["2θ 上限 (°)"].singleStep(), 0.1)
            self.assertEqual(w.params["输出点数"].singleStep(), 100)
            for key in ("视图 2θ 下限 (°)", "视图 2θ 上限 (°)",
                        "热图视图 2θ 下限 (°)", "热图视图 2θ 上限 (°)"):
                self.assertAlmostEqual(w.params[key].singleStep(), 0.1,
                                       msg=key)
        finally:
            w.close()

    def test_peak_controls(self):
        w = create_window()
        try:
            self.assertAlmostEqual(
                w.params["寻峰显著度"].singleStep(), 0.1)
            for key in ("寻峰最小峰距 (°)", "寻峰最小 FWHM (°)"):
                self.assertAlmostEqual(w.params[key].singleStep(), 0.001,
                                       msg=key)
            self.assertEqual(w.params["寻峰最多根数"].singleStep(), 1)
        finally:
            w.close()

    def test_calibration_geometry_steps(self):
        self.assertEqual(gui_calib_form.CALIB_FIELD_STEPS["wavelength_a"],
                         0.001)
        self.assertEqual(gui_calib_form.CALIB_FIELD_STEPS["rot1_deg"], 0.01)
        self.assertEqual(gui_calib_form.CALIB_FIELD_STEPS["poni1_px"], 0.5)
        self.assertEqual(gui_calib_form.CALIB_BEAM_STEP, 0.5)

    def test_edit_dialog_applies_calibration_steps(self):
        w = create_window()
        try:
            geom = dict(w.config["geometry"])
            geom["beam_center_rc"] = w.config.get("beam_center")
            w.calib_state = {"current_geom": geom}
            captured = {}

            def fake_exec(dlg):
                for name in ("calib_wavelength_a_spin",
                             "calib_rot1_deg_spin",
                             "calib_poni1_px_spin",
                             "beam_row_spin", "beam_col_spin"):
                    box = dlg.findChild(QDoubleSpinBox, name)
                    captured[name] = (None if box is None
                                      else box.singleStep())
                return QDialog.Rejected

            with mock.patch.object(QDialog, "exec", new=fake_exec):
                gui_calib_form._edit_current(w)
            self.assertAlmostEqual(captured["calib_wavelength_a_spin"],
                                   0.001)
            self.assertAlmostEqual(captured["calib_rot1_deg_spin"], 0.01)
            self.assertAlmostEqual(captured["calib_poni1_px_spin"], 0.5)
            self.assertAlmostEqual(captured["beam_row_spin"], 0.5)
            self.assertAlmostEqual(captured["beam_col_spin"], 0.5)
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
