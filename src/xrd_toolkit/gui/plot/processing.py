"""处理页产物链：单张采用 / 批量处理 / 补算 1D / 台账。

2026-10-22 从 plot_views.py 原样搬出（只改结构，不动逻辑）。
plot_views 再导出。
"""
import time
from pathlib import Path

from PySide6.QtWidgets import QMainWindow

from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui.panel_state import (
    _collect_geometry, _log, _proc_params, _proc_settings)
from xrd_toolkit.gui.plot.curves import (
    _anchors_on_curve, _consume_data_params, _curve_source, _proc_recipe,
    _retarget_product_panel_to_source, note_proc_recipe)
from xrd_toolkit.gui.plot.drawing import _bg_path_of
from xrd_toolkit.gui.plot.spawn import _progress_hide, _progress_show, _spawn
from xrd_toolkit.services import process, stage_cache


def _process_source(window, source, kw, settings, params0, blank_curve,
                    anchor_source=None) -> dict:
    """把**一条**来源处理一遍并落成产物（[批量处理] 与 [采用这份结果] 共用）。

    为什么必须是同一份实现：屏幕上的曲线与写进产物的这一份必须来自同一个
    `apply_chain` 的输出（见 _proc_curve 的说明）——两处各写一遍必然漂移，
    而漂移的表现是"图上看着好好的、对比里数字对不上"，最难查。

    返回 dict：ok / why（没产出的原因，人话）/ path / key / anchors / dropped
    / kind（这条来源是哪一类条目）。锚点只传 2θ 位置、强度在这条来源自己的
    曲线上重取（跨批次复用与整批处理一贯的口径，见 services/recipes 与
    _proc_batch_apply）。
    """
    path = Path(source.path)
    # 落产物时挂在哪把键下面：RAW 按当前设置算，产物条目挂在它自己那份上
    base_key = None
    if source.kind == gui_sources.RAW:
        tth, intensity, why = _curve_source(window, path, kw)
        if why is not None:
            return {"ok": False, "why": why, "path": path}
    elif source.kind == gui_sources.BG:
        # 处理产物：**从它底下的原始 1D 曲线重做**（用户 2026-09-28 第 3 条）。
        # 不是拿扣完的曲线再扣一遍（那是二次相减，曲线会整体往下掉一截，
        # 看着还挺像"扣得更干净"）。血缘能查到哪把 1D 键就挂回哪把，
        # 查不到（老产物）就按当前设置那把——两种都是"这个文件的一条 1D"。
        got = stage_cache.load_proc_source(source.key)
        if "why" in got:
            return {"ok": False, "why": got["why"], "path": path}
        tth, intensity = got["tth"], got["intensity"]
        base_key = got.get("base")
    else:
        # 1D 产物：读那一份（钉住的键），不按当前设置重算
        got = gui_sources.load_product(source)
        if got is None:
            return {"ok": False, "why": "产物读不到了（被删了？）", "path": path}
        tth, intensity = got
    xs = [x for x, _ in settings["anchors"]]
    per_file, dropped = _anchors_on_curve(xs, tth, intensity)
    params = {**params0, "anchors": per_file}
    processed, base = process.apply_chain(tth, intensity, params,
                                          blank_curve=blank_curve)
    if base is None and settings["mode"] != "off":
        return {"ok": False, "path": path,
                "why": "背景拟合没成功（锚点太少 / 全落在数据范围外？）"}
    proc_settings = {**settings, "anchors": per_file}
    if source.kind == gui_sources.ONED:
        # 挂在**那份 1D 产物**的键下面：勾的是哪一条，处理的就是哪一条
        produced = stage_cache.store_proc_by_key(source.key, tth, processed,
                                                 settings=proc_settings,
                                                 source=path.name)
    elif base_key:
        # 处理产物重做：挂回它血缘上那把 1D 键（查得到的话）
        produced = stage_cache.store_proc_by_key(base_key, tth, processed,
                                                 settings=proc_settings,
                                                 source=path.name)
    else:
        produced = stage_cache.store_proc(path, tth, processed, **kw,
                                          settings=proc_settings)
    if getattr(window, "bg_anchors", None) is None:
        window.bg_anchors = {}
    window.bg_anchors[str(path)] = per_file   # 面板跟着用同一套锚点
    # 配方**按文件**记下来（处理页那行"本图配方"读它）。**不写进面板
    # 快照**：面板显示的曲线只由它自己的快照决定，处理不该偷偷改掉人家
    # 正在看的曲线（用户 2026-09-27 晚："原始数据会被改变"）
    note_proc_recipe(window, path,
                     _proc_recipe({**settings, "anchors": per_file},
                                  source=anchor_source))
    return {"ok": True, "why": None, "path": path, "key": Path(produced).stem,
            "anchors": per_file, "dropped": len(dropped),
            "kind": source.kind}

