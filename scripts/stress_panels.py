#!/usr/bin/env python3
"""面板压力探针：一个进程里"开 N 块面板 → 关窗"重复 R 轮，看会不会挂死。

**这是已知问题的复现脚本**（不是回归检查——它按设计会挂）：
offscreen 环境下，同一进程里建到一定数量的面板（实测 20 块一窗、第 5 轮
左右 ≈ 累计 80 块）之后，再建下一块面板会**偶发**在 matplotlib 的
`NavigationToolbar2QT.__init__` 里无限递归（栈深 5000+ 帧、100% CPU、
永不返回；`sample` / faulthandler 都能抓到）。cocoa（真窗口）下没复现，
真程序开 81 块面板也正常。

排查记录（2026-09-24）：
  * **不是本项目改动引入的**：用 `git worktree` 拉改动前的 `423ac99`
    跑同一个探针，同样挂（3/3）；把开面板循环里的 `_settle` 换成空操作
    也照样挂（NOSETTLE=1）。
  * 与环境负载无关：挂的几轮里内存充裕（vm_stat 空闲 12 GB、无 swap）。
  * 触发概率：同样参数 10 次里挂 3–10 次（同一进程内累计新建的面板越
    多越容易挂）；单轮 ≤16 块没挂过。
  * 栈里的落点固定：`_open_plot_panel → _build_canvas_panel →
    _SlimToolbar.__init__ → NavigationToolbar2QT.__init__`（mpl 在建
    工具栏 action 的那段）。面板工具栏其实是隐藏的、只当 action 仓库用
    （见 plot_panels._SlimToolbar 的说明）——真要根治，方向是"别用 mpl
    的工具栏，自己拿 4 个 QAction"，但要动 `content.toolbar._actions` /
    `_nav_stack` 这些既有契约，得先跟用户确认。

用法：
    python scripts/stress_panels.py            # 20 块面板 × 10 轮
    python scripts/stress_panels.py 12 5       # 12 块面板 × 5 轮

环境变量：
    HANG_S=40      faulthandler 超时秒数（挂死时打印栈并以非 0 退出）
    NOSETTLE=1     把开面板循环里的 _settle 换成空操作（对照实验用）

挂死时 faulthandler 会打印主线程栈；每轮打一行，"挂在哪一轮"一眼可见。
正常结束时最后一行是 DONE（退出码 0）。
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
    w.close()
    QApplication.processEvents()
print("DONE")
