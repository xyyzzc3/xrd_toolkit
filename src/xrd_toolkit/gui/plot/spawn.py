"""单文件视图的后台任务与完成回调：启动、进度记账、画回面板。

2026-10-22 从 plot_views.py 原样搬出（只改结构，不动逻辑）。
plot_views 再导出；_batch_step 对 _reload_deferred 用函数内延迟导入破环。
"""
import time
from pathlib import Path

from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui.panel_state import _content, _log, _set_focus
from xrd_toolkit.gui.plot.curves import _consume_data_params
from xrd_toolkit.gui.plot.drawing import (
    _draw_1d, _draw_2d, _draw_profile, _draw_waterfall)
from xrd_toolkit.gui.plot.workers import _compute_integration
from xrd_toolkit.gui.tasks import BackgroundTask
from xrd_toolkit.services import stage_cache


def _spawn_task(window: QMainWindow, key: str, worker, args: tuple,
                on_done, on_error) -> None:
    """启动后台任务：worker(*args) 在线程里跑，done/error 回调回主线程。

    _spawn（1D 积分）的本体抽出来通用化：新视图（2D/剖面/瀑布）
    换 worker 与回调即可，三条规则同一份——任务引用挂在
    window._tasks 防垃圾回收、每面板只认最新任务（_latest_task）、
    收尾清登记（不残留已完成任务）。task 变量在闭包外定义、闭包
    内只引用：done/error 回调在任务结束时才被调用，那时 task 早
    已完成赋值。
    """
    task = None

    def done(result):
        window._tasks.remove(task)
        on_done(window, key, task, result)
        # 收尾后再清登记：过期检查（回调里对比 _latest_task）要先看得到自己
        if window._latest_task.get(key) is task:
            del window._latest_task[key]   # 不残留已完成任务（防涨爆）

    def error(msg):
        window._tasks.remove(task)
        on_error(msg)
        if window._latest_task.get(key) is task:
            del window._latest_task[key]

    task = BackgroundTask(worker, *args, on_done=done, on_error=error)
    window._latest_task[key] = task   # 每面板只认最新任务（防旧结果覆盖）
    window._tasks.append(task)
    task.start()

def _spawn(window: QMainWindow, path: Path, geom: dict, npt: int,
           key: str, on_done=None, on_error=None) -> None:
    """启动后台积分任务（1D）：_spawn_task 的 1D 特化，保持旧签名。

    单文件面板走默认回调（_on_integration_done 画一张图）；对比
    面板传入 on_done/on_error——一个面板有多个任务，各自把结果
    画到同一张图上、出错时各自计数。

    1D 的产物顺手落进分阶段缓存（services/stage_cache）：写盘放在
    算完那一刻（后台线程里，不占界面），下次开会话直接命中。
    """
    # 派活这一刻抓住当前的 _compute_integration——**不是**任务跑起来后
    # 再查模块属性：测试 patch 的正是 gui_views._compute_integration，而
    # 它们的 mock 常常在点完按钮（with 块结束）就撤销了，现查会在那时
    # 变回真函数、拿假路径去积分（2026-09-24 踩过：9 条测试因此超时）。
    compute = _compute_integration

    def worker(path_str, geom_, npt_, config):
        """后台线程：算 1D，顺手把产物落盘。"""
        tth, intensity = compute(path_str, geom_, npt_)
        try:
            stage_cache.store_1d(
                path_str, tth, intensity, config=config, npt=npt_,
                tth_min=geom_.get("tth_min_deg"),
                tth_max=geom_.get("tth_max_deg"))
        except Exception:                                # noqa: BLE001
            pass          # 缓存写失败不影响这次计算（下次重算一遍而已）
        return tth, intensity

    def done(window_, key_, task, result):
        (on_done or _on_integration_done)(window_, key_, task, result)

    def error(msg):
        if on_error is not None:
            on_error(msg)
        else:
            _on_integration_error(window, path, key, msg)

    # 派活了：坞顶那组数据参数**被用掉**（见 _consume_data_params）
    _consume_data_params(window)
    _spawn_task(window, key, worker,
                (str(path), geom, npt, window.config_name), done, error)

