"""后台任务运行器：把耗时计算挪出界面线程。

类比：界面线程 = 服务员（招待客人、点单上菜），工作线程 = 后厨
（炒菜）。服务员把单子递进厨房就回去继续招待，菜好了后厨按铃
（信号），铃声经 Qt 排队送回服务员手里。要是服务员自己炒菜，
客人就只能干等（界面卡死）——这就是积分/校准必须放后台的原因。

与旧版（**每个任务起一条 QThread**）的区别（2026-09-24，为治一处实测
死锁）：现在是**固定几条长驻工作线程**，任务排队交给它们跑。
为什么必须换：任务收尾要销毁那条 QThread，而销毁一个 Python 派生的
Qt 对象要在**工作线程**里回调进 Python（shiboken 要 GIL）；主线程此刻
可能正握着 GIL 等一把 Qt 内部锁（`QObject::connect`，例如建面板时的
`QMdiArea::addSubWindow`）→ 锁序反转，两边都不让，进程永远停住。
复现脚本 `scripts/stress_panels.py`（把对象销毁关掉，复现率从 6/6
掉到 1/6，指向的就是这条路）。长驻线程不再反复创建/销毁，这条路径
整个不存在了。

用法（**没变**）：
    task = BackgroundTask(fn, *args, on_done=回调, on_error=回调)
    task.start()          # fn(*args) 在后台线程执行
    # 完成时：on_done(返回值) 或 on_error(报错文字) 在主线程被调用；
    # 同时 task.done / task.error 信号发射（测试用 QSignalSpy 监听）

线程安全要点（**没变**）：
  - fn 里绝不能碰任何界面控件（QWidget），只能做纯计算；
  - 结果经信号送回主线程，回调在主线程执行，可以安全更新界面。
"""
import atexit
import threading

from PySide6.QtCore import (QMetaObject, QObject, QThread, Qt, Signal, Slot)

# ══ 并发闸门：批量任务不许一次全开 ═══════════════════════════
# 为什么必须有（2026-09-23 实测，真数据 2048² ×81 张）：
#   * 批量积分 = 每个文件一个后台任务，原样就是"81 个任务同时跑"；
#   * 每个任务要把 2048² 图像读进来、pyFAI 内部再复制几份，
#     **实测峰值 ≈ 0.5 GB/张**（12 张 → 6.1 GB；81 张 → 约 41 GB，
#     机器开始 swap——这就是"一次 80 张就卡"的真凶）；
#   * 而**并发几乎不加速**：复用 integrator 后实测 0.16 s/张（串行）
#     vs 0.25 s/张（3 线程）——pyFAI 吃的是 CPU/内存带宽。
# 闸门现在由**线程条数**直接实现（见下面的长驻线程池）：最多这么多条
# 线程同时在跑，峰值内存 ≈ 1 GB，速度与串行基本相同。
MAX_CONCURRENT_TASKS = 2

# 任务对象在跑完（或被确认放弃）之前不许被 Python 回收：排队中的任务
# 只有 worker 被引用着，而调用方（比如关窗时）会把自己的引用放掉。
_live_tasks = set()

_pool = []                     # 长驻工作线程（懒创建，进程内复用）
_pool_lock = threading.Lock()
_pool_rr = 0                   # 轮转派活的下标


def _pool_threads():
    """懒创建长驻工作线程：最多 MAX_CONCURRENT_TASKS 条，之后一直复用。

    线程只跑自己的事件循环，任务（_Worker.run）以排队调用的形式投进去
    ——先到先跑，天然就是"最多 N 个同时干活"的闸门。
    """
    with _pool_lock:
        while len(_pool) < MAX_CONCURRENT_TASKS:
            t = QThread()
            t.setObjectName(f"xrd-worker-{len(_pool) + 1}")
            t.start()
            _pool.append(t)
        return list(_pool)


def _dispatch():
    """挑一条工作线程（轮转：两条轮流吃，负载大致均匀）。"""
    global _pool_rr
    threads = _pool_threads()
    with _pool_lock:
        t = threads[_pool_rr % len(threads)]
        _pool_rr += 1
    return t


