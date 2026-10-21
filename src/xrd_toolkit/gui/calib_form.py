"""校准表单：参数坞第 0 页、编辑当前几何、表单复位、按钮/标签同步。

2026-10-21 从 calib.py 原样搬出（只改结构，不动逻辑）。
calib 再导出；对 _default_config_name / _start_* / _refresh_current_metrics
用函数内延迟导入破环。
"""
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.colors import LogNorm
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QMdiSubWindow, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget)

# 常量都住在 calib_model（纯逻辑层）。这里把面板/表格用的几个也一并
# 导入，保持『经 gui_calib 取用』的历史引用可用（同 app.py 的再导出惯例）。
from xrd_toolkit.gui.calib_model import (
    CALIB_PANEL_RESERVE_PX, COMPARE_HINT, COMPARE_ROWS, CP_COLOR,
    KIND_LABELS, MIN_POINTS, MIN_RINGS, PANEL_SCALE, RING_COLOR,
    SLOT_LABELS, SNAP_TOL_DEG,
    SOURCE_IMPROVE_MIN_PX, _add_custom_result, _add_result, _adopt_decision,
    _calib_state, _clear_calib_session, _fill_slot, _geom_to_result_shape,
    _next_name, _result_by_name, _result_dev, _result_to_geom, _slot_label,
    _verdict)
from xrd_toolkit.gui.calib_table import (
    _on_slot_changed, _refresh_table, _sync_slot_combos)
from xrd_toolkit.gui.calib_panel import (
    _CalibSubWindow, _apply_selected_ring, _calib_image,
    _calib_standard_path, _clear_calib_points, _close_calib_panel,
    _geom_px_keys, _load_image, _open_calib_panel, _redraw_calib,
    _redraw_calib_if_open, _undo_calib_point, _warn_rings_off_image)
# 标样辅助（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.calib_calibrant import (
    _adopt_result, _borrow_entry, _current_geom_text, _ensure_current,
    _init_calibrant_for_path, _on_calibrant_changed, _on_ring_count_changed,
    _pixel_ok, _set_pixel_ok, _sync_calibrant_widgets, _update_ring_count,
    _warn_pixel_unchecked)
from xrd_toolkit.gui.config_ops import (
    _delete_config, _import_poni, _save_calib_config, _save_poni,
    _suggest_config_key, _sync_del_config_btn)

from xrd_toolkit import config
from xrd_toolkit.core.processor import find_ring_center, fit_center_from_rings
from xrd_toolkit.gui.panels import _settle
from xrd_toolkit.gui.panel_state import (
    _auto_contrast_values, _collect_geometry, _log, _reload_config_combo)
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.services.calibrants import (
    COMMON_CALIBRANTS, DEFAULT_CALIBRANT, MAX_GUI_RINGS,
    calibrant_label, calibrant_short_label, calibrant_tooltip,
    complete_ring_count, detect_calibrant_from_stem, ring_total)
from xrd_toolkit.services.integrator import (
    calibrate_geometry, refine_geometry_from_points, snap_ring)
from xrd_toolkit.services.ring_metrics import ring_metrics