def _on_integration_done(window: QMainWindow, key: str, task, result) -> None:
    """面板的计算完成（主线程）：画进它自己的面板。

    每个面板绑定自己的文件，结果永远画回自己的面板；唯一的过期
    情况是同一面板连点两次开了两个任务——先开的晚到会被丢弃
    （每面板只认最新任务，旧结果不得覆盖新图）。
    """
    # 单张 1D 完成要刷文件栏（用户 2026-10-05 报的 bug：打开单张算完 1D，
    # 文件栏不长「1D 产物」，再打开另一张才忽然出现——刷新原先只挂在
    # 批量收尾里（_batch_step），单张走的这条路从来没人刷）。批内的交给
    # 批收尾统一刷；放在最前：过期结果/面板已关这两条早退分支里，产物
    # 已经在盘上，文件栏同样该更新。走 quiet 入口：单张不算"上一轮的活
    # 干完了"，**不清勾选**（不清，用户才能再点一次 [1D] 看缓存——
    # 2026-10-05 实测：非 quiet 会顺手把勾清掉，那条路直接断）。
    if getattr(window, "_batch", None) is None and key.split("|", 1)[0] == "1D":
        window.refresh_groups_quiet()
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:   # 面板还开着才记日志；关了静默丢弃
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（该图面板已有更新的计算）")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已被关闭（关闭即遗忘）：迟到结果静默丢弃
    tth, intensity = result
    # 结果留在面板上：图像参数 [应用] 只改显示时，用已有数据重画，
    # 不用重新积分
    dock.last_tth = tth
    dock.last_intensity = intensity
    _set_focus(window, key, dock.windowTitle())   # 最新出的图成为编辑对象
    _draw_1d(window, dock, tth, intensity)
    if quiet:
        return          # 大批量：逐张那行不写（进度与汇总由 _batch_step 出）
    if len(tth):
        _log(window, f"积分完成：{dock.panel_display}（{len(tth)} 点，"
                     f"2θ {tth.min():.3f}–{tth.max():.3f}°）{suffix}")
    else:
        _log(window, f"积分完成：{dock.panel_display}（0 点，无有效数据）"
                     f"{suffix}")

def _progress_show(window: QMainWindow, total: int, value: int = 0) -> None:
    """批量进度条：显示出来并把范围设成 total（开面板阶段也用它）。"""
    bar = getattr(window, "batch_progress", None)
    if bar is None:
        return
    bar.setRange(0, max(1, int(total)))
    bar.setValue(int(value))
    bar.setVisible(True)

def _progress_hide(window: QMainWindow) -> None:
    """批收尾：进度条收起来（平时不占状态栏的地方）。"""
    bar = getattr(window, "batch_progress", None)
    if bar is not None:
        bar.setVisible(False)

def _batch_step(window: QMainWindow, key: str, name: str = ""):
    """批量进度计数：+1、推进度条，返回 (k/n 后缀, 是否让调用方别写日志)。

    [1D] 等按钮一次勾 N 个文件 = 一批（_plot_view 记账 total/视图）。
    每个任务结束时恰好回调一次（done 或 error），进度按"完成数/总
    数"计；批外零散的面板（[应用] 重算、单个开图）不计数。

    日志（2026-09-25 用户："81 张 = 81 行「打开面板」+ 81 行「积分完成」，
    把日志刷没了"）：大批量（total > BATCH_LOG_MERGE_AFTER）不逐张写，
    改成每 8 张一行进度 + 批收尾一行汇总（带用时与复用缓存张数），
    调用方拿到 quiet=True 就别写自己那行。**失败路径传 name=""**：它照常
    用返回的（k/n）后缀逐条写——失败是要看的，不合并。单张与小批照旧
    逐条写（一眼看清哪张好了）。
    """
    batch = getattr(window, "_batch", None)
    if batch is None or key.split("|", 1)[0] != batch["view"]:
        return "", False
    batch["done"] += 1
    total = batch["total"]
    quiet = total > BATCH_LOG_MERGE_AFTER
    bar = getattr(window, "batch_progress", None)
    if bar is not None:
        bar.setValue(batch["done"])
    suffix = f"（{batch['done']}/{total}）"
    if quiet and name and batch["done"] % 8 == 0 and batch["done"] < total:
        _log(window, f"{batch['view']} 进度：{batch['done']}/{total}"
                     f"（最近：{name}）")
    if batch["done"] >= total:
        _progress_hide(window)
        if quiet:
            dt = time.time() - batch.get("start", time.time())
            cached = batch.get("cached", 0)
            extra = f"；其中复用缓存 {cached} 张" if cached else ""
            # 重复条目**这里再说一遍**：按 [1D] 那一刻的提示在几十行进度
            # 之前，用户盯的是尾巴（2026-10-09：83 条勾选 → 81 条新产物，
            # 用户问"这个昨天不是也做了吗"——做了，但没长在他看的地方）
            dupe = batch.get("dupe", 0)
            dupe_note = (f"；勾选里有 {dupe} 条是同一文件的重复条目"
                         "——同一份数据只算一遍") if dupe else ""
            _log(window, f"{batch['view']} 批完成：{total} 张"
                         f"（用时 {dt:.1f} s{extra}{dupe_note}）")
        del window._batch   # 批走完：清账，之后零散任务回到无计数
        # 1D 批走完 → 文件栏的「1D 产物」分组该长出来了（这次算的这批
        # 现在有产物了）；别的视图没有产物，刷了也没变化
        if batch["view"] == "1D":
            window.refresh_groups()
        # 重复条目的面板此刻补画（_batch 已清账 → 补画不会再计数；
        # 缓存命中，毫秒级。见 _plot_selected 的 deferred）
        from xrd_toolkit.gui.plot.view_flow import _reload_deferred  # 破环
        _reload_deferred(window, batch["view"],
                         batch.get("deferred") or [])
    return suffix, quiet

