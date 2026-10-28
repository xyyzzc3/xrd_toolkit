"""文件 → 视图闭环：打开/复用面板、防爆线、只算不画、批尾补画。

2026-10-22 从 plot_views.py 原样搬出（只改结构，不动逻辑）。
plot_views 再导出。
"""
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import (
    _data_snapshot, _log, _set_focus, _collect_geometry)
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.plot.curves import _consume_data_params
from xrd_toolkit.gui.plot.drawing import _draw_1d
from xrd_toolkit.gui.plot.runners import _apply_range_memory, _run_view
from xrd_toolkit.gui.plot.spawn import (
    BATCH_LOG_MERGE_AFTER, MAX_PANELS_PER_BATCH, _batch_step, _progress_show,
    _spawn, show_placeholder)
from xrd_toolkit.gui.plot_panels import _open_plot_panel
from xrd_toolkit.gui.tasks import user_error_text
from xrd_toolkit.services import stage_cache


def _resolve_dock(window: QMainWindow, name: str, item):
    """按「视图 + 文件条目」找已有面板：返回 (键, 面板或 None)。

    键 = "视图|路径"。同一文件可以有多条条目（重复文件改名加入）：短键
    归**先开出面板的那条**，后来者键补显示名区分，各自成图、互不当过期；
    条目删后重加 → 面板归位到新条目（显示名一样就是同一条）。

    注意这条规则**依赖调用时机**：第二条条目的键要靠"短键上已有面板"
    才补显示名，所以必须在**同一次单遍循环**里边找边建（2026-09-24 踩过：
    先跑一遍预算、再跑一遍建面板，第二条条目预算时看不到第一条刚建的
    面板 → 两条抢同一个键、只出一张图）。
    """
    path = Path(item.data(Qt.UserRole))
    display = item.text()
    key = f"{name}|{path}"
    dock = window.plot_docks.get(key)
    if dock is not None and getattr(dock, "panel_item", None) is not item:
        if getattr(dock, "panel_display", None) == display:
            dock.panel_item = item   # 条目删后重加：面板归位到新条目
        else:
            # 同路径的另一条目（重复文件改名加入）→ 键补显示名区分
            key = f"{name}|{path}|{display}"
            dock = window.plot_docks.get(key)
    return key, dock

def _pending_products(window: QMainWindow, name: str, rest: list,
                      seen=None) -> list:
    """超限文件里**还需要算**的那些（1D 专用；其余视图返回空表）。

    为什么只认 1D：只有 1D 有产物可留（2D/剖面/瀑布是显示阶段，见
    services/stage_cache 的说明），"只算不画"对它们没有意义。
    已有产物的直接跳过——不重算，**也不计进这一批的总数**：总数为 0
    的批不该开进度条，更不该让 k/n 永远到不了 n。
    **同一路径的重复条目也只算一遍**（2026-10-09 用户："去重，减少
    工作量，出图速度永远是越快越好"）——勾 83 条里有 2 条重复，这批
    就是 81 张，产物也是 81 条，数字处处对得上。
    """
    if name != "1D" or not rest:
        return []
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    _consume_data_params(window)   # 按这把范围取数：这组值用掉了
    todo = []
    done = set(seen or ())    # 已在面板那批开算的路径（重复条目被防爆线
    #                           劈开时，后半截也不能再算一遍）；同批内同理
    for source in rest:                       # rest = 来源对象（已按原始数据过滤）
        path = Path(source.path)
        if path in done:
            continue
        done.add(path)
        if not stage_cache.has_1d(path, **kw):
            todo.append(path)
    return todo

def _on_headless_done(window: QMainWindow, key: str, task, result) -> None:
    """"只算不画"的完成回调：只记账（不画图、不写逐张日志）。"""
    _batch_step(window, key, Path(key.split("|", 1)[1]).name)

def _spawn_headless(window: QMainWindow, name: str, path: Path) -> None:
    """只算不画：后台算 1D 并把产物落盘，**不建面板**（用户 2026-09-25 的 B 项）。

    worker 与画面板那条完全共用（_spawn：算完顺手 stage_cache.store_1d），
    差别只在回调——这里不画任何东西。记账归到**同一批**（键的视图段与
    画面板一致），所以进度条与日志是一条线走完；失败照旧逐条报
    （_spawn 的默认 error 回调）。
    """
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    key = f"{name}|{path}"
    _spawn(window, path, geom, npt, key, on_done=_on_headless_done)

