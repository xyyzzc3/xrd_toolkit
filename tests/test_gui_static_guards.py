"""GUI 静态护栏：patch 目标存在性 + 源文件未绑定名。

2026-10-30 从 tests/test_gui_integration.py 拆出（跑得快，属 --fast 档）。
"""
try:
    from tests import _gui_integration_support as _support
except ModuleNotFoundError:  # 直接运行本文件
    import sys
    from pathlib import Path as _Path
    sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from tests import _gui_integration_support as _support

import ast
import builtins
import types
import unittest
from pathlib import Path


class TestPatchTargetsResolve(unittest.TestCase):
    """静态护栏：每个 mock.patch 目标的名字必须真的在被打的模块里。

    拆模块后最常踩的坑（本仓踩过两次）：patch 目标跟着"函数的新家"改，
    或者别名混了（gui_panels 指 panels.py，不是 plot_panels.py）。打一
    个不存在的名字 = AttributeError，但只有当那条测试跑到时才炸；要是
    它同时让真弹窗打开，整套会卡死在 exec() 上（实测卡过一次，20 分钟
    后才发现）。

    拆分后（2026-10-30）：扫全部 test_gui_integration_*.py；别名表取公共
    支持模块的命名空间（各拆文件的别名都从它星号导入，是同一份对象）。
    """

    def test_patch_targets_exist(self):
        aliases = {name: obj for name, obj in vars(_support).items()
                   if isinstance(obj, types.ModuleType)}
        checked = 0
        for path in sorted(Path(__file__).parent.glob(
                "test_gui_integration_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("object", "patch")
                        and isinstance(node.func.value, ast.Attribute)
                        and node.func.value.attr == "patch"
                        and len(node.args) >= 1):
                    continue
                tgt = node.args[0]
                if isinstance(tgt, ast.Name):
                    if len(node.args) < 2                             or not isinstance(node.args[1], ast.Constant):
                        continue
                    mod, attr = aliases.get(tgt.id), node.args[1].value
                elif isinstance(tgt, ast.Attribute):
                    if not isinstance(tgt.value, ast.Name):
                        continue
                    mod, attr = aliases.get(tgt.value.id), tgt.attr
                elif isinstance(tgt, ast.Constant)                         and isinstance(tgt.value, str):
                    continue          # mock.patch("a.b.c") 形式本套件没用
                else:
                    continue
                if mod is None:
                    continue          # 不是模块（如 patch.object(window, ...)）
                checked += 1
                self.assertTrue(
                    hasattr(mod, attr),
                    f"{path.name}:{node.lineno}：{mod.__name__} 没有 {attr}"
                    f"——拆模块后 patch 目标要指向调用点所在的模块")
        self.assertGreater(checked, 100, "patch 目标没扫到，检查本测试的解析")



class TestNoUndefinedNames(unittest.TestCase):
    """静态护栏：源文件里不许出现"用了、但全文件没绑定过"的名字。

    为什么要有（2026-09-28 用户报"配方保存无效"）：`app._save_recipe_dialog`
    里调了 `_proc_settings`，而这个名字**从来没在 app 模块里导入过**——每点
    一次 [保存为配方…] 都是 NameError：日志区一个字都没有，`outputs/
    recipes.json` 从来没生成过。这类伤**只有点到那条按钮才现形**，而配方那边
    的测试全都直接调 `recipe_store.save`（绕过了按钮），于是全绿。

    口径（宽）：模块里**任意作用域**绑定过的名字都算"有"——只抓"整个文件里
    根本没出现过这个名字"这一档，所以不会误报；代价是不抓"跨函数漏绑定"。
    真想抓后者要做完整的作用域分析，那是另一件事（需要时再上 mypy/pyflakes）。
    """

    ROOT = Path(__file__).resolve().parents[1] / "src" / "xrd_toolkit"
    BUILTINS = set(dir(builtins)) | {
        "__file__", "__name__", "__doc__", "__package__", "__spec__",
        "__loader__", "__builtins__", "__debug__"}

    @classmethod
    def _bound(cls, tree):
        out = set()

        def target(t):
            if isinstance(t, ast.Name):
                out.add(t.id)
            elif isinstance(t, (ast.Tuple, ast.List)):
                for e in t.elts:
                    target(e)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.add(node.name)
                args = node.args
                for a in args.args + args.kwonlyargs + args.posonlyargs:
                    out.add(a.arg)
                if args.vararg:
                    out.add(args.vararg.arg)
                if args.kwarg:
                    out.add(args.kwarg.arg)
            elif isinstance(node, ast.ClassDef):
                out.add(node.name)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for al in node.names:
                    out.add((al.asname or al.name).split(".")[0])
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                for t in (node.targets if isinstance(node, ast.Assign)
                          else [node.target]):
                    target(t)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                target(node.target)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if item.optional_vars is not None:
                        target(item.optional_vars)
            elif isinstance(node, ast.ExceptHandler):
                if node.name:
                    out.add(node.name)
            elif isinstance(node, ast.comprehension):
                target(node.target)
            elif isinstance(node, ast.Lambda):
                args = node.args
                for a in args.args + args.kwonlyargs + args.posonlyargs:
                    out.add(a.arg)
                if args.vararg:
                    out.add(args.vararg.arg)
                if args.kwarg:
                    out.add(args.kwarg.arg)
            elif isinstance(node, (ast.Nonlocal, ast.Global)):
                out.update(node.names)
        return out

    def test_every_name_read_is_bound_somewhere(self):
        bad = []
        scanned = 0
        for path in sorted(self.ROOT.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            if "import *" in src:
                continue                      # 星号导入没法静态判断
            scanned += 1
            tree = ast.parse(src)
            have = self._bound(tree) | self.BUILTINS
            for node in ast.walk(tree):
                if (isinstance(node, ast.Name)
                        and isinstance(node.ctx, ast.Load)
                        and node.id not in have):
                    bad.append(f"{path.relative_to(self.ROOT)}:{node.lineno}"
                               f" 读了没绑定过的名字 {node.id!r}")
        self.assertGreater(scanned, 20, "没扫到几个文件，检查 ROOT")
        self.assertEqual(bad, [], "这些名字一跑到就是 NameError：\n  "
                                  + "\n  ".join(bad))
