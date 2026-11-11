"""参数坞里的背景/预设处理设置 helper。

2026-10-21 从 gui/app.py 原样搬出（只改结构，不动逻辑）。这些函数只被
参数坞和 create_window 调用；app.py 再导出，外部照旧经 gui_app 取用。
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QMainWindow, QMessageBox, QPushButton, QWidget)

from xrd_toolkit.config import entry_display
from xrd_toolkit.gui.file_dock import FILE_FILTER
from xrd_toolkit.gui.panel_state import (
    _bg_geom_sig, _collect_geometry, _log, _note_user_edit, _pending_names,
    _proc_settings)
from xrd_toolkit.gui.plot_views import (
    _compute_integration, _refresh_proc, _retarget_product_panel_to_source,
    _spawn_task, apply_recipe, chain_label_of, recipe_text)


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
    _sync_preset_proc_rows(window)
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


def _sync_preset_proc_rows(window: QMainWindow) -> None:
    """处理页底部两行：本图上次处理（只读）+ 当前预设的处理设置。

    由 _refresh_proc 收尾时经 `window._preset_proc_rows_sync` 回调一次
    （与 _bg_rows_sync 同一套路，避免 plot_views 反向 import app）。
    """
    # 处理来处：这个文件上次是用哪套设置处理的（**只作展示**，不回填面板）
    lbl = getattr(window, "bg_prov_lbl", None)
    if lbl is not None:
        dock_ = window.plot_docks.get(window.focus_panel)
        path_ = getattr(dock_, "panel_file", None)
        recipe = (getattr(window, "proc_recipes", None)
                  or {}).get(str(path_))
        lbl.setText(recipe_text(recipe))
    preset_lbl = getattr(window, "preset_proc_lbl", None)
    if preset_lbl is None:
        return
    name = str(getattr(window, "preset_name", "") or "")
    preset = getattr(window, "preset", None) or {}
    proc = preset.get("processing")
    if isinstance(proc, dict):
        preset_lbl.setText(
            f"当前预设「{name}」：{chain_label_of(proc)}\n"
            "点 [套用当前预设] 把这套设置用到编辑对象；调好新设置后"
            "点 [存进预设…] 写回去。")
    else:
        preset_lbl.setText(
            f"当前预设「{name}」还没有处理设置；调好背景/平滑/裁剪后"
            "点 [存进预设…]。")


def _apply_preset_processing(window: QMainWindow) -> None:
    """[套用当前预设]：把当前预设的 `processing` 用到编辑对象（显式动作）。

    锚点位置照搬、强度按本图曲线重取；其余设置填进本页控件并立刻重画。
    面板打开时**不会**自动套用（见 plot_views._proc_recipe）。
    """
    preset = getattr(window, "preset", None) or {}
    recipe = preset.get("processing")
    if not isinstance(recipe, dict):
        _log(window, "当前预设没有处理设置；先在「处理」页调好，"
                     "再点 [存进预设…]")
        return
    dock = window.plot_docks.get(window.focus_panel)
    path = getattr(dock, "panel_file", None)
    if dock is None or path is None:
        _log(window, "先把一张 1D 图设为编辑对象，再套用处理设置")
        return
    if recipe.get("mode") == "blank":
        # 空扫相减靠的是那张**空扫图**（window.bg_blank，在内存里、不进预设）
        _log(window, "当前预设的处理设置是「空扫相减」：它靠的是那张"
                     "空扫图，空扫图不在预设里，套不了")
        return
    # 处理产物面板：**就地换成它底下的原始 1D 曲线**，设置照常往上套
    if not _retarget_product_panel_to_source(window, dock):
        return
    apply_recipe(window, dock, path, recipe)


def _save_processing_to_preset_dialog(window: QMainWindow) -> None:
    """[存进预设…]：把编辑对象当前处理设置写进一份预设（只改 processing）。

    预设的几何 / 相库 / 原位目录 / 掩膜工程保持原值；用户预设已存在时
    先问一次，内置预设不能覆盖（要另存新名字）。
    """
    dock = window.plot_docks.get(window.focus_panel)
    path = getattr(dock, "panel_file", None)
    if dock is None or path is None:
        _log(window, "先把一张 1D 图设为编辑对象，再把当前处理设置存进预设")
        return
    settings = _proc_settings(window, dock, path)
    from xrd_toolkit.services import recipes
    if not recipes.is_meaningful(settings):
        _log(window, "「处理」页三项都关着（或是空扫相减），没有可存的设置")
        return
    snapshot = recipes.normalize(settings)
    from xrd_toolkit.presets import (
        DEFAULT_PRESET_NAME, get_preset, is_builtin, preset_names,
        save_user_preset)
    current = str(getattr(window, "preset_name", DEFAULT_PRESET_NAME))
    default = current if not is_builtin(current) else f"{current} 副本"
    name, ok = QInputDialog.getText(
        window, "存进预设",
        "预设名称（处理设置只写进这一份预设；几何/相库等保持原值）：",
        text=default)
    if not ok or not str(name).strip():
        return
    name = str(name).strip()
    if is_builtin(name):
        _log(window, f"「{name}」是内置预设，不能覆盖；换个名字")
        return
    existing = name in preset_names()
    if existing:
        answer = QMessageBox.question(
            window, "存进预设",
            f"把当前处理设置写进用户预设「{name}」？\n"
            "该预设的几何 / 相库 / 原位目录 / 掩膜工程保持原值。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
    # 只改 processing：其余字段照抄目标预设（已有）或当前预设（新建副本）。
    # 不能走"当前设置快照"——那会把没打开的工作台清空、把几何漂移一并
    # 写进去，与按钮上"其他项保持原值"的承诺不符。
    from xrd_toolkit.presets import raw_preset
    template = name if existing else str(
        getattr(window, "preset_name", "") or "")
    values = dict(raw_preset(template) or {})
    values["processing"] = snapshot
    try:
        save_user_preset(name, values)
    except (OSError, ValueError) as err:
        QMessageBox.warning(window, "存进预设", str(err))
        return
    _log(window, f"处理设置已存进预设：{name}"
                 f"（{chain_label_of(snapshot)}）")
    from xrd_toolkit.gui.preset_ops import refresh_preset_combo
    refresh_preset_combo(window)          # 新建的预设立刻出现在下拉里
    if getattr(window, "preset_name", None) == name:
        window.preset = get_preset(name)
        _sync_preset_proc_rows(window)


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

    _spawn_task(window, key, _compute_integration,
                (path_str, geom, npt), done, error)
    _log(window, f"开始积分空扫图：{Path(path_str).name}")


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