def _open_source_view(window: QMainWindow, name: str, source) -> str:
    """按需打开**一条**条目的图面板（文件栏双击 / 右键 [打开 1D 图]）。

    用户 2026-09-26 定："选完原始图，进行 1d 后不弹框，直接列出在文件区，
    然后可以点开看"——大于上限的批次只算不画，看哪张就点哪张。
    不碰勾选状态（双击的第一次单击可能顺手改了勾选，但不影响这里）；已有
    面板就前置，没有就建；数据走同一条 _run_view（1D 命中缓存即秒开）。
    """
    if not gui_sources.is_rawlike(source.kind):
        return _open_product_panel(window, source)   # 曲线产物：直接读盘画线
    path = Path(source.path)
    key, dock = _resolve_dock(window, name, source.item)
    if dock is None:
        _apply_range_memory(window, path)   # 甲：新图用该文件自己的范围，不用盒子里的遗留值
        dock = _open_plot_panel(window, name, key, f"{name}_{source.display}")
        dock.panel_file = path
        dock.panel_item = source.item
        dock.panel_display = source.display
        dock.figure_saved = False
        dock.params_snapshot = _data_snapshot(window)
        # 面板先建、数据后到（后台积分）——中间那段别让人对着一块空白
        show_placeholder(dock, "正在计算…")
        view_word = f" {name} " if name.isascii() else name   # 中英混排：1D/2D 两侧留空格
        _log(window, f"打开{view_word}面板：{source.display}")
    dock.setVisible(True)
    dock.raise_()          # 从文件栏点开的图，摆到最前面
    _run_view(window, name, path, key)
    return key

def _reload_deferred(window: QMainWindow, name: str, keys) -> None:
    """批尾补画"重复条目"的面板：从产物缓存读（毫秒级），不重算。

    两条到达路径（见 _plot_selected 尾部）：批正常收尾时由 _batch_step
    调（此时 _batch 已清账 → 补画不会二次计数）；整批在循环里就收完尾
    （全缓存命中、同步完成）时由 _plot_selected 自己调。面板已被关掉
    的键跳过；缓存万一没命中会走一次正常积分（可接受，且有日志）。
    """
    for key in keys:
        dock = window.plot_docks.get(key)
        if dock is None:
            continue
        _run_view(window, name, Path(dock.panel_file), key)

def _open_source_group(window: QMainWindow, name: str, sources) -> int:
    """整组打开（右键 [打开整组 N 张]）：逐条走 _open_source_view。

    开面板是主线程上的活、而且每张 ≈15 MB，所以每 8 张报一次进度并消化
    事件（同批量开图的老套路，界面不会闷住）；返回真开了几条。
    """
    sources = list(sources)
    view_word = f" {name} " if name.isascii() else name   # 中英混排：1D/2D 两侧留空格
    _log(window, f"打开整组{view_word}图：{len(sources)} 张…")
    for i, source in enumerate(sources):
        try:
            _open_source_view(window, name, source)
        except Exception as err:                       # 单张失败不拖垮整组
            _log(window, f"{source.display}：打开失败"
                         f"（{user_error_text(err)}）")
        if i and (i + 1) % 8 == 0:
            _log(window, f"  已开 {i + 1}/{len(sources)}…")
            _settle(window)
    _log(window, f"打开整组{view_word}图完成：{len(sources)} 张")
    return len(sources)

