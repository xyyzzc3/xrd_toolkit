"""导出文件格式（services/export.py + paths.py）的单元测试——零 Qt、零网络。

这些断言就是"导出文件规范"的可执行版本（docs/UI_COPY.zh-CN.md
「导出文件规范」）：表头逐字、纯 ASCII、CSV 带 BOM 且可被 loadtxt 读回、
批量文件夹撞名规则。时间全部注入固定值，断言不依赖真实时钟。
"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from xrd_toolkit import paths
from xrd_toolkit.services import export as data_export

NOW = datetime(2026, 10, 2, 22, 55, 3)


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="xrd_export_test_"))


class TestPaths(unittest.TestCase):
    def test_outputs_dir_is_one_absolute_place(self):
        """所有导出/存图/看门狗的基准目录 = ROOT/outputs（唯一出处）。

        ROOT 由**代码位置**推算：源码树里 = 仓库根（editable 装法，开发
        机一直如此）；pip 装进 site-packages 时 = Python 安装目录那一带。
        所以这里钉的是不变量——OUTPUTS_DIR 就是 ROOT/outputs、且是绝对
        路径；"ROOT 是仓库根"只在源码树里成立，用 pyproject.toml 在不在
        来认（2026-10-03：CI 首次跑就栽在这条上——加 CI 之前没有任何
        测试用非 editable 装法跑过）。
        """
        self.assertEqual(paths.OUTPUTS_DIR, paths.ROOT / "outputs")
        self.assertTrue(paths.OUTPUTS_DIR.is_absolute())
        if (paths.ROOT / "pyproject.toml").exists():
            self.assertTrue((paths.ROOT / "src" / "xrd_toolkit").is_dir(),
                            "源码树里 ROOT 应当是仓库根")


class TestWriteCurve(unittest.TestCase):
    def test_header_verbatim(self):
        """固定时间下的表头逐字断言（规范表就是按这份写的）。"""
        target = _tmpdir() / "a_处理产物.txt"
        n = data_export.write_curve(
            target, np.array([1.0, 2.0, 3.0, 4.0]),
            np.array([10.0, np.nan, 30.0, 40.0]),
            category=data_export.CATEGORY_PROCESSED,
            chain="bg=auto/win=0.18 -> cut=2.7-3deg", config="lmfp1_lab6",
            now=NOW)
        self.assertEqual(n, 3)
        lines = target.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[:7], [
            "# 2theta(deg)  intensity",
            "# data category: Processed",
            "# chain: bg=auto/win=0.18 -> cut=2.7-3deg",
            "# cut: 1 points removed",
            "# points: 3  range: 1.000-4.000 deg",
            "# config: lmfp1_lab6",
            "# exported: 2026-10-02 22:55:03",
        ])
        # 裁剪点不写行：文件里不该出现 nan
        self.assertNotIn("nan", target.read_text(encoding="utf-8").lower())
        data = np.loadtxt(str(target))
        np.testing.assert_allclose(data[:, 1], [10.0, 30.0, 40.0])

    def test_raw_header_states_nothing_was_done(self):
        """原始数据也写明类别与链：不用猜"是原始还是处理过没写"。"""
        target = _tmpdir() / "a.txt"
        data_export.write_curve(target, np.array([1.0]), np.array([5.0]),
                                category=data_export.CATEGORY_RAW, now=NOW)
        text = target.read_text(encoding="utf-8")
        self.assertIn("# data category: Raw", text)
        self.assertIn("# chain: none", text)
        self.assertIn("# cut: 0 points removed", text)

    def test_header_is_pure_ascii(self):
        """表头永远纯 ASCII：漂亮版链、非 ASCII 配置名都不能漏进来。"""
        target = _tmpdir() / "a.txt"
        data_export.write_curve(
            target, np.array([1.0, 2.0]), np.array([1.0, 2.0]),
            category=data_export.CATEGORY_PROCESSED,
            chain="bg=auto/win=0.18 → cut=2.7–3°、2.7–3.1°",
            config="我的配置", now=NOW)
        header = [ln for ln in target.read_text(encoding="utf-8").splitlines()
                  if ln.startswith("#")]
        for ln in header:
            self.assertTrue(ln.isascii(), ln)
        # 非 ASCII 配置名整行省掉（而不是写进去乱码）
        self.assertNotIn("config:", "\n".join(header))
        self.assertIn("cut=2.7-3deg,2.7-3.1deg", "\n".join(header))


class TestBatchDir(unittest.TestCase):
    def test_name_and_same_second_collision(self):
        out = _tmpdir()
        first = data_export.batch_dir(out, now=NOW)
        second = data_export.batch_dir(out, now=NOW)
        self.assertEqual(first.name, "导出_2026-10-02_225503")
        self.assertEqual(second.name, "导出_2026-10-02_225503_2",
                         "同秒第二次导出不覆盖上一批")
        self.assertTrue(first.is_dir() and second.is_dir())


class TestWriteCsv(unittest.TestCase):
    def _results(self):
        tth = np.array([1.0, 2.0, 3.0, 4.0])
        return [("a", tth, np.array([10.0, np.nan, 30.0, 40.0]),
                 "bg=auto/win=0.18", data_export.CATEGORY_PROCESSED,
                 "lmfp1_lab6"),
                ("b", tth, np.array([1.0, 2.0, 3.0, 4.0]), "", "", None)]

    def test_bom_first_line_and_details(self):
        target = _tmpdir() / data_export.CSV_NAME
        data_export.write_csv(target, self._results(), now=NOW)
        self.assertEqual(target.read_bytes()[:3], b"\xef\xbb\xbf",
                         "CSV 必须带 BOM，Excel 才不乱码")
        lines = target.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(lines[0], "2theta(deg),a,b")
        self.assertEqual(lines[1], "# exported: 2026-10-02 22:55:03")
        self.assertIn("# column 2: a | category=Processed"
                      " | chain=bg=auto/win=0.18 | cut=1 points removed"
                      " | 4 points | 2theta=1.000-4.000 deg"
                      " | config=lmfp1_lab6", lines[2])
        self.assertIn("category=Raw | chain=none | cut=0 points removed",
                      lines[3])
        self.assertNotIn("config=", lines[3], "配置未知就不写这个 token")
        self.assertEqual(lines[4],
                         "# blank cells = 2theta points inside a cut range"
                         " (no data)")
        # 空值格留空，不写字面 nan
        self.assertEqual(lines[5], "1,10,1")
        self.assertEqual(lines[6], "2,,2")
        self.assertNotIn("nan", "\n".join(lines).lower())

    def test_loadtxt_readback_and_no_blank_line_when_no_cut(self):
        tth = np.array([1.0, 2.0, 3.0])
        target = _tmpdir() / data_export.CSV_NAME
        data_export.write_csv(
            target, [("a", tth, np.array([1.0, 2.0, 3.0])),
                     ("b", tth, np.array([4.0, 5.0, 6.0]))], now=NOW)
        data = np.loadtxt(str(target), delimiter=",", skiprows=1)
        self.assertEqual(data.shape, (3, 3))
        np.testing.assert_allclose(data[:, 2], [4.0, 5.0, 6.0])
        text = target.read_text(encoding="utf-8-sig")
        self.assertNotIn("blank cells", text,
                         "没有裁剪列就不写 blank 说明行")

    def test_row_normalization(self):
        """3 元组（老调用方/测试的形状）→ 补成没做处理的诚实默认值。"""
        tth = np.array([1.0])
        row = data_export.normalize_row(("a", tth, np.array([1.0])))
        self.assertEqual(row[3:], ("none", data_export.CATEGORY_RAW, None))
        row = data_export.normalize_row(
            ("a", tth, np.array([1.0]), "bg=auto/win=0.2"))
        self.assertEqual(row[4], data_export.CATEGORY_PROCESSED)


if __name__ == "__main__":
    unittest.main()