def _shutdown_pool():
    """解释器退出：收起长驻线程（有任务在跑就等它跑完，最多 3 秒）。"""
    for t in _pool:
        try:
            t.quit()
            t.wait(3000)
        except RuntimeError:
            pass    # 线程对象已被销毁：没什么可收的


atexit.register(_shutdown_pool)


class _Worker(QObject):
    """在后台线程里跑一个函数；结果/报错通过信号送回主线程。"""

    done = Signal(object)     # 成功：携带返回值
    error = Signal(str)       # 失败：携带报错文字

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args
        self.cancelled = False    # 关窗口时置位：还没开跑的直接放弃
        self.running = False      # 正在跑（discard 靠它决定要不要等）
        self.finished = threading.Event()   # 跑完/放弃后置位

    @Slot()
    def run(self):
        if self.cancelled:
            # 窗口已在关闭：不干活，但**必须发一次信号**让排队投递收尾
            # （回调已被清空，没人会处理这个结果）
            self.finished.set()
            self.done.emit(None)
            return
        self.running = True
        try:
            try:
                result, err = self._fn(*self._args), None
            except Exception as exc:
                # 后台线程里任何异常都转成信号，绝不直接在后台崩溃
                result = None
                err = f"{type(exc).__name__}: {exc}"
        finally:
            self.running = False
            self.finished.set()
        if err is not None:
            self.error.emit(err)
        else:
            self.done.emit(result)


class BackgroundTask(QObject):
    """一个后台任务：交给长驻工作线程跑，替调用方管好清理。

    生命周期（都发生在**主线程**，见模块说明的死锁原因）：
      __init__        建 worker（归主线程）
      start()         worker moveToThread 到某条长驻线程，排队调用 run
      run 结束        done/error 信号排队回主线程 → 本对象发同名信号、
                      调回调、把任务从 _live_tasks 放掉（此后可被回收）
      discard()       关窗口：丢弃回调 + 标记取消；正在跑的等它跑完

    worker 的销毁发生在主线程（随本对象被回收时），所以"工作线程里销毁
    Python 派生的 Qt 对象"这条死锁路径不会出现。
    """

    done = Signal(object)     # 转发 _Worker.done（测试监听用）
    error = Signal(str)       # 转发 _Worker.error

    def __init__(self, fn, *args, on_done=None, on_error=None):
        super().__init__()
        _live_tasks.add(self)   # 收尾前保持存活（见 _live_tasks 处的说明）
        self._worker = _Worker(fn, *args)
        self._worker.done.connect(self._on_worker_done)
        self._worker.error.connect(self._on_worker_error)
        self._on_done_cb = on_done
        self._on_error_cb = on_error
        self._started = False
        self._done = False

    def start(self):
        """派活：worker 搬进一条长驻线程，排队调用 run（先到先跑）。"""
        self._worker.moveToThread(_dispatch())
        self._started = True
        QMetaObject.invokeMethod(self._worker, "run", Qt.QueuedConnection)

    def is_done(self) -> bool:
        """任务是否已经收尾（跑完 / 报错 / 被取消放弃）——测试与关窗用。"""
        return self._done

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
        self._on_done_cb = None
        self._on_error_cb = None
        self._done = True
        _live_tasks.discard(self)   # 线程已收尾：允许本对象被回收

    def discard(self):
        """窗口关闭时调用：丢弃回调，必要时等后台函数返回。

        - **正在跑**的任务：等它跑完——计算函数无法被中途打断，所以最多
          等"一个任务"的时间（批量时就是当前并行的那两条）；
        - **还没开跑**（排队中）的：**立刻返回**——它轮到自己时会看见
          cancelled 直接放弃。旧版这里是 quit()+wait() 干等（实测排队
          任务要等 4.8 s），现在不等了，关窗更快。
        两种情况回调都被丢弃：不会有人再触碰已经关掉的窗口。
        """
        self._on_done_cb = None
        self._on_error_cb = None
        # 无条件置位：正在跑的任务已经过了检查点（它照常跑完），
        # 还在排队的任务被调用时立刻放弃。
        self._worker.cancelled = True
        if self._started and self._worker.running:
            self._worker.finished.wait()