def _open_product_panel(window: QMainWindow, source) -> str:
    """产物条目出一张 1D 面板：直接读产物画线（不重算）。

    用户 2026-09-25 定："照旧用，日志说一句"——勾的就是那一份产物，所以
    不比对当前设置、不重新积分，读到什么画什么（读不到就记日志说清楚）。

    两类产物在这一步的待遇不同：
      - **扣背景产物**：快照里的背景扣除置「不扣」——它本身已经是扣完的，
        再扣一遍就是二次相减（图上会往下掉一截，看着还挺像"扣得更干净"）；
      - **1D 产物**：就是那条原始积分曲线（只是钉在某一份缓存上），快照
        照常走默认，**锚点/自动基线一样能用**、也能被 [批量扣背景] 扣
        （用户 2026-09-25 问起才把这条掰正：原先一刀切当成了"已完成"）。
    """
    key = f"1D|{gui_sources.source_id(source)}"
    got = gui_sources.load_product(source)
    if got is None:
        _log(window, f"{source.display}：产物读不到了（被删了？）"
                     "——重算一次，或右键分组删掉这一条")
        return key
    dock = window.plot_docks.get(key)
    if dock is None:
        title = f"1D_{source.display}"
        dock = _open_plot_panel(window, "1D", key, title)
        dock.panel_display = title
        dock.figure_saved = False
        snap = _data_snapshot(window)
        if source.kind == gui_sources.BG:
            snap["背景扣除模式"] = "off"     # 已经扣过，别再扣（见上）
        dock.params_snapshot = snap
        _log(window, f"打开 1D 面板：{source.display}"
                     f"（{gui_sources.describe_source(source)}，直接读取，不重算"
                     + ("；面板背景扣除已置「不扣」）"
                        if source.kind == gui_sources.BG else "）"))
    dock.setVisible(True)
    dock.panel_file = Path(source.path)   # 锚点/另存名用的源文件
    dock.panel_source = source            # 面板记住自己的产物来源
    tth, intensity = got
    dock.last_tth, dock.last_intensity = tth, intensity
    _set_focus(window, key, dock.windowTitle())
    _draw_1d(window, dock, tth, intensity)
    return key