def _edit_current(window: QMainWindow) -> None:
    """[编辑…]：对话框里改当前配置的几何（改完标成"自定义"）。

    对话框顶部可以先"从条目预填"（= 借另一条条目的几何），再逐个改数值
    ——于是"借用其它批次"与"手输"是同一件事的两个入口，页面上不占地方。

    两处按用户 2026-09-28 第 1、2 条改的：
      * **束心行/列也在这里改**（`beam_center_rc`，表里的"环心行/环心列"
        两行）。改之前对话框里根本没有这两个框，`_beam` 存了却从没被读回
        ——手输几何的束心永远是接手时那一个（"从条目预填"也只搬 7 个数字、
        留下旧束心），而束心正是校准最常动的东西；
      * 确定之后这条几何**进累积结果列表**（`_add_custom_result`）：于是
        A/B 两个下拉（选项 = 结果名）里能选到它、能钉进槽、能被采纳成
        「当前配置」，切走再切回来也还在——改之前它只活在 current_geom
        这个没有户口的变量里。
    """
    state = _calib_state(window)
    geom = dict(state["current_geom"] or {})
    if not geom:
        _log(window, "当前配置还没设定（先选一个标准文件进校准模式）")
        return
    px = _geom_px_keys(geom)
    beam = geom.get("beam_center_rc") or (None, None)
    row0, col0 = (beam + (None, None))[:2] if beam else (None, None)
    dlg = QDialog(window)
    dlg.setWindowTitle("编辑当前配置")
    form = QFormLayout(dlg)
    pre = QComboBox()
    pre.addItem("（不改名称，只编辑数值）", None)
    for name in config.CONFIGS:
        pre.addItem(f"从条目预填：{name}", name)
    form.addRow("预填", pre)
    fields = (
        ("像素尺寸 (µm)", "pixel_um", px["pixel_size_m"] * 1e6, 1, " µm"),
        ("波长 (Å)", "wavelength_a", px["wavelength_m"] * 1e10, 4, " Å"),
        ("距离 (mm)", "dist_mm", px["dist_m"] * 1e3, 2, " mm"),
        ("PONI1 (px)", "poni1_px", px["poni1_px"], 2, ""),
        ("PONI2 (px)", "poni2_px", px["poni2_px"], 2, ""),
        ("rot1 (°)", "rot1_deg", px["rot1_deg"], 4, " °"),
        ("rot2 (°)", "rot2_deg", px["rot2_deg"], 4, " °"),
    )
    boxes = {}
    for text, key_, value, dec, suffix in fields:
        box = QDoubleSpinBox()
        box.setRange(-1e5, 1e5)
        box.setDecimals(dec)
        if suffix:
            box.setSuffix(suffix)
        box.setValue(float(value))
        form.addRow(text, box)
        boxes[key_] = box
    # 束心（行/列，像素）：表里的"环心行/环心列"两行就是它。留 0 位小数
    # （它是像素坐标；默认值取当前几何，没有就 0——图上点一下就能改）
    for text, key_, init in (("束心行 (px)", "beam_row", row0),
                             ("束心列 (px)", "beam_col", col0)):
        box = QDoubleSpinBox()
        box.setRange(-1e5, 1e5)
        box.setDecimals(2)
        box.setValue(float(init) if init is not None else 0.0)
        box.setObjectName(f"{key_}_spin")     # 测试按名字找（不靠索引顺序）
        form.addRow(text, box)
        boxes[key_] = box

    def prefill(_idx):
        key = pre.currentData()
        if key is None:
            return
        entry = config.CONFIGS[key]
        g = entry["geometry"]
        boxes["pixel_um"].setValue(g["pixel_size_m"] * 1e6)
        boxes["wavelength_a"].setValue(g["wavelength_m"] * 1e10)
        boxes["dist_mm"].setValue(g["dist_m"] * 1e3)
        boxes["poni1_px"].setValue(g["poni1_m"] / g["pixel_size_m"])
        boxes["poni2_px"].setValue(g["poni2_m"] / g["pixel_size_m"])
        boxes["rot1_deg"].setValue(g["rot1_deg"])
        boxes["rot2_deg"].setValue(g["rot2_deg"])
        # 束心也跟着预填（改之前这里存进 boxes["_beam"] 就再没人读过：
        # "从条目预填"于是只搬 7 个数字、留下旧束心）
        bc = entry.get("beam_center") or (None, None)
        if bc[0] is not None:
            boxes["beam_row"].setValue(float(bc[0]))
        if bc[1] is not None:
            boxes["beam_col"].setValue(float(bc[1]))

    pre.currentIndexChanged.connect(prefill)
    row = QHBoxLayout()
    ok_btn = QPushButton("确定")
    cancel_btn = QPushButton("取消")
    ok_btn.setObjectName("edit_current_ok")
    ok_btn.clicked.connect(dlg.accept)
    cancel_btn.clicked.connect(dlg.reject)
    row.addWidget(ok_btn)
    row.addWidget(cancel_btn)
    form.addRow(row)
    if dlg.exec() != QDialog.Accepted:
        return
    pixel = boxes["pixel_um"].value() * 1e-6
    new_geom = {
        "pixel_size_m": pixel,
        "wavelength_m": boxes["wavelength_a"].value() * 1e-10,
        "dist_m": boxes["dist_mm"].value() * 1e-3,
        "poni1_m": boxes["poni1_px"].value() * pixel,
        "poni2_m": boxes["poni2_px"].value() * pixel,
        "rot1_deg": boxes["rot1_deg"].value(),
        "rot2_deg": boxes["rot2_deg"].value(),
        "beam_center_rc": (boxes["beam_row"].value(), boxes["beam_col"].value()),
    }
    for key in ("tth_min_deg", "tth_max_deg"):
        if key in geom:
            new_geom[key] = geom[key]
    state["current_geom"] = new_geom
    state["slots"]["current"] = None
    state["current_from"] = "手输"
    state["custom"] = True                    # 改过就是"自定义"
    state["current_metrics"] = None
    state["current_error"] = None
    # 进累积列表（用户 2026-09-28 第 1 条）：A/B 下拉与基准都能选到它
    name = _add_custom_result(state, new_geom)
    _log(window, f"当前配置已手动修改（{name}，自定义）：距离 "
                 f"{new_geom['dist_m'] * 1e3:.2f} mm、像素 {pixel * 1e6:.1f} µm"
                 f"、束心 {new_geom['beam_center_rc'][1]:.1f} 列 / "
                 f"{new_geom['beam_center_rc'][0]:.1f} 行"
                 f"——已进结果列表（对比位 A、B 的下拉里能选到它）")
    from xrd_toolkit.gui.calib import _refresh_current_metrics  # 破循环
    _refresh_current_metrics(window)
    _calib_sync(window)
    # 改完即重画：不然表里数字换了、图上的青线还是旧几何的（2026-09-26
    # 用户报的"编辑当前配置青环不动"——改的是状态、漏了这一笔）
    _redraw_calib_if_open(window)

