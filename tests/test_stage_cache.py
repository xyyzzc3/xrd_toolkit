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


class TestProductLedger(unittest.TestCase):
    """产物台账：record_batch / list_batches / drop_batch / clear 联动。

    界面上"一次 [批量扣背景] = 一个分组（阶段文件夹）"，这一层是它的
    数据来源——产物键是哈希，反查不出来，所以必须单独记一笔。
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="xrd_cache_src_"))
        self.kw = dict(config="lmfp1_lab6", npt=1000, tth_min=1.0, tth_max=8.0)
        self.tth = np.linspace(1.0, 8.0, 100)
        self.inten = np.cos(self.tth) * 10
        # 每个用例自己一份台账（缓存根是模块级共享的临时目录）
        stage_cache.write_batches("bg", [])
        stage_cache.write_batches("1d", [])

    def _store_bg(self, name: str):
        """真存一份 bg 产物，返回 (源文件路径, 产物键)。"""
        f = self.dir / name
        f.write_bytes(b"0" * 1024)
        produced = stage_cache.store_bg(
            f, self.tth, self.inten, **self.kw,
            settings={"mode": "anchor", "window_deg": 2.0,
                      "anchors": [(1.0, 5.0)]})
        return f, produced.stem

    def _record(self, batch, items, label="扣背景 09-25 10:10（锚点 2 个，窗口 2°）"):
        return stage_cache.record_batch(
            "bg", batch, label=label, items=items, **self.kw,
            settings={"mode": "anchor", "window_deg": 2.0})

    def test_record_then_list(self):
        a, ka = self._store_bg("a.tif")
        b, kb = self._store_bg("b.tif")
        self.assertEqual(self._record("20260925-101010-ab12cd", [(a, ka), (b, kb)]), 2)
        batches = stage_cache.list_batches("bg")
        self.assertEqual(len(batches), 1)
        node = batches[0]
        self.assertEqual(node["id"], "20260925-101010-ab12cd")
        self.assertIn("锚点 2 个", node["label"])
        self.assertEqual(node["config"], "lmfp1_lab6")
        self.assertEqual(sorted(m["source"] for m in node["items"].values()),
                         ["a.tif", "b.tif"])
        self.assertEqual(stage_cache.list_batches("1d"), [], "别的类没有台账")

    def test_same_batch_id_overwrites_and_new_is_first(self):
        """同一个批次号再记一次 = 覆盖（重复点不长得重复分组）；
        列出来的顺序 = 新的在前。"""
        a, ka = self._store_bg("a.tif")
        b, kb = self._store_bg("b.tif")
        self._record("batch-1", [(a, ka)])
        self._record("batch-1", [(a, ka), (b, kb)])
        self._record("batch-2", [(a, ka)], label="扣背景 09-25 11:11（空扫相减）")
        batches = stage_cache.list_batches("bg")
        self.assertEqual([n["id"] for n in batches], ["batch-2", "batch-1"])
        self.assertEqual(len(batches[1]["items"]), 2, "同号覆盖成后记的那份")

    def test_list_prune_hides_dead_items(self):
        """产物被删掉（用户清了 outputs/）→ prune 后这一条不再出现，
        但**不写盘**（要不要落盘由调用方决定）。"""
        a, ka = self._store_bg("a.tif")
        b, kb = self._store_bg("b.tif")
        self._record("batch-1", [(a, ka), (b, kb)])
        (stage_cache.CACHE_ROOT / "bg" / f"{kb}.npz").unlink()
        self.assertEqual(len(stage_cache.list_batches("bg")[0]["items"]), 2)
        self.assertEqual(sorted(stage_cache.list_batches("bg", prune=True)[0]["items"]),
                         [str(a.resolve())])
        self.assertEqual(len(stage_cache.list_batches("bg")[0]["items"]), 2,
                         "prune 只清返回值，不落盘")

    def test_write_batches_persists_prune(self):
        a, ka = self._store_bg("a.tif")
        b, kb = self._store_bg("b.tif")
        self._record("batch-1", [(a, ka), (b, kb)])
        (stage_cache.CACHE_ROOT / "bg" / f"{kb}.npz").unlink()
        stage_cache.write_batches("bg", stage_cache.list_batches("bg", prune=True))
        self.assertEqual(len(stage_cache.list_batches("bg")[0]["items"]), 1)

    def test_drop_batch_removes_files_and_entry(self):
        """删一组 = 台账条目 + 盘上的产物一起没；别的组的产物不受影响。"""
        a, ka = self._store_bg("a.tif")
        b, kb = self._store_bg("b.tif")
        self._record("batch-1", [(a, ka), (b, kb)])
        ka_path = stage_cache.CACHE_ROOT / "bg" / f"{ka}.npz"
        self.assertTrue(ka_path.exists())
        self.assertEqual(stage_cache.drop_batch("bg", "batch-1"), 2)
        self.assertFalse(ka_path.exists())
        self.assertEqual(stage_cache.list_batches("bg"), [])

    def test_drop_batch_is_missing_safe(self):
        self.assertEqual(stage_cache.drop_batch("bg", "没有这个批"), 0)

    def test_clear_one_kind_keeps_other_kinds_ledger(self):
        a, ka = self._store_bg("a.tif")
        self._record("batch-1", [(a, ka)])
        stage_cache.record_batch("1d", "one-d", label="1D", items=[(a, "k1")],
                                 **self.kw)
        removed = stage_cache.clear("bg")
        self.assertGreaterEqual(removed, 1)
        self.assertEqual(stage_cache.list_batches("bg"), [])
        self.assertEqual(len(stage_cache.list_batches("1d")), 1,
                         "只清一类，别类的台账要留着")

    def test_clear_all_removes_ledger(self):
        a, ka = self._store_bg("a.tif")
        self._record("batch-1", [(a, ka)])
        stage_cache.clear()
        self.assertFalse((stage_cache.CACHE_ROOT / "index.json").exists())
        self.assertEqual(stage_cache.list_batches("bg"), [])

    def test_corrupt_index_reads_empty_not_crash(self):
        """台账坏了当空表：它只是台账，产物还在、还能命中。"""
        (stage_cache.CACHE_ROOT / "index.json").write_text("{半截",
                                                           encoding="utf-8")
        self.assertEqual(stage_cache.list_batches("bg"), [])
        a, ka = self._store_bg("a.tif")
        self._record("batch-1", [(a, ka)])          # 还能正常记
        self.assertEqual(len(stage_cache.list_batches("bg")), 1)


if __name__ == "__main__":
    unittest.main()