def _proc_save(window: QMainWindow) -> None:
    """处理页唯一的产出按钮（2026-10-07 用户定：两个合成一个）。

    勾了文件 = 批量处理勾选的每一份（原 [批量处理] 的语义——RAW /
    1D 产物 / 处理产物都能当输入）；一个没勾 = 存编辑对象这一张
    （原 [存成产物] 的语义——产物面板先就地换回它底下的原始曲线）。
    两种模式共用同一台机器（`_process_source` 逐条处理 + 同一套台账），
    只是目标集不同，所以这里只做分发。按钮文字跟着勾选数走（
    file_dock._refresh_check_labels）。
    """
    if gui_sources.checked_sources(window):
        _proc_batch_apply(window)
    else:
        _proc_keep_this(window)

def _proc_keep_this(window: QMainWindow) -> None:
    """[采用这份结果]：把编辑对象这张图上**眼下这条处理后的曲线**落成产物。

    用户 2026-09-28 第 4 条："处理单张 1d 数据应该在用户确定处理结果后出现在
    文件区的处理后数据中"——单张处理原先只活在画布上（_refresh_proc 只重画、
    什么都不写），想留住成果得"勾上自己再点 [批量处理]"，绕且不好理解。
    这里就是那一步的显式入口：一个来源、一次台账、文件栏立刻长出一组
    「处理后 …」。落的产物与 [批量处理] 完全同一种（同一份 apply_chain 输出、
    同一套台账），所以对比 / 热图 / 导出 / 双击打开全都能直接用它。
    """
    dock = window.plot_docks.get(window.focus_panel)
    path = _bg_path_of(dock) if dock is not None else None
    if dock is None or path is None:
        _log(window, "先点一张 1D 图（编辑对象），再点 [存成产物]")
        return
    # 处理产物面板：先就地换成它底下的原始 1D 曲线（用户 2026-09-28 第 3 条），
    # 换成功就照常"采用"——存下来的是一份**重做**的产物，不是二次相减
    if not _retarget_product_panel_to_source(window, dock):
        return
    settings = _proc_settings(window, dock, path)
    params0 = _proc_params(window, dock, path)
    if settings["mode"] == "off" and not process.chain_parts(settings):
        _log(window, "「处理」页里三项都关着（背景扣除 / 平滑 / 裁剪）——"
                     "先开一项，再点 [存成产物]（否则存下来的与原始曲线一样）")
        return
    src = getattr(dock, "panel_source", None)
    source = src if src is not None else gui_sources.make_source(path)
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    _consume_data_params(window)   # 按这把范围取数：这组值用掉了
    blank = getattr(window, "bg_blank", None)
    blank_curve = ((blank["tth"], blank["intensity"])
                   if settings["mode"] == "blank" and blank is not None
                   else None)
    # 锚点来处 = 本图手点（None）：单张处理的锚点就是这个文件自己的
    res = _process_source(window, source, kw, settings, params0, blank_curve,
                          anchor_source=None)
    if not res["ok"]:
        _log(window, f"没存成产物：{res['why']}")
        return
    label, batch = _proc_batch_label(settings)
    stage_cache.record_batch(
        "bg", batch, label=label, items=[(res["path"], res["key"])],
        config=window.config_name, npt=npt,
        tth_min=kw.get("tth_min"), tth_max=kw.get("tth_max"),
        settings={k: v for k, v in settings.items() if k != "anchors"},
        note="1 个文件")
    _log(window, f"已存成产物：{Path(path).name}"
                 f"（{process.chain_label(settings)}）——文件栏「{label}」里"
                 f"可以整组拿去 [对比] / [热图]，双击看这一条")
    window.refresh_groups()       # 文件栏里立刻长出这一组

