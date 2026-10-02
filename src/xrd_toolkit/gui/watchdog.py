"""卡死 / 崩溃现场记录：出事了让程序自己留证据，别靠猜。

两套机制：
  * **卡死**（本文件下半部）：界面线程停摆超过阈值 → 所有线程的栈写进
    `outputs/hang-*.txt`；
  * **崩溃**（install_crash_dump）：SIGSEGV/SIGABRT/SIGBUS/SIGFPE → 所有
    线程的 Python 栈追加进 `outputs/crash-watch.txt`（2026-10-03 加：
    用户在批量处理时闪退过一次，系统崩溃报告只有 C 栈，Python 那一层
    是黑的）。

卡死那套的由来：

为什么要有（2026-10-01 用户："不点像素尺寸，点击这几个出图的按键会卡死"）：
本地（离屏）把那条路径压了几十次都复现不出来，而"卡死"这种事**猜不得**
——猜错方向改半天，下次照卡。所以让程序自己留证据：真卡住时，栈里写着
那一刻每个线程停在哪一行。

怎么工作：
  * 界面线程挂一个 QTimer（心跳），每次触发就把计数 +1；
  * 一条守护线程每 0.5 s 看一眼计数：超过 STALL_SECONDS 没动 = 界面线程
    停摆 → 用 faulthandler 把所有线程的 Python 栈写进
    `outputs/hang-<时间>.txt`（同时打到 stderr，PyCharm 控制台也看得见）；
  * 恢复正常后再写一条"停了多久"，同一段停摆只dump一次（不刷屏）；
  * 窗口真正关上（closeEvent 没被取消）或销毁 → 守护线程跟着退，不留
    残留监控（测试套件每用例一个窗口，不退就会一起刷栈快照）。

它**只记录、不干预**：不杀任务、不弹窗、不改任何状态——现场越干净越好。
真正干活的库有没有释放 GIL、卡在 Qt 还是 numpy，栈里一眼能看出来。
"""
import faulthandler
import os
import threading
import time
from pathlib import Path

from xrd_toolkit import paths

STALL_SECONDS = 8.0     # 停摆超过这么久才写现场（正常的大动作：开 2048² 面板 ~1.5 s）
POLL_SECONDS = 0.5
REDUMP_SECONDS = 30.0   # 一直没恢复 → 每这么久再补一份（栈可能在变，别只留第一帧）
# 现场写到哪：钉在项目根 outputs（以前是相对路径，跟着进程工作目录跑——
# PyCharm 把工作目录设成源码子目录时，现场文件会写进源码树，2026-10-02
# 统一到 paths）。测试在建窗口前把它换成临时目录。
OUT_DIR = paths.OUTPUTS_DIR
_seq = 0                    # 窗口序号：同一进程里多个看门狗写不同文件（同秒也不撞）

# 说明：心跳与"上次写现场时的停摆时长"都放在**每个窗口自己的 state dict** 里
# （2026-10-01 踩到：用模块级全局时，多个窗口的看门狗互相踩，后来的窗口不再
# 写现场——测试套件里每个用例一个窗口，一踩一个准）。


_crash_watch = None    # 崩溃现场的日志文件句柄（faulthandler 要求一直开着）


def install_crash_dump() -> None:
    """装上致命信号现场（进程内只装一次；由 start() 顺带调用）。

    为什么要（2026-10-03）：用户在批量处理时闪退过一次（SIGSEGV，崩在
    PySide 绑定层的对象析构里）；macOS 的崩溃报告只有 C 栈，Python 那一层
    看不见。"卡死"那套现场已经证明有用，崩溃也照办——致命信号时把**所有
    线程的 Python 栈**追加进 `outputs/crash-watch.txt`。

    用 `faulthandler.enable(file=…)` 而不是 `register(…：SIGSEGV 这类
    致命信号是 enable() 专用的，register 会直接 RuntimeError（第一版
    踩到：异常被吞 → 每个窗口写一行头、还每次都漏一个文件句柄）。
    enable 的处理器会链到默认处理——系统崩溃报告与进程终止照旧，这里
    只是多加一份 Python 视角。

    留现场失败不能挡住程序启动：整段吞异常，且无论成败都记下"已经装过"。
    """
    global _crash_watch
    if _crash_watch is not None:
        return
    import faulthandler
    try:
        out = Path(OUT_DIR)
        out.mkdir(parents=True, exist_ok=True)
        fh = open(out / "crash-watch.txt", "a", encoding="utf-8")
        fh.write(f"# ── 运行开始 {time.strftime('%Y-%m-%d %H:%M:%S')}"
                 f"（PID {os.getpid()}）；本段之后若出现调用栈，就是这次"
                 f"运行的崩溃现场\n")
        fh.flush()
        faulthandler.enable(file=fh, all_threads=True)
        _crash_watch = fh        # 保活：文件在进程活着期间必须一直开着
    except Exception:            # noqa: BLE001
        _crash_watch = True      # 装不上也别每次重试、别反复写头


