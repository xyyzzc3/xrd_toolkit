"""分阶段产物缓存（services/stage_cache.py）的单元测试——纯逻辑、零 Qt。

数据用 numpy 造的合成曲线，不碰真数据；缓存根指到临时目录（模块级
setUpModule），跑完就删——绝不写用户 outputs/_stage 里的真产物。
"""
import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from xrd_toolkit.services import stage_cache


def setUpModule():
    """整个模块把缓存根指到临时目录（隔离不能依赖真目录内容）。"""
    stage_cache.CACHE_ROOT = Path(tempfile.mkdtemp(prefix="xrd_cache_test_"))


class TestKey(unittest.TestCase):
    """缓存键：变一个因素就换一把键（否则旧产物会被当成新的复用）。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="xrd_cache_src_"))
        self.f = self.dir / "sample.tif"
        self.f.write_bytes(b"0" * 1024)
        self.base = dict(config="lmfp1_lab6", npt=3000, tth_min=1.0,
                         tth_max=8.0)

    def _key(self, **over):
        return stage_cache.cache_key(self.f, **{**self.base, **over})

    def test_same_inputs_same_key(self):
        self.assertEqual(self._key(), self._key())

    def test_each_part_changes_the_key(self):
        base = self._key()
        for over in ({"config": "lab6_only"}, {"npt": 2000},
                     {"tth_min": 1.5}, {"tth_max": 7.5}):
            self.assertNotEqual(base, self._key(**over), over)

    def test_file_change_changes_the_key(self):
        before = self._key()
        self.f.write_bytes(b"1" * 2048)      # 大小变了
        self.assertNotEqual(before, self._key())

    def test_engine_version_changes_the_key(self):
        """积分实现升版本 → 键变 → 旧的产物自然作废（这是防"拿旧算法
        结果冒充新结果"的那道闸）。"""
        before = self._key()
        orig = stage_cache.INTEGRATION_VERSION
        try:
            stage_cache.INTEGRATION_VERSION = orig + 1
            self.assertNotEqual(before, self._key())
        finally:
            stage_cache.INTEGRATION_VERSION = orig

    def test_same_size_and_mtime_but_different_names(self):
        """实测踩过的坑：同批拷进来的两个文件 (size, mtime) 可能一模一样
        → 只靠这两项键会撞，第二张覆盖第一张、下次静默拿错数据。"""
        a = self.dir / "a.tif"
        b = self.dir / "b.tif"
        a.write_bytes(b"x" * 4096)
        b.write_bytes(b"x" * 4096)
        os.utime(b, ns=(a.stat().st_atime_ns, a.stat().st_mtime_ns))
        self.assertEqual(stage_cache.fingerprint(a)[1:3],
                         stage_cache.fingerprint(b)[1:3],
                         "前提：两个文件 size/mtime 相同")
        self.assertNotEqual(stage_cache.cache_key(a, **self.base),
                            stage_cache.cache_key(b, **self.base),
                            "名字与内容哈希必须把它们区分开")

    def test_missing_file_raises(self):
        with self.assertRaises(OSError):
            stage_cache.cache_key(self.dir / "nope.tif", **self.base)


class TestRoundTrip(unittest.TestCase):
    """存/取、坏产物清理、清空。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="xrd_cache_src_"))
        self.f = self.dir / "sample.tif"
        self.f.write_bytes(b"0" * 1024)
        self.kw = dict(config="lmfp1_lab6", npt=1000, tth_min=1.0, tth_max=8.0)
        self.tth = np.linspace(1.0, 8.0, 1000)
        self.inten = np.sin(self.tth) * 100 + 500

    def test_store_then_load(self):
        self.assertIsNone(stage_cache.load_1d(self.f, **self.kw),
                          "还没存过 = 没缓存")
        path = stage_cache.store_1d(self.f, self.tth, self.inten, **self.kw)
        self.assertTrue(path.exists())
        got = stage_cache.load_1d(self.f, **self.kw)
        self.assertIsNotNone(got)
        self.assertTrue(np.array_equal(got[0], self.tth))
        self.assertTrue(np.array_equal(got[1], self.inten))

    def test_other_keys_do_not_hit(self):
        stage_cache.store_1d(self.f, self.tth, self.inten, **self.kw)
        other = {**self.kw, "npt": 2000}
        self.assertIsNone(stage_cache.load_1d(self.f, **other))

    def test_corrupt_product_is_dropped_not_kept(self):
        """坏产物（半截文件）读时删掉、返回 None——留着它每次都在读这步
        失败，重建的代价只是一次积分。"""
        path = stage_cache.store_1d(self.f, self.tth, self.inten, **self.kw)
        path.write_bytes(b"not an npz")
        self.assertIsNone(stage_cache.load_1d(self.f, **self.kw))
        self.assertFalse(path.exists(), "坏产物该被删掉")

    def test_mismatched_shapes_refused(self):
        with self.assertRaises(ValueError):
            stage_cache.store_1d(self.f, self.tth, self.inten[:-1], **self.kw)

    def test_describe_and_clear(self):
        stage_cache.store_1d(self.f, self.tth, self.inten, **self.kw)
        info = stage_cache.describe()
        self.assertGreaterEqual(info["files"], 1)
        self.assertGreater(info["bytes"], 0)
        removed = stage_cache.clear()
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(stage_cache.describe()["files"], 0)
        self.assertEqual(stage_cache.clear(), 0, "再清一次是幂等的")


if __name__ == "__main__":
    unittest.main()
