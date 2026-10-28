"""Mask 后的 2D 产物（manifest：原图 + mask）测试（2026-10-28）。

钉住：
  * manifest 建/复用/列出/删除；不改写原始像素；
  * `load_diffraction_image(manifest)` 返回 MaskedArray；
  * 积分器自动排除 masked 像素（与显式传 mask 等价）；
  * GUI 双击 Mask 2D 条目 → 2D 面板；右键/显式 → 1D 面板。
"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import fabio  # noqa: E402
import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from xrd_toolkit.gui import sources as gui_sources  # noqa: E402
from xrd_toolkit.gui.app import create_window  # noqa: E402
from xrd_toolkit.gui.files.actions import _open_entry_view  # noqa: E402
from xrd_toolkit.gui import plot_export as gui_export  # noqa: E402
from xrd_toolkit.gui.plot import curves as gui_curves  # noqa: E402
from xrd_toolkit.gui.files.menus import _drop_masked2d  # noqa: E402
from xrd_toolkit.services import masked2d, stage_cache  # noqa: E402
from xrd_toolkit.services.data_loader import load_diffraction_image  # noqa: E402
from xrd_toolkit.services.integrator import integrate_1d  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _image(seed=0):
    rng = np.random.default_rng(seed)
    img = (100.0 + rng.normal(0, 3.0, (200, 200))).astype(np.float32)
    img[150, 50] = 1.0e6
    return img


def _write(path, img):
    fabio.edfimage.EdfImage(data=img).write(str(path))


def _geom():
    return dict(pixel_size_m=0.0002, wavelength_m=1.0e-10, dist_m=1.5,
                poni1_m=0.0, poni2_m=0.0, rot1_deg=0.0, rot2_deg=0.0)


class TestManifest(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old = stage_cache.CACHE_ROOT
        stage_cache.point_root(Path(self._td.name) / "cache")
        self.img = _image()
        self.raw = Path(self._td.name) / "x.edf"
        _write(self.raw, self.img)
        self.mask = np.zeros(self.img.shape, bool)
        self.mask[150, 50] = True

    def tearDown(self):
        stage_cache.point_root(self._old)
        self._td.cleanup()

    def test_create_dedupe_list_delete(self):
        m1 = masked2d.create(self.raw, self.mask, label="a")
        m2 = masked2d.create(self.raw, self.mask, label="a")
        self.assertEqual(m1["path"], m2["path"])
        self.assertEqual(len(masked2d.list_products()), 1)
        self.assertEqual(masked2d.meta_of(m1["path"])["masked_pixels"], 1)
        self.assertTrue(masked2d.delete(m1["path"]))
        self.assertEqual(masked2d.list_products(), [])

    def test_identical_manifest_is_not_rewritten(self):
        m1 = masked2d.create(self.raw, self.mask, label="a")
        stamp = Path(m1["path"]).stat().st_mtime_ns
        m2 = masked2d.create(self.raw, self.mask, label="a")
        self.assertEqual(m1["path"], m2["path"])
        self.assertEqual(stamp, Path(m2["path"]).stat().st_mtime_ns)

    def test_label_update_does_not_rewrite_manifest(self):
        m1 = masked2d.create(self.raw, self.mask, label="a")
        stamp = Path(m1["path"]).stat().st_mtime_ns
        m2 = masked2d.create(self.raw, self.mask, label="saved.npy")
        self.assertEqual(m2["path"], m1["path"])
        self.assertEqual(m2["label"], "saved.npy")
        self.assertEqual(stamp, Path(m2["path"]).stat().st_mtime_ns)
        self.assertEqual(masked2d.list_products()[0]["label"], "saved.npy")
        self.assertEqual(masked2d.meta_of(m2["path"])["label"], "saved.npy")

    def test_different_mask_creates_new_product(self):
        m1 = masked2d.create(self.raw, self.mask)
        self.mask[10, 10] = True
        m2 = masked2d.create(self.raw, self.mask)
        self.assertNotEqual(m1["path"], m2["path"])
        self.assertEqual(len(masked2d.list_products()), 2)

    def test_load_returns_masked_array_and_keeps_pixels(self):
        meta = masked2d.create(self.raw, self.mask)
        got = load_diffraction_image(meta["path"])
        self.assertTrue(np.ma.isMaskedArray(got))
        np.testing.assert_array_equal(np.ma.getmaskarray(got), self.mask)
        np.testing.assert_array_equal(np.ma.getdata(got), self.img)

    def test_drop_masked2d_removes_related_1d_cache(self):
        meta = masked2d.create(self.raw, self.mask)
        t = np.arange(10.0)
        y = np.arange(10.0)
        stage_cache.store_1d(meta["path"], t, y, config="c", npt=10,
                             tth_min=1.0, tth_max=8.0)
        self.assertTrue(stage_cache.has_1d(meta["path"], config="c", npt=10,
                                           tth_min=1.0, tth_max=8.0))
        n_masks, n_related = _drop_masked2d(None, [meta["path"]])
        self.assertEqual((n_masks, n_related), (1, 1))
        self.assertEqual(masked2d.list_products(), [])
        self.assertFalse(stage_cache.has_1d(meta["path"], config="c", npt=10,
                                            tth_min=1.0, tth_max=8.0))

    def test_export_npz_roundtrip(self):
        meta = masked2d.create(self.raw, self.mask, label="a")
        out = Path(self._td.name) / "masked2d_export"
        target, shape, npx = masked2d.export_npz(meta["path"], out)
        self.assertEqual(target.suffix, ".npz")
        self.assertEqual(shape, self.mask.shape)
        self.assertEqual(npx, int(self.mask.sum()))
        z = np.load(target, allow_pickle=False)
        np.testing.assert_array_equal(z["image"], self.img)
        np.testing.assert_array_equal(z["mask"], self.mask)
        self.assertIn("raw_path", str(z["meta"].item()))

    def test_integration_matches_explicit_mask(self):
        meta = masked2d.create(self.raw, self.mask)
        got = load_diffraction_image(meta["path"])
        t1, y1 = integrate_1d(got, npt=80, **_geom())
        t2, y2 = integrate_1d(self.img.astype(np.float64), npt=80,
                              mask=self.mask, **_geom())
        np.testing.assert_array_equal(t1, t2)
        np.testing.assert_allclose(y1, y2, rtol=0, atol=0)


class TestMasked2DGui(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self._old = stage_cache.CACHE_ROOT
        stage_cache.point_root(Path(self._td.name) / "cache")
        self.raw = Path(self._td.name) / "frame.edf"
        _write(self.raw, _image())
        self.w = create_window()
        self.w.show()
        self.w.add_files([str(self.raw)], select=True)
        QApplication.processEvents()
        self.w.mask_btn.click()
        QApplication.processEvents()
        ed = self.w.mask_editor
        ed.load_image(self.raw)
        ed._fill_rect((10, 10), (30, 30), True)
        ed._apply_to_main()
        QApplication.processEvents()
        self.src = [s for s in gui_sources.all_sources(self.w)
                    if s.kind == gui_sources.MASKED2D][0]

    def tearDown(self):
        self.w.mask_editor.close()
        self.w.hide()          # 关窗确认只在可见时弹；测试先藏起来
        self.w.close()
        QApplication.processEvents()
        stage_cache.point_root(self._old)
        self._td.cleanup()

    def _wait(self, pred, timeout=120.0):
        end = time.time() + timeout
        while time.time() < end:
            QApplication.processEvents()
            if pred():
                return True
            time.sleep(0.02)
        return False

    def test_context_menu_offers_npz_export(self):
        from PySide6.QtWidgets import QMenu
        from xrd_toolkit.gui.files import menus
        menu = QMenu(self.w)
        actions = menus._entry_menu_actions(menu, self.w, self.src.item)
        texts = [a.text() for a in actions]
        self.assertTrue(any("导出 Mask 2D 数据" in t for t in texts))

    def test_double_click_opens_masked_2d(self):
        _open_entry_view(self.w, self.src.item)      # 双击 = 2D
        key = "2D|" + str(self.src.path)
        ok = self._wait(lambda: key in self.w.plot_docks
                        and getattr(self.w.plot_docks[key], "last_image",
                                    None) is not None)
        self.assertTrue(ok)
        img = self.w.plot_docks[key].last_image
        self.assertTrue(np.ma.isMaskedArray(img))
        self.assertGreater(int(np.ma.getmaskarray(img).sum()), 0)

    def test_explicit_opens_1d(self):
        _open_entry_view(self.w, self.src.item, explicit=True)
        key = "1D|" + str(self.src.path)
        ok = self._wait(lambda: key in self.w.plot_docks
                        and getattr(self.w.plot_docks[key], "last_tth",
                                    None) is not None)
        self.assertTrue(ok)
        QApplication.processEvents()
        oned = [s for s in gui_sources.all_sources(self.w)
                if s.kind == gui_sources.ONED]
        self.assertTrue(oned)          # 1D 产物挂回 Mask 2D 条目
        kw = dict(config=self.w.config_name,
                  npt=int(self.w.params["输出点数"].value()),
                  tth_min=1.0, tth_max=8.0)
        self.assertIsNotNone(gui_curves._curve_for(self.w, self.src.path))
        tth, _y, why = gui_curves._curve_source(
            self.w, Path(self.src.path), kw)
        self.assertIsNotNone(tth)
        self.assertIsNone(why)
        exp = gui_export._checked_1d_results(self.w, sources=[self.src])
        self.assertTrue(exp)
        self.assertIn("px", exp[0][6])      # 文件头 mask 溯源
        out = Path(self._td.name) / "out.txt"
        gui_export._write_export(out, exp[0][1], exp[0][2], exp[0][3],
                                 category=exp[0][4], config=exp[0][5],
                                 mask=exp[0][6])
        self.assertIn("# mask:", out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
