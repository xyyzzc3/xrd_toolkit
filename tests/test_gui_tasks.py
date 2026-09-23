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

class TestConcurrencyGate(unittest.TestCase):
    """并发闸门（MAX_CONCURRENT_TASKS）：批量任务不许一次全开。

    为什么值得测：一次 81 张 [1D] = 81 个后台任务，每个要吃 ~0.5 GB
    （2048² 图像 + pyFAI 内部副本），全开会把内存打爆（实测 12 张
    6.1 GB）——而并发几乎不加速（pyFAI 吃 CPU/内存带宽）。所以闸门
    是"一次 80 张不卡"的关键，不许被无意改掉。
    """

    def test_at_most_max_concurrent_run_at_once(self):
        import threading

        lock = threading.Lock()
        state = {"running": 0, "peak": 0, "started": 0}

        def job(i):
            with lock:
                state["running"] += 1
                state["started"] += 1
                state["peak"] = max(state["peak"], state["running"])
            time.sleep(0.05)
            with lock:
                state["running"] -= 1
            return i

        n = 6
        done = []
        tasks = [BackgroundTask(job, i, on_done=done.append)
                 for i in range(n)]
        for t in tasks:
            t.start()
        self.assertTrue(_wait_until(lambda: len(done) == n, 10000),
                        f"只完成 {len(done)}/{n}")
        from xrd_toolkit.gui.tasks import MAX_CONCURRENT_TASKS as CAP
        self.assertLessEqual(state["peak"], CAP,
                             f"同时跑了 {state['peak']} 个（上限 {CAP}）")
        self.assertEqual(sorted(done), list(range(n)), "结果都回来了")

    def test_queued_task_gives_up_when_discarded(self):
        """关窗口：还在排队等闸门的任务立刻放弃（否则 81 张排队时
        关窗要等整批跑完）。"""
        import threading

        release = threading.Event()
        entered = threading.Event()

        def blocker():
            entered.set()
            release.wait(5)
            return "blocker"

        def should_not_run():
            raise AssertionError("排队中的任务被取消了，不该真的跑起来")

        t1 = BackgroundTask(blocker)
        t2 = BackgroundTask(should_not_run)
        t3 = BackgroundTask(should_not_run)
        t1.start()
        self.assertTrue(entered.wait(3), "第一个任务没进闸门")
        t2.start()
        t3.start()
        time.sleep(0.05)             # 让 t2/t3 去排队
        t0 = time.time()
        t2.discard()                 # 排队中 → 立刻返回
        t3.discard()
        dt = time.time() - t0
        release.set()
        t1.discard()
        self.assertLess(dt, 1.0, f"取消排队任务耗时 {dt:.2f}s，应该立刻返回")

