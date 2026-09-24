#!/usr/bin/env python3
"""跑全量单元测试，**带看门狗**：卡住不返回时打印栈并以非 0 退出。

为什么要有它（2026-09-24）：offscreen 测试环境里存在一处 PySide6 侧的
锁序反转——主线程握着 GIL 调 Qt（例如 QMdiArea.addSubWindow 里的
QObject::connect）等一把 Qt 内部锁，而工作线程正持着那把锁、要等 GIL
（它正在销毁一个 Python 派生的 Qt 对象，QObject::~QObject →
disconnectNotify → PyGILState_Ensure）。两边都不让，进程就永远停住
（实测概率不低，复现脚本 scripts/stress_panels.py，那里有完整排查记录）。
`python -m unittest discover -s tests` 遇到它只会静默挂着，看不出卡在
哪个用例；本脚本挂的是 faulthandler 的定时器，会打印**当前所有线程的
Python 栈**再退出，一眼能看出卡点。

用法：
    python scripts/run_tests.py               # 全量，看门狗 20 分钟
    python scripts/run_tests.py -t 600        # 看门狗 10 分钟
    python scripts/run_tests.py -p "test_stage*"    # 只跑某些文件

退出码：0 = 全过；1 = 有用例失败；非 0/1 = 看门狗触发（卡住了）。
"""
import argparse
import faulthandler
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="带看门狗的全量测试")
    ap.add_argument("-t", "--timeout", type=float, default=1200.0,
                    help="看门狗秒数（默认 1200）")
    ap.add_argument("-p", "--pattern", default="test*.py",
                    help="文件名通配（默认 test*.py）")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    # 看门狗：到点打印所有线程的 Python 栈并直接退出（不会拖着不返回）
    faulthandler.dump_traceback_later(args.timeout, exit=True)

    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "tests"), pattern=args.pattern)
    result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(
        suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    # 与 README 里那条命令等价：从仓库根跑，能 import 到 tests/ 与 src/
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
