"""会开真界面的脚本不许往用户 outputs/ 里写产物（2026-10-03 用户定）。

用户原话："不是用户自己操作的，就不应该出现在文件区，会困惑。"——
一次真数据探针跑完，文件栏里冒出用户没做过的「处理产物」，根因是
脚本用真实缓存跑真数据。规矩：脚本把产物缓存与配方指到系统临时目录，
而且要在 **import xrd_toolkit 之前**设——那几个环境变量是模块导入时
读的，写在导入后面等于没写。这条测试静态扫 scripts/，防回归。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

def _imports_create_window(src: str) -> bool:
    """脚本**真的 import 了** create_window 吗（AST 判定，不看注释）。

    2026-10-04 实测：文本匹配会被散文误伤——run_tests.py 的一句注释里
    提到这个词，就被判成"开真界面的脚本"。开真界面的判定必须是代码事实：
    check_env.py 只是 import 模块、不建窗口，所以不在管辖内。
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(
                a.name == "create_window" for a in node.names):
            return True
        if isinstance(node, ast.Import) and any(
                a.name.split(".")[-1] == "create_window" for a in node.names):
            return True
    return False
ANCHOR = "from xrd_toolkit"          # 第一次导入包的位置（注释里不会出现这串）
NEEDED = ("XRD_STAGE_CACHE", "XRD_RECIPES")


class TestScriptsDoNotPolluteOutputs(unittest.TestCase):
    def test_gui_scripts_keep_demo_exports_out_of_the_repo(self):
        """演示导出不许落进仓库的 outputs/（2026-10-09 加）。

        make_gui_shots 的批处理图把导出对话框指向 `ROOT / "outputs"`，
        于是每重拍一次就在仓库里留一个 `导出_时间戳_原始/`——翻出来有
        四个（10-04 起）。和产物缓存同一条规矩：脚本自己跑的演示导出
        该指到临时目录。静态判定按 AST 认 `ROOT / "outputs"` 这个具体
        写法（注释里提到 outputs 不算，文本匹配会误伤）。
        """
        bad = []
        for path in sorted(SCRIPTS.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.BinOp)
                        and isinstance(node.op, ast.Div)
                        and isinstance(node.right, ast.Constant)
                        and node.right.value == "outputs"
                        and isinstance(node.left, ast.Name)
                        and node.left.id == "ROOT"):
                    bad.append(path.name)
                    break
        self.assertEqual(
            [], bad,
            "这些脚本把演示导出写进仓库 outputs/（应改指系统临时目录，"
            "抄 make_gui_shots.py 的 _EXPORT_DEMO）：" + "、".join(bad))

    def test_gui_scripts_isolate_product_cache(self):
        bad = []
        for path in sorted(SCRIPTS.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if not _imports_create_window(text):
                continue
            mark = text.find(ANCHOR)
            head = text[:mark] if mark >= 0 else ""
            if any(name not in head for name in NEEDED):
                bad.append(path.name)
        self.assertEqual(
            [], bad,
            "这些脚本开真界面却不隔离产物缓存（要在 import xrd_toolkit 之前"
            "把 XRD_STAGE_CACHE / XRD_RECIPES 指到临时目录，抄 check_gui.py"
            "顶部那段）：" + "、".join(bad))
