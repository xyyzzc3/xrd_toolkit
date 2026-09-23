"""后台任务运行器：把耗时计算挪出界面线程。

类比：界面线程 = 服务员（招待客人、点单上菜），工作线程 = 后厨
（炒菜）。服务员把单子递进厨房就回去继续招待，菜好了后厨按铃
（信号），铃声经 Qt 排队送回服务员手里。要是服务员自己炒菜，
客人就只能干等（界面卡死）——这就是积分/校准必须放后台的原因。

用法：
    task = BackgroundTask(fn, *args, on_done=回调, on_error=回调)
    task.start()          # fn(*args) 在后台线程执行
    # 完成时：on_done(返回值) 或 on_error(报错文字) 在主线程被调用；
    # 同时 task.done / task.error 信号发射（测试用 QSignalSpy 监听）

线程安全要点：
  - fn 里绝不能碰任何界面控件（QWidget），只能做纯计算；
  - 结果经信号送回主线程，回调在主线程执行，可以安全更新界面。
"""
import threading

from PySide6.QtCore import QObject, QThread, Signal, Slot

# 模块级引用：线程没真正结束前，任务对象不许被 Python 回收。
# 调用方在回调里会把 task 从自己的列表移除（window._tasks），但
# 此刻线程事件循环往往还没退出（quit 是排队送达的）；包装对象若
# 先被 GC，C++ 的 QThread 会在运行中被销毁 → Qt 直接 abort。等
# finished 送达（事件循环处理）再放手，这个窗口期就安全了。
_live_tasks = set()

# ══ 并发闸门：批量任务不许一次全开 ═══════════════════════════
# 为什么必须有（2026-09-23 实测，真数据 2048² ×81 张）：
#   * 批量积分 = 每个文件一个后台任务，原样就是"81 个任务同时跑"；
#   * 每个任务要把 2048² 图像读进来、pyFAI 内部再复制几份，
#     **实测峰值 ≈ 0.5 GB/张**（12 张 → 6.1 GB；81 张 → 约 41 GB，
#     机器开始 swap——这就是"一次 80 张就卡"的真凶）；
#   * 而**并发几乎不加速**：复用 integrator 后实测 0.16 s/张（串行）
#     vs 0.25 s/张（3 线程）——pyFAI 吃的是 CPU/内存带宽。
# 所以限额取 2：峰值内存 ≈ 1 GB，速度与串行基本相同。
MAX_CONCURRENT_TASKS = 2
_task_slots = threading.Semaphore(MAX_CONCURRENT_TASKS)


class _Worker(QObject):
    """在后台线程里跑一个函数；结果/报错通过信号送回主线程。"""

    done = Signal(object)     # 成功：携带返回值
    error = Signal(str)       # 失败：携带报错文字

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args
        self.cancelled = False    # 关窗口时置位：排队中的任务直接放弃

    @Slot()
    def run(self):
        # 重活先进闸门（见模块说明的实测数字）：批量时并发压在
        # MAX_CONCURRENT_TASKS 内，峰值内存才不会随文件数线性上涨。
        with _task_slots:
            if self.cancelled:
                # 窗口已在关闭：不干活，但**必须发一次信号**让线程退出
                # （否则 discard 的 wait() 会一直等下去）。此时回调已被
                # 清空，没人会处理这个结果。
                self.done.emit(None)
                return
            try:
                result = self._fn(*self._args)
            except Exception as err:
                # 后台线程里任何异常都转成信号，绝不直接在后台崩溃
                self.error.emit(f"{type(err).__name__}: {err}")
                return
        self.done.emit(result)


class BackgroundTask(QObject):
    """一个后台任务：QThread + _Worker 的组合，替调用方管好清理。

    清理连接（Qt 文档标准做法）：
      worker.done/error → thread.quit     函数跑完就让线程事件循环退出
      worker.done/error → worker.deleteLater  工作对象随线程销毁
      thread.finished → thread.deleteLater 线程对象自己销毁
      thread.finished → self._release     线程真结束后才允许任务被回收
    调用方照常把 task 挂在自己的列表里（如 window._tasks，回调里
    移除）——挂住是为了防回收，_live_tasks 负责补上回调移除后到
    线程真正退出前的窗口期。
    """

    done = Signal(object)     # 转发 _Worker.done（测试监听用）
    error = Signal(str)       # 转发 _Worker.error

    def __init__(self, fn, *args, on_done=None, on_error=None):
        super().__init__()
        _live_tasks.add(self)   # 线程结束前保持存活（见模块说明）
        self._thread = QThread()
        self._worker = _Worker(fn, *args)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.done.connect(self._on_worker_done)
        self._worker.error.connect(self._on_worker_error)
        # 清理连接（顺序见类文档）
        self._worker.done.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._worker.done.connect(self._worker.deleteLater)
        self._worker.error.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._release)
        self._on_done_cb = on_done
        self._on_error_cb = on_error

    def _release(self):
        """线程事件循环已退出 → 允许任务对象被回收。"""
        _live_tasks.discard(self)

    def start(self):
        self._thread.start()

    # worker 在后台线程发射信号 → 这两个槽属于主线程的 BackgroundTask，
    # Qt 自动排队投递，所以槽内可以安全触碰界面
    @Slot(object)
    def _on_worker_done(self, result):
        cb = self._on_done_cb
        self._finish()
        self.done.emit(result)
        if cb:
            cb(result)

    @Slot(str)
    def _on_worker_error(self, msg):
        cb = self._on_error_cb
        self._finish()
        self.error.emit(msg)
        if cb:
            cb(msg)

    def _finish(self):
        self._thread.quit()
        self._on_done_cb = None
        self._on_error_cb = None

    def discard(self):
        """窗口关闭时调用：丢弃回调并等后台函数返回。

        不等待就关窗口，线程会在运行中被销毁（Qt 直接 abort）；
        计算函数本身无法被中途打断，所以这里阻塞最多等于
        剩余计算时间（典型积分几秒钟）。
        **已经在排队等闸门的任务**（批量时大部分都是）会被标记
        cancelled：拿到闸门后立刻放弃，所以关窗不用等完整批跑完
        （81 张排队时这一条很关键）。
        """
        self._on_done_cb = None
        self._on_error_cb = None
        # 无条件置位：正在跑的任务已经过了检查点（它照常跑完），
        # 还在排队等闸门的任务拿到闸门后立刻放弃。
        self._worker.cancelled = True
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait()
