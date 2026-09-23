#!/usr/bin/env python3
"""环境自检：一条命令回答"这个项目现在能不能跑"。

什么时候跑：
  * 项目文件夹被搬动 / 改名 / 换机器之后——最先跑这个
  * 任何"莫名其妙跑不起来"的现象，先自检再排查，别猜

为什么需要它（2026-09-23 真事故）：项目从桌面搬到 ~/Projects 之后，
conda 环境里的"可编辑安装"还指着旧路径，`import xrd_toolkit` 直接
ModuleNotFoundError——**源码其实一个字都没坏**，只是安装记录过期。
根因就是下面第 3 项检查的内容；当时没有这个脚本，排查花了很久。
修复方法：在项目根目录重跑 `pip install -e .`。

用法示例：
    python scripts/check_env.py          # 退出码 0 = 一切正常，1 = 有问题
"""
import os
import sys
from pathlib import Path

# 项目根 = 本文件所在 scripts/ 的上一级（与 scripts/ 下其他脚本同一惯例：
# 不依赖当前工作目录，从 __file__ 推导，搬到哪里都对）
ROOT = Path(__file__).resolve().parents[1]

_failed = []


def report(level: str, name: str, detail: str = "") -> None:
    """打印一行结论。level: OK / WARN / FAIL（FAIL 会进总结并让退出码非 0）"""
    if level == "FAIL":
        _failed.append(name)
    tail = f"  {detail}" if detail else ""
    print(f"[{level:>4}] {name}{tail}")


FIX_INSTALL = f"修复：cd {ROOT} && pip install -e ."


def main() -> int:
    print(f"项目根目录：{ROOT}\n")

    # ── 1. 解释器与仓库完整性 ──────────────────────────────────
    v = sys.version_info
    report("OK", f"Python {v.major}.{v.minor}.{v.micro}（{sys.executable}）")
    if v < (3, 9):
        report("FAIL", "Python 版本过低", "pyproject.toml 要求 >= 3.9")
    if (ROOT / "pyproject.toml").is_file():
        report("OK", "仓库结构完整（pyproject.toml 在位）")
    else:
        report("FAIL", "找不到 pyproject.toml", f"确认项目根是不是 {ROOT}")

    # ── 2. 第三方依赖（pyproject 的 dependencies + GUI 的 Qt 绑定）──
    import importlib

    deps = ["numpy", "scipy", "matplotlib", "fabio", "pyFAI", "PySide6"]
    missing = []
    vers = []
    for name in deps:
        try:
            mod = importlib.import_module(name)
            # fabio/pyFAI 不暴露 __version__，退回按发行包名查元数据
            ver = getattr(mod, "__version__", None)
            if ver is None:
                from importlib.metadata import PackageNotFoundError, version

                try:
                    ver = version(name)
                except PackageNotFoundError:
                    ver = "?"
            vers.append(f"{name} {ver}")
        except Exception as exc:                       # noqa: BLE001
            missing.append(name)
            report("FAIL", f"依赖缺失：{name}", str(exc))
    if not missing:
        report("OK", "依赖齐全", " / ".join(vers))

    # ── 3. 可编辑安装指向的是不是**这个**文件夹（今天事故的根因）──
    try:
        import xrd_toolkit
        # __file__ = <根>/src/xrd_toolkit/__init__.py → parents[2] 即仓库根
        installed_root = Path(xrd_toolkit.__file__).resolve().parents[2]
        if installed_root == ROOT:
            report("OK", "xrd_toolkit 装的就是本文件夹", str(ROOT))
        else:
            report("FAIL", "装的是**别的副本**（多半是搬家前的旧路径）",
                   f"当前指向 {installed_root}")
            print(f"       {FIX_INSTALL}")
    except ImportError as exc:
        report("FAIL", "import xrd_toolkit 失败（可编辑安装过期/未安装）",
               str(exc))
        print(f"       {FIX_INSTALL}")

    # ── 4. GUI 能否导入（PySide6 在无显示器环境下给 offscreen 兜底）──
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import time

        t0 = time.time()
        from xrd_toolkit.gui import app as gui_app
        assert callable(gui_app.main)
        report("OK", f"GUI 可导入（{time.time() - t0:.1f} s）", "入口 app.main()")
    except Exception as exc:                           # noqa: BLE001
        report("FAIL", "GUI 导入失败", f"{type(exc).__name__}: {exc}")

    # ── 5. 测试与数据（信息项：数据文件被 gitignore，新 clone 没有是正常的）──
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    n_cases = sum(f.read_text(encoding="utf-8").count("def test_")
                  for f in tests)
    if tests:
        report("OK", f"测试文件 {len(tests)} 个，用例 {n_cases} 条")
        print("       跑全量测试：QT_QPA_PLATFORM=offscreen "
              f"{Path(sys.executable).name} -m unittest discover -s tests")
    data = sorted((ROOT / "data").glob("*.tif*"))
    if data:
        report("OK", f"数据文件 {len(data)} 个",
               ", ".join(p.name for p in data[:3]) + ("…" if len(data) > 3 else ""))
    else:
        report("WARN", "data/ 下没有 .tif 数据",
               "数据不入仓库（隐私），换机器要自己拷")

    print()
    if _failed:
        print(f"== {len(_failed)} 项有问题，见上面的 FAIL ==")
        return 1
    print("== 环境正常，可以跑 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
