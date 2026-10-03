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

    def test_content_tag_in_folder_name(self):
        """文件夹名带内容标签（2026-10-03）：从外面一眼看出装的是什么。"""
        out = _tmpdir()
        d = data_export.batch_dir(out, now=NOW, tag="处理产物")
        self.assertEqual(d.name, "导出_2026-10-02_225503_处理产物")
        self.assertTrue(d.is_dir())


class TestCategoryTag(unittest.TestCase):
    """内容标签按曲线**实际**类别判定（不猜、不美化）。"""

    def _row(self, category):
        return ("a", np.array([1.0]), np.array([1.0]), "none", category, None)

    def test_all_raw(self):
        self.assertEqual(
            data_export.category_tag([self._row(data_export.CATEGORY_RAW)]),
            "原始")

    def test_all_processed_and_all_oned(self):
        self.assertEqual(
            data_export.category_tag(
                [self._row(data_export.CATEGORY_PROCESSED)]), "处理产物")
        self.assertEqual(
            data_export.category_tag([self._row(data_export.CATEGORY_ONED)]),
            "1D产物")

    def test_mixed(self):
        self.assertEqual(
            data_export.category_tag([self._row(data_export.CATEGORY_RAW),
                                      self._row(data_export.CATEGORY_PROCESSED)]),
            "混合")

    def test_old_shapes_normalize(self):
        """3 元组（老调用方）→ 没做处理 → 原始。"""
        self.assertEqual(
            data_export.category_tag([("a", np.array([1.0]),
                                      np.array([1.0]))]), "原始")


class TestUniquePath(unittest.TestCase):
    """不覆盖已有曲线（2026-10-03）：同名顺延 _2、_3…"""

    def test_free_name_untouched(self):
        target = _tmpdir() / "x.txt"
        self.assertEqual(data_export.unique_path(target), target)

    def test_existing_gets_suffix_then_next(self):
        d = _tmpdir()
        first = d / "x.txt"
        first.write_text("第一份", encoding="utf-8")
        second = data_export.unique_path(first)
        self.assertEqual(second.name, "x_2.txt")
        second.write_text("第二份", encoding="utf-8")
        third = data_export.unique_path(first)
        self.assertEqual(third.name, "x_3.txt")
        # 两份都在，谁也没被盖掉
        self.assertEqual(first.read_text(encoding="utf-8"), "第一份")
        self.assertEqual(second.read_text(encoding="utf-8"), "第二份")


class TestWriteCsv(unittest.TestCase):
    def _results(self):
        tth = np.array([1.0, 2.0, 3.0, 4.0])
        return [("a", tth, np.array([10.0, np.nan, 30.0, 40.0]),
                 "bg=auto/win=0.18", data_export.CATEGORY_PROCESSED,
                 "lmfp1_lab6"),
                ("b", tth, np.array([1.0, 2.0, 3.0, 4.0]), "", "", None)]

    def test_meta_on_top_header_next_to_data(self):
        """说明块在最上面；表头行**紧跟其后**就是数据（2026-10-03 定）。

        用户原话："总的 csv，2theta 和名称跟数据不挨着，把无关信息放到
        最上面"——以前说明夹在表头和数据之间（81 列 = 81 行），Excel 里
        一滚名字和数据就错位。
        """
        target = _tmpdir() / data_export.CSV_NAME
        data_export.write_csv(target, self._results(), now=NOW)
        self.assertEqual(target.read_bytes()[:3], b"\xef\xbb\xbf",
                         "CSV 必须带 BOM，Excel 才不乱码")
        lines = target.read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(lines[0], "# exported: 2026-10-02 22:55:03")
        self.assertIn("# column 2: a | category=Processed"
                      " | chain=bg=auto/win=0.18 | cut=1 points removed"
                      " | 4 points | 2theta=1.000-4.000 deg"
                      " | config=lmfp1_lab6", lines[1])
        self.assertIn("category=Raw | chain=none | cut=0 points removed",
                      lines[2])
        self.assertNotIn("config=", lines[2], "配置未知就不写这个 token")
        self.assertEqual(lines[3],
                         "# blank cells = 2theta points inside a cut range"
                         " (no data)")
        # 表头行紧挨着第一条数据
        self.assertEqual(lines[4], "2theta(deg),a,b")
        self.assertEqual(lines[5], "1,10,1")
        self.assertEqual(lines[6], "2,,2")
        self.assertNotIn("nan", "\n".join(lines).lower())

    def test_readback_skips_comment_block(self):
        """数据能读回来：跳过 # 注释块、拿第一行非注释当表头。

        （pandas 的 `read_csv(comment="#")` 就是这个语义；原来的
        `np.loadtxt(skiprows=1)` 已不适用——注释行数随列数变。）
        """
        tth = np.array([1.0, 2.0, 3.0])
        target = _tmpdir() / data_export.CSV_NAME
        data_export.write_csv(
            target, [("a", tth, np.array([1.0, 2.0, 3.0])),
                     ("b", tth, np.array([4.0, 5.0, 6.0]))], now=NOW)
        body = [ln for ln in target.read_text(encoding="utf-8-sig")
                .splitlines() if not ln.startswith("#")]
        self.assertEqual(body[0], "2theta(deg),a,b")
        data = np.loadtxt(body[1:], delimiter=",")
        self.assertEqual(data.shape, (3, 3))
        np.testing.assert_allclose(data[:, 2], [4.0, 5.0, 6.0])
        text = target.read_text(encoding="utf-8-sig")
        self.assertNotIn("blank cells", text,
                         "没有裁剪列就不写 blank 说明行")

    def test_no_meta_line_between_header_and_data(self):
        """回归护栏：表头行之后第一行必须是数据（不许再插入说明）。"""
        tth = np.array([1.0, 2.0])
        target = _tmpdir() / data_export.CSV_NAME
        data_export.write_csv(
            target, [("a", tth, np.array([1.0, 2.0])),
                     ("b", tth, np.array([np.nan, 2.0]))], now=NOW)
        lines = target.read_text(encoding="utf-8-sig").splitlines()
        head = next(i for i, ln in enumerate(lines)
                    if ln.startswith("2theta(deg),"))
        self.assertTrue(lines[head + 1].startswith("1,"),
                        f"表头下一行应当是数据：{lines[head + 1]!r}")

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
