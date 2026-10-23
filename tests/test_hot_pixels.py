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

from xrd_toolkit.services.hot_pixels import mask_hot_pixels  # noqa: E402


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

    def test_full_resolution_matches_downsampled(self):
        img = _synthetic()
        _c1, m1, _ = mask_hot_pixels(img, downsample=1)
        _c2, m2, _ = mask_hot_pixels(img, downsample=2)
        self.assertTrue(m1[150, 50])
        self.assertTrue(m2[150, 50])


if __name__ == "__main__":
    unittest.main()