def _plot_view(window: QMainWindow, name: str) -> None:
    """工具栏作图按钮的动作：对每个对号文件开面板（或复用）并计算。

    支持批量：勾选几个文件（选入/拖入即全勾），点一下视图按钮 =
    一次开出多张图。面板按「视图 + 文件条目」成对创建：同一文件
    重复点 = 刷新那张图；面板标题 = 视图_显示名（如 1D_lab6-
    00024.tif），多张图一眼分清。重复文件改名加入的条目显示名不同
    （xxx (1).tif），面板键补显示名区分，各自成图、互不当过期。
    按钮是纯动作不是开关——点一下算一下，重复点击安全；面板的
    开/关只由 × 和拖动管理。

    超过 MAX_PANELS_PER_BATCH 张**一张都不画**（防爆图，边界与理由见
    常量旁的注释）：日志写明这批多少张、出口在哪（1D 只算不画、进
    「1D 产物」；其余视图用 [热图]/[对比] 看整批），不静默少画。进度
    记账的总数 = 这一批真要跑的张数，否则 k/n 永远到不了 n、批也不清账。
    """
    checked = gui_sources.checked_sources(window)
    if not checked:
        _log(window, "未勾选任何项")
        return
    # 产物条目（"1D"/"扣背景"）是现成的 1D 曲线，没有"从原始图算"这一步：
    # 只有 1D 视图能出，其余视图点名跳过（不静默少画）
    raw = [s for s in checked if gui_sources.is_rawlike(s.kind)]
    products = [s for s in checked if not gui_sources.is_rawlike(s.kind)]
    n_dupe = 0            # 同一路径的重复条目数（批完成时还要再提一句）
    if name == "1D" and raw:
        # 重复条目（同一条数据以 xxx.tif / xxx (1).tif 加了两次）：本批
        # **只算一遍**（2026-10-09 用户："去重，减少工作量，出图速度永远
        # 是越快越好"）——第一次出现照常，后来的条目整条跳过（下面的
        # repeat_items），产物也按**数据**只存一份（键 = 文件指纹）。
        # 以前一个字都不说，只看见"勾了两条、文件栏只长出 1 条"（用户
        # 2026-10-07："重复的原始数据生成产物时会只生成一个，在日志里
        # 提到这个事情"）。[批量处理] 那条路已有逐条"同一文件只扣一份"
        # 的日志（_proc_batch_run 的 seen），这里给 1D 补一句汇总。
        paths = [str(Path(s.path)) for s in raw]
        n_dupe = len(paths) - len(set(paths))
        if n_dupe:
            _log(window, f"注意：勾选里有 {n_dupe} 条是同一文件的重复条目"
                         "——同一份数据只算一遍（图照开，重复条目的面板"
                         "等本批算完从缓存补画），产物共用")
    if products and name != "1D":
        _log(window, f"跳过 {len(products)} 个产物条目：{name} 要从原始图像"
                     "算（产物是 1D 曲线，只能出 1D 图 / 对比 / 热图）")
        products = []
    targets = raw[:MAX_PANELS_PER_BATCH]
    rest = raw[len(targets):]
    if len(raw) > MAX_PANELS_PER_BATCH:
        # 勾得比防爆线还多 → **一张都不画**（用户 2026-09-26 定："防爆图"；
        # 2026-10-08 从"只有 1D、且原图只画前 N 张"统一到所有视图——用户：
        # "原图批量出图没有防爆图"）。1D 全部只算不画、进「1D 产物」；
        # 其余视图（2D/剖面/瀑布）是显示阶段没有产物可留，直接不弹面板。
        targets, rest = [], list(raw)
    # 只算不画的文件**照样算完入库**：1D 有产物可留，之后单独点开就是
    # 复用缓存；已有产物的直接跳过（不重算、也不占进度总数）。
    # 其余视图（2D/剖面/瀑布）是显示阶段、没有产物可留，就只记日志。
    pending = _pending_products(window, name, rest,
                                seen={Path(s.path) for s in targets})
    if rest:
        why = (f"这批 {len(raw)} 张超过一次最多画的 {MAX_PANELS_PER_BATCH} 张"
               "（每张 ≈ 15 MB、越开越慢）")
        # 出口：右键「打开勾选的 N 张图」→ 选视图（2026-10-09 起四种视图
        # 都有——此前只有 1D 一条入口，原图三类超限后没有任何手动全开的
        # 路，用户："原图功能无法批量出超过防爆图数字"）
        if name == "1D":
            _log(window, f"{why}：全部只算不画——结果进文件栏"
                         "「1D 产物」，双击看一张；想一次全开：右键文件栏"
                         "（或「原始数据」组）→「打开勾选的 N 张图」→ 1D")
        else:
            _log(window, f"{why}：这次一张都不弹——要看整批用 [热图] / "
                         f"[对比]（一张图看完）；想一张张全开：右键文件栏 →"
                         f"「打开勾选的 N 张图」→ {name}（先弹确认）；"
                         "或减少勾选分批看")
    # 同一文件的重复条目：**本批只算一遍**（2026-10-09 用户："去重，减少
    # 工作量，出图速度永远是越快越好"），但**图仍是每条一张**（2026-10-10
    # 用户："出 1d 图变少，这个怎么还没修复"——上一版把面板也省了，
    # 勾 5 条出 4 张，看着就是少画了）：第一次出现照常算；后来的条目
    # 照常建面板、只是**不排积分**，批收尾时从产物缓存补画（毫秒级）。
    repeat_items = set()
    if n_dupe:
        seen_paths = set()
        for source in targets:
            p = str(Path(source.path))
            if p in seen_paths:
                repeat_items.add(source.item)
            else:
                seen_paths.add(p)
    total_tasks = len(targets) - len(repeat_items) + len(pending)
    # 只开**一张**新图：2θ/点数默认用该文件自己上次的（甲，见 _apply_range_memory）；
    # 批量（≥2 张）仍用坞顶那行当输入——一组图总得有一个共同范围
    single_new = total_tasks == 1
    if total_tasks > 1:
        # 批量进度记账：这一批的总数/视图名/起算时刻；每个任务结束回调
        # 计数一次（k/n 后缀与进度条都靠它，批走完自动清账）
        window._batch = {"view": name, "total": total_tasks, "done": 0,
                         "start": time.time(), "cached": 0, "dupe": n_dupe}
        _progress_show(window, total_tasks)
    opened = []          # 新开的面板显示名（大批量时合并成一行）
    merged = total_tasks > BATCH_LOG_MERGE_AFTER
    view_word = f" {name} " if name.isascii() else name   # 中英混排：1D/2D 两侧留空格
    # 产物条目先画：读盘画线（毫秒级）不需要排队等积分，也不进进度条
    for source in products:
        _open_product_panel(window, source)
    skipped_repeats = 0
    deferred = []        # 重复条目的面板键：不排积分，批尾从缓存补画
    for i, source in enumerate(targets):
        path = Path(source.path)
        display = source.display
        key, dock = _resolve_dock(window, name, source.item)
        if dock is None:
            if i and i % 8 == 0:
                # 开面板是主线程上的活（每块 130~290 ms）：每 8 块报一次
                # 进度、顺手消化事件，界面不会一口气闷十几秒没反应
                # （用户反馈"图一多就很卡"——开 81 张时的观感）
                _log(window, f"正在开面板：{i + 1}/{len(targets)}…")
                _progress_show(window, len(targets), i + 1)   # 开面板也有进度
                _settle(window)
            title = f"{name}_{display}"
            if single_new and source.kind == gui_sources.RAW:
                _apply_range_memory(window, path)   # 甲：单张新图用该文件自己的范围
            # 新面板级联摆放，现有面板原地不动（开新图不再重排旧图）
            dock = _open_plot_panel(window, name, key, title)
            dock.panel_file = path   # 面板绑定自己的文件（删文件不影响已开的面板）
            dock.panel_item = source.item   # 面板绑定自己的列表条目（重名条目各自成图）
            show_placeholder(dock, "正在计算…")   # 同上：算完自然被冲掉
            dock.panel_display = display   # 显示名（标题/日志/默认存盘名用）
            dock.figure_saved = False   # 有没有存过盘（关窗询问用）
            dock.params_snapshot = _data_snapshot(window)   # 开图快照：数据用当前值，显示从默认起步
            if merged:
                opened.append(display)      # 大批量：攒着，循环后一行写完
            else:
                _log(window, f"打开{view_word}面板：{display}")
        dock.setVisible(True)
        if source.item in repeat_items:
            # 同一文件本批已算/在算：面板照建，但不排任务——批尾从缓存补画
            skipped_repeats += 1
            if getattr(dock, "last_tth", None) is None:
                show_placeholder(dock, "同一文件本批只算一遍——算完自动补画")
            deferred.append(key)
            continue
        _run_view(window, name, path, key)
    if opened:
        head = "、".join(opened[:5]) + ("…" if len(opened) > 5 else "")
        _log(window, f"打开{view_word}面板 {len(opened)} 张：{head}")
    if skipped_repeats:
        _log(window, f"{skipped_repeats} 条重复条目本批只算一遍：它们的图"
                     "照开，等本批算完从缓存补画（毫秒级）")
    if total_tasks > 1:
        _progress_show(window, total_tasks)   # 进入计算阶段（总数已按去重算）
    for path in pending:
        _spawn_headless(window, name, path)
    if deferred:
        # 批还没收尾 → 交给收尾（_batch_step）补；已经在循环里收完尾
        # （全缓存命中、同步完成）→ 这里直接补。两条路都不落空
        if getattr(window, "_batch", None) is not None:
            window._batch["deferred"] = list(deferred)
        else:
            _reload_deferred(window, name, deferred)
    if name == "1D" and rest and not pending:
        # 超限 + 产物全都在（刚做过对比、或上一轮 1D 刚算完，缓存全热）：
        # 0 个任务 → 没有进度条、也不会有批收尾（_batch_step 的完成行与
        # 文件栏刷新都挂在那上面）——不在这儿收尾，点 [1D] 就是"什么都
        # 不发生"：日志只有上面那行防爆线提示、产物也不进文件栏。用户
        # 2026-10-15 现场："导入原始数据后，直接进行对比出图，然后再点
        # 击 1d 出图，就会卡住"——复现实测主线程没卡（看门狗 8 s 阈值
        # 没留档），就是这条全缓存静默路。这里补一次与批正常走完同一个
        # 出口的收尾：刷新文件栏（"文件栏出现 N 条新产物"由它写）+ 明说
        # 一句，让这一次点击有下文。
        window.refresh_groups()
        _log(window, f"这 {len(rest)} 张的 1D 结果都已经算好（复用缓存，"
                     "本次无需重算）——产物就在文件栏「1D 产物」：双击看"
                     "一张；想一次开多张：勾上要看的产物 → 右键 →「打开"
                     "勾选的 N 张图」→ 1D")
        return
