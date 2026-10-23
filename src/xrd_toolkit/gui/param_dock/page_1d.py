"""参数坞「1D」页：页面指路灰字、1D 显示参数、数据参数复位、出图按钮。

2026-10-22 从 builder.py 的 _build_param_dock 原样搬出（只改结构，不动逻辑）。
"""
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QSizePolicy, QSpinBox, QTableWidget, QVBoxLayout)

from xrd_toolkit.gui.panel_state import _apply_auto_ylim, _apply_config, _log
from xrd_toolkit.gui.param_dock.widgets import add_caption, add_float, add_range
from xrd_toolkit.gui.peak_view import (
    install_peak_view, zoom_to_peak)
from xrd_toolkit.gui.plot_views import (
    _apply_params, _plot_view, _refresh_1d_display)


def build_1d_page(window, form_1d, btns_1d):
    # 标定几何那三行只读字段（像素尺寸/波长/分析用距离）撤了：它们的
    # 值改在坞顶"几何配置"那一行的悬停提示里看（用户 2026-09-26 定：
    # "只留能选的那一行，下面的灰色的变成鼠标长放显示"）。控件撤掉后
    # window.params 里不再有这三个键，快照也就不会再记它们——几何随
    # 快照回放一直是走 "config" 那条（见 _snapshot_params/_restore）。

    # 积分设置（2θ 范围 + 输出点数）搬去坞顶第三行：所有分析页共用，
    # 见 _build_param_dock 里 data_row 的说明（用户 2026-09-27）
    # 这一页因此只剩"出图"这件事——留一行灰字指路（用户 2026-09-27 让
    # 我按自己的想法收尾）：整页空白看着像没做完
    page_hint = QLabel(
        "2θ 范围与点数在参数面板顶部，改了按 [重算这张图]。勾选超过 25 项"
        "只计算、不弹面板——结果都在文件栏「1D 产物」：双击看单张、右键"
        "整组打开")
    page_hint.setWordWrap(True)
    page_hint.setStyleSheet("color: gray;")
    form_1d.addRow(page_hint)

    # ── 1D 显示（小节，2026-10-07 从「原图」页搬回本页）──
    # 它本来就是曲线图自己的显示参数，原图页改名后屋里只剩 2D/剖面/瀑布，
    # 这一节留在那儿名不对题（用户："搬回 1d"）。**同时改成即改即画**：
    # 原来要按 [应用显示设置]，而那个按钮住在「原图」页——搬过来就够不着
    # 了（这个应用反复在修的"够不着"病；对照 10-02 对比四参数、10-07 瀑布
    # 行距的同款处理）。作用域 = **编辑对象这一个面板**：显示参数每张图各
    # 记各的（10-02 起的规矩），不学 _refresh_compare 的"所有同类面板"——
    # 1D 面板可以同时开很多张，每张各有各的缩放/刻度。
    add_caption(form_1d, "1D 显示")
    log_y = QCheckBox("对数纵轴")
    log_y.setToolTip("对数刻度：强弱峰差几个数量级时弱峰也看得清（改完立刻重画）")
    window.params["对数纵轴"] = log_y
    form_1d.addRow(log_y)

    auto_y = QCheckBox("纵轴自动")
    auto_y.setChecked(True)
    auto_y.setToolTip("按曲线 1%–99.9% 分位自动确定纵轴区间")
    window.params["纵轴自动"] = auto_y
    form_1d.addRow(auto_y)

    add_range(window, form_1d, "纵轴下限", "纵轴上限", 0.0, 1e9, 1.0, 100000.0,
              label="纵轴范围", decimals=1,
              tooltip="取消自动后手填的纵轴区间（下限–上限）；改完立刻重画")

    def sync_ylim(checked):
        window.params["纵轴下限"].setEnabled(not checked)
        window.params["纵轴上限"].setEnabled(not checked)
        if checked:
            _apply_auto_ylim(window)   # 勾回自动：立刻按焦点图算并填回（同对比度套路）

    auto_y.toggled.connect(sync_ylim)
    sync_ylim(True)   # 初始状态：自动开 → 输入框置灰
    # 即改即画（先接 sync_ylim 再接它：连接按顺序触发，先填值后重画）
    log_y.toggled.connect(lambda _=False: _refresh_1d_display(window))
    auto_y.toggled.connect(lambda _=False: _refresh_1d_display(window))
    window.params["纵轴下限"].valueChanged.connect(
        lambda _v=0.0: _refresh_1d_display(window))
    window.params["纵轴上限"].valueChanged.connect(
        lambda _v=0.0: _refresh_1d_display(window))

    # ── 寻峰（2026-10-23；2026-10-24 去按钮、标签避让）──
    # 只是显示：按面板快照里的参数在画出来的曲线上找峰、画标记、填峰表。
    # 默认关——不在别人图上冒标记；参数改了立刻重画（同「1D 显示」）。
    add_caption(form_1d, "寻峰")
    add_float(window, form_1d, "寻峰显著度", 0.5, 50.0, 5.0,
              decimals=1, suffix=" σ", label="显著度", step=0.1,
              tooltip="噪声的倍数：越高越只留明显的峰（默认 5σ；滚轮/箭头每格 0.1）")
    add_float(window, form_1d, "寻峰最小峰距 (°)", 0.0, 1.0, 0.008,
              decimals=4, suffix=" °", label="最小峰距", step=0.001,
              tooltip="两根峰之间至少隔多少度；靠得太近只留较高的一根"
                      "（滚轮/箭头每格 0.001°）")
    add_float(window, form_1d, "寻峰最小 FWHM (°)", 0.0, 1.0, 0.0,
              decimals=4, suffix=" °", label="最小 FWHM", step=0.001,
              tooltip="只保留不窄于它的峰；0 = 不过滤（噪声尖通常很窄；"
                      "滚轮/箭头每格 0.001°）")
    spin_max = QSpinBox()
    spin_max.setRange(1, 100)
    spin_max.setValue(20)
    spin_max.setToolTip("最多列出多少根峰（按高度取最强的）")
    window.params["寻峰最多根数"] = spin_max
    form_1d.addRow("最多根数", spin_max)

    show_peaks = QCheckBox("寻峰并显示峰位")
    show_peaks.setToolTip("在曲线上画出峰位短线与 2θ 标签，并在下方列出峰表"
                          "（按当前面板自己的参数；勾上即为寻峰）")
    window.params["显示峰位"] = show_peaks
    form_1d.addRow(show_peaks)
    label_all = QCheckBox("标注全部峰位")
    label_all.setToolTip("默认只标放得下的峰（峰表里仍列全，双击放大后补齐）；"
                         "勾上后每根都标、标签贴峰顶，密集处允许重叠")
    window.params["标注全部峰位"] = label_all
    form_1d.addRow(label_all)
    for name in ("寻峰显著度", "寻峰最小峰距 (°)", "寻峰最小 FWHM (°)",
                 "寻峰最多根数"):
        window.params[name].valueChanged.connect(
            lambda _v=0.0: _refresh_1d_display(window))
    label_all.toggled.connect(lambda _=False: _refresh_1d_display(window))

    def toggle_peaks(checked):
        if checked and not str(window.focus_panel or "").startswith("1D|"):
            _log(window, "寻峰：先点开一张 1D 图（或双击文件栏的「1D 产物」）")
        _refresh_1d_display(window)
        if not checked:
            return
        dock = window.plot_docks.get(window.focus_panel)
        if dock is None:
            return
        n = len(getattr(dock, "detected_peaks", None) or [])
        shown = int(getattr(dock, "labeled_peaks", n))
        factor = window.params["寻峰显著度"].value()
        distance = window.params["寻峰最小峰距 (°)"].value()
        extra = ("" if shown >= n or label_all.isChecked()
                 else f"；图上标了 {shown} 个，其余放大后补全")
        _log(window, f"寻峰：{n} 根（显著度 ≥{factor:g}σ，"
                     f"最小间距 {distance:g}°{extra}）")

    show_peaks.toggled.connect(toggle_peaks)

    # 峰表：带浅色边框的小卡片；没有峰时整块隐藏（页面不留一块空表）
    peak_box = QFrame()
    peak_box.setObjectName("peak_table_card")
    peak_box.setStyleSheet(
        "QFrame#peak_table_card { background: #f7f9fb;"
        " border: 1px solid #e3e8ee; border-radius: 4px; }")
    peak_lay = QVBoxLayout(peak_box)
    peak_lay.setContentsMargins(8, 7, 8, 7)
    peak_lay.setSpacing(4)
    peak_caption = QLabel("峰表")
    peak_caption.setStyleSheet("color: #5b6773; font-weight: 600;")
    peak_caption.setWordWrap(True)
    peak_caption.setSizePolicy(QSizePolicy.Ignored,
                               peak_caption.sizePolicy().verticalPolicy())
    peak_table = QTableWidget(0, 3)
    peak_table.setHorizontalHeaderLabels(["2θ (°)", "FWHM (°)", "高度"])
    peak_table.verticalHeader().setVisible(False)
    peak_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    peak_table.setSelectionBehavior(QAbstractItemView.SelectRows)
    peak_table.setAlternatingRowColors(True)
    peak_table.setShowGrid(False)
    peak_table.setMinimumHeight(0)
    peak_table.setMaximumHeight(220)
    # 窄坞里不许把参数栏撑宽：表格默认的 sizeHint 会随列头要宽度，
    # 全屏时坞变宽它也跟着铺满（用户 2026-10-24："全屏后峰表还是过宽"）。
    # 卡在卡片/表格 320 px，坞更宽时靠左留白；高度在 fill 时按行数定。
    peak_table.setMinimumSize(0, 0)
    peak_table.setMaximumWidth(320)
    peak_table.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    peak_table.horizontalHeader().setMinimumSectionSize(30)
    peak_box.setMinimumWidth(0)
    peak_box.setMaximumWidth(320)
    peak_box.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    peak_table.verticalHeader().setDefaultSectionSize(22)
    # 三列平均分宽度（用户 2026-10-24："峰表三列平均分宽度吧"）
    peak_table.horizontalHeader().setSectionResizeMode(
        QHeaderView.Stretch)
    peak_lay.addWidget(peak_caption)
    peak_lay.addWidget(peak_table)
    peak_box.setVisible(False)
    window.peak_box = peak_box
    window.peak_table = peak_table
    window.peak_caption = peak_caption
    form_1d.addRow(peak_box)
    peak_table.cellDoubleClicked.connect(
        lambda row, _col: zoom_to_peak(window, row))
    install_peak_view(window)

    # [恢复默认] + [应用] 并排：[恢复默认] 只把参数复位（几何回到
    # 当前配置条目、区间/点数回到初值），不计算；[应用] 才重算焦点视图
    data_defaults = {
        "2θ 下限 (°)": 1.0,
        "2θ 上限 (°)": 8.0,
        "输出点数": 3000,
    }
    btn_reset_data = QPushButton("恢复默认")
    btn_reset_data.setObjectName("reset_data_btn")
    btn_reset_data.setToolTip("把上面三项数据参数复位成初值（几何回到当前配置"
                              "条目）——<b>只复位，不计算</b>，要算再点右边的"
                              " [重算这张图]")
    # 名字按"对谁、算什么"写（用户 2026-09-27："1d 的应用和出图有点歧义"）：
    # 这个按钮只作用于**编辑对象**那一张（见 _apply_params），与下面那个
    # 对一批勾选文件出图的按钮是两件事
    btn_apply = QPushButton("重算这张图")
    btn_apply.setObjectName("apply_btn")
    btn_apply.setToolTip("按参数面板顶部的 2θ 范围 / 点数重新积分，"
                         "并重画<b>编辑对象</b>那张图（不是一批）")
    btn_apply.clicked.connect(lambda: _apply_params(window))

    def reset_data():
        _apply_config(window, window.config_combo.currentIndex())
        for name, value in data_defaults.items():
            window.params[name].setValue(value)
        _log(window, "数据参数已恢复默认（未计算，点 [重算这张图] 生效）")

    btn_reset_data.clicked.connect(reset_data)

    btn_col = QHBoxLayout()
    btn_col.setSpacing(4)
    # 通栏宽一分为二（与用户讨论定稿）：[恢复默认] 在左、[应用] 在右
    btn_col.addWidget(btn_reset_data, 1)
    btn_col.addWidget(btn_apply, 1)
    btns_1d.addLayout(btn_col)   # 按钮固定在本页最下方（滚动区之外）
    # 本页产出：对勾选文件出 1D 图（常用循环不用切到「原图」页）
    btn_plot_1d = QPushButton("出图（尚未勾选）")
    btn_plot_1d.setObjectName("plot_1d_btn")
    btn_plot_1d.setToolTip("对<b>文件栏里勾选</b>的条目出 1D 图（勾了多少项，"
                           "按钮上就写着多少）：勾原始文件 = 现场积分；"
                           "勾产物条目 = 直接读那一份，不重算")
    window.plot_1d_btn = btn_plot_1d   # 登记按钮（测试用）
    # 兼容别名（2026-10-07 类型行拆分）：view_buttons["1D"] 从此指向这颗
    # 按钮——1D 的出图入口就在「1D」页，其它视图名（对比/热图）同理见下。
    window.view_buttons["1D"] = btn_plot_1d
    btn_plot_1d.clicked.connect(lambda: _plot_view(window, "1D"))
    btns_1d.addWidget(btn_plot_1d)
