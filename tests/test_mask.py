"""2D 掩膜测试（2026-10-27）。

钉住三件事：
  * `.npy` / `.edf` 读写往返；
  * 自动坏点只抓孤立亮团（径向 nσ 默认不启用，避免误伤真实环）；
  * mask 在 1D/扇区积分里真正生效（被遮像素不参与平均）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.services.integrator import (  # noqa: E402
    _integrate_1d_diy,
    integrate_1d,
    integrate_sectors,
)
from xrd_toolkit.services.mask import (  # noqa: E402
    auto_mask,
    combine_masks,
    load_mask,
    mask_stats,
    radial_outlier_mask,
    save_mask,
)


def _synthetic(seed=0):
    """背景 + 一圈细环 + 一个孤立热像素 + 扫描噪声。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.indices((200, 200))
    r = np.hypot(yy - 100.0, xx - 100.0)
    img = 100.0 + 400.0 * np.exp(-0.5 * ((r - 60.0) / 1.5) ** 2)
    img += rng.normal(0.0, 3.0, img.shape)
    img = np.maximum(img, 0.0)
    img[150, 50] = 1.0e6          # r ≈ 70.7，孤立热像素
    return img


class TestMaskIO(unittest.TestCase):
    def test_npy_and_edf_roundtrip(self):
        mask = np.zeros((8, 10), dtype=bool)
        mask[2, 3] = True
        mask[5, 7] = True
        with tempfile.TemporaryDirectory() as td:
            for suffix in (".npy", ".edf"):
                path = Path(td) / f"mask{suffix}"
                save_mask(path, mask)
                back = load_mask(path)
                self.assertEqual(back.shape, mask.shape)
                self.assertEqual(back.dtype, np.bool_)
                np.testing.assert_array_equal(back, mask)

    def test_rejects_bad_shape_and_suffix(self):
        with self.assertRaises(ValueError):
            save_mask("/tmp/mask.txt", np.zeros((4, 4), bool))
        with self.assertRaises(ValueError):
            combine_masks(np.zeros((4, 4), bool), np.zeros((5, 4), bool))

    def test_load_rejects_non_mask_image(self):
        """选错 .tif 不能把整张图当 mask（2026-10-28）。"""
        import fabio
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "image.tif"
            fabio.tifimage.TifImage(
                data=(np.ones((6, 6)) * 5).astype(np.float32)).write(str(path))
            with self.assertRaises(ValueError):
                load_mask(path)

    def test_combine_is_union(self):
        a = np.zeros((4, 4), bool)
        b = np.zeros((4, 4), bool)
        a[0, 0] = True
        b[1, 1] = True
        out = combine_masks(a, b)
        self.assertTrue(out[0, 0] and out[1, 1])
        self.assertEqual(int(out.sum()), 2)


class TestAutoMask(unittest.TestCase):
    def test_radial_flags_hot_pixel_but_not_ring(self):
        img = _synthetic()
        m = radial_outlier_mask(img, (100.0, 100.0),
                                n_sigma=5.0, bin_px=4.0)
        self.assertTrue(m[150, 50])
        # 环上取几个点：不应被当成坏点（带噪声时 5σ 足够保守）
        self.assertFalse(m[100, 160])
        self.assertFalse(m[100, 40])
        self.assertLess(int(m.sum()), int(m.size * 0.01))

    def test_auto_mask_default_is_local_only(self):
        img = _synthetic()
        out = auto_mask(img, (100.0, 100.0))
        self.assertTrue(out["mask"][150, 50])
        self.assertEqual(int(out["radial"].sum()), 0)  # 径向默认关
        self.assertTrue(int(out["hot"].sum()) > 0)

    def test_auto_mask_can_include_radial(self):
        img = _synthetic()
        out = auto_mask(img, (100.0, 100.0), include_radial=True)
        self.assertTrue(out["mask"][150, 50])
        self.assertTrue(int(out["radial"].sum()) > 0)

    def test_mask_stats(self):
        m = np.zeros((10, 10), bool)
        m[:2, :] = True
        stats = mask_stats(m)
        self.assertEqual(stats["masked_pixels"], 20)
        self.assertEqual(stats["total_pixels"], 100)
        self.assertAlmostEqual(stats["fraction"], 0.2)


class TestMaskedIntegration(unittest.TestCase):
    def test_diy_1d_excludes_masked_pixel(self):
        img = _synthetic()
        clean = img.copy()
        clean[150, 50] = 100.0
        mask = np.zeros(img.shape, bool)
        mask[150, 50] = True
        t, y_hot = _integrate_1d_diy(
            img, 0.0002, 1.5, 0.02, 0.02, 80, mask=mask)
        t2, y_clean = _integrate_1d_diy(
            clean, 0.0002, 1.5, 0.02, 0.02, 80)
        _t, y_no = _integrate_1d_diy(img, 0.0002, 1.5, 0.02, 0.02, 80)
        np.testing.assert_array_equal(t, t2)
        # 掩膜后与"把热像素改成背景"几乎一致；不掩则差几个数量级
        self.assertLess(float(np.nanmax(np.abs(y_hot - y_clean))), 0.5)
        self.assertGreater(float(np.nanmax(np.abs(y_no - y_clean))), 10.0)

    def test_public_1d_excludes_masked_pixel(self):
        img = _synthetic()
        clean = img.copy()
        clean[150, 50] = 100.0
        mask = np.zeros(img.shape, bool)
        mask[150, 50] = True
        # 束心放到 (0,0)，偏离中心 >100 px → 走自研路径，结果确定
        t, y_hot = integrate_1d(
            img, 0.0002, 1.0e-10, 1.5, 0.0, 0.0, 0.0, 0.0,
            npt=80, mask=mask)
        t2, y_clean = integrate_1d(
            clean, 0.0002, 1.0e-10, 1.5, 0.0, 0.0, 0.0, 0.0, npt=80)
        _t, y_no = integrate_1d(
            img, 0.0002, 1.0e-10, 1.5, 0.0, 0.0, 0.0, 0.0, npt=80)
        self.assertLess(float(np.nanmax(np.abs(y_hot - y_clean))), 0.5)
        self.assertGreater(float(np.nanmax(np.abs(y_no - y_clean))), 10.0)

    def test_sectors_exclude_masked_pixel(self):
        img = _synthetic()
        clean = img.copy()
        clean[150, 50] = 100.0
        mask = np.zeros(img.shape, bool)
        mask[150, 50] = True
        t, i2d_hot, _chi = integrate_sectors(
            img, 0.0002, 1.0e-10, 1.5, 0.0, 0.0, 0.0, 0.0,
            n_sectors=12, npt=80, mask=mask)
        t2, i2d_clean, _chi2 = integrate_sectors(
            clean, 0.0002, 1.0e-10, 1.5, 0.0, 0.0, 0.0, 0.0,
            n_sectors=12, npt=80)
        _t, i2d_no, _chi3 = integrate_sectors(
            img, 0.0002, 1.0e-10, 1.5, 0.0, 0.0, 0.0, 0.0,
            n_sectors=12, npt=80)
        self.assertLess(
            float(np.nanmax(np.abs(i2d_hot - i2d_clean))), 0.5)
        self.assertGreater(
            float(np.nanmax(np.abs(i2d_no - i2d_clean))), 10.0)


if __name__ == "__main__":
    unittest.main()
