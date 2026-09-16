"""取点拟合圆心（fit_center_from_rings）的单元测试（unittest，环境无 pytest）。

合成图像 + 真实几何常量（dist = 1.5958 m、pixel = 200 µm，与
lmfp1_lab6 配置一致）验证：
  - 完整环/半环/四分之一环/束心在图像外四种摆法下圆心都能正确
    反推（FFT 法 find_ring_center 只对完整环可靠，本方法无此限制）；
  - 环半径与画入值一致、残差是精度标尺；
  - 噪声容忍；远离真值的初值也能收敛；
  - 无环/单环时如实返回 None（调用方兜底）。

运行：python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.core.processor import fit_center_from_rings

SHAPE = (2048, 2048)
PIXEL_M = 200e-6
DIST_M = 1.5958
TTHS = (3.0, 5.0, 8.0)
R_PX = [DIST_M * np.tan(np.radians(t)) / PIXEL_M for t in TTHS]


def _synthetic_rings(x0, y0, tths=TTHS, shape=SHAPE, value=100.0):
    """画 1 px 宽的合成衍射环（无噪声、无背景，只在图像内画）。"""
    img = np.zeros(shape)
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    for tth in tths:
        r = DIST_M * np.tan(np.radians(tth)) / PIXEL_M
        rr = np.hypot(cols - x0, rows - y0)
        img[np.abs(rr - r) < 0.5] = value
    return img


class TestCenterRecovery(unittest.TestCase):
    """圆心反推：四种摆法（完整环/半环/四分之一环/束心在图像外）。"""

    def test_centered_full_rings(self):
        img = _synthetic_rings(1024.0, 1024.0)
        fit = fit_center_from_rings(img)
        self.assertIsNotNone(fit)
        self.assertLess(np.hypot(fit["cx"] - 1024.0, fit["cy"] - 1024.0), 1.0)
        self.assertLess(fit["residual_px"], 1.0)
        self.assertGreater(fit["n_points"], 200)

    def test_ring_radii_match_painted_values(self):
        # 环半径是共同圆心拟合的副产物，应与画入半径一致（升序）
        img = _synthetic_rings(1024.0, 1024.0)
        fit = fit_center_from_rings(img)
        self.assertEqual(len(fit["ring_radii"]), len(R_PX))
        for got, want in zip(fit["ring_radii"], R_PX):
            self.assertLess(abs(got - want), 2.0)

    def test_half_ring_image(self):
        # 只保留左半幅图像（右半清零）：半圆弧。FFT 法在此失效，
        # 取点拟合法应照常反推圆心
        img = _synthetic_rings(1024.0, 1024.0)
        img[:, 1024:] = 0
        fit = fit_center_from_rings(img)
        self.assertIsNotNone(fit)
        self.assertLess(np.hypot(fit["cx"] - 1024.0, fit["cy"] - 1024.0), 3.0)

    def test_corner_beam_quarter_arcs(self):
        # 束心在图像角 (0, 0)：只剩第一象限的四分之一圆弧
        img = _synthetic_rings(0.0, 0.0)
        fit = fit_center_from_rings(img)
        self.assertIsNotNone(fit)
        self.assertLess(np.hypot(fit["cx"] - 0.0, fit["cy"] - 0.0), 3.0)

    def test_beam_outside_image(self):
        # 束心在图像左侧外 200 px：只有右侧弧段可见（浅弧、半径大），
        # 圆心仍可从弧的曲率反推
        img = _synthetic_rings(-200.0, 1024.0)
        fit = fit_center_from_rings(img)
        self.assertIsNotNone(fit)
        self.assertLess(np.hypot(fit["cx"] + 200.0, fit["cy"] - 1024.0), 4.0)


class TestRobustness(unittest.TestCase):
    """噪声容忍、初值不敏感、失败如实报告。"""

    def test_noise_tolerance(self):
        rng = np.random.default_rng(42)
        img = _synthetic_rings(1024.0, 1024.0) + rng.normal(0.0, 5.0, SHAPE)
        fit = fit_center_from_rings(img)
        self.assertIsNotNone(fit)
        self.assertLess(np.hypot(fit["cx"] - 1024.0, fit["cy"] - 1024.0), 2.0)

    def test_wrong_seed_converges(self):
        # 初值远离真值 512 px：第一轮寻峰不受影响（完整环任何方向
        # 剖面都穿环），拟合跳到真值附近，第二轮收敛
        img = _synthetic_rings(1024.0, 1024.0)
        fit = fit_center_from_rings(img, center0=(512.0, 512.0))
        self.assertIsNotNone(fit)
        self.assertLess(np.hypot(fit["cx"] - 1024.0, fit["cy"] - 1024.0), 2.0)

    def test_flat_image_returns_none(self):
        # 没有任何环：取不到点，如实返回 None（调用方用 FFT 兜底）
        self.assertIsNone(fit_center_from_rings(np.zeros(SHAPE)))

    def test_single_ring_returns_none(self):
        # 只有一个环时最小二乘欠定（圆心可在环内任意处配平残差），
        # 拒绝返回结果而不是给出假答案
        img = _synthetic_rings(1024.0, 1024.0, tths=(5.0,))
        self.assertIsNone(fit_center_from_rings(img))


if __name__ == "__main__":
    unittest.main()
