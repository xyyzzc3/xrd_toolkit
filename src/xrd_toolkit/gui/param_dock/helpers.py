"""参数坞里的背景/配方 helper。

2026-10-21 从 gui/app.py 原样搬出（只改结构，不动逻辑）。这些函数只被
参数坞和 create_window 调用；app.py 再导出，外部照旧经 gui_app 取用。
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QPushButton, QWidget)

from xrd_toolkit.config import entry_display
from xrd_toolkit.gui.file_dock import FILE_FILTER
from xrd_toolkit.gui.panel_state import (
    _bg_geom_sig, _collect_geometry, _log, _note_user_edit, _pending_names,
    _proc_settings)
from xrd_toolkit.gui.plot_views import (
    _compute_integration, _refresh_proc, _retarget_product_panel_to_source,
    _spawn_task, apply_recipe, chain_label_of, recipe_text)
from xrd_toolkit.services import recipes as recipe_store


def _bg_edit_dock(window: QMainWindow):
    """"背景扣除"里锚点操作作用的面板：优先当前编辑对象（若是 1D 面板），
    否则第一个开着的 1D 面板；都没有就 None。

    锚点在 1D 图上点选（那里能看到单条曲线的真实形状），但作用范围是
    **文件**——对比/热图/瀑布里同一条曲线也跟着扣。
    """
    key = window.focus_panel
    if key is not None and key.split("|", 1)[0] == "1D":
        dock = window.plot_docks.get(key)
        if dock is not None:
            return dock
    for k, d in window.plot_docks.items():
        if k.split("|", 1)[0] == "1D":
            return d
    return None


def _bg_anchor_file(window: QMainWindow):
    """锚点当前作用在哪个文件上（路径，供显示/日志）。"""
    dock = _bg_edit_dock(window)
    return getattr(dock, "panel_file", None) if dock is not None else None


def _bg_anchor_count(window: QMainWindow) -> int:
    """当前作用文件的锚点数（给参数坞那个计数标签用）。"""
    path = _bg_anchor_file(window)
    if path is None:
        return 0
    return len((getattr(window, "bg_anchors", None) or {}).get(str(path), []))


def _update_bg_count(window: QMainWindow) -> None:
    """刷新锚点计数标签：个数 + **覆盖范围**。

    为什么非写范围（2026-09-27）：锚点之外那几度不是点出来的，是沿自动
    基线的形状推出来的（见 background.fit_anchor_baseline）。只显示"21 点"
    会让人以为整条曲线都点过了——用户那批锚点全挤在 1.29–2.70°、数据却到
    8°，标签上却只有个数，看不出问题来。
    """
    lbl = getattr(window, "bg_count_lbl", None)
    if lbl is None:
        return
    path = _bg_anchor_file(window)
    if path is None:
        lbl.setText("")          # 没有可作用的 1D 面板：数字对用户没有意义
        return
    anchors = (getattr(window, "bg_anchors", None) or {}).get(str(path), [])
    if not anchors:
        lbl.setText("0 个锚点")
        return
    xs = sorted(float(x) for x, _ in anchors)
    # "覆盖"两个字是这行标签的意义所在（锚点之外是推出来的，不让用户
    # 以为整条曲线都点过了，TestBackgroundSubtraction 钉着）——坞窄时由
    # _ElideLabel 打省略号、悬停看全，不需要为了宽度减字
    lbl.setText(f"{len(xs)} 个锚点（覆盖 {xs[0]:.2f}–{xs[-1]:.2f}°）")


def _sync_bg_rows(window: QMainWindow) -> None:
    """按背景扣除模式收起/放出专用行，并同步按钮可用状态。

    三个模式各有一行专用控件（空扫：选图+归一化；自动：窗口宽度；
    锚点：拾取+清空+计数），只放出当前模式那一行——参数坞窄，全部
    摊开既挤又让人不知道该填哪个。隐藏的行不占布局高度，也不计入
    坞的最小宽度（所以本函数只允许在宽度量完之后调用）。
    """
    rows = getattr(window, "bg_rows", None)
    if not rows:
        return
    mode = window.params["背景扣除模式"].currentData()
    # 锚点行在 auto 下也露出来：自动 + 锚点校正（2026-09-27）就是靠它点
    # 锚点把自动基线校准到"纯背景"上
    for name, row in rows.items():
        row.setVisible(name == mode
                       or (name == "anchor" and mode == "auto"))
    _sync_recipe_rows(window)
    # "显示原始曲线对比"只在真的在扣的时候才有意义
    window.params["背景显示原始"].setEnabled(mode != "off")
    window.params["负值截断为 0"].setEnabled(mode != "off")
    if hasattr(window, "bg_pick_btn"):
        pickable = mode in ("anchor", "auto")   # auto = 自动 + 锚点校正
        window.bg_pick_btn.setEnabled(pickable)
        window.bg_clear_btn.setEnabled(pickable)
        if not pickable and window.bg_pick_btn.isChecked():
            window.bg_pick_btn.setChecked(False)   # 离开锚点模式即停止拾取
    _update_bg_count(window)


def _sync_recipe_rows(window: QMainWindow) -> None:
    """「配方」这一小节的两样显示：「本图配方」来处那行 + 配方下拉。

    **为什么单独一个函数、而且要在记录配方之后调**（2026-09-28 探针查出来的
    用户报"套用无效"）：填下拉的动作原先只在 _sync_bg_rows 里做，而
    _on_bg_mode 是"_sync_bg_rows → _refresh_proc"这个顺序——配方是
    _refresh_proc 末尾才写进 window.proc_recipes 的，于是**刚调完参数那一刻
    下拉里还只有占位项**（"还没有配方…"），用户按 [套用] 只得到一句"先在下拉
    里选一份配方"。真实的手势恰恰就是"调好参数马上点 [套用]"。

    现在由 _refresh_proc 收尾时经 window._recipe_rows_sync 回调一次（与
    _bg_rows_sync 同一套路，避免 plot_views 反向 import app），顺序问题从
    根上消失——不管谁先谁后，记录之后就刷一次。
    """
    # 处理来处：这个文件上次是用哪套设置处理的（**只作展示**，不回填面板——
    # 用户 2026-09-27 晚："原始数据会被改变…应该是原始 tif"）
    lbl = getattr(window, "bg_prov_lbl", None)
    if lbl is not None:
        # 配方按 str(path) 存；面板自己记着它的文件（panel_file）
        dock_ = window.plot_docks.get(window.focus_panel)
        path_ = getattr(dock_, "panel_file", None)
        recipe = (getattr(window, "proc_recipes", None)
                  or {}).get(str(path_))
        lbl.setText(recipe_text(recipe))
    _recipe_combo_fill(window)


def _recipe_combo_fill(window: QMainWindow) -> None:
    """配方下拉：本图配方（这个文件上次那套）+ 已保存的命名配方。

    data 用字符串（"local" / "saved|名字"）：`findData` 比的是值，字符串
    最稳（元组也能比，但不值得赌 PySide 的转换）。
    """
    combo = getattr(window, "recipe_combo", None)
    if combo is None:
        return
    dock = window.plot_docks.get(window.focus_panel)
    path = getattr(dock, "panel_file", None)
    local = (getattr(window, "proc_recipes", None) or {}).get(str(path))
    keep = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    if local:
        combo.addItem(f"本图配方（{Path(path).name}）", "local")
    for name in recipe_store.names():
        combo.addItem(f"已保存：{name}", f"saved|{name}")
    if combo.count() == 0:
        combo.addItem("（还没有配方：调好参数后点 [存成配方…]）", "")
    idx = combo.findData(keep) if keep else -1
    combo.setCurrentIndex(max(0, idx))
    combo.blockSignals(False)


def _selected_recipe(window: QMainWindow):
    """下拉里选中的那份配方 → (配方 dict 或 None, 是不是"本图配方")。"""
    combo = getattr(window, "recipe_combo", None)
    data = combo.currentData() if combo is not None else None
    dock = window.plot_docks.get(window.focus_panel)
    path = getattr(dock, "panel_file", None)
    if not data or path is None:
        return None, False
    if data == "local":
        return (getattr(window, "proc_recipes", None) or {}).get(str(path)), True
    return recipe_store.load_all().get(str(data).split("|", 1)[1]), False


def _apply_selected_recipe(window: QMainWindow) -> None:
    """[套用]：把选中的配方用到**编辑对象**那张图上（显式动作，按了才动）。

    锚点位置照搬、强度按本图曲线重取（跨批次复用的核心）；其余设置填进本页
    控件并立刻重画。面板打开时**不会**自动套用——见 plot_views._proc_recipe。
    """
    recipe, _is_local = _selected_recipe(window)
    dock = window.plot_docks.get(window.focus_panel)
    path = getattr(dock, "panel_file", None)
    if recipe is None:
        _log(window, "先在下拉里选一份配方，再点 [套用]"
                     "（还没有就调好参数 [存成配方…]）")
        return
    if dock is None or path is None:
        _log(window, "先把一张 1D 图设为编辑对象，再套配方")
        return
    if (recipe or {}).get("mode") == "blank":
        # 空扫相减靠的是那张**空扫图**（window.bg_blank，在内存里、不进配方）
        # ——套上去也只会得到"没有空扫可减"，不如直说
        _log(window, "这份配方是「空扫相减」：它靠的是那张空扫图，"
                     "空扫图不在配方里，套不了")
        return
    # 处理产物面板：**就地换成它底下的原始 1D 曲线**，配方照常往上套
    # （用户 2026-09-28 第 3 条："套用别的配方的处理图不能二次处理了，修正"）。
    # 旧版在这里一句拒绝（怕二次相减），界面上就没路可走了——现在把"输入"
    # 换成没扣过背景的那条曲线，二次相减的隐患从根上没有了
    # （见 plot_views._retarget_product_panel_to_source）
    if not _retarget_product_panel_to_source(window, dock):
        return
    apply_recipe(window, dock, path, recipe)


def _save_recipe_dialog(window: QMainWindow) -> None:
    """[保存为配方…]：把编辑对象这张图当前这套设置存成命名配方。

    存的是**设置本身**（模式/窗口/锚点位置/平滑/裁剪）——强度不参与跨批次
    复用，锚点的强度只在"本图配方"里作参考（见 services/recipes 的说明）。
    """
    dock = window.plot_docks.get(window.focus_panel)
    path = getattr(dock, "panel_file", None)
    if dock is None or path is None:
        _log(window, "先点一张 1D 图（编辑对象），再保存配方")
        return
    settings = _proc_settings(window, dock, path)
    if settings.get("mode") == "off" and not settings.get("smooth_deg") \
            and not settings.get("cut_ranges"):
        _log(window, "「处理」页三项都关着，没有可存的配方")
        return
    if settings.get("mode") == "blank":
        _log(window, "「空扫相减」存不了配方：它靠的是那张空扫图，"
                     "空扫图不在配方里（别的模式都能存）")
        return
    default = Path(path).stem
    name, ok = QInputDialog.getText(window, "存成配方",
                                    "配方名（例如：LMFP 自动+锚点）：",
                                    text=f"{default} 配方")
    if not ok or not str(name).strip():
        return
    recipe_store.save(str(name).strip(), settings)
    _log(window, f"配方已保存：{name}（{chain_label_of(settings)}）"
                 "——「处理」页下拉里可以给别的批次套用")
    _recipe_combo_fill(window)


def _delete_selected_recipe(window: QMainWindow) -> None:
    """[删除]：删掉下拉里选中的**已保存**配方（"本图配方"跟着文件走，删不掉）。"""
    recipe, is_local = _selected_recipe(window)
    combo = getattr(window, "recipe_combo", None)
    data = combo.currentData() if combo is not None else None
    if not data:
        _log(window, "先在下拉里选一份配方")
        return
    if is_local:
        _log(window, "「本图配方」删不掉——它跟着这个文件走；"
                     "只有 [存成配方…] 存下来的那些能删")
        return
    name = str(data).split("|", 1)[1]
    if recipe_store.delete(name):
        _log(window, f"配方已删除：{name}")
    _recipe_combo_fill(window)


def _on_bg_mode(window: QMainWindow) -> None:
    """背景扣除模式切换：调好专用行的显隐、立刻重画、记一条日志。

    模式是显示参数（不重新积分），所以走 _refresh_proc 实时重画——
    与其余显示参数"等 [应用]"不同，理由见 _refresh_proc 的说明。
    """
    _sync_bg_rows(window)
    mode = window.params["背景扣除模式"].currentData()
    labels = {"off": "关闭", "blank": "空扫相减", "auto": "自动基线",
              "anchor": "手动锚点"}
    if mode == "blank" and getattr(window, "bg_blank", None) is None:
        _log(window, "背景扣除：空扫相减——还没选空扫图，先点 [选择空扫图]")
    elif mode == "anchor" and _bg_anchor_count(window) == 0:
        _log(window, "背景扣除：手动锚点——点 [拾取锚点] 后在 1D 图上"
                     "左键点选「只有背景」的位置")
    else:
        _log(window, f"背景扣除：{labels.get(mode, mode)}")
    _refresh_proc(window)


def _on_pick_anchor(window: QMainWindow, on: bool) -> None:
    """[拾取锚点] 开关：打开后在 1D 面板上点选锚点。

    点选逻辑在 plot_views 的 _anchor_press / _anchor_release（按 5 px
    位移阈值区分"点击"与"拖拽平移"）。
    """
    if on:
        dock = _bg_edit_dock(window)
        name = Path(dock.panel_file).name if dock is not None else "（无 1D 面板）"
        _log(window, f"开始拾取锚点：在 1D 图上左键点选「只有背景」的位置"
                     f"（对象 {name}，再点已有的锚点可删除）")
    else:
        _log(window, "已停止拾取锚点")


def _on_clear_anchors(window: QMainWindow) -> None:
    """[清空锚点]：清掉当前作用文件的全部锚点并立刻重画。"""
    path = _bg_anchor_file(window)
    if path is None:
        _log(window, "清空锚点：先打开一个 1D 面板")
        return
    # 不能写 `getattr(...) or {}`：空 dict 是 falsy，`or` 会换成临时新
    # 字典，pop 掉的是临时的（同 _anchor_release 的坑）
    anchors = getattr(window, "bg_anchors", None)
    if anchors is None:
        anchors = window.bg_anchors = {}
    n = len(anchors.get(str(path), []))
    if not n:
        _log(window, f"清空锚点：{Path(path).name} 本来就没有锚点")
        return
    anchors.pop(str(path), None)
    _update_bg_count(window)
    _refresh_proc(window)
    _log(window, f"已清空 {Path(path).name} 的 {n} 个锚点")


def _on_choose_blank(window: QMainWindow) -> None:
    """[选择空扫图]：挑一张空扫图 → 后台按当前几何积分 → 存进窗级缓存。

    空扫只需积分一次（三种模式共用曲线层扣除，见 services/background.py
    关于积分线性的说明），之后切文件/调参数都不再重算它。
    几何取参数坞当前选中的配置——空扫必须与样品同几何，否则"先扣图再
    积分 ≡ 先积分再扣"的线性前提不成立。
    """
    path_str, _ = QFileDialog.getOpenFileName(
        window, "选择空扫图（没有样品的空白测量）", "", FILE_FILTER)
    if not path_str:
        return
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    key = f"空扫|{path_str}"   # 独立键：不占 1D 面板缓存

    def done(window_, key_, task, result):
        tth, intensity = result
        window.bg_blank = {"path": path_str, "tth": tth,
                           "intensity": intensity,
                           "geom_sig": _bg_geom_sig(geom)}
        # 换了空扫图，两条提示重新计起
        window._bg_geom_warned = False
        window._bg_cover_warned = False
        _refresh_proc(window)
        _log(window, f"空扫积分完成：{Path(path_str).name}"
                     f"（{len(tth)} 个点，2θ {tth[0]:.3f}–{tth[-1]:.3f}°）")

    def error(msg):
        _log(window, f"空扫积分失败：{Path(path_str).name}——{msg}")

    mask = getattr(window, "current_mask", None)
    args = ((path_str, geom, npt) if mask is None
            else (path_str, geom, npt, mask))
    _spawn_task(window, key, _compute_integration, args, done, error)
    _log(window, f"开始积分空扫图：{Path(path_str).name}"
                 + ("（使用当前 Mask）" if mask is not None else ""))


def _connect_pending_hooks(window: QMainWindow) -> None:
    """给"要按一下才生效"的控件挂上「未应用」灰字的刷新（用户 2026-09-30）。

    只管刷灰字这一件事：改动**按图**记住、切图时回填，都是 panel_state 那套
    （_capture_pending / _overlay_pending）在 _set_focus 里做的。名单见
    panel_state._PENDING_GROUPS——即改即生效的控件不在里面。
    """
    for name in _pending_names():
        w = window.params.get(name)
        if w is None:
            continue
        # 回放快照（切图）时程序在写控件——那不算"用户改动"，别把
        # _pending_consumed 打成"没被用掉"（2026-10-03：甲方案拿这个标记
        # 判"盒子里是不是用户刚填的指令"，回放误标会让别的图的范围当成指令）
        hook = lambda *_, win=window, n=name: (                       # noqa: E731
            None if getattr(win, "_param_replaying", False)
            else _note_user_edit(win, n))
        if isinstance(w, QCheckBox):
            w.toggled.connect(hook)
        elif isinstance(w, QComboBox):
            w.currentIndexChanged.connect(hook)
        else:
            w.valueChanged.connect(hook)


def _fit_max_width(box, old_cap=None) -> None:
    """把数字框的上限设成"完整显示量程里最长文本"所需的宽度（= sizeHint）。

    写死过的 84/72 是在某台机器、某个量程上量的：换平台（mac 转盘的
    内边距更肥）或换量程（纵轴上限到 1e9，最长文本是 "1000000000.0"）
    就不够。实测（2026-10-04）：点数缺 2px、对比度/热图/纵轴上下限各
    缺 33px——上限一旦盖住，坞拖多宽都补不回来，用户看到的就是
    "点数不全，拉伸参数栏也不动"。sizeHint 按**当前平台的字体与转盘
    内边距**、按量程里最长的文本自己算，拿它当上限：坞够宽时任何可能
    的值都能完整显示，也不会长过需要的宽度（坞再宽就是行内留白，
    不是把数字框摊大）。

    参数 old_cap = 这个框原来写死的上限。转盘的**有效下限**是
    min(sizeHint, 上限)——上限抬到 sizeHint 就等于"压不窄了"，会把
    坞的宽下限顶大（0.1.1 特意做过的"窄坞能压"不许丢）。所以原来被
    旧上限压着的框（old_cap < sizeHint，正是会裁长值的那批）把旧上限
    降级成显式下限：任何坞宽下都不比以前窄，宽出来的部分才用来"看全"。

    用法：必须在 setRange / setDecimals / setSuffix 之后调用（后缀算
    进文本宽度），得到的宽度随平台自适应，不写数字。
    """
    box.ensurePolished()
    need = box.sizeHint().width()
    if old_cap is not None and old_cap < need:
        box.setMinimumWidth(max(old_cap, box.minimumWidth()))
    box.setMaximumWidth(need)
