"""后台任务运行器（gui/tasks.py BackgroundTask）的单元测试。

验证：
  - 函数在后台线程执行、参数透传；
  - 结果/报错跨线程送回主线程，回调在主线程执行；
  - 任务结束线程退出；discard() 丢弃回调并等线程收尾（关窗口保护）。

等待方式：回调 + processEvents 轮询。不用 QSignalSpy.wait——
实测当前 PySide6 版本里它的内部事件循环不处理跨线程的排队信号
投递，信号永远"等不到"（后台函数确实执行了，但投递一直没被
处理）；真实 GUI 事件循环完全正常，仅测试等待工具有此怪癖。

运行：python -m unittest discover -s tests -v
"""
import os
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication

from xrd_toolkit.gui.tasks import BackgroundTask

_app = QApplication.instance() or QApplication([])


def _wait_until(predicate, timeout_ms=5000):
    """轮询处理事件直到条件成立（后台结果靠事件循环排队投递）。"""
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


class TestBackgroundTask(unittest.TestCase):
    """结果 / 报错 / 回调路径。"""

    def test_result_delivered(self):
        results = []
        task = BackgroundTask(lambda: 42, on_done=results.append)
        task.start()
        self.assertTrue(_wait_until(lambda: results), "结果应在 5 s 内送达")
        self.assertEqual(results, [42], "on_done 回调应收到返回值")
        self.assertTrue(_wait_until(lambda: not task._thread.isRunning()),
                        "结果送达后线程应退出")

    def test_error_delivered(self):
        def boom():
            raise ValueError("test boom")

        errors = []
        task = BackgroundTask(boom, on_error=errors.append)
        task.start()
        self.assertTrue(_wait_until(lambda: errors), "报错应在 5 s 内送达")
        self.assertIn("ValueError: test boom", errors[0])
        self.assertTrue(_wait_until(lambda: not task._thread.isRunning()))

    def test_arguments_passed_through(self):
        results = []
        task = BackgroundTask(lambda a, b: a + b, 2, 3, on_done=results.append)
        task.start()
        self.assertTrue(_wait_until(lambda: results))
        self.assertEqual(results, [5])
        self.assertTrue(_wait_until(lambda: not task._thread.isRunning()))

    def test_fn_runs_in_worker_thread_not_main(self):
        """后台函数确实在别的线程执行（界面线程不能被它占用）。"""
        import threading
        from PySide6.QtCore import QThread

        main_tid = threading.get_ident()
        seen = {}
        task = BackgroundTask(
            lambda: seen.setdefault("tid", threading.get_ident()),
            on_done=lambda v: seen.setdefault("done", v))
        task.start()
        self.assertTrue(_wait_until(lambda: "done" in seen))
        self.assertNotEqual(seen["tid"], main_tid, "函数应跑在后台线程")
        # 等线程真正退出再放手：直接放手曾撞上 GC 抢先销毁运行中
        # 的 QThread → Qt abort（tasks.py 的 _live_tasks 也是防这个）
        self.assertTrue(_wait_until(lambda: not task._thread.isRunning()))


class TestDiscard(unittest.TestCase):
    """关窗口保护：discard 丢弃回调并等待线程结束。"""

    def test_discard_waits_and_silences_callbacks(self):
        called = []

        def slow():
            time.sleep(0.3)
            return "late result"

        task = BackgroundTask(slow, on_done=called.append)
        task.start()
        task.discard()   # 阻塞直到后台函数返回
        self.assertFalse(task._thread.isRunning())
        QApplication.processEvents()   # 排空可能已排队的结果投递
        self.assertEqual(called, [], "discard 后的回调必须被丢弃")


if __name__ == "__main__":
    unittest.main()