def show_placeholder(dock, text: str) -> None:
    """面板还没算出数据时，在画布中间写一句话（别让人对着一块空白发懵）。

    用户 2026-09-27："重新打开原始文件…有的是空白的图"——那其实是**还在后台
    算**：面板先建出来，积分结果过一会儿才到（单张 ≈0.4–1 s；81 张排队时
    最后几张要等十几秒）。写一句"正在计算…"，真画上去时 `ax.clear()` 自然
    把它冲掉；算失败时换成"计算失败——见日志"。
    """
    try:
        from xrd_toolkit.gui.plot_panels import _panel_axes
        ax = _panel_axes(dock)
        if ax is None:
            return
        ax.text(0.5, 0.5, text, transform=ax.transAxes, ha="center",
                va="center", color="gray", fontsize=11)
        _content(dock).canvas.draw_idle()
    except Exception:                                    # noqa: BLE001
        pass      # 占位提示画不出来不算事，别拖垮出图这条主路

def _on_integration_error(window: QMainWindow, path: Path, key: str,
                          msg: str) -> None:
    """积分失败（主线程）：报错进日志区，不崩溃（批内带进度计数）。"""
    suffix, _ = _batch_step(window, key)   # 失败永远逐条写（不合并）
    _log(window, f"积分失败：{path.name} —— {msg}{suffix}")
    dock = window.plot_docks.get(key)
    if dock is not None:
        show_placeholder(dock, "计算失败，详见日志")

def _on_view_error(window: QMainWindow, path: Path, key: str, what: str,
                   msg: str) -> None:
    """新视图计算失败（主线程）：报错进日志区，不崩溃。

    what = 计算名（读取/剖面计算/瀑布积分），日志统一 "{what}失败：
    文件名 — 原因"；批内带进度计数后缀。
    """
    suffix, _ = _batch_step(window, key)   # 失败永远逐条写（不合并）
    dock = window.plot_docks.get(key)
    if dock is not None:
        show_placeholder(dock, f"{what}失败，详见日志")
    _log(window, f"{what}失败：{path.name} —— {msg}{suffix}")

def _cache_image(window: QMainWindow, path_str: str, image) -> None:
    """把读好的图存进路径键图像缓存（_apply_auto_contrast 共用）。

    上限 3 张、弹出最早的一张——同一文件反复点不再重复解码。
    """
    cache = window._image_cache
    cache[path_str] = image
    while len(cache) > 3:
        cache.pop(next(iter(cache)))

def _on_image_done(window: QMainWindow, key: str, task, result) -> None:
    """2D 读图完成（主线程）：缓存图像、画进面板。

    与 _on_integration_done 同款过期防护：每面板只认最新任务，
    面板关了静默丢弃。
    """
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:   # 面板还开着才记日志；关了静默丢弃
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（该图面板已有更新的计算）")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已被关闭（关闭即遗忘）：迟到结果静默丢弃
    image = result
    dock.last_image = image   # 图像 [应用] 只改对比度时用已有图重画
    _cache_image(window, str(dock.panel_file), image)
    _set_focus(window, key, dock.windowTitle())   # 最新出的图成为编辑对象
    _draw_2d(window, dock, image)
    if not quiet:
        _log(window, f"读取完成：{dock.panel_display}"
                     f"（{image.shape[0]}×{image.shape[1]} px）{suffix}")

