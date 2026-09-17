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
from PySide6.QtCore import QObject, QThread, Signal, Slot


class _Worker(QObject):
    """在后台线程里跑一个函数；结果/报错通过信号送回主线程。"""

    done = Signal(object)     # 成功：携带返回值
    error = Signal(str)       # 失败：携带报错文字

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args

    @Slot()
    def run(self):
        try:
            result = self._fn(*self._args)
        except Exception as err:
            # 后台线程里任何异常都转成信号，绝不直接在后台崩溃
            self.error.emit(f"{type(err).__name__}: {err}")
        else:
            self.done.emit(result)


class BackgroundTask(QObject):
    """一个后台任务：QThread + _Worker 的组合，替调用方管好清理。

    三个清理连接（Qt 文档标准做法）：
      worker.done/error → thread.quit     函数跑完就让线程事件循环退出
      worker.done/error → worker.deleteLater  工作对象随线程销毁
      thread.finished → thread.deleteLater 线程对象自己销毁
    调用方只需把 task 对象挂在窗口上（如 window._tasks 列表），
    防垃圾回收；结束回调里移除即可。
    """

    done = Signal(object)     # 转发 _Worker.done（测试监听用）
    error = Signal(str)       # 转发 _Worker.error

    def __init__(self, fn, *args, on_done=None, on_error=None):
        super().__init__()
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
        self._on_done_cb = on_done
        self._on_error_cb = on_error

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
        """
        self._on_done_cb = None
        self._on_error_cb = None
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait()
