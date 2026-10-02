"""界面文案规范守卫（规范见 docs/UI_COPY.zh-CN.md）。

扫 src/xrd_toolkit/gui/*.py 的字符串常量（跳过 docstring），检查两件事：

1. 禁词 / 禁记号——裸露 Markdown（**）、全角空格、单破折号 " — "、
   范围连接 "~"、内部黑话（参数坞、坞顶、门槛、落盘、口径、尚未接线）、
   旧英文名（Customize）。
2. [按钮引用] 完整性——文案里点名的 [xxx] 必须在某个字符串常量里真有出处，
   防止出现"提示里写 [保存为配方…]，按钮实际叫 [存成配方…]"这类断链。

只认含中文的字符串：英文串（文件名、字段名、正则）不参与检查。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI_DIR = ROOT / "src" / "xrd_toolkit" / "gui"

# 禁词：出现即错（限含中文的字符串）
BANNED = (
    "**",        # 裸露 Markdown 粗体
    "　",    # 全角空格
    "参数坞", "坞顶", "门槛", "落盘", "尚未接线", "口径",
    "Customize",  # 外观对话框的旧英文名
    " — ",       # 单破折号（统一用 ——）
    "~",         # 范围连接（统一用 –）
)

# 引用豁免：行内并列写法或非按钮引用，逐个写明原因
ALLOW_REFS: set = set()

REF_RE = re.compile(r"\[([^\[\]\n]{1,24}?)\]")


def _iter_strings(path: Path):
    """产出 (行号, 字符串常量, 是否用户可见)，跳过 docstring。

    用户可见 = 串本身含中文，**或**它是某个含中文 f-string 的片段
    （否则 f"{a}~{b}°" 里的 "~" 这种纯 ASCII 片段会漏检）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    cjk_fstring_parts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and any(
            isinstance(v, ast.Constant)
            and isinstance(v.value, str)
            and _has_cjk(v.value)
            for v in node.values
        ):
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    cjk_fstring_parts.add(id(v))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            visible = _has_cjk(node.value) or id(node) in cjk_fstring_parts
            yield node.lineno, node.value, visible


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _all_strings():
    for py in sorted(GUI_DIR.glob("*.py")):
        for lineno, value, visible in _iter_strings(py):
            yield py.name, lineno, value, visible


class TestNoBannedTokens(unittest.TestCase):
    def test_banned_tokens(self):
        bad = []
        for name, lineno, value, visible in _all_strings():
            if not visible:
                continue
            for token in BANNED:
                if token in value:
                    bad.append(f"{name}:{lineno} 含 {token!r}：{value[:60]}")
        self.assertEqual([], bad, "界面文案含禁词/禁记号：\n" + "\n".join(bad))


class TestBracketRefsExist(unittest.TestCase):
    def test_refs_exist(self):
        hay = []
        refs = []
        for name, lineno, value, visible in _all_strings():
            hay.append(value)
            if not visible:
                continue
            for ref in REF_RE.findall(value):
                refs.append((name, lineno, ref))
        bad = []
        for name, lineno, ref in refs:
            # 排除行内并列写法（含 / 、格式串 %、算式 =）——不是按钮引用
            if any(ch in ref for ch in "/=%"):
                continue
            core = ref.rstrip("…").strip()
            if not core or core in ALLOW_REFS:
                continue
            # 前缀匹配：按钮引用要么是完整按钮文字，要么是它的省略写法
            # （如 [编辑…] → 按钮"编辑当前配置…"）。子串匹配太松，会把
            # "已保存为配方"这种句子里恰好包含的名字放过去。
            if not any(candidate.startswith(core) for candidate in hay):
                bad.append(f"{name}:{lineno} 引用 [{ref}]，但工程里没有以它开头的按钮/文字")
        self.assertEqual([], bad, "按钮引用断链：\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
