"""坏点团掩膜测试（2026-10-23）。

合成一张"背景 + 细环 + 孤立亮团 + 大面积亮区"的图，确认：
  * 孤立亮团被替换成局部中值；
  * 真实的细环不被误伤；
  * 面积大的亮区（比如饱和的强环/真实斑点）不被当成坏点；
  * 峰值没到门槛的小亮团不动。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.services.hot_pixels import (  # noqa: E402
    mask_hot_pixels, mask_suspicion)


def _synthetic():
    yy, xx = np.indices((200, 200))
    r = np.hypot(yy - 100.0, xx - 100.0)
    img = 100.0 + 500.0 * np.exp(-0.5 * ((r - 60.0) / 1.5) ** 2)
    img[146:154, 46:54] = 50000.0        # 孤立亮团（8×8）
    img[10:50, 130:170] = 5000.0         # 大面积亮区（40×40，真实强环/斑点）
    img[170:172, 30:32] = 150.0          # 小亮点，峰值不够
    return img


class TestHotPixelMask(unittest.TestCase):
    def test_blob_masked_ring_and_big_patch_kept(self):
        img = _synthetic()
        clean, mask, info = mask_hot_pixels(img)
        self.assertTrue(mask[150, 50])          # 亮团中心
        self.assertGreater(info["masked_pixels"], 0)
        self.assertLess(clean[150, 50], 1000.0)  # 替换成局部中值
        # 细环（r=60）不被误伤
        self.assertFalse(mask[100, 160])
        self.assertAlmostEqual(clean[100, 160], img[100, 160])
        # 大面积亮区不被当成坏点
        self.assertFalse(mask[30, 150])
        self.assertAlmostEqual(clean[30, 150], img[30, 150])
        # 峰值不够的小亮团不动
        self.assertFalse(mask[171, 31])

    def test_grow_extends_into_halo_only(self):
        img = _synthetic()
        # 给亮团加一圈光晕（1000 counts：高于背景、低于芯）
        img[143:157, 43:57] = 1000.0
        img[146:154, 46:54] = 50000.0
        _c0, core, _ = mask_hot_pixels(img)
        _c3, grown, info = mask_hot_pixels(img, grow_px=3)
        self.assertGreater(int(grown.sum()), int(core.sum()))
        self.assertEqual(info["grow_px"], 3)
        self.assertTrue(grown[150, 50])          # 芯还在
        self.assertTrue(grown[144, 44])          # 光晕被扩到
        self.assertFalse(grown[10, 10])          # 远处背景不动
        self.assertFalse(grown[30, 150])         # 大面积亮区仍不碰

    def test_full_resolution_matches_downsampled(self):
        img = _synthetic()
        _c1, m1, _ = mask_hot_pixels(img, downsample=1)
        _c2, m2, _ = mask_hot_pixels(img, downsample=2)
        self.assertTrue(m1[150, 50])
        self.assertTrue(m2[150, 50])


class TestMaskSuspicion(unittest.TestCase):
    """结果护栏：斑点状衍射（标样）不该被静默当成坏点。"""

    def test_lmfp_result_is_fine(self):
        ok, why = mask_suspicion(
            {"masked_components": 24, "masked_pixels": 2824}, (2048, 2048))
        self.assertFalse(ok)
        self.assertEqual(why, "")

    def test_lab6_result_is_flagged(self):
        ok, why = mask_suspicion(
            {"masked_components": 2219, "masked_pixels": 217390},
            (2048, 2048))
        self.assertTrue(ok)
        self.assertIn("2219", why)
        self.assertIn("LaB₆", why)
        self.assertIn("斑点", why)

    def test_component_count_alone_flags(self):
        ok, _why = mask_suspicion(
            {"masked_components": 201, "masked_pixels": 10}, (2048, 2048))
        self.assertTrue(ok)

    def test_pixel_fraction_alone_flags(self):
        # 2048² 的 0.5% = 20971.5 px
        ok, _why = mask_suspicion(
            {"masked_components": 10, "masked_pixels": 20972}, (2048, 2048))
        self.assertTrue(ok)

    def test_boundaries_are_inclusive(self):
        ok, _why = mask_suspicion(
            {"masked_components": 200, "masked_pixels": 20971}, (2048, 2048))
        self.assertFalse(ok)

    def test_missing_info_is_fine(self):
        ok, why = mask_suspicion({}, (2048, 2048))
        self.assertFalse(ok)
        self.assertEqual(why, "")


if __name__ == "__main__":
    unittest.main()
