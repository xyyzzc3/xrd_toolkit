#!/usr/bin/env python3
"""跑单元测试，**带看门狗**：卡住不返回时打印栈并以非 0 退出。

为什么要有它（2026-09-24）：offscreen 测试环境里存在一处 PySide6 侧的
锁序反转——主线程握着 GIL 调 Qt（例如 QMdiArea.addSubWindow 里的
QObject::connect）等一把 Qt 内部锁，而工作线程正持着那把锁、要等 GIL
（它正在销毁一个 Python 派生的 Qt 对象，QObject::~QObject →
disconnectNotify → PyGILState_Ensure）。两边都不让，进程就永远停住
（实测概率不低，复现脚本 scripts/stress_panels.py，那里有完整排查记录）。
`python -m unittest discover -s tests` 遇到它只会静默挂着，看不出卡在
哪个用例；本脚本挂的是 faulthandler 的定时器，会打印**当前所有线程的
Python 栈**再退出，一眼能看出卡点。

2026-10-30 起支持两档：
    --fast        只跑快档（跳过 test_gui_integration*，约 3 分钟；
                  再加 --jobs 8 约 1.5 分钟）
    --jobs N      按测试文件分进程并行跑（GUI 集成套件可从 ~64 分钟
                  压到 15–20 分钟；每个文件一个进程，互不共享状态）

用法：
    python scripts/run_tests.py                    # 全量，单进程，看门狗 45 分钟
    python scripts/run_tests.py --fast             # 日常快档（单测 + 静态护栏）
    python scripts/run_tests.py --jobs 6           # 全量并行（推荐 6 个进程）
    python scripts/run_tests.py -t 600             # 看门狗 10 分钟
    python scripts/run_tests.py -p "test_stage*"   # 只跑某些文件

退出码：0 = 全过；1 = 有用例失败；非 0/1 = 看门狗触发（卡住了）。
"""
import argparse
import faulthandler
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# --fast 跳过的模块前缀：GUI 集成套件（594 条，占全量约 99% 时间）。
# 不带下划线，旧的单文件 test_gui_integration.py 和拆分后的
# test_gui_integration_*.py 都覆盖；静态护栏 test_gui_static_guards.py
# 不匹配这个前缀，留在快档里。
FAST_SKIP_PREFIXES = ("test_gui_integration",)


def _test_modules(pattern: str) -> list:
    return sorted(p for p in (ROOT / "tests").glob(pattern)
                  if p.is_file() and p.suffix == ".py"
                  and p.name != "__init__.py")


def _load_module(module: str):
    return unittest.defaultTestLoader.loadTestsFromName(module)


def _run_one_module(module: str, timeout: float, verbose: bool) -> int:
    """子进程入口：只跑一个模块（--jobs 的 worker 用它）。"""
    faulthandler.dump_traceback_later(timeout, exit=True)
    suite = _load_module(module)
    result = unittest.TextTestRunner(verbosity=2 if verbose else 1).run(suite)
    return 0 if result.wasSuccessful() else 1


def _run_parallel(modules: list, args) -> int:
    """一个测试文件一个子进程，最多 args.jobs 个同时跑。"""
    def one(module):
        cmd = [sys.executable, str(Path(__file__).resolve()),
               "--only", module, "-t", str(args.timeout)]
        if args.verbose:
            cmd.append("-v")
        with tempfile.NamedTemporaryFile(
                prefix=f"xrd_test_{module}_", suffix=".log",
                delete=False) as fh:
            log_path = fh.name
            t0 = time.time()
            try:
                proc = subprocess.run(
                    cmd, stdout=fh, stderr=subprocess.STDOUT,
                    cwd=str(ROOT), timeout=args.timeout + 60)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = 124
            return module, code, log_path, time.time() - t0

    failed = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        futures = [ex.submit(one, m) for m in modules]
        for fut in as_completed(futures):
            module, code, log_path, dt = fut.result()
            print(f"[{'OK  ' if code == 0 else 'FAIL'}] {module}  {dt:.1f} s",
                  flush=True)
            if code != 0:
                failed.append((module, log_path))
    print(f"\n并行 {len(modules)} 个文件 / {args.jobs} 进程，"
          f"{time.time() - t0:.1f} s")
    if failed:
        for module, log_path in failed:
            print(f"\n===== {module} 失败，日志尾部 =====")
            try:
                lines = Path(log_path).read_text(
                    encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []
            print("\n".join(lines[-100:]))
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="带看门狗的全量测试")
    # 默认 2700 秒（2026-10-04 从 1800 再调上来）：套件已经涨到 734 条、
    # 实测 1739 秒——30 分钟只剩 60 秒余量，当天连着两次"跑完 795 个点
    # 但超时被杀"，两回转储都拍在 create_window 的正常构件路径上（不同
    # 位置），是**误报**不是死锁。看门狗宽了不会漏掉真死锁：真死锁永远
    # 跑不完、特征是 0% CPU（`ps -o %cpu` 一眼可辨，脚本也打印全线程栈）。
    ap.add_argument("-t", "--timeout", type=float, default=2700.0,
                    help="每个文件/整轮的看门狗秒数（默认 2700）")
    ap.add_argument("-p", "--pattern", default="test*.py",
                    help="文件名通配（默认 test*.py）")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--fast", action="store_true",
                    help="跳过 test_gui_integration* 集成套件（其余约 1 分钟）")
    ap.add_argument("--jobs", type=int, default=1,
                    help="按测试文件并行进程数（默认 1；推荐 6）")
    ap.add_argument("--only", default=None,
                    help=argparse.SUPPRESS)   # 并行 worker 内部使用
    args = ap.parse_args()

    if args.only:
        return _run_one_module(args.only, args.timeout, args.verbose)

    files = _test_modules(args.pattern)
    if args.fast:
        files = [p for p in files
                 if not p.stem.startswith(FAST_SKIP_PREFIXES)]
    modules = [p.stem for p in files]
    if not modules:
        print("没有匹配的测试文件")
        return 1

    if args.jobs > 1:
        return _run_parallel(modules, args)

    # 看门狗：到点打印所有线程的 Python 栈并直接退出（不会拖着不返回）
    faulthandler.dump_traceback_later(args.timeout, exit=True)
    suite = unittest.TestSuite()
    for module in modules:
        suite.addTests(_load_module(module))
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(
        suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    # 从仓库根跑：能 import 到 tests/ 与 src/；测试模块直接按文件名加载
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    sys.exit(main())