def start(window, out_dir: Path = None) -> None:
    """挂上心跳与守护线程（window 建好后调一次）。

    **防重复**（2026-10-01 踩到）：测试套件里每个用例都建一次窗口，没有这道
    闸就会在一个进程里堆出上百条看门狗线程——现场文件里 100 条栈全是看门狗
    自己，主线程反而被挤没了（用户报卡死时，那份文件毫无用处）。
    """
    install_crash_dump()   # 进程级、幂等：致命信号也留一份 Python 视角的现场
    if getattr(window, "_hang_watchdog_on", False):
        return
    window._hang_watchdog_on = True
    global _seq
    _seq += 1
    seq = _seq
    from PySide6.QtCore import QEvent, QObject, QTimer, Signal

    out = Path(out_dir) if out_dir is not None else Path(OUT_DIR)

    class _Note(QObject):
        """把守护线程的日志行排队送进日志区（跨线程只许走信号）。"""
        note = Signal(str)

    sender = _Note(window)
    sender.note.connect(lambda text: _log_safe(window, text))
    window._hang_note = sender

    state = {"beat": 0, "dumped_at": None, "lock": threading.Lock(),
             "seq": seq}
    # 暴露给测试：心跳计数在这里，测试可以自己打拍子（不依赖 Qt 定时器
    # 在 processEvents 里到底跳不跳——那点不确定性让一条用例抖过一次）
    window._hang_state = state
    timer = QTimer(window)
    timer.setInterval(int(POLL_SECONDS * 1000))
    timer.timeout.connect(
        lambda: state.__setitem__("beat", state["beat"] + 1))
    timer.start()

    stop = threading.Event()
    try:
        # 窗口销毁 → 守护线程跟着退：测试套件每个用例建一次窗口，不退就是
        # 一堆常驻线程（还各自往 outputs/ 写现场，成了垃圾场）
        window.destroyed.connect(lambda *_: stop.set())
    except Exception:                                        # noqa: BLE001
        pass

    class _CloseWatcher(QObject):
        """窗口真正关上（没被"存不存盘"对话框取消）→ 停表。

        为什么要有（2026-10-02 实测）：关掉的窗口没有界面可停摆，但监控
        线程还在；测试套件每个用例建一次窗口，残留监控越积越多，主线程
        一卡，几百个监控就一起写全线程栈快照——一次全量测试写出 2000+
        份，套件被拖到一小时都跑不完。singleShot 推迟一拍再看：closeEvent
        被对话框取消时窗口仍然可见，这时不停，真程序继续有看门狗。
        """
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Close:
                QTimer.singleShot(
                    0, lambda: stop.set() if not window.isVisible() else None)
            return False

    window._hang_close_watcher = _CloseWatcher(window)
    window.installEventFilter(window._hang_close_watcher)

    t = threading.Thread(target=_watch, args=(window, out, stop, state),
                         daemon=True, name="xrd-hang-watchdog")
    t.start()


def _log_safe(window, text: str) -> None:
    """把一行写进日志区（在主线程被调用；日志区不在就只忽略）。"""
    try:
        from xrd_toolkit.gui.panel_state import _log
        _log(window, text)
    except Exception:                                        # noqa: BLE001
        pass


def _watch(window, out: Path, stop: threading.Event, state: dict) -> None:
    """守护线程：界面线程停摆 → 写现场（状态都在 state 里，各窗口互不干扰）。"""
    last_seen = state["beat"]
    stalled_since = None
    while not stop.is_set():
        time.sleep(POLL_SECONDS)
        now = state["beat"]
        if now != last_seen:                     # 还在动
            if stalled_since is not None:
                # 只有**够到阈值**的那次停顿才值得写一行（启动、开 2048² 面板
                # 这类正常的大动作也会停 1 s 上下，逐条报就是刷屏）
                if time.time() - stalled_since >= STALL_SECONDS:
                    _note(window, out, f"界面无响应 "
                                       f"{time.time() - stalled_since:.1f} "
                                       "秒后已恢复")
                stalled_since = None
            with state["lock"]:
                state["dumped_at"] = None
            last_seen = now
            continue
        if stalled_since is None:
            stalled_since = time.time()
        stalled = time.time() - stalled_since
        if stalled < STALL_SECONDS:
            continue
        with state["lock"]:
            if state["dumped_at"] is not None \
                    and stalled < state["dumped_at"] + REDUMP_SECONDS:
                continue
            state["dumped_at"] = stalled
        path = out / (f"hang-{os.getpid()}-{state['seq']}-"
                      f"{time.strftime('%Y%m%d-%H%M%S')}.txt")
        try:
            out.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"# 界面无响应 {stalled:.1f} 秒（阈值 "
                         f"{STALL_SECONDS:g} 秒）\n"
                         f"# 每个线程停在哪儿，都在这份调用栈里\n")
                faulthandler.dump_traceback(file=fh, all_threads=True)
        except Exception as err:                             # noqa: BLE001
            _note(window, out, f"卡死现场写入失败：{err}")
            continue
        _note(window, out, f"界面无响应 {stalled:.1f} 秒——已把现场存到 {path}"
                           f"（含各线程调用栈，可交给开发者定位）")


def _note(window, out: Path, text: str) -> None:
    """记一行（stderr + 日志区）。

    日志区经**信号**送过去（本函数跑在守护线程里，直接碰 QWidget 是跨线程
    改界面——那是 Qt 明令禁止的）。信号跨线程是排队投递，安全。
    """
    print(text, flush=True)
    sender = getattr(window, "_hang_note", None)
    if sender is not None:
        try:
            sender.note.emit(text)
        except Exception:                                    # noqa: BLE001
            pass