def _proc_ensure_curves(window: QMainWindow, missing: list, kw: dict,
                        geom: dict, npt: int, then) -> None:
    """把"按当前设置还没算过 1D"的文件后台补算出来，算完调 then()。

    为什么要有这一步（用户 2026-09-30 报的尾巴）：[批量处理] 的输入是"每个
    文件按**当前设置**在缓存里的那条 1D 曲线"（见 _curve_source 为什么必须
    同一把键）。改了坞顶的 2θ 范围直接点 [批量处理]，这些文件的曲线还是旧
    范围的——旧版**逐条跳过**并写明原因（2026-09-28 定的口径，底线是不许拿
    旧范围的曲线凑数，那条不变），但用户的期待很直白：改了范围，[批量处理]
    就该按新范围出产物，而不是每次先手动跑一遍 1D。

    所以这里补上：复用 [1D] 那条后台任务（同一个 _spawn，算完顺手
    stage_cache.store_1d），进度条与失败原因都走已有通路；全部落地后接着
    跑处理链（then）。补算出来的 1D 照常进文件栏「1D 产物」分组——它们
    本来就是这一趟要用的曲线，不是临时垃圾。

    同一时间只允许一拨：上一拨还在算时再点 [批量处理] 只记一句（否则
    两拨回调会各自接着跑一遍处理，产物与台账都翻倍）。
    """
    if getattr(window, "_proc_prep", None) is not None:
        _log(window, "上一批还在按当前设置重算 1D，等它跑完再点 [存成产物]")
        return
    lo, hi = kw.get("tth_min"), kw.get("tth_max")
    span = (f"2θ {lo:g}–{hi:g}°" if lo is not None and hi is not None
            else "当前设置")
    state = {"left": len(missing), "total": len(missing), "failed": 0}
    window._proc_prep = state
    _log(window, f"这批里有 {len(missing)} 个文件按{span}还没算过 1D"
                 "——先在后台补算，算完自动接着处理")
    _progress_show(window, len(missing))

    def one_finished() -> None:
        state["left"] -= 1
        _progress_show(window, state["total"], state["total"] - state["left"])
        if state["left"] > 0:
            return
        window._proc_prep = None
        _progress_hide(window)
        window.refresh_groups()      # 刚补算的 1D 该进文件栏了
        if state["failed"]:
            _log(window, f"其中 {state['failed']} 个文件没算成（见上面的日志），"
                         "它们会被跳过")
        _log(window, f"1D 补算完成（{state['total']} 个），接着处理…")
        then()

    def on_done(window_, key_, task, result) -> None:
        one_finished()

    def on_error(msg) -> None:
        state["failed"] += 1
        _log(window, f"1D 补算失败：{msg}")
        one_finished()

    for source in missing:
        _spawn(window, Path(source.path), geom, npt,
               "1D|" + str(source.path), on_done=on_done, on_error=on_error)

