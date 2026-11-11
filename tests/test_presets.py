"""项目预设（B1）：内置/用户层、读写、活动预设与缺字段回退。

这些测试只碰 `presets.py` 的纯函数和临时文件，不建窗口；GUI 侧的
下拉/保存/应用在 test_gui_presets.py。
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xrd_toolkit import presets  # noqa: E402
from xrd_toolkit.config import DEFAULT_CONFIG  # noqa: E402


def _tmp_path():
    folder = tempfile.mkdtemp()
    return Path(folder) / "presets_user.json"


class TestPresetService(unittest.TestCase):
    def test_builtin_default_resolves(self):
        preset = presets.get_preset(presets.DEFAULT_PRESET_NAME)
        self.assertEqual(preset["geometry"], "lmfp1_lab6")
        self.assertEqual(preset["second_phase_cif"], "Al_metal.cif")
        self.assertIn(presets.DEFAULT_PRESET_NAME,
                      presets.preset_names())
        self.assertTrue(presets.is_builtin(presets.DEFAULT_PRESET_NAME))

    def test_save_read_and_active_roundtrip(self):
        path = _tmp_path()
        values = {
            "geometry": "lmfp1_lab6", "second_phase_cif": "",
            "insitu_dir": "~/frames", "insitu_mask_name": "",
            "phase_library": "/tmp/library",
        }
        self.assertTrue(presets.save_user_preset("我的项目", values,
                                                 path=path))
        self.assertIn("我的项目", presets.preset_names(path))
        preset = presets.get_preset("我的项目", path)
        self.assertEqual(preset["insitu_dir"], "~/frames")
        self.assertEqual(preset["phase_library"], "/tmp/library")
        # 再存同名 = 覆盖
        self.assertFalse(presets.save_user_preset("我的项目", values,
                                                  path=path))
        presets.set_active_preset("我的项目", path)
        self.assertEqual(presets.active_preset_name(path), "我的项目")

    def test_missing_fields_fill_defaults(self):
        path = _tmp_path()
        presets.save_user_preset("稀疏预设",
                                 {"geometry": "lmfp1_lab6"}, path=path)
        preset = presets.get_preset("稀疏预设", path)
        for key, value in presets.DEFAULTS.items():
            self.assertEqual(preset[key], value, key)

    def test_bad_geometry_falls_back_with_warning(self):
        preset = presets.resolve_preset(
            {"name": "坏几何", "geometry": "does-not-exist"})
        self.assertEqual(preset["geometry"], DEFAULT_CONFIG)
        self.assertTrue(preset["warnings"])

    def test_builtin_cannot_be_overwritten_or_removed(self):
        path = _tmp_path()
        with self.assertRaises(ValueError):
            presets.save_user_preset(presets.DEFAULT_PRESET_NAME,
                                     {"geometry": "lmfp1_lab6"}, path=path)
        with self.assertRaises(ValueError):
            presets.remove_user_preset(presets.DEFAULT_PRESET_NAME, path=path)

    def test_remove_active_falls_back_to_default(self):
        path = _tmp_path()
        presets.save_user_preset("临时", {"geometry": "lmfp1_lab6"},
                                 path=path)
        presets.set_active_preset("临时", path)
        self.assertTrue(presets.remove_user_preset("临时", path=path))
        self.assertEqual(presets.active_preset_name(path),
                         presets.DEFAULT_PRESET_NAME)

    def test_corrupt_file_ignored_with_warning(self):
        path = _tmp_path()
        path.write_text("{not json", encoding="utf-8")
        with redirect_stderr(io.StringIO()) as buf:
            names = presets.preset_names(path)
            active = presets.active_preset_name(path)
        self.assertEqual(names, [presets.DEFAULT_PRESET_NAME])
        self.assertEqual(active, presets.DEFAULT_PRESET_NAME)
        self.assertIn("读取失败", buf.getvalue())

    def test_legacy_plain_dict_file(self):
        path = _tmp_path()
        path.write_text(json.dumps({
            "旧预设": {"geometry": "lmfp1_lab6", "insitu_dir": "~/old"}
        }, ensure_ascii=False), encoding="utf-8")
        self.assertIn("旧预设", presets.preset_names(path))
        self.assertEqual(presets.get_preset("旧预设", path)["insitu_dir"],
                         "~/old")
        self.assertEqual(presets.active_preset_name(path),
                         presets.DEFAULT_PRESET_NAME)

    def test_phase_library_falls_back_to_structures(self):
        from xrd_toolkit import paths
        got = presets.preset_phase_library(
            {"phase_library": "/definitely/not/a/dir"})
        self.assertEqual(got, paths.structures_dir())

    def test_processing_and_mask_roundtrip(self):
        """可选两项随预设存取；配方归一化但不带 created 时间戳。"""
        path = _tmp_path()
        processing = {
            "mode": "anchor", "window_deg": 2.0, "anchor_method": "pchip",
            "clip": True, "anchors": [[3.0, 100.0], [4.5, 90.0]],
            "smooth_deg": 0.15, "smooth_method": "boxcar",
            "smooth_order": 3, "cut_ranges": [[2.0, 3.0]],
        }
        values = {
            "geometry": "lmfp1_lab6", "second_phase_cif": "",
            "insitu_dir": "", "insitu_mask_name": "", "phase_library": "",
            "processing": processing,
            "mask_recipe": "~/masks/lmfp.mask.npz",
        }
        presets.save_user_preset("带配方", values, path=path)
        preset = presets.get_preset("带配方", path)
        self.assertEqual(preset["processing"]["mode"], "anchor")
        self.assertEqual(preset["processing"]["anchors"],
                         [[3.0, 100.0], [4.5, 90.0]])
        self.assertEqual(preset["processing"]["smooth_deg"], 0.15)
        self.assertNotIn("created", preset["processing"])
        self.assertEqual(preset["mask_recipe"], "~/masks/lmfp.mask.npz")

        raw = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 2)
        self.assertEqual(raw["presets"]["带配方"]["processing"]["mode"],
                         "anchor")
        self.assertEqual(raw["presets"]["带配方"]["mask_recipe"],
                         "~/masks/lmfp.mask.npz")

    def test_processing_only_when_meaningful(self):
        """全关 / 空扫相减不算配方；只开平滑/裁剪仍然算。"""
        path = _tmp_path()
        base = {"geometry": "lmfp1_lab6"}
        presets.save_user_preset(
            "全关", {**base, "processing": {"mode": "off"}}, path=path)
        presets.save_user_preset(
            "空扫", {**base, "processing": {"mode": "blank"}}, path=path)
        presets.save_user_preset(
            "空扫加平滑", {**base, "processing": {"mode": "blank",
                                                "smooth_deg": 0.2}}, path=path)
        presets.save_user_preset(
            "只平滑", {**base, "processing": {"mode": "off",
                                             "smooth_deg": 0.2}}, path=path)
        self.assertIsNone(presets.get_preset("全关", path)["processing"])
        self.assertIsNone(presets.get_preset("空扫", path)["processing"])
        self.assertIsNone(
            presets.get_preset("空扫加平滑", path)["processing"])
        self.assertEqual(
            presets.get_preset("只平滑", path)["processing"]["smooth_deg"],
            0.2)

    def test_old_file_without_optional_fields(self):
        """version 1 文件读进来：两个可选字段保持未设置，不补 LMFP 默认。"""
        path = _tmp_path()
        path.write_text(json.dumps({
            "version": 1, "active": "旧预设",
            "presets": {"旧预设": {"geometry": "lmfp1_lab6"}},
        }, ensure_ascii=False), encoding="utf-8")
        preset = presets.get_preset("旧预设", path)
        self.assertIsNone(preset["processing"])
        self.assertEqual(preset["mask_recipe"], "")


if __name__ == "__main__":
    unittest.main()
