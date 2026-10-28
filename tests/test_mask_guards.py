"""Mask 生效后的数据新鲜度栅栏（2026-10-28）。

应用 mask 只影响**之后**的积分；stage_cache 里的 1D/产物没有 mask 标记，
可能是遮罩前算的。这里钉住：
  * compare / heatmap 只认当前 `mask_epoch` 算出的 1D 面板；
  * 遮罩生效时 `_curve_for` 不读落盘产物；
  * `_curve_source` 优先用批量处理临时留的遮罩曲线；
  * 旧一代的曲线被拒绝并给人话原因。
"""
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from xrd_toolkit.gui.app import create_window  # noqa: E402
from xrd_toolkit.gui import plot_export as gui_export  # noqa: E402
from xrd_toolkit.gui import sources as gui_sources  # noqa: E402
from xrd_toolkit.gui.plot import curves as gui_curves  # noqa: E402
from xrd_toolkit.gui.plot import processing as gui_processing  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestMaskFreshnessGuards(unittest.TestCase):
    def setUp(self):
        self.w = create_window()
        self.path = Path("/tmp/mask_guard_x.tif")
        self.display = self.path.name
        self.w.current_mask = np.zeros((4, 4), bool)
        self.w.current_mask[0, 0] = True
        self.w.mask_epoch = 3
        self.w._masked_curves = {}

    def tearDown(self):
        # 假 dock 没有真实面板的 content；关窗前清掉，别让 closeEvent 去访问
        self.w.plot_docks.pop("1D|" + str(self.path), None)
        self.w.close()
        QApplication.processEvents()

    def _dock(self, epoch, mask_on=True):
        dock = SimpleNamespace(
            last_tth=np.arange(3.0), last_intensity=np.arange(3.0),
            mask_epoch=epoch, mask_on=mask_on)
        self.w.plot_docks["1D|" + str(self.path)] = dock
        return dock

    def test_ready_curve_rejects_previous_mask_generation(self):
        self._dock(2)
        self.assertIsNone(
            gui_curves._ready_curve(self.w, self.path, self.display))
        self._dock(3)
        got = gui_curves._ready_curve(self.w, self.path, self.display)
        self.assertIsNotNone(got)

    def test_ready_curve_rejects_masked_panel_after_clear(self):
        self.w.current_mask = None
        self._dock(3, mask_on=True)
        self.assertIsNone(
            gui_curves._ready_curve(self.w, self.path, self.display))
        self._dock(3, mask_on=False)
        self.assertIsNotNone(
            gui_curves._ready_curve(self.w, self.path, self.display))

    def test_ready_curve_uses_masked_session_curve(self):
        self.w._masked_curves[str(self.path)] = (
            np.arange(3.0), np.arange(3.0))
        got = gui_curves._ready_curve(self.w, self.path, self.display)
        self.assertIsNotNone(got)
        self.assertEqual(got[2], "遮罩后的 1D")

    def test_curve_for_skips_cached_products_when_mask_active(self):
        with mock.patch.object(gui_curves.stage_cache, "load_1d") as load1d, \
                mock.patch.object(gui_curves.stage_cache,
                                  "load_proc") as loadproc:
            self.assertIsNone(gui_curves._curve_for(self.w, self.path))
            load1d.assert_not_called()
            loadproc.assert_not_called()

    def test_export_skips_products_when_mask_active(self):
        src = gui_sources.make_source(
            self.path, kind=gui_sources.BG, key="prod-key")
        with mock.patch.object(gui_sources, "load_product") as load:
            out = gui_export._checked_1d_results(self.w, sources=[src])
            self.assertEqual(out, [])
            load.assert_not_called()

    def test_export_skips_stale_1d_panel_when_mask_active(self):
        self._dock(2)
        out = gui_export._checked_1d_results(
            self.w, sources=[gui_sources.make_source(self.path)])
        self.assertEqual(out, [])

    def test_mask_signature_is_stable_and_sensitive(self):
        a = np.zeros((3, 3), bool)
        b = a.copy()
        b[1, 1] = True
        self.assertEqual(gui_processing._mask_signature(a),
                         gui_processing._mask_signature(a.copy()))
        self.assertNotEqual(gui_processing._mask_signature(a),
                            gui_processing._mask_signature(b))

    def test_curve_source_requires_current_generation(self):
        kw = {"config": "c", "npt": 100, "tth_min": 1.0, "tth_max": 8.0}
        self._dock(2)
        tth, _inten, why = gui_curves._curve_source(self.w, self.path, kw)
        self.assertIsNone(tth)
        self.assertIn("Mask", why)
        self._dock(3)
        tth, _inten, why = gui_curves._curve_source(self.w, self.path, kw)
        self.assertIsNotNone(tth)
        self.assertIsNone(why)


if __name__ == "__main__":
    unittest.main()