# ══ 参数坞第 0 页：校准表单 ═══════════════════════════════════
def _build_calib_form(window: QMainWindow) -> QWidget:
    """参数坞第 0 页：校准功能。

    布局（自上而下）：
      操作区  [编辑…] [导入][保存] / [删除][存为配置] + 条目 key/备注
              + **像素尺寸确认**（单独一行：校准的前置门禁，得在按钮之前
              看得见；2026-10-01 从数据表里搬回来）
      手动    选点计数 + 撤销/清空 + 用选点精修（右键点 = 选中它改环号）
      自动取点  定位束心并精修
      三列表  当前配置说明行（框顶）+ 灰字列头 + 三个槽下拉（当前配置 /
              A / B——各贴自己那一列；都从累积结果里选；当前配置还能借
              条目或手输，只是不在这个下拉里表达）+ 8 行数值 + 2 行 Δ
              （相对「当前配置」）+ 结论行 + ⚠ 说明
    整页套滚动区；进校准模式时参数坞会按本页内容拉宽（见 _enter_calib）。

    数据表在**最下**（用户 2026-09-30："校准数据放最下，跟自动手动换位置"）：
    流程上先跑自动/手动、再回头看结果，所以动作区在上、结果区在下。
    手动区在自动区**上面**（用户 2026-10-13："校准顺序调为手动在上，自动在下"）：
    手动选点是常用路径，打开校准页第一眼就看到它。

    操作区排在最上面（用户 2026-09-26 晚定）：里面的 [加载参数][保存参数]
    [删除] 作用的就是坞顶「几何配置」那一行选中的条目，紧挨着它才看得出
    "先选条目、再对条目动手"；原先这栏压在 13 行对比表下面，得先滚下去。

    出口 [返回分析模式] 与几何配置一行不在这里：两者都固定在参数坞顶部
    （app._build_param_dock 建，window.calib_exit_btn）——出口原先钉在本页
    最底部，窗口不高时要滚好几百像素才看得见（用户 2026-09-26 报"没有
    退出校准的按钮了"）。像素确认也搬上来挨着"当前配置"那行（它确认的
    就是那行末尾写的像素值），原先夹在「操作」组的按钮堆里。
    """
    from xrd_toolkit.gui.calib import _default_config_name, _start_auto_calib, _start_manual_calib  # 破循环
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setSpacing(4)

    # ── 操作区：编辑 / 配置条目进出 / 保存 ──────────────────
    # 本页第一栏，紧跟坞顶「几何配置」那一行（用户 2026-09-26 晚定）：
    # [加载参数][保存参数][删除] 作用的就是那一行选中的条目，摆在一起
    # 才看得出"先在上面选条目、再对条目动手"这层关系；原先它们压在
    # 13 行对比表下面，要滚下去才够得着，而选中项却远在坞顶。
    ops_box = QGroupBox("操作")
    ops = QVBoxLayout(ops_box)
    btn_edit = QPushButton("编辑当前配置…")
    btn_edit.setObjectName("edit_current_btn")
    btn_edit.setToolTip("用已有条目预填，或直接改像素/波长/距离/"
                        "PONI/倾斜角；改过就是「自定义」，不再被自动替换")
    btn_edit.clicked.connect(lambda: _edit_current(window))
    ops.addWidget(btn_edit)
    row1 = QHBoxLayout()
    row1.setSpacing(2)
    btn_poni = QPushButton("加载几何…")
    btn_poni.setObjectName("poni_btn")    # 保持历史 objectName（测试引用）
    btn_poni.setToolTip("加载 .poni：读 pyFAI 交换格式几何文件，存成配置"
                        "条目并自动选中；同时作为当前配置的起点")
    btn_poni.clicked.connect(lambda: _import_poni(window))
    btn_save_poni = QPushButton("保存几何…")
    btn_save_poni.setObjectName("save_poni_btn")
    btn_save_poni.setToolTip("保存 .poni 几何文件：把<b>当前配置</b>（本页表里"
                             "第一列那个，含手输/自定义）的几何写成 pyFAI 交换"
                             "格式——与 [保存为配置] 取的是同一份几何")
    btn_save_poni.clicked.connect(lambda: _save_poni(window))
    btn_del = QPushButton("删除")
    btn_del.setObjectName("del_config_btn")
    btn_del.setToolTip("删除参数面板「几何配置」里当前选中的<b>用户</b>"
                       "配置条目（内置条目不可删）")
    btn_del.clicked.connect(lambda: _delete_config(window))
    window.del_config_btn = btn_del
    row2 = QHBoxLayout()
    row2.setSpacing(2)
    btn_save_cfg = QPushButton("保存为配置")
    btn_save_cfg.setObjectName("save_calib_config")
    btn_save_cfg.setToolTip("把<b>当前配置</b>存成命名配置条目（本地文件，"
                            "不进 git），保存后参数面板「几何配置」下拉框"
                            "自动选中")
    btn_save_cfg.clicked.connect(lambda: _save_calib_config(window))
    window.calib_save_btn = btn_save_cfg
    for btn in (btn_poni, btn_save_poni, btn_del, btn_save_cfg):
        btn.setStyleSheet("padding: 2px 5px;")   # 紧凑内边距
    for btn in (btn_poni, btn_save_poni, btn_del):
        row1.addWidget(btn, 1)
    row2.addWidget(btn_save_cfg, 1)
    ops.addLayout(row1)
    ops.addLayout(row2)
    lay.addWidget(ops_box)

    # 像素尺寸确认：单独一行，钉在「操作」组下面（页面顶部区域）。
    # **2026-10-05 起它只是提醒，不是门禁**（用户："改为提醒的样式，不要求
    # 用户必须选择了"）：没勾也照常跑/照常存，只是这行旁边常显一条橙色
    # 提示、动作开始时日志再提醒一句（见 _warn_pixel_unchecked）。
    # 位置来回搬过三趟：原挤在「操作」按钮堆里（用户 2026-09-26 报"容易被
    # 忽视"，因为卡在参数坞可见区最下沿）→ 搬进数据表上方 → 2026-09-30
    # 数据表挪到页面最下，它又跟着沉下去了（用户 2026-10-01："把像素尺寸
    # 放上面，太下面了不方便"）→ 现在单独一行。
    chk_pix = QCheckBox("已核对像素尺寸")
    chk_pix.setToolTip("环的位置只由 λ、像素尺寸、距离的组合决定：像素填错"
                       "时拟合会把距离凑回来，环位偏差看着正常但报出的距离"
                       "是错的。只在像素值变了时才清掉核对标记")
    chk_pix.toggled.connect(lambda on: (_set_pixel_ok(window, on),
                                        _calib_sync(window)))
    # 短提醒（原因写在勾选框 tooltip 与动作时的日志里）：不把校准页撑宽
    # ——参数坞宽度是按内容算的（_enter_calib），内容宽度关系到给绘图区
    # 留的位置（CALIB_PANEL_RESERVE_PX），这行只当一个醒目的记号。
    warn_pix = QLabel("⚠ 未核对，建议先核对")
    warn_pix.setWordWrap(True)
    warn_pix.setStyleSheet("color: #c0392b;")
    pix_row = QHBoxLayout()
    pix_row.addWidget(chk_pix)
    pix_row.addWidget(warn_pix, 1)
    lay.addLayout(pix_row)
    window.calib_pixel_chk = chk_pix
    window.calib_pixel_warn = warn_pix

    # ── 标样 + 环数 ─────────────────────────────────────────
    # 这两项一起决定"理论 2θ 阶梯"：标样给 d 值，环数给用前多少条。
    # 放在动作区之前——自动/手动/画环/指标全都吃它；换标样只清本次
    # 会话（选点/结果），当前几何保留（距离/PONI/倾斜角是仪器属性）。
    std_box = QGroupBox("标样")
    std_lay = QHBoxLayout(std_box)
    combo = QComboBox()
    combo.setObjectName("calib_standard_combo")
    for name, label, _aliases in COMMON_CALIBRANTS:
        combo.addItem(label, name)
        # 不在这里给每一项算 tooltip：那会把 18 个标样的 d 值库全部
        # 同步加载一遍（实测建窗卡 8 秒，界面看门狗报警）。选中/同步时
        # 只算当前这一项，见 _sync_calibrant_widgets。
    # 下拉文字（“LaB₆（NIST SRM 660c，默认）”）很长，别让它按文字要宽度
    # 把参数坞撑宽：水平 SizePolicy=Ignored，列宽由页面其余内容定。
    combo.setSizePolicy(QSizePolicy.Ignored,
                        combo.sizePolicy().verticalPolicy())
    combo.currentIndexChanged.connect(
        lambda _i: _on_calibrant_changed(window, _i))
    ring_count = QSpinBox()
    ring_count.setObjectName("calib_ring_count_spin")
    ring_count.setRange(1, MAX_GUI_RINGS)
    ring_count.setMaximumWidth(80)
    ring_count.valueChanged.connect(
        lambda v: _on_ring_count_changed(window, v))
    std_lay.addWidget(combo, 1)
    std_lay.addWidget(QLabel("环数"))
    std_lay.addWidget(ring_count)
    lay.addWidget(std_box)
    window.calib_standard_combo = combo
    window.calib_ring_count_spin = ring_count

    # 「条目名称」一个框（用户 2026-10-07："一个框，预填文件名的不含-
    # 后的长数字部分……两个框保存后最后变成一个连在一起的"）：名字本身
    # 是备注，命令行用的标识在存盘时自动洗出来（config_ops.
    # _sanitize_config_key）；进校准/换标样时按文件名重填预填值
    # （_refresh_config_name_prefill，用户自己改过的名字不覆盖）。
    name_edit = QLineEdit()
    name_edit.setPlaceholderText("条目名称（按标样文件名预填，可改）")
    name_edit.setToolTip("存成一个几何配置条目：这个名字就是备注；命令行"
                         " --config 用的标识由它自动生成（非字母数字换成"
                         "下划线）。下拉框里显示成「标识_备注」。")
    name_edit.setText(_default_config_name(window))
    window._calib_name_auto = name_edit.text()   # 上次预填值：用户改过就不再覆盖
    save_hint = QLabel("还没有校准结果")
    save_hint.setStyleSheet("color: gray;")
    save_hint.setWordWrap(True)
    for w_ in (name_edit, save_hint):
        ops.addWidget(w_)
    window.calib_name_edit = name_edit
    window.calib_save_hint = save_hint

    # 三列表的说明：讲的是下面那张表，所以跟着表走
    intro = QLabel("用标样图标定几何（束心、距离、倾斜角）；跑出的结果"
                   "<b>直接成为当前配置</b>，并与对比位 A、B 三列并排对照")
    intro.setWordWrap(True)

    # ── 三列表 ─────────────────────────────────────────────
    table_box = QGroupBox("数据")
    tb = QVBoxLayout(table_box)

    # 当前配置说明行放**框顶**（用户 2026-10-08："放到最上面"）：先看见
    # "现在用的是哪一份几何"，再往表里看——它就是"往下看对比表之前必须
    # 先看见的"那件事；放框顶还有个好处：下拉框下面直接就是自己列的数字，
    # 中间不再隔一行。（像素尺寸确认曾经也住这儿，2026-10-01 单独搬回
    # 页面顶部了，见上面。）
    cur_lbl = QLabel("当前配置：—")
    cur_lbl.setWordWrap(True)
    tb.addWidget(cur_lbl)
    window.calib_current_lbl = cur_lbl

    # 三个槽下拉框进网格：灰字列头正下方、各贴自己那一列（用户
    # 2026-10-08："三个选择框和对应的当前 A/B 太远了"——原先三框单独占
    # 一行等宽摊开，跟下面的列对不上：实测左偏 31~91 px、中间还隔两行）。
    # 水平 SizePolicy=Ignored：列宽照旧由数字和行名定（和加下拉框之前
    # 一样），下拉框只占用自己那一格、长了省略——不然它按最长条目要宽度，
    # 会把列撑到 102 px、参数坞整体从 359 顶宽到 465（实测）。
    grid = QGridLayout()
    grid.setHorizontalSpacing(6)
    window.calib_slot_combo = {}
    for c, (slot, text) in enumerate(
            (("current", "当前配置"), ("A", "A"), ("B", "B")), start=1):
        hdr = QLabel(text)
        hdr.setAlignment(Qt.AlignCenter)
        hdr.setStyleSheet("color: gray;")
        grid.addWidget(hdr, 0, c)
        combo = QComboBox()
        combo.setToolTip({
            "current": "当前配置：选一条结果即采纳为当前配置；"
                       "[编辑…] 可用条目预填或手输",
            "A": "对比位 A：从累积结果里选；手动选过之后新结果不再覆盖它",
            "B": "对比位 B：同上"}[slot])
        combo.setSizePolicy(QSizePolicy.Ignored,
                            combo.sizePolicy().verticalPolicy())
        combo.currentIndexChanged.connect(
            lambda _i, s=slot: _on_slot_changed(window, s))
        grid.addWidget(combo, 1, c)
        window.calib_slot_combo[slot] = combo

    window.calib_vals = {"current": {}, "A": {}, "B": {}, "delta": {}}
    row_idx = 2
    for key_, name, _scale, _fmt, has_delta in COMPARE_ROWS:
        grid.addWidget(QLabel(name), row_idx, 0)
        for c, slot in enumerate(("current", "A", "B"), start=1):
            label = QLabel("—")
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(label, row_idx, c)
            window.calib_vals[slot][key_] = label
        row_idx += 1
        if has_delta:                     # Δ 行紧跟在它下面
            grid.addWidget(QLabel(f"Δ{name}"), row_idx, 0)
            for c, slot in enumerate(("current", "A", "B"), start=1):
                label = QLabel("—")
                label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                label.setStyleSheet("color: gray;")
                grid.addWidget(label, row_idx, c)
                window.calib_vals["delta"].setdefault(key_, {})[slot] = label
            row_idx += 1
    tb.addLayout(grid)

    # 「对比基准」下拉框已删（用户 2026-09-30："直接把对比基准删了，直接出
    # 结论当前配置和 a、b 对比分别怎么样，随着用户选择 ab 当前进行变化"）。
    # 参照系固定 = 当前配置：Δ 行与结论都朝它比，A/B 槽换了内容结论就跟着变。
    # 历史：2026-09-27 加它是因为"不知道谁在和谁比"；2026-09-28 让它同时管
    # 结论与 Δ；现在干脆去掉这一层——一屏只讲"当前配置 vs A、vs B"这一件事，
    # 比"选一个基准再看结论"少一步，也不会再出现"基准列写着 A、结论在讲 B"。
    delta_note = QLabel("Δ = 该列 − 当前配置；结论同样以「当前配置」为准")
    delta_note.setWordWrap(True)
    delta_note.setStyleSheet("color: gray;")
    tb.addWidget(delta_note)

    verdict = QLabel("结论：—")
    verdict.setWordWrap(True)
    tb.addWidget(verdict)
    hint = QLabel(COMPARE_HINT)
    hint.setWordWrap(True)
    hint.setStyleSheet("color: gray;")
    tb.addWidget(hint)
    lay.addWidget(intro)
    # 数据表（三列表 + Δ + 结论）**放最下**（用户 2026-09-30："校准数据放最下，
    # 跟自动手动换位置"）：页面顺序 = 介绍 → 手动 → 自动 → 数据（手动在
    # 上是用户 2026-10-13 定的："校准顺序调为手动在上，自动在下"）。
    # 它在最后才 addWidget（见下），这里只留引用
    window.calib_verdict = verdict

    # ── 自动取点 ─────────────────────────────────────────────
    # 2026-10-08：删掉旧的 [再精修]（以当前配置的束心为初值直接再精修）
    # ——真 lab6 实测两个按钮产出等价（环位偏差 0.24 vs 0.23 px，在重复
    # 跑 0.014~0.029 px 的抖动以内），连按两个按钮 = 空转，收敛成一个。
    auto_box = QGroupBox("自动取点")
    al = QVBoxLayout(auto_box)
    auto_hint = QLabel("从<b>当前配置</b>出发，自动定位束心并精修；结果"
                       "<b>直接成为当前配置</b>（图上青环立刻跟着动），"
                       "同时进下方列表")
    auto_hint.setWordWrap(True)
    al.addWidget(auto_hint)
    btn_auto = QPushButton("定位束心并精修")
    btn_auto.setObjectName("start_auto_calib")
    btn_auto.clicked.connect(lambda: _start_auto_calib(window))
    al.addWidget(btn_auto)
    window.calib_start_auto = btn_auto
    # （自动区在手动区**下面**才加进布局——用户 2026-10-13："手动在上、自动
    # 在下"；数据表照旧在最后，见本函数末尾）

    # ── 手动选点 ─────────────────────────────────────────────
    manual_box = QGroupBox("手动选点")
    ml = QVBoxLayout(manual_box)
    manual_hint = QLabel("在中央图上点衍射环（自动吸附最近的环，±0.5°）；"
                         "<b>右键某个点</b>可选中并在下方改环号；"
                         "至少 3 个点、覆盖 2 个环")
    manual_hint.setWordWrap(True)
    ml.addWidget(manual_hint)
    points_label = QLabel("已选 0 个点 / 0 个环")
    points_label.setWordWrap(True)      # 不够格时它会写一长句原因（见 _calib_sync）
    ml.addWidget(points_label)
    # 选中点的环号：右键选中 → 在这里改（2026-10-03 起不弹对话框——
    # 模态框在 macOS 全屏下"开着切走再切回"会把应用卡成收不到输入）
    sel_row = QHBoxLayout()
    sel_lbl = QLabel("选中点：—（右键图上某个点来选）")
    sel_lbl.setWordWrap(True)
    sel_row.addWidget(sel_lbl, 1)
    ring_spin = QSpinBox()
    ring_spin.setObjectName("calib_ring_spin")
    ring_spin.setRange(0, 15)     # 实际上界由 _calib_sync 按标样/环数刷新
    ring_spin.setEnabled(False)
    btn_ring = QPushButton("改")
    btn_ring.setObjectName("calib_ring_apply_btn")
    btn_ring.setToolTip("把选中点的环号改成左边这个数字")
    btn_ring.setEnabled(False)
    sel_row.addWidget(ring_spin)
    sel_row.addWidget(btn_ring)
    ml.addLayout(sel_row)
    row = QHBoxLayout()
    btn_undo = QPushButton("撤销一点")
    btn_clear = QPushButton("清空选点")
    row.addWidget(btn_undo)
    row.addWidget(btn_clear)
    ml.addLayout(row)
    btn_manual = QPushButton("用选点精修")
    btn_manual.setObjectName("start_manual_calib")
    btn_manual.setToolTip(f"按你选的点反推几何。至少要 {MIN_POINTS} 个点、"
                          f"覆盖 {MIN_RINGS} 个<b>不同的环</b>才能点（灰着时看上面"
                          f"那行说明；都判成同一个环号了：右键选中那个点，"
                          f"把环号改掉）")
    ml.addWidget(btn_manual)
    lay.addWidget(manual_box)     # 手动在上、自动在下（用户 2026-10-13 定）
    lay.addWidget(auto_box)
    lay.addWidget(table_box)      # 数据表放最下（用户 2026-09-30 定）
    window.calib_points_label = points_label
    window.calib_selected_lbl = sel_lbl
    window.calib_ring_spin = ring_spin
    window.calib_ring_apply = btn_ring
    window.calib_undo_btn = btn_undo
    window.calib_clear_btn = btn_clear
    window.calib_start_manual = btn_manual
    btn_ring.clicked.connect(lambda: _apply_selected_ring(window))
    btn_undo.clicked.connect(lambda: _undo_calib_point(window))
    btn_clear.clicked.connect(lambda: _clear_calib_points(window))
    btn_manual.clicked.connect(lambda: _start_manual_calib(window))

    # 出口按钮不在这儿：原来放在本页最底部，窗口 1000 高时它落在内容
    # y=1236（要往下滚 434 px 才看得见，用户 2026-09-26 报"没有退出校准
    # 的按钮了"）。现在固定在参数坞顶部那一行（app._build_param_dock 建，
    # window.calib_exit_btn），永远可见。

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)   # 窄窗口兜底
    scroll.setWidget(page)
    window.calib_scroll = scroll
    _ensure_current(window)
    _sync_calibrant_widgets(window)
    _calib_sync(window)
    return scroll

