"""校准引擎的单元测试：snap_ring / refine_geometry_from_points /
calibrate_geometry 的 control_points / config_entry_template / 常用标样。

合成图像 + 真实几何常量（dist = 1.5958 m、pixel = 200 µm，与
lmfp1_lab6 配置一致）验证：
  - 判环吸附：环上点 → 正确环号；离所有环 ≥1° 的点 → None；
  - 手动精修：合成控制点确定性收敛（距离/残差回到画入值附近）；
    点数不足 / 只覆盖一环 / 环号越界 → ValueError；
  - calibrate_geometry 返回 control_points 键（GUI 画绿点验证精修用）；
  - CONFIGS 条目模板与 CLI 输出逐字一致的关键行。

运行：python -m unittest discover -s tests -v
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pyFAI.detectors import Detector
from pyFAI.goniometer import Geometry

from xrd_toolkit.config import config_entry_template
from xrd_toolkit.services.calibrants import (
    COMMON_CALIBRANTS, DEFAULT_CALIBRANT, calibrant_label,
    complete_ring_count, detect_calibrant_from_stem)
from xrd_toolkit.services.integrator import (
    calibrant_2theta, calibrate_geometry, lab6_theoretical_2theta,
    refine_geometry_from_points, snap_ring, theoretical_ring_paths)

PIXEL_M = 200e-6
WAVELENGTH_M = 0.1223e-10
DIST_M = 1.5958
PONI1_PX, PONI2_PX = 1045.2, 1022.0
ROT1_DEG, ROT2_DEG = -0.005, -0.163

THEO = calibrant_2theta(DEFAULT_CALIBRANT, WAVELENGTH_M, 16)


def _ring_point(ring, ang_deg=0.0, d2th=0.0):
    """环 ring 上一点的像素坐标 (x=列, y=行)：距 PONI r = dist·tan(2θ)/pixel
    处、方位角 ang_deg；d2th = 2θ 偏移（度，测吸附容差用）。

    注意行列：pyFAI 惯例 poni1↔行/y、poni2↔列/x，而绘图坐标是
    (x=列, y=行)——两者交叉，写反会让点整体偏离真实环 23 px。
    """
    r = DIST_M * np.tan(np.radians(THEO[ring] + d2th)) / PIXEL_M
    a = np.radians(ang_deg)
    return (PONI2_PX + r * np.cos(a), PONI1_PX + r * np.sin(a))


def _synthetic_rings(shape=(2048, 2048), cx=1024.0, cy=1024.0, n_rings=11):
    """画合成 LaB₆ 衍射环（2.4 px 宽亮环 + 背景 1 + 小噪声）。"""
    return _synthetic_rings_for(THEO[:n_rings], shape=shape, cx=cx, cy=cy,
                                dist_m=DIST_M)


def _synthetic_rings_for(theo, shape=(2048, 2048), cx=1024.0, cy=1024.0,
                         dist_m=DIST_M):
    """按任意标样的理论 2θ 画同心亮环（零倾斜几何，圆心 = cx,cy）。"""
    img = np.zeros(shape) + 1.0
    rng = np.random.default_rng(7)
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]]
    rr = np.hypot(cols - cx, rows - cy)
    for tth in theo:
        r = dist_m * np.tan(np.radians(tth)) / PIXEL_M
        img[np.abs(rr - r) < 1.2] = 300.0
    img += rng.normal(0, 2.0, shape)
    return img


class TestSnapLab6Ring(unittest.TestCase):
    """点击吸附：按当前几何算 2θ → 最近理论环（容差 0.5°）。"""

    def _snap(self, x, y):
        return snap_ring(
            x, y, pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
            dist_m=DIST_M, poni1_m=PONI1_PX * PIXEL_M,
            poni2_m=PONI2_PX * PIXEL_M, rot1_deg=0.0, rot2_deg=0.0)

    def test_on_ring_point_snaps_to_that_ring(self):
        """环上一点（零倾斜几何）→ 判出它所属的环。

        高角环间距窄（0.24°），判环几何若差半个环（行列写反 23 px
        ≈ 0.17°）就会滑到邻环——所以这组断言同时是行列约定的护栏。
        """
        for ring in (0, 3, 6, 9):
            x, y = _ring_point(ring, ang_deg=ring * 37)
            self.assertEqual(self._snap(x, y), ring)

    def test_point_far_from_any_ring_returns_none(self):
        """低于首环 1°（> 容差 0.5°、下面无邻环可吸）→ None。"""
        x, y = _ring_point(0, d2th=-1.0)
        self.assertIsNone(self._snap(x, y))


class TestSnapLab6RingOffDiagonal(unittest.TestCase):
    """判环的行列约定护栏：偏置摆法（束心远离图像对角线）下才不会互相
    掩盖——本数据的束心几乎在对角线上，行列写反时对角线镜像不改变到
    束心的距离，错误被掩盖（见 snap_ring 的备注）。"""

    def _snap(self, x, y, poni1_px, poni2_px):
        return snap_ring(
            x, y, pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
            dist_m=DIST_M, poni1_m=poni1_px * PIXEL_M,
            poni2_m=poni2_px * PIXEL_M)

    def test_off_diagonal_center_snaps_correctly(self):
        for poni1_px, poni2_px in ((200.0, 1800.0), (1800.0, 200.0)):
            for ring, ang in ((0, 0.0), (6, 37.0), (9, 250.0)):
                r = DIST_M * np.tan(np.radians(THEO[ring])) / PIXEL_M
                a = np.radians(ang)
                # (x=列, y=行)：列用 poni2、行用 poni1
                x = poni2_px + r * np.cos(a)
                y = poni1_px + r * np.sin(a)
                self.assertEqual(self._snap(x, y, poni1_px, poni2_px), ring,
                                 f"PONI=({poni1_px},{poni2_px}) 环{ring}")


class TestTheoreticalRingPaths(unittest.TestCase):
    """理论环路径：精确反解（不是"圆心 + 半径"的正圆近似）。"""

    def _paths(self, **kw):
        base = dict(pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
                    dist_m=DIST_M, poni1_px=PONI1_PX, poni2_px=PONI2_PX,
                    image_shape=(2048, 2048))
        base.update(kw)
        return theoretical_ring_paths(**base)

    def test_zero_tilt_is_circle_centered_at_poni(self):
        """零倾斜：路径 = 圆心 PONI、半径 dist·tan(2θ)/pixel 的正圆。

        这条同时是坐标约定的护栏：poni/getFit2D 是"索引 + 0.5"
        （见 PIXEL_CENTER_OFFSET），漏换算或换算两次都会让整条路径
        偏 0.707 px——够让判环在高角环上滑到邻环（环间距 0.24°）。
        """
        p = self._paths()
        # 零倾斜时直射束落点 B = PONI − 0.5（索引坐标）
        b_row, b_col = p["beam_center_rc"]
        self.assertAlmostEqual(b_row, PONI1_PX - 0.5, places=6)
        self.assertAlmostEqual(b_col, PONI2_PX - 0.5, places=6)
        for k, xy in p["rings"]:
            r_nom = DIST_M * np.tan(np.radians(THEO[k])) / PIXEL_M
            r = np.hypot(xy[:, 0] - b_col, xy[:, 1] - b_row)
            self.assertLess(float(np.abs(r - r_nom).max()), 1e-3)
        self.assertEqual(p["n_inside"], 16)
        self.assertAlmostEqual(p["r_min_px"],
                               DIST_M * np.tan(np.radians(THEO[0])) / PIXEL_M,
                               places=2)

    def test_tilted_path_is_ellipse_at_beam_center_not_poni(self):
        """有倾斜：圆心 = 直射束落点（getFit2D − 0.5），不是 PONI；且
        半径随方位角起伏（真椭圆）——正圆近似画不出这个，会整体偏
        21~23 px（本数据实测）。"""
        rot1, rot2 = -0.005, -0.163
        p = self._paths(rot1_deg=rot1, rot2_deg=rot2)
        geo = Geometry(dist=DIST_M, poni1=PONI1_PX * PIXEL_M,
                       poni2=PONI2_PX * PIXEL_M,
                       rot1=np.radians(rot1), rot2=np.radians(rot2),
                       detector=Detector(pixel1=PIXEL_M, pixel2=PIXEL_M),
                       wavelength=WAVELENGTH_M)
        fit = geo.getFit2D()
        b_row, b_col = p["beam_center_rc"]
        self.assertAlmostEqual(b_row, fit["centerY"] - 0.5, places=6)
        self.assertAlmostEqual(b_col, fit["centerX"] - 0.5, places=6)
        # 与 PONI 拉开 ~23 px（正是画正圆会错掉的量）
        self.assertGreater(np.hypot(b_col - PONI2_PX, b_row - PONI1_PX), 20.0)
        # 每条路径点都落在自己的理论上（1e-4° 以内）
        for k, xy in p["rings"]:
            t = np.degrees(geo.tth(xy[:, 1], xy[:, 0]))
            self.assertLess(float(np.abs(t - THEO[k]).max()), 1e-4)
        # 真椭圆：半径随方位角有起伏（正圆近似画不出这一项）。本数据
        # 倾角小，起伏只有 0.7 px；倾角每增大 1° 约放大 5 倍。
        xy = p["rings"][15][1]
        r = np.hypot(xy[:, 0] - b_col, xy[:, 1] - b_row)
        self.assertGreater(float(r.max() - r.min()), 0.3)
        self.assertLess(float(r.max() - r.min()), 3.0)

    def test_matches_matplotlib_contour(self):
        """独立实现交叉验证：与 matplotlib 等值线（marching squares）
        给出的同一条环重合到 0.05 px 以内。"""
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.figure import Figure

        rot1, rot2 = -0.005, -0.163
        p = self._paths(rot1_deg=rot1, rot2_deg=rot2)
        geo = Geometry(dist=DIST_M, poni1=PONI1_PX * PIXEL_M,
                       poni2=PONI2_PX * PIXEL_M,
                       rot1=np.radians(rot1), rot2=np.radians(rot2),
                       detector=Detector(pixel1=PIXEL_M, pixel2=PIXEL_M),
                       wavelength=WAVELENGTH_M)
        step = 8.0
        xs = np.arange(0.0, 2048.0, step)
        X, Y = np.meshgrid(xs, xs)
        tth_map = geo.tth(Y, X)          # 行, 列
        level = np.radians(THEO[10])
        fig = Figure()
        ax = fig.add_subplot(111)
        cs = ax.contour(X, Y, tth_map, levels=[level])
        vertices = np.vstack(cs.allsegs[0])   # 该 2θ 的全部等值线段
        b_row, b_col = p["beam_center_rc"]
        phi_c = np.arctan2(vertices[:, 1] - b_row, vertices[:, 0] - b_col)
        r_c = np.hypot(vertices[:, 0] - b_col, vertices[:, 1] - b_row)
        xy = p["rings"][10][1]
        phi_p = np.arctan2(xy[:, 1] - b_row, xy[:, 0] - b_col)
        r_p = np.hypot(xy[:, 0] - b_col, xy[:, 1] - b_row)
        order = np.argsort(phi_c)
        r_interp = np.interp(phi_p, phi_c[order], r_c[order])
        self.assertLess(float(np.abs(r_p - r_interp).max()), 0.05)

    def test_rings_off_image_are_counted(self):
        """守卫数据：把距离按"米"填（1000 倍，即 1595.8 m）→ 16 条环
        全在图像外；反过来小探测器（256²、距离 0.4 m）→ 内环在里面、
        外环被截断。"""
        p = self._paths(dist_m=1595.8)
        self.assertEqual(p["n_inside"], 0)
        self.assertGreater(p["r_min_px"], 1e5)
        p_small = self._paths(image_shape=(256, 256), dist_m=0.4,
                              poni1_px=128.5, poni2_px=128.5)
        self.assertGreater(p_small["n_inside"], 0)
        self.assertLess(p_small["n_inside"], 16)

    def test_unreachable_ring_yields_nan_not_fake_points(self):
        """退化几何（大倾角下外侧环被拉成开口锥面）时输出 NaN 而不是
        硬凑点——画出来就是一条假线，比不画更糟。60° 起外侧环反解
        不出，80° 全部反解不出（实测）。"""
        p60 = self._paths(rot2_deg=-60.0)
        self.assertTrue(any(np.isnan(xy[:, 0]).any()
                            for _, xy in p60["rings"]))
        p80 = self._paths(rot2_deg=-80.0)
        self.assertTrue(all(np.isnan(xy[:, 0]).any()
                            for _, xy in p80["rings"]))


class TestRefineLab6FromPoints(unittest.TestCase):
    """手动选点精修：合成控制点 → 确定性收敛；坏输入如实报错。"""

    def test_synthetic_points_converge(self):
        """真值几何（零倾斜）：环 2/4/6 各 2 点，初值距离/束心故意偏
        （差 4 mm / ~10 px）→ 精修回到画入值附近，残差接近 0。"""
        points, rings = [], []
        for ri, ang in ((2, 0), (2, 90), (4, 30), (4, 150), (6, 70), (6, 240)):
            points.append(_ring_point(ri, ang))
            rings.append(ri)
        res = refine_geometry_from_points(
            points, rings, pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
            dist0_m=1.6, center0_px=(1035.0, 1032.0))
        self.assertAlmostEqual(res["dist_m"] * 1000, 1595.8, delta=0.2)
        self.assertLess(res["residual_deg"], 1e-3)
        # 与 calibrate_geometry 同形：键齐全（GUI 共用同一结果区）
        for key in ("dist_m", "poni1_px", "poni2_px", "offset_px",
                    "rot1_deg", "rot2_deg", "rot3_deg", "residual_deg"):
            self.assertIn(key, res)

    def test_too_few_points_raises(self):
        with self.assertRaises(ValueError):
            refine_geometry_from_points(
                [_ring_point(2), _ring_point(4)], [2, 4],
                pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
                dist0_m=1.6, center0_px=(1035.0, 1032.0))

    def test_single_ring_raises(self):
        """点数够但只覆盖一个环 → ValueError（欠定，环号要 ≥2）。"""
        with self.assertRaises(ValueError):
            refine_geometry_from_points(
                [_ring_point(2, a) for a in (0, 90, 180)], [2, 2, 2],
                pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
                dist0_m=1.6, center0_px=(1035.0, 1032.0))

    def test_ring_index_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            refine_geometry_from_points(
                [_ring_point(2), _ring_point(4), _ring_point(6)],
                [2, 4, 16],   # 环号 0~15，16 越界
                pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
                dist0_m=1.6, center0_px=(1035.0, 1032.0))


class TestCalibrateLab6ControlPoints(unittest.TestCase):
    """calibrate_geometry 返回 control_points（GUI 画绿点验证精修用）。"""

    def test_result_contains_control_points(self):
        img = _synthetic_rings()
        res = calibrate_geometry(img, pixel_size_m=PIXEL_M,
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

    def test_empty_control_points_get_a_readable_error(self):
        """一个峰都匹不上时报"看得懂"的错，不是 pyFAI 的形状术语（2026-10-03）。

        用户报："校准失败（自动定位）：RuntimeError: data is expected to be
        of shape (nb control-points, [3|4])"——空控制点（一维 (0,)）喂给
        GeometryRefinement 时 pyFAI 的原话，看不懂也不知道怎么办。
        """
        with self.assertRaisesRegex(RuntimeError,
                                    "找不到与 LaB₆ 理论环匹配的峰"):
            calibrate_geometry(np.zeros((512, 512)), pixel_size_m=PIXEL_M,
                           wavelength_m=WAVELENGTH_M, dist0_m=DIST_M,
                           center0_px=(256.0, 256.0))


class TestCommonCalibrants(unittest.TestCase):
    """常用标样：理论阶梯、完整环数、文件名预选、未知名称的可读报错。"""

    def test_ceo2_ladder_matches_pyfai(self):
        from pyFAI.calibrant import get_calibrant
        cal = get_calibrant("CeO2")
        cal.wavelength = WAVELENGTH_M
        expected = np.degrees(np.asarray(
            [t for t in cal.get_2th() if t is not None])[:10])
        got = calibrant_2theta("CeO2", WAVELENGTH_M, 10)
        np.testing.assert_allclose(got, expected, atol=1e-12)

    def test_complete_ring_count_reproduces_lab6_16(self):
        """LaB₆ 当前几何仍是 16 环；CeO₂ 按同一规则是 10 环。"""
        kw = dict(wavelength_m=WAVELENGTH_M, pixel_size_m=PIXEL_M,
                  dist_m=DIST_M, beam_center_rc=(1022.0, 1022.3),
                  image_shape=(2048, 2048))
        self.assertEqual(complete_ring_count("LaB6", **kw), 16)
        self.assertEqual(complete_ring_count("CeO2", **kw), 10)

    def test_ceo2_synthetic_calibration(self):
        """合成 CeO₂ 环 → calibrate_geometry 应回到画入的距离/圆心。"""
        theo = calibrant_2theta("CeO2", WAVELENGTH_M, 10)
        img = _synthetic_rings_for(theo, dist_m=1.5)
        res = calibrate_geometry(
            img, pixel_size_m=PIXEL_M, wavelength_m=WAVELENGTH_M,
            dist0_m=1.5, center0_px=(1024.0, 1024.0), max_rings=10,
            calibrant="CeO2")
        self.assertEqual(res["calibrant"], "CeO2")
        self.assertEqual(res["n_rings"], 10)
        self.assertAlmostEqual(res["dist_m"], 1.5, delta=0.01)
        self.assertAlmostEqual(res["poni1_px"], 1024.0, delta=3.0)
        self.assertAlmostEqual(res["poni2_px"], 1024.0, delta=3.0)

    def test_small_calibrant_ring_count_is_clamped(self):
        """Ni 只有 5 条反射：max_rings=16 也不能越界（报可读的"没匹配"）。"""
        with self.assertRaisesRegex(RuntimeError, "Ni"):
            calibrate_geometry(
                np.zeros((512, 512)), pixel_size_m=PIXEL_M,
                wavelength_m=WAVELENGTH_M, dist0_m=DIST_M,
                center0_px=(256.0, 256.0), max_rings=16, calibrant="Ni")

    def test_common_aliases_resolve(self):
        """小写别名（lab6/ceo2/si…）与精确名称给同一条阶梯。"""
        np.testing.assert_allclose(
            calibrant_2theta("ceo2", WAVELENGTH_M, 10),
            calibrant_2theta("CeO2", WAVELENGTH_M, 10))
        np.testing.assert_allclose(
            calibrant_2theta("lab6", WAVELENGTH_M, 16),
            calibrant_2theta("LaB6", WAVELENGTH_M, 16))

    def test_duplicate_lab6_660c_is_not_listed(self):
        """LaB6 默认项就是 SRM 660c：下拉框不再重复列 660c（2026-10-24）。"""
        names = [name for name, _label, _aliases in COMMON_CALIBRANTS]
        self.assertNotIn("LaB6_SRM660c", names)
        # 旧名称/小写别名仍可用，且与默认项是同一条阶梯
        np.testing.assert_allclose(
            calibrant_2theta("LaB6_SRM660c", WAVELENGTH_M, 16),
            calibrant_2theta("LaB6", WAVELENGTH_M, 16))
        np.testing.assert_allclose(
            calibrant_2theta("lab6_srm660c", WAVELENGTH_M, 16),
            calibrant_2theta("LaB6", WAVELENGTH_M, 16))
        self.assertEqual(detect_calibrant_from_stem("lab6_srm660c-1"), "LaB6")
        self.assertIn("660c", calibrant_label("LaB6_SRM660c"))

    def test_unknown_calibrant_has_readable_error(self):
        with self.assertRaisesRegex(ValueError, "未知标样"):
            calibrant_2theta("NoSuchStandard", WAVELENGTH_M, 5)

    def test_old_lab6_names_still_work(self):
        """兼容包装：旧脚本 import 的名字仍给同一组数字。"""
        np.testing.assert_allclose(
            lab6_theoretical_2theta(WAVELENGTH_M, 4),
            calibrant_2theta(DEFAULT_CALIBRANT, WAVELENGTH_M, 4))

    def test_filename_prefill(self):
        self.assertEqual(detect_calibrant_from_stem("lab6-00024"), "LaB6")
        self.assertEqual(detect_calibrant_from_stem("CeO2_0001"), "CeO2")
        self.assertEqual(detect_calibrant_from_stem("Si_SRM640a-2"),
                         "Si_SRM640a")
        self.assertIsNone(detect_calibrant_from_stem("series-1"))


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
        self.assertIn('"calibrant": "LaB6",', text)   # 默认标样

    def test_calibrant_is_written(self):
        geometry = {"pixel_size_m": PIXEL_M, "wavelength_m": WAVELENGTH_M,
                    "dist_m": 1.5, "poni1_px": 1.0, "poni2_px": 2.0,
                    "rot1_deg": 0.0, "rot2_deg": 0.0}
        text = config_entry_template(geometry=geometry,
                                     beam_center_rc=(3.0, 4.0),
                                     calibrant="CeO2")
        self.assertIn('"calibrant": "CeO2",', text)

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
