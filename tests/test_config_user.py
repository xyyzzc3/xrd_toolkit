"""用户配置文件（config_user.json）：读取合并 / 保存 / 校验。

GUI 校准工作台 [保存为配置] 把标定结果存成命名配置条目（本地文件，
不进 git；内置 config.py 注册表仍走 CLI 模板人工登记）。本文件用
临时路径直接测读写函数，不碰真文件。
"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from xrd_toolkit import config

GEOM = {
    "pixel_size_m": 200e-6, "wavelength_m": 0.1223e-10,
    "dist_m": 1.5958, "poni1_m": 0.20904, "poni2_m": 0.2044,
    "rot1_deg": -0.005, "rot2_deg": -0.163,
}
ENTRY = {
    "label": "测试批次",
    "geometry": GEOM,
    "beam_center": [1022.0, 1022.3],
    "residual_deg": 0.0037,
}


def _tmpfile(text=None):
    d = tempfile.mkdtemp()
    p = Path(d) / "config_user.json"
    if text is not None:
        p.write_text(text, encoding="utf-8")
    return p


class TestReadUserConfig(unittest.TestCase):
    """_read_user_config：容错读取（坏文件不拖垮程序启动）。"""

    def test_missing_file_returns_empty(self):
        self.assertEqual(config._read_user_config(_tmpfile()), {})

    def test_valid_entry_parsed(self):
        p = _tmpfile(json.dumps({"t1": ENTRY}, ensure_ascii=False))
        got = config._read_user_config(p)
        self.assertEqual(got["t1"]["label"], "测试批次")
        # beam_center 列表 → (row, col) 元组；geometry 数值保留
        self.assertEqual(got["t1"]["beam_center"], (1022.0, 1022.3))
        self.assertEqual(got["t1"]["geometry"], GEOM)
        self.assertEqual(got["t1"]["residual_deg"], 0.0037)

    def test_malformed_json_ignored_with_warning(self):
        p = _tmpfile("{not json")
        with redirect_stderr(io.StringIO()) as buf:
            got = config._read_user_config(p)
        self.assertEqual(got, {})
        self.assertIn("读取失败", buf.getvalue())

    def test_non_object_top_ignored(self):
        p = _tmpfile("[1, 2, 3]")
        with redirect_stderr(io.StringIO()) as buf:
            got = config._read_user_config(p)
        self.assertEqual(got, {})
        self.assertIn("顶层不是对象", buf.getvalue())

    def test_invalid_entries_skipped_valid_kept(self):
        bad = dict(ENTRY, geometry={k: v for k, v in GEOM.items()
                                    if k != "dist_m"})   # 缺一个键
        p = _tmpfile(json.dumps({"good": ENTRY, "bad": bad}))
        with redirect_stderr(io.StringIO()) as buf:
            got = config._read_user_config(p)
        self.assertEqual(list(got), ["good"])
        self.assertIn("已跳过", buf.getvalue())

    def test_extra_geometry_keys_dropped(self):
        # 归一化：geometry 只留 7 个标准键（rot3/offset 等诊断量不存）
        entry = dict(ENTRY, geometry=dict(GEOM, rot3_deg=0.0))
        got = config._read_user_config(
            _tmpfile(json.dumps({"t1": entry})))
        self.assertEqual(set(got["t1"]["geometry"]),
                         set(config._GEOMETRY_KEYS))


class TestMergeConfigs(unittest.TestCase):
    """_merge_configs：内置在前、用户追加、重名内置优先。"""

    def test_order_and_precedence(self):
        builtin = {"a": 1, "b": 2}
        user = {"c": 3, "b": 99}
        merged = config._merge_configs(builtin, user)
        self.assertEqual(list(merged), ["a", "b", "c"])
        self.assertEqual(merged["b"], 2)   # 内置条目不可被用户文件覆盖


class TestSaveUserConfig(unittest.TestCase):
    """save_user_config：校验 / 原子落盘 / 内存合并 / 覆盖语义。"""

    def setUp(self):
        self._user_backup = dict(config.USER_CONFIGS)
        self._confs_backup = dict(config.CONFIGS)
        self._tmpdir = tempfile.mkdtemp()
        self._path = Path(self._tmpdir) / "config_user.json"
        patcher = mock.patch.object(config, "USER_CONFIG_PATH", self._path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        config.USER_CONFIGS.clear()
        config.USER_CONFIGS.update(self._user_backup)
        config.CONFIGS.clear()
        config.CONFIGS.update(self._confs_backup)

    def test_save_writes_file_and_merges(self):
        self.assertTrue(config.save_user_config("t1", ENTRY))
        self.assertTrue(self._path.is_file())
        on_disk = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(list(on_disk), ["t1"])
        # 内存三处一致：USER_CONFIGS / CONFIGS / 落盘文件
        self.assertEqual(config.USER_CONFIGS["t1"]["label"], "测试批次")
        self.assertEqual(config.CONFIGS["t1"]["beam_center"],
                         (1022.0, 1022.3))
        # 原子写入：临时文件不残留
        self.assertFalse(self._path.with_suffix(".json.tmp").exists())

    def test_save_overwrite_returns_false_and_updates(self):
        config.save_user_config("t1", ENTRY)
        new = dict(ENTRY, label="重新标定的批次",
                   geometry=dict(GEOM, dist_m=1.6000))
        self.assertFalse(config.save_user_config("t1", new))
        self.assertEqual(config.USER_CONFIGS["t1"]["label"], "重新标定的批次")
        on_disk = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(len(on_disk), 1)
        self.assertEqual(on_disk["t1"]["geometry"]["dist_m"], 1.6)

    def test_save_refuses_builtin_key(self):
        with self.assertRaises(ValueError):
            config.save_user_config("lmfp1_lab6", ENTRY)
        self.assertFalse(self._path.exists())

    def test_save_validates_entry(self):
        bad = dict(ENTRY, label="   ")
        with self.assertRaises(ValueError):
            config.save_user_config("t1", bad)
        self.assertFalse(self._path.exists())
        self.assertNotIn("t1", config.USER_CONFIGS)

    def test_roundtrip_read(self):
        config.save_user_config("t1", ENTRY)
        got = config._read_user_config(self._path)
        self.assertEqual(got["t1"], config.USER_CONFIGS["t1"])


if __name__ == "__main__":
    unittest.main()
