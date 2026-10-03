"""打包自检模块的单测（2026-10-03）。

selftest.run() 在**源码模式**下必须全过——它检查的正是"随包数据"：
fabio 编解码、pyFAI 积分与标样数据、matplotlib 图标、Qt 中文翻译。
打包后的真实环境由 CI 的冒烟步骤跑同一个 run()（见 release.yml）。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xrd_toolkit import paths, selftest


class TestSelftest(unittest.TestCase):
    def test_passes_in_source_mode_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(selftest, "_LINES", []), \
                    mock.patch.object(paths, "OUTPUTS_DIR", Path(tmp)):
                code = selftest.run()
            self.assertEqual(code, 0, "自检失败：\n" + "\n".join(selftest._LINES))
            report = Path(tmp) / "selftest.txt"
            self.assertTrue(report.exists(), "报告没落盘——排障通道就断了")
            self.assertIn("全过", report.read_text(encoding="utf-8"))
