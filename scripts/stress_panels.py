#!/usr/bin/env python3
"""面板压力探针：一个进程里"开 N 块面板（带后台积分）→ 关窗"重复 R 轮。

**回归检查**：offscreen 下这里曾经挂死过（2026-09-24 修掉两个机制），
现在正常应当打印 DONE、退出码 0；**它挂住 = 那处问题回来了**。

当时挂死的两个机制（都查到底、都修了）：
  ① **mpl 工具栏构造递归**：`_open_plot_panel → _build_canvas_panel →
     _SlimToolbar.__init__ → NavigationToolbar2QT.__init__` 里无限递归
     （栈 5000+ 帧、100% CPU）。→ 面板工具栏改成自绘的
     `_SlimToolbar`（自己拿四个 QAction，图标仍用 mpl 的 PNG）。
  ② **GIL / Qt 锁序反转**：主线程握 GIL 调 Qt（`QObject::connect`，例如
     `QMdiArea::addSubWindow` 建面板）等一把 Qt 内部锁，而工作线程持着
     那把锁、正销毁 Python 派生的 Qt 对象（shiboken 要 GIL）→ 两边都不
     让。旧版每个任务一条 QThread，线程收尾就要销毁一批对象。→ 后台
     任务运行器改成**长驻工作线程**（见 gui/tasks.py）。
排查时的关键实验（留档）：把 `tasks.py` 的对象销毁关掉，复现率从
6/6 掉到 1/6；单独把 worker 搬回主线程无效；关不关窗无关；
`git worktree` 拉改动前的 `423ac99` 跑同样挂（3/3）——**不是某次改动
引入的**，与内存/负载也无关（挂时内存空闲 12 GB、无 swap）。

用法：
    python scripts/stress_panels.py            # 20 块面板 × 10 轮
    python scripts/stress_panels.py 12 5       # 12 块面板 × 5 轮

环境变量：
    HANG_S=40      faulthandler 超时秒数（卡住时打印栈并以非 0 退出）
    NOCLOSE=1      不关窗（复现机制 ② 的变体：旧版这样 6/6 挂）
    NOSETTLE=1     把开面板循环里的 _settle 换成空操作（排查用的对照）

每轮打一行，"卡在哪一轮"一眼可见；最后一行 DONE = 通过。
"""
import faulthandler
import os
import sys
import time
from pathlib import Path
from unittest import mock

# 项目根 = 本文件所在 scripts/ 的上一级（与 scripts/ 下其他脚本同一惯例）
ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(ROOT / "src"))

faulthandler.dump_traceback_later(
    float(os.environ.get("HANG_S", "40")), exit=True)

import numpy as np                                        # noqa: E402
from PySide6.QtWidgets import QApplication                # noqa: E402

from xrd_toolkit.gui import plot_views as gui_views       # noqa: E402
from xrd_toolkit.gui.app import create_window             # noqa: E402

if os.environ.get("NOSETTLE") == "1":
    # 只影响 _plot_view 里那一处（模块内的名字查找）
    gui_views._settle = lambda window: None

app = QApplication.instance() or QApplication([])

N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 10


def fake(path_str, geom, npt):
    """假积分：不碰真数据，只喂一条三点曲线。"""
    return np.array([0.5, 1.0, 8.5]), np.array([1.0, 2.0, 3.0])


def pump(predicate, timeout_s=10.0):
    end = time.time() + timeout_s
    while time.time() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


keep = []        # NOCLOSE=1 时把窗口留活口（不触发窗口销毁那条路）
t0 = time.time()
for r in range(ROUNDS):
    w = create_window()
    keys = [f"1D|data/s{i}.tif" for i in range(N)]
    with mock.patch.object(gui_views, "_compute_integration", side_effect=fake):
        w.add_files([f"data/s{i}.tif" for i in range(N)])
        w.view_buttons["1D"].click()
        ok = pump(lambda: all(
            getattr(w.plot_docks.get(k), "last_tth", None) is not None
            for k in keys))
    print(f"[{time.time() - t0:6.1f}s] round {r + 1}/{ROUNDS}: "
          f"面板 {len(w.plot_docks)} 算完={ok}", flush=True)
    if os.environ.get("NOCLOSE") == "1":
        keep.append(w)
    else:
        w.close()
        QApplication.processEvents()
print("DONE")
