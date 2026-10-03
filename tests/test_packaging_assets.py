"""打包资产守卫（2026-10-03）：说明书与许可声明必须真的随包。

用户要求"按行业规范、市场规范来做"：桌面软件的发行包里要有
《使用说明》和第三方许可声明（LGPL/MIT/BSD 都要求随二进制分发文本），
并且 zip 里也要放一份（同事转发的就是 zip，文档得跟着文件走）。
这条测试静态扫打包配置——漏收、改名、挪位置都当场报出来。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from xrd_toolkit import paths

ROOT = Path(__file__).resolve().parents[1]

MANUAL = "使用说明.html"
NOTICES = "THIRD_PARTY_NOTICES.txt"


class TestShippedFiles(unittest.TestCase):
    """paths.shipped_file：开发模式按仓库找、打包后按包目录找。"""

    def test_manual_resolves_in_dev(self):
        got = paths.shipped_file(MANUAL, "docs/使用说明.html")
        self.assertIsNotNone(got, "开发模式应能按仓库相对路径找到使用说明")
        self.assertTrue(got.is_file())

    def test_notices_resolves_in_dev(self):
        got = paths.shipped_file(
            NOTICES, "packaging/THIRD_PARTY_NOTICES.txt")
        self.assertIsNotNone(got)
        self.assertTrue(got.is_file())

    def test_missing_returns_none(self):
        self.assertIsNone(paths.shipped_file("不存在.html", "也不存在.html"))


class TestBundledInApp(unittest.TestCase):
    def test_spec_ships_manual_and_notices(self):
        spec = (ROOT / "packaging" / "xrd_toolkit.spec").read_text(
            encoding="utf-8")
        self.assertIn("使用说明.html", spec, "PyInstaller spec 要把使用说明打进包")
        self.assertIn("THIRD_PARTY_NOTICES.txt", spec,
                      "第三方许可声明必须随包（合规硬要求）")


class TestZipContents(unittest.TestCase):
    def test_release_workflow_ships_manual_beside_app(self):
        wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8")
        self.assertIn("使用说明.html", wf,
                      "zip 里要带说明书（macOS 与 Windows 两侧都要）")
        self.assertEqual(wf.count("使用说明.html"), 2,
                         "mac 与 win 各一次：漏一边同事拿到的包里就没有文档")


class TestNoticesContent(unittest.TestCase):
    def test_notices_lists_qt_and_has_full_texts(self):
        text = (ROOT / "packaging" / NOTICES).read_text(encoding="utf-8")
        for needle in ("PySide6", "pyFAI", "numpy", "matplotlib",
                       "GNU LESSER GENERAL PUBLIC LICENSE",
                       "GNU GENERAL PUBLIC LICENSE"):
            self.assertIn(needle, text, f"许可声明里应当有 {needle}")


if __name__ == "__main__":
    unittest.main()
