"""积分性能相关的行为护栏：integrator 复用 + 算法回退链。

这两条是"批量从 2.3 分钟降到十几秒"的全部原因（见
services/integrator.py 文件头），所以必须有测试钉住——否则以后有人
"顺手"改成每次新建 integrator，性能会静默回到从前。
"""
import unittest
from unittest import mock

import numpy as np

from xrd_toolkit.services import integrator as it

GEOM = dict(pixel_size_m=200e-6, wavelength_m=0.1223e-10, dist_m=1.5958,
            poni1_m=1022.0 * 200e-6, poni2_m=1045.2 * 200e-6,
            rot1_deg=-0.005, rot2_deg=-0.163)
# 真几何的束心在 1022 px 处（真实探测器是 2048²）；测试用的 64×64 小图
# 上它远在图像外 → 引擎会（正确地）走自研积分。要测 pyFAI 路径就得用
# 一个束心落在小图中心附近的几何。
GEOM_SMALL = dict(GEOM, poni1_m=32 * 200e-6, poni2_m=32 * 200e-6)


class TestIntegratorReuse(unittest.TestCase):
    """同一几何的 integrator 只建一次（pyFAI 首调用要算映射表，最贵）。"""

    def setUp(self):
        # 每个测试从空缓存开始（threading.local）
        it._integrator_cache.items = {}
        it._backend["name"] = None

    def test_same_geometry_reuses_one_integrator(self):
        key = it._integrator_key(GEOM["pixel_size_m"], GEOM["wavelength_m"],
                                 GEOM["dist_m"], GEOM["poni1_m"],
                                 GEOM["poni2_m"], GEOM["rot1_deg"],
                                 GEOM["rot2_deg"])
        a = it._integrator_for(key, **GEOM)
        b = it._integrator_for(key, **GEOM)
        self.assertIs(a["ai"], b["ai"], "同一几何必须复用同一个 integrator")

    def test_different_geometry_gets_a_new_one(self):
        k1 = it._integrator_key(**GEOM)
        k2 = it._integrator_key(**dict(GEOM, dist_m=GEOM["dist_m"] + 0.01))
        self.assertNotEqual(k1, k2)
        self.assertIsNot(it._integrator_for(k1, **GEOM)["ai"],
                         it._integrator_for(
                             k2, **dict(GEOM, dist_m=GEOM["dist_m"] + 0.01))["ai"])

    def test_key_ignores_float_rounding_noise(self):
        """几何键按有效位取整：末位噪声不该导致缓存不命中。"""
        k1 = it._integrator_key(**GEOM)
        k2 = it._integrator_key(**dict(GEOM, dist_m=GEOM["dist_m"] + 1e-15))
        self.assertEqual(k1, k2)


class TestMethodFallback(unittest.TestCase):
    """算法回退链：cython 最快；它不可用时回退 numpy，且**只回退一次**。"""

    def setUp(self):
        it._integrator_cache.items = {}
        it._backend["name"] = None

    def test_uses_cython_when_available(self):
        img = np.ones((64, 64), dtype=np.float32) * 10
        it.integrate_1d(img, npt=64, tth_min_deg=1.0, tth_max_deg=3.0,
                        **GEOM_SMALL)
        self.assertIn(it.integration_backend(), ("cython", "numpy"))
        # 本机装了 cython 引擎 → 应该是它
        try:
            import pyFAI  # noqa: F401
            self.assertEqual(it.integration_backend(), "cython")
        except ImportError:
            pass

    def test_falls_back_to_numpy_and_remembers_it(self):
        """cython 抛错 → 回退 numpy；之后同几何不再重试 cython。"""
        img = np.ones((64, 64), dtype=np.float32) * 10
        key = it._integrator_key(**GEOM_SMALL)
        entry = it._integrator_for(key, **GEOM_SMALL)
        calls = []

        class FakeAI:
            def integrate1d(self, image, npt, **kw):
                calls.append(kw.get("method"))
                if kw.get("method") == "cython":
                    raise RuntimeError("cython engine not available")
                return (np.linspace(1.0, 3.0, npt), np.ones(npt) * 5.0)

        entry["ai"] = FakeAI()
        entry["method"] = "cython"
        with mock.patch.object(it, "_integrator_for", return_value=entry):
            it.integrate_1d(img, npt=64, tth_min_deg=1.0, tth_max_deg=3.0,
                            **GEOM_SMALL)
            it.integrate_1d(img, npt=64, tth_min_deg=1.0, tth_max_deg=3.0,
                            **GEOM_SMALL)
        self.assertEqual(calls, ["cython", "numpy", "numpy"],
                         "第一次 cython 失败→numpy；之后记住 numpy 不再试")
        self.assertEqual(it.integration_backend(), "numpy")

    def test_lut_is_not_in_the_fallback_chain(self):
        """lut/csr 比 numpy 快但**数值不等价**（实测中位差 0.3%）——
        换算法不许悄悄改数据，所以它们不在链里。"""
        self.assertEqual(it._INTEGRATOR_METHODS, ("cython", "numpy"))

    def test_off_center_uses_diy_and_reports_it(self):
        img = np.ones((64, 64), dtype=np.float32)
        # 束心放到 400 px 处（64×64 小图的中心在 32 px）→ 偏移 ≫ 阈值
        off = dict(GEOM_SMALL, poni1_m=400 * GEOM["pixel_size_m"],
                   poni2_m=400 * GEOM["pixel_size_m"])
        it.integrate_1d(img, npt=64, **off)
        self.assertIn("自研", it.integration_backend())


if __name__ == "__main__":
    unittest.main()