def _reset_calib_form(window: QMainWindow) -> None:
    """表单复位：表格清空、结论/标签复位（按钮状态由 _calib_sync 管）。"""
    for col in ("current", "A", "B"):
        for label in window.calib_vals[col].values():
            label.setText("—")
    for per_slot in window.calib_vals["delta"].values():
        for label in per_slot.values():
            label.setText("—")
    window.calib_verdict.setText("结论：—")
    _calib_sync(window)

def _calib_sync(window: QMainWindow) -> None:
    """把状态刷到界面：槽下拉框 / 表格 / Δ / 结论 / 按钮 / 像素确认。"""
    state = _calib_state(window)
    points = state["points"]
    n_rings = len({p[2] for p in points})
    enough = len(points) >= MIN_POINTS and n_rings >= MIN_RINGS
    # 不够格时**把原因写出来**（用户 2026-10-01："手选完了点击用选点精修无效"）：
    # 那时按钮是灰的，点下去 Qt 直接丢掉、一个字都不写——而最可能的原因正是
    # 判环挤在一起（同一个环号好几个点），顺手把出路（右键选中改环号）也指出来
    # 只有"环数不够"才多嘴解释（那才是让人看不懂的那种：点明明点了一堆，
    # 按钮却灰着）。单纯点还不够多时保持简短——上方提示本来就写着"至少 3 个
    # 点"，计数也在眼前。一个点都没点时更不必解释。
    window.calib_points_label.setText(
        f"已选 {len(points)} 个点 / {n_rings} 个环"
        if (n_rings >= MIN_RINGS or not points) else
        f"已选 {len(points)} 个点 / {n_rings} 个环——至少要 {MIN_POINTS} 个点、"
        f"覆盖 {MIN_RINGS} 个不同的环才能精修（都判成同一个环号了？"
        f"右键选中那个点，把环号改掉）")
    # 选中点的环号框上界跟当前标样/环数走（不再写死 LaB₆ 的 0–15）
    n_sel = max(1, int(state.get("n_rings") or 1))
    ring_spin = getattr(window, "calib_ring_spin", None)
    if ring_spin is not None:
        ring_spin.setRange(0, n_sel - 1)
        ring_spin.setToolTip(
            f"选中的点该算第几环（{calibrant_short_label(state['calibrant'])} "
            f"理论环 0–{n_sel - 1}）——位置不动，拟合按新环号算")
    # 选中点的环号行：谁被选中、现在几环；没选中（或越界）就禁用
    sel_lbl = getattr(window, "calib_selected_lbl", None)
    if sel_lbl is not None:
        sel = state.get("selected")
        has_sel = sel is not None and 0 <= sel < len(points)
        if has_sel:
            sel_lbl.setText(f"选中点：第 {sel + 1} 个"
                            f"（现在判成环 {int(points[sel][2])}）")
            sel_key = (sel, int(points[sel][2]))
            # 只在"换了选中点 / 环号变了"时回填数字框：后台任务随时会调
            # _calib_sync，每次都回填会把用户正拨的数字打回去（2026-10-03）
            if sel_key != getattr(window, "_calib_ring_loaded", None):
                window.calib_ring_spin.setValue(int(points[sel][2]))
            window._calib_ring_loaded = sel_key
        else:
            sel_lbl.setText("选中点：—（右键图上某个点来选）")
            window._calib_ring_loaded = None
        window.calib_ring_spin.setEnabled(has_sel)
        window.calib_ring_apply.setEnabled(has_sel)
    window.calib_start_manual.setEnabled(enough)
    window.calib_undo_btn.setEnabled(bool(points))
    window.calib_clear_btn.setEnabled(bool(points))
    window.calib_start_auto.setEnabled(state["current_geom"] is not None)
    window.calib_current_lbl.setText(_current_geom_text(window))
    # 像素确认：标记只在像素值真的变了时才被清掉（规则 (b)）；
    # 没核对时那行橙色提醒常显（不拦动作，2026-10-05 起）
    cur_px = (state["current_geom"] or {}).get("pixel_size_m")
    window.calib_pixel_chk.setText(
        f"已核对像素尺寸（{(cur_px or 0) * 1e6:.1f} µm）"
        if cur_px is not None else "已核对像素尺寸")
    window.calib_pixel_chk.setChecked(_pixel_ok(window))
    window.calib_pixel_warn.setVisible(
        cur_px is not None and not _pixel_ok(window))
    # 保存区
    has_cur = state["current_geom"] is not None
    window.calib_save_btn.setEnabled(has_cur)
    window.calib_save_hint.setText(
        f"将保存：「{_slot_label(state, 'current')}」的几何"
        if has_cur else "还没有可保存的几何")
    _sync_slot_combos(window)
    _refresh_table(window)
    _sync_del_config_btn(window)

