"""后台任务运行器（gui/tasks.py BackgroundTask）的单元测试。

验证：
  - 函数在后台线程执行、参数透传；
  - 结果/报错跨线程送回主线程，回调在主线程执行；
  - 长驻线程被复用（2026-09-24 起不再是"每任务一条 QThread"，
    那正是 offscreen 那处 GIL/Qt 锁序死锁的来路）；
  - discard() 丢弃回调并等正在跑的任务收尾（关窗口保护），
    排队中的任务则立刻放弃、且**一个都不跑**。

等待方式：回调 + processEvents 轮询。不用 QSignalSpy.wait——
实测当前 PySide6 版本里它的内部事件循环不处理跨线程的排队信号
投递，信号永远"等不到"（后台函数确实执行了，但投递一直没被
处理）；真实 GUI 事件循环完全正常，仅测试等待工具有此怪癖。

"任务收尾"看 `task.is_done()`（跑完/报错/放弃都算）——旧版看的是
`task._thread.isRunning()`，长驻线程模型的线程本来就一直活着，
那个断言不再有意义（也没有可等的收尾）。

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
        self.assertTrue(_wait_until(task.is_done), "结果送达后任务应已收尾")

    def test_error_delivered(self):
        def boom():
            raise ValueError("test boom")

        errors = []
        task = BackgroundTask(boom, on_error=errors.append)
        task.start()
        self.assertTrue(_wait_until(lambda: errors), "报错应在 5 s 内送达")
        self.assertIn("ValueError: test boom", errors[0])
        self.assertTrue(_wait_until(task.is_done))

    def test_arguments_passed_through(self):
        results = []
        task = BackgroundTask(lambda a, b: a + b, 2, 3, on_done=results.append)
        task.start()
        self.assertTrue(_wait_until(lambda: results))
        self.assertEqual(results, [5])
        self.assertTrue(_wait_until(task.is_done))

    def test_fn_runs_in_worker_thread_not_main(self):
        """后台函数确实在别的线程执行（界面线程不能被它占用）。"""
        import threading

        main_tid = threading.get_ident()
        seen = {}
        task = BackgroundTask(
            lambda: seen.setdefault("tid", threading.get_ident()),
            on_done=lambda v: seen.setdefault("done", v))
        task.start()
        self.assertTrue(_wait_until(lambda: "done" in seen))
        self.assertNotEqual(seen["tid"], main_tid, "函数应跑在后台线程")
        self.assertTrue(_wait_until(task.is_done))


class TestDiscard(unittest.TestCase):
    """关窗口保护：discard 丢弃回调并等正在跑的任务收尾。"""

    def test_discard_waits_and_silences_callbacks(self):
        called = []

        def slow():
            time.sleep(0.3)
            return "late result"

        task = BackgroundTask(slow, on_done=called.append)
        task.start()
        task.discard()   # 阻塞直到后台函数返回
        # 排空排队中的结果投递后再断言（discard 只等到"函数返回"那一刻，
        # 之后那次投递还得过一轮事件循环）
        self.assertTrue(_wait_until(task.is_done), "discard 后任务应已收尾")
        self.assertEqual(called, [], "discard 后的回调必须被丢弃")


if __name__ == "__main__":
    unittest.main()

class TestConcurrencyGate(unittest.TestCase):
    """并发闸门（MAX_CONCURRENT_TASKS）：批量任务不许一次全开。

    为什么值得测：一次 81 张 [1D] = 81 个后台任务，每个要吃 ~0.5 GB
    （2048² 图像 + pyFAI 内部副本），全开会把内存打爆（实测 12 张
    6.1 GB）——而并发几乎不加速（pyFAI 吃 CPU/内存带宽）。所以闸门
    是"一次 80 张不卡"的关键，不许被无意改掉。

    实现方式换了（2026-09-24）：闸门现在是**长驻线程的条数**，不再是
    每条任务一条线程 + 信号量。下面同时钉住"峰值不超过上限"和"线程
    被复用"两件事。
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

    def test_worker_threads_are_reused(self):
        """长驻线程：第二批任务复用第一批用过的线程，不新建。

        为什么值得测：这正是那处 offscreen 死锁的修法——旧版每个任务
        新建一条 QThread，线程收尾时要在工作线程里销毁 Python 派生的
        Qt 对象（shiboken 要 GIL），撞上主线程握 GIL 等 Qt 锁的场景就
        死锁（见 scripts/stress_panels.py）。线程被复用 = 那条路径
        不存在了；将来谁改回"每任务一条线程"，这条会红。
        """
        import threading

        from xrd_toolkit.gui.tasks import MAX_CONCURRENT_TASKS as CAP

        def tid():
            return threading.get_ident()

        batches = []
        for _ in range(2):
            seen = []
            tasks = [BackgroundTask(tid, on_done=seen.append)
                     for _ in range(CAP * 2)]
            for t in tasks:
                t.start()
            self.assertTrue(_wait_until(lambda: len(seen) == CAP * 2, 10000),
                            f"只完成 {len(seen)}/{CAP * 2}")
            batches.append(set(seen))
        self.assertLessEqual(len(batches[0]), CAP, "并发线程数不该超过闸门")
        self.assertTrue(batches[1] <= batches[0],
                        f"第二批该复用同一批线程：{batches[0]} vs {batches[1]}")

    def test_queued_task_gives_up_when_discarded(self):
        """关窗口：还在排队的任务**一个都不跑**，且 discard 立刻返回。

        （旧版这条用例没有牙齿：它只用 2 个任务，而闸门上限就是 2 →
        第二个任务根本不在排队、直接开跑，函数里那句 AssertionError
        被 tasks 的异常包装吞掉，测试照样绿。现在用"闸门上限 + 1"个
        任务保证真排队，并**记下 fn 到底被跑过几次**；同时等排队的
        任务确实轮到过（都收尾了）再断言——否则"没跑"可能只是还没
        轮到，等于没测。）
        """
        import threading

        from xrd_toolkit.gui.tasks import MAX_CONCURRENT_TASKS as CAP

        release = threading.Event()
        entered = threading.Event()
        ran = []

        def blocker():
            entered.set()
            release.wait(5)
            return "blocker"

        def should_not_run():
            ran.append(threading.get_ident())
            return "ran"

        busy = [BackgroundTask(blocker) for _ in range(CAP)]
        for t in busy:
            t.start()
        self.assertTrue(entered.wait(3), "第一条没进闸门")
        queued = [BackgroundTask(should_not_run) for _ in range(2)]
        for t in queued:
            t.start()
        time.sleep(0.05)              # 让它们去排队
        t0 = time.time()
        for t in queued:
            t.discard()               # 排队中 → 立刻返回
        dt = time.time() - t0
        release.set()
        for t in busy:
            t.discard()
        self.assertLess(dt, 1.0, f"取消排队任务耗时 {dt:.2f}s，应该立刻返回")
        # 等它们真的轮到过（收尾）——再断言一个都没跑
        self.assertTrue(_wait_until(lambda: all(t.is_done() for t in queued),
                                    10000), "排队的任务该都收尾")
        self.assertEqual(ran, [], "排队中的任务一个都不该真的跑起来")

