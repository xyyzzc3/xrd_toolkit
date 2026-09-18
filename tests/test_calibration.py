"""校准引擎的单元测试：snap_lab6_ring / refine_lab6_from_points /
calibrate_lab6 的 control_points / config_entry_template。

合成图像 + 真实几何常量（dist = 1.5958 m、pixel = 200 µm，与
lmfp1_lab6 配置一致）验证：
  - 判环吸附：环上点 → 正确环号；离所有环 ≥1° 的点 → None；
  - 手动精修：合成控制点确定性收敛（距离/残差回到画入值附近）；
    点数不足 / 只覆盖一环 / 环号越界 → ValueError；
  - calibrate_lab6 返回 control_points 键（GUI 画绿点验证精修用）；
  - CONFIGS 条目模板与 CLI 输出逐字一致的关键行。

运行：python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit.config import config_entry_template
from xrd_toolkit.services.integrator import (
    calibrate_lab6, lab6_theoretical_2theta, refine_lab6_from_points,
    snap_lab6_ring)

PIXEL_M = 200e-6
WAVELENGTH_M = 0.1223e-10
DIST_M = 1.5958
PONI1_PX, PONI2_PX = 1045.2, 1022.0
ROT1_DEG, ROT2_DEG = -0.005, -0.163

THEO = lab6_theoretical_2theta(WAVELENGTH_M, 16)


def _ring_point(ring, ang_deg=0.0, d2th=0.0):
    """环 ring 上一点的像素坐标：距 PONI r = dist·tan(2θ)/pixel 处、
    方位角 ang_deg；d2th = 2θ 偏移（度，测吸附容差用）。"""
    r = DIST_M * np.tan(np.radians(THEO[ring] + d2th)) / PIXEL_M
    a = np.radians(ang_deg)
    return (PONI1_PX + r * np.cos(a), PONI2_PX + r * np.sin(a))


def _synthetic_rings(shape=(2048, 2048), cx=1024.0, cy=1024.0, n_rings=11):
    """画合成 LaB₆ 衍射环（2.4 px 宽亮环 + 背景 1 + 小噪声）。"""
    img = np.zeros(shape) + 1.0
    rng = np.random.default_rng(7)
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    rr = np.hypot(cols - cx, rows - cy)
    for tth in THEO[:n_rings]:
        r = DIST_M * np.tan(np.radians(tth)) / PIXEL_M
        img[np.abs(rr - r) < 1.2] = 300.0
    img += rng.normal(0, 2.0, shape)
    return img


class TestSnapLab6Ring(unittest.TestCase):
    """点击吸附：按当前几何算 2θ → 最近理论环（容差 0.5°）。"""

    def _snap(self, x, y):
        return snap_lab6_ring(
            x, y, pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
            dist_m=DIST_M, poni1_m=PONI1_PX * PIXEL_M,
            poni2_m=PONI2_PX * PIXEL_M, rot1_deg=0.0, rot2_deg=0.0)

    def test_on_ring_point_snaps_to_that_ring(self):
        """环上一点（零倾斜几何）→ 判出它所属的环。

        判环几何带倾斜时环上点会整体偏移（高角环间距窄，可能滑到
        邻环），倾斜下的容差行为由 GUI 集成测试覆盖。
        """
        for ring in (0, 3, 6, 9):
            x, y = _ring_point(ring, ang_deg=ring * 37)
            self.assertEqual(self._snap(x, y), ring)

    def test_point_far_from_any_ring_returns_none(self):
        """低于首环 1°（> 容差 0.5°、下面无邻环可吸）→ None。"""
        x, y = _ring_point(0, d2th=-1.0)
        self.assertIsNone(self._snap(x, y))


class TestRefineLab6FromPoints(unittest.TestCase):
    """手动选点精修：合成控制点 → 确定性收敛；坏输入如实报错。"""

    def test_synthetic_points_converge(self):
        """真值几何（零倾斜）：环 2/4/6 各 2 点，初值距离/束心故意偏
        （差 4 mm / ~10 px）→ 精修回到画入值附近，残差接近 0。"""
        points, rings = [], []
        for ri, ang in ((2, 0), (2, 90), (4, 30), (4, 150), (6, 70), (6, 240)):
            points.append(_ring_point(ri, ang))
            rings.append(ri)
        res = refine_lab6_from_points(
            points, rings, pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
            dist0_m=1.6, center0_px=(1035.0, 1032.0))
        self.assertAlmostEqual(res["dist_m"] * 1000, 1595.8, delta=0.2)
        self.assertLess(res["residual_deg"], 1e-3)
        # 与 calibrate_lab6 同形：键齐全（GUI 共用同一结果区）
        for key in ("dist_m", "poni1_px", "poni2_px", "offset_px",
                    "rot1_deg", "rot2_deg", "rot3_deg", "residual_deg"):
            self.assertIn(key, res)

    def test_too_few_points_raises(self):
        with self.assertRaises(ValueError):
            refine_lab6_from_points(
                [_ring_point(2), _ring_point(4)], [2, 4],
                pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
                dist0_m=1.6, center0_px=(1035.0, 1032.0))

    def test_single_ring_raises(self):
        """点数够但只覆盖一个环 → ValueError（欠定，环号要 ≥2）。"""
        with self.assertRaises(ValueError):
            refine_lab6_from_points(
                [_ring_point(2, a) for a in (0, 90, 180)], [2, 2, 2],
                pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
                dist0_m=1.6, center0_px=(1035.0, 1032.0))

    def test_ring_index_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            refine_lab6_from_points(
                [_ring_point(2), _ring_point(4), _ring_point(6)],
                [2, 4, 16],   # 环号 0~15，16 越界
                pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
                dist0_m=1.6, center0_px=(1035.0, 1032.0))


class TestCalibrateLab6ControlPoints(unittest.TestCase):
    """calibrate_lab6 返回 control_points（GUI 画绿点验证精修用）。"""

    def test_result_contains_control_points(self):
        img = _synthetic_rings()
        res = calibrate_lab6(img, pixel_size_m=PIXEL_M,
                             wavelength_m=WAVELENGTH_M, dist0_m=DIST_M,
                             center0_px=(1024.0, 1024.0))
        cps = res["control_points"]
        self.assertIsInstance(cps, list)
        self.assertGreater(len(cps), 100)
        x, y, ring = cps[0]   # [x, y, 环号] 三元组（像素坐标 + 0 起环号）
        self.assertIsInstance(ring, int)
        self.assertTrue(0 <= ring < 16)
        # 合成环画在真值几何上：精修应回到画入值附近（引擎自检）
        self.assertAlmostEqual(res["dist_m"] * 1000, 1595.8, delta=1.0)
        self.assertLess(res["residual_deg"], 0.02)


class TestConfigEntryTemplate(unittest.TestCase):
    """CONFIGS 条目模板：与 CLI 输出逐字一致的关键行。"""

    def _template(self, **overrides):
        geometry = {
            "pixel_size_m": PIXEL_M, "wavelength_m": WAVELENGTH_M,
            "dist_m": 1.59580, "poni1_px": 1045.2, "poni2_px": 1022.0,
            "rot1_deg": -0.005, "rot2_deg": -0.163,
            "residual_deg": 0.0037,
        }
        geometry.update(overrides)
        return config_entry_template(geometry=geometry,
                                     beam_center_rc=(1022.0, 1022.3))

    def test_template_key_lines(self):
        text = self._template()
        self.assertIn("pixel_size_m=200e-6,", text)
        self.assertIn("wavelength_m=0.1223e-10,", text)
        self.assertIn("dist_m=1.59580,", text)
        self.assertIn("poni1_m=1045.200 * 200e-6,", text)
        self.assertIn("poni2_m=1022.000 * 200e-6,", text)
        self.assertIn("rot1_deg=-0.0050,", text)
        self.assertIn("rot2_deg=-0.1630,", text)
        self.assertIn('"beam_center": (1022.00, 1022.30),', text)
        self.assertIn("# refined residual: 0.0037 deg", text)
        self.assertIn('"lmfp2_lab6"', text)   # 默认 key 提示（同 CLI）

    def test_custom_key_and_label(self):
        text = config_entry_template(
            key_hint="test1_lab6", label_hint="测试批次",
            geometry={"pixel_size_m": PIXEL_M, "wavelength_m": WAVELENGTH_M,
                      "dist_m": 1.5, "poni1_px": 1.0, "poni2_px": 2.0,
                      "rot1_deg": 0.0, "rot2_deg": 0.0},
            beam_center_rc=(3.0, 4.0))
        self.assertIn('"test1_lab6"', text)
        self.assertIn('"label": "测试批次"', text)
        self.assertIn('"beam_center": (3.00, 4.00),', text)


if __name__ == "__main__":
    unittest.main()