def _proc_batch_apply(window: QMainWindow) -> None:
    """[批量处理]：把「处理」页当前这套链用到勾选文件，各生成一份处理产物。

    链 = **背景扣除 → 平滑 → 裁剪**（顺序见 services/process），三项都可关：
    全关时这个按钮只记一条提示、不写产物。

    锚点**只传 2θ 位置**，强度到每个文件自己的曲线上重新取：一批数据的
    背景**形状**（空气散射 / 光路 / 探测器）是共同的，绝对强度不是——
    直接套 A 的强度会把 B 的基线抬错几倍（与用户 2026-09-24 讨论定稿）。
    平滑窗口与裁剪区间是整批共用的（它们是"要看什么"的选择，不是每张图
    各自的物理属性）。

    处理完存进分阶段产物：对比 / 热图 / 导出下次直接读它，跨会话秒开。
    每个目标面板的参数快照也写成同一套设置——这样"面板上看到的曲线"与
    "对比里用的曲线"是同一条（否则两处数字对不上，最容易让人怀疑自己）。

    改了坞顶 2θ 范围后直接点这里（2026-09-30 起）：这批文件按当前设置还没
    算过 1D 的，**先在后台补算再处理**（见 _proc_ensure_curves）——不再"逐条
    跳过"。补算出来的 1D 照常进文件栏「1D 产物」。

    可处理的条目 = **原始数据** + **1D 产物**（它就是那条原始积分曲线，只是
    钉在某一份缓存上，见 _open_product_panel 的说明）+ **处理产物**——后者
    按**它底下的原始 1D 曲线重做**（用户 2026-09-28 第 3 条："套用别的配方的
    处理图不能二次处理了，修正"）。旧版把处理产物整条跳过（"幂等，不再来
    一遍"），可用户要的是"换套参数重做"：现在输入换成没扣过背景的那条曲线，
    所以既做得到、又不会二次扣除/二次平滑（见 stage_cache.load_proc_source）。
    """
    dock = window.plot_docks.get(window.focus_panel)
    focus_path = _bg_path_of(dock) if dock is not None else None
    if dock is None or focus_path is None:
        _log(window, "先点一张 1D 图（编辑对象），再点 [存成产物]")
        return
    settings = _proc_settings(window, dock, focus_path)
    params0 = _proc_params(window, dock, focus_path)
    focus_display = getattr(dock, "panel_display", None) or Path(focus_path).name
    chain = process.chain_parts(settings)
    if settings["mode"] == "off" and not chain:
        _log(window, "「处理」页里三项都关着（背景扣除 / 平滑 / 裁剪）——"
                     "先开一项，再点 [存成产物]")
        return
    xs = [x for x, _ in settings["anchors"]]
    if settings["mode"] == "anchor" and not xs:
        _log(window, "先在图上点几个锚点（背景扣除模式为「手动锚点」），"
                     "再点 [存成产物]")
        return
    # 可处理的是原始数据 + 1D 产物 + 处理产物（后者按它底下的原始曲线重做，
    # 见 docstring——用户 2026-09-28 第 3 条）
    picked = gui_sources.checked_sources(window)
    targets = [s for s in picked
               if s.kind in (gui_sources.RAW, gui_sources.ONED, gui_sources.BG)]
    redone = [s for s in targets if s.kind == gui_sources.BG]
    if not targets:
        _log(window, "未勾选任何项")
        return
    if redone:
        _log(window, f"其中 {len(redone)} 条是处理产物：按它底下的原始 1D "
                     f"曲线重做（不是二次相减）")
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    kw = dict(config=window.config_name, npt=npt,
              tth_min=geom.get("tth_min_deg"), tth_max=geom.get("tth_max_deg"))
    _consume_data_params(window)   # 按这把范围取数：这组值用掉了
    blank = getattr(window, "bg_blank", None)
    blank_curve = ((blank["tth"], blank["intensity"])
                   if settings["mode"] == "blank" and blank is not None
                   else None)
    # 改了 2θ 范围就直接点这里：这批文件按**当前设置**还没算过 1D
    # （判断口径与取数完全同一把键，见 _curve_source）。缺就先在后台补算，
    # 算完自动接着处理——用户 2026-09-30："改完 2θ 范围后 [批量处理]
    # 不会自动按新范围重积分，只逐条跳过并写明原因"。
    missing = [s for s in targets
               if s.kind == gui_sources.RAW
               and _curve_source(window, Path(s.path), kw)[2] is not None]
    if missing:
        _proc_ensure_curves(
            window, missing, kw, geom, npt,
            lambda: _proc_batch_run(window, targets, kw, settings, params0,
                                    blank_curve, focus_display))
        return
    _proc_batch_run(window, targets, kw, settings, params0, blank_curve,
                    focus_display)