def _on_profile_done(window: QMainWindow, key: str, task, result) -> None:
    """剖面计算完成（主线程）：结果留面板、画曲线（同 1D 的过期防护）。"""
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（该图面板已有更新的计算）")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已被关闭：迟到结果静默丢弃
    t, intensity = result
    dock.last_profile_t = t
    dock.last_profile_intensity = intensity
    _set_focus(window, key, dock.windowTitle())
    _draw_profile(window, dock, t, intensity)
    if quiet:
        return          # 大批量：逐张那行不写（见 _batch_step 的说明）
    if len(t):
        _log(window, f"剖面完成：{dock.panel_display}（{len(t)} 点，"
                     f"距离 {t.min():.0f}–{t.max():.0f} px）{suffix}")
    else:
        _log(window, f"剖面完成：{dock.panel_display}（0 点，无有效数据）"
                     f"{suffix}")

def _on_waterfall_done(window: QMainWindow, key: str, task, result) -> None:
    """扇形积分完成（主线程）：结果留面板、画堆叠瀑布（同 1D 的过期防护）。"""
    # 批量进度：完成任务即计数（大批量时 quiet=True → 不写张张一条）
    suffix, quiet = _batch_step(
        window, key, getattr(window.plot_docks.get(key), "panel_display", ""))
    if window._latest_task.get(key) is not task:
        dock = window.plot_docks.get(key)
        if dock is not None:
            _log(window, f"已忽略 {dock.panel_display} 的过期结果"
                         f"（该图面板已有更新的计算）")
        return
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已被关闭：迟到结果静默丢弃
    tth, i2d, chi = result
    dock.last_waterfall = (tth, i2d, chi)
    _set_focus(window, key, dock.windowTitle())
    _draw_waterfall(window, dock, tth, i2d, chi)
    if quiet:
        return          # 大批量：逐张那行不写（见 _batch_step 的说明）
    if len(tth):
        _log(window, f"扇形积分完成：{dock.panel_display}"
                     f"（{i2d.shape[1]} 扇区 × {i2d.shape[0]} 点）{suffix}")
    else:
        _log(window, f"扇形积分完成：{dock.panel_display}"
                     f"（0 点，无有效数据）{suffix}")

# 一次批量作图的防爆线（2026-09-24 用户"图一多就很卡"；开图是唯一随数量
# 变慢的成本：第 1 张 136 ms、第 100 张 287 ms——每开一张都要把已开的子窗口
# 重排一遍——而且每张 ≈15 MB，真机 81 张 ≈1.4 GB）。**超过这个数一张都不画**
# （边界 = **25 张全画、26 张起防爆**——2026-10-08 晚用户先拍的"24 全画"，
# 2026-10-09 用户改为"最多 25"："超过一次最多画的 24 张改为最多25"；
# 10-08 同一天从"只有 1D 防爆、原图只画前 N 张"统一到**所有视图同款防爆**，
# 用户："原图批量出图没有防爆图"）。1D 超限照旧全部只算不画、进文件栏
# 「1D 产物」；其余视图没有产物可留，直接不弹面板——出口都在日志里写明
# （[热图]/[对比] 一张图放完整批；想一张张全开走文件栏右键的
# 「打开勾选的 N 张图」→ 选视图，没有上限、只弹确认——2026-10-09 起
# 四种视图都有，见 file_dock._open_checked_views）。
MAX_PANELS_PER_BATCH = 25

# 批量多大之后"日志合并"（2026-09-25 用户："81 张 = 81 行「打开面板」+
# 81 行「积分完成」，把日志刷没了"）：超过这个张数就不再逐张写，改成
# 开面板一行汇总 + 每 8 张一行进度 + 收尾一行汇总（用时/复用缓存张数）；
# **失败永远逐条写**（那是要看的）。小批维持逐张写，一眼看清哪张好了。
BATCH_LOG_MERGE_AFTER = 8
