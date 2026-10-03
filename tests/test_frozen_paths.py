"""打包（PyInstaller）路径分支的单测（2026-10-03）。

背景：打包后代码目录是只读的（.app / 安装目录内部），用户数据必须
改去"找得到、写得进"的地方。开发分支一个字没改——现状仍由
tests/test_data_export.py::TestPaths 钉着（OUTPUTS_DIR == 仓库/outputs）。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from xrd_toolkit import config, paths


class TestFrozenOutputsDir(unittest.TestCase):
    def test_dev_mode_is_repo_outputs(self):
        """没打包 = 现状：仓库根/outputs（脚本、测试、肌肉记忆都靠它）。"""
        self.assertEqual(paths.default_outputs_dir(frozen=False),
                         paths.ROOT / "outputs")

    def test_frozen_macos_stays_out_of_documents(self):
        """打包 + macOS：~/XRD_Toolkit——故意不进"文档"（TCC 授权弹窗
        与 iCloud 同步两条理由，见 paths.default_outputs_dir 的说明）。"""
        home = Path("/Users/someone")
        got = paths.default_outputs_dir(frozen=True, platform="darwin",
                                        home=home)
        self.assertEqual(got, home / "XRD_Toolkit")
        self.assertNotIn("Documents", str(got))

    def test_frozen_windows_uses_documents(self):
        home = Path("C:/Users/someone")
        got = paths.default_outputs_dir(frozen=True, platform="win32",
                                        home=home)
        self.assertEqual(got, home / "Documents" / "XRD_Toolkit")

    def test_this_process_is_dev_mode(self):
        """本测试进程没打包：FROZEN=False、OUTPUTS_DIR 就是仓库 outputs。"""
        self.assertFalse(paths.FROZEN)
        self.assertEqual(paths.OUTPUTS_DIR, paths.ROOT / "outputs")


class TestUserConfigPath(unittest.TestCase):
    def test_packaged_lands_in_user_data_dir(self):
        """打包后：[保存为配置] 落在用户数据目录（绝不写包内部）。"""
        with mock.patch.object(paths, "FROZEN", True), \
                mock.patch.object(paths, "OUTPUTS_DIR",
                                  Path("/Users/someone/XRD_Toolkit")):
            got = config._default_user_config_path()
        self.assertEqual(got, Path("/Users/someone/XRD_Toolkit")
                         / "config_user.json")

    def test_dev_stays_next_to_module(self):
        """开发模式：仍在 config.py 同目录（你现有的标定文件不动）。"""
        with mock.patch.object(paths, "FROZEN", False):
            got = config._default_user_config_path()
        self.assertEqual(got, Path(config.__file__).with_name(
            "config_user.json"))

    def test_current_process_reads_the_real_file(self):
        """本进程（开发模式）读的就是包目录里那份——现状未变。"""
        self.assertEqual(config.USER_CONFIG_PATH,
                         Path(config.__file__).with_name("config_user.json"))