def _proc_batch_run(window: QMainWindow, targets: list, kw: dict, settings: dict,
                    params0: dict, blank_curve, focus_display: str) -> None:
    """[批量处理] 的主循环：逐条取曲线 → 跑链 → 落产物 → 记台账。

    从 _proc_batch_apply 拆出来（2026-09-30）：它现在有两种到达方式——
    曲线都齐就直接跑；缺"按当前设置那条曲线"的（改了 2θ 还没重算的批）
    先由 _proc_ensure_curves 后台补算，算完在回调里再跑同一段。
    """
    npt = int(kw["npt"])
    done = skipped = from_product = from_bg = 0
    skip_why = {}                         # 跳过原因 → 个数（末尾各记一行）
    out_of_range = files_with_drop = 0    # 锚点落到数据范围外的统计
    records = []    # [(源文件, 产物键)]：整批完了写一次台账（不是每张一次）
    seen = set()    # 同一文件只扣一份（勾了它的原始条目又勾了它的 1D 产物）
    for i, source in enumerate(targets):
        path = Path(source.path)
        if str(path) in seen:
            skipped += 1
            _log(window, f"跳过 {source.display}：同一文件在批里只扣一份"
                         "（另一条已经算过了）")
            continue
        seen.add(str(path))
        # 一条来源的"取曲线 → 重取锚点强度 → 跑整条链 → 落产物 → 记配方"
        # 整段都在 _process_source 里（与 [采用这份结果] 共用同一份实现）
        res = _process_source(window, source, kw, settings, params0,
                              blank_curve, anchor_source=focus_display)
        if not res["ok"]:
            skipped += 1
            skip_why[res["why"]] = skip_why.get(res["why"], 0) + 1
            continue
        if res["dropped"]:
            # 锚点落在本图数据范围外：**丢掉**（np.interp 会静默钳到端点值，
            # 等于拿边缘那一点当锚点，图上完全看不出来）。跨批次复用配方时
            # 这条最常见——用户 2026-09-27 讨论时一起定的口径。
            out_of_range += res["dropped"]
            files_with_drop += 1
        records.append((res["path"], res["key"]))
        if res["kind"] == gui_sources.ONED:
            from_product += 1
        elif res["kind"] == gui_sources.BG:
            from_bg += 1
        done += 1
        if (i + 1) % 20 == 0:
            _log(window, f"批量处理：{i + 1}/{len(targets)}…")
    tail = f"，跳过 {skipped} 个" if skipped else ""
    via = ""
    if from_product:
        via += f"，其中 {from_product} 条来自 1D 产物"
    if from_bg:
        via += f"，其中 {from_bg} 条是处理产物（按各自的原始曲线重做）"
    _log(window, f"批量处理完成：{done}/{len(targets)} 个文件"
                 f"（{process.chain_label(settings)}）{tail}{via}")
    # 跳过原因逐条写清楚（不静默）：最常见的是"2θ 改过、按新范围还没算"
    # ——旧版这种情况会**拿面板里旧范围的曲线**接着扣，键与数据对不上
    for why, n in sorted(skip_why.items(), key=lambda kv: -kv[1]):
        _log(window, f"  跳过 {n} 个：{why}")
    if out_of_range:
        _log(window, f"注意：{files_with_drop} 个文件上有 {out_of_range} 个锚点"
                     f"落在数据 2θ 范围外，已跳过那些锚点"
                     f"（跨批次复用配方时常见——确认装置/几何没换）")
    # 记台账：这一批 = 文件坞里的一个"扣背景"分组（用户 2026-09-25 定：
    # 每次 [批量扣背景] 一组）。整批写一次，中途不留半截台账。跳过的
    # 文件不进台账（它们没有产物，进组了也是空壳）
    if records:
        label, batch = _proc_batch_label(settings)
        stage_cache.record_batch(
            "bg", batch, label=label, items=records,
            config=window.config_name, npt=npt,
            tth_min=kw.get("tth_min"), tth_max=kw.get("tth_max"),
            settings={k: v for k, v in settings.items() if k != "anchors"},
            note=f"{len(records)} 个文件")
        _log(window, f"产物分组：{label}（{len(records)} 个文件，"
                     f"文件栏里可整组勾选去 [对比]/[热图]）")
        window.refresh_groups()   # 文件栏里立刻长出这一组

def _proc_batch_label(settings: dict) -> tuple:
    """这一批处理的标签与批次号。

    标签给人看（进文件坞的分组名 + 日志）：「处理产物 09-25 16:40（锚点 5 个、
    窗口 2°、平滑 0.15°、删 2–3°）」——三项都真的作用在数据上，所以三项都
    写；组名必须描述数据本身，不能写没生效的东西（见 process.chain_label）。
    时间 = **最近一次保存的时刻**（同一配置重复点会把它刷新）。

    批次号给程序用 = **处理链参数的哈希（不含时间戳，2026-10-07 改）**：
    用户的原话——"每次只有当前配置的一批进入文件栏……不同参数多次点击，
    那就都进文件栏"。所以：
      * 同一套参数不管点几次、隔多久点，都归同一批：台账里再记一次 =
        那份节点原地更新（组名里的时间与排序刷新到最近这次，**不再冒
        "· 第 2 组"重复组**——改之前批次号带秒级时间戳，隔一秒再点就
        长出第二个组）；
      * 换了参数（模式/窗口/锚点/平滑/裁剪——proc_settings_hash 的全部门槛）
        就是另一批：各组并存，正是"不同参数分别点击"的保存。
    """
    label = (f"处理产物 {time.strftime('%m-%d %H:%M')}"
             f"（{process.chain_label(settings)}）")
    batch = stage_cache.proc_settings_hash(settings)
    return label, batch
