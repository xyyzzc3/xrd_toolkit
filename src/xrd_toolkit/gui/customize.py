"""Customize 自绘轴属性对话框（替换 mpl 自带子图配置器）。

mpl 自带的 Qt 图选项编辑器是英文技术术语（Left/Bottom/hspace/
wspace/Export values），hspace/wspace 对单图无用、字段还挤——
自绘版只留单图面板真正用得上的：标题 / X 轴标签 / Y 轴标签 /
纵轴刻度（线性/对数）/ 图边距（左/下/右/上）。表单标签左对齐
（macOS 风格默认把表单整块水平居中，真机探针实测后显式设左）、
按节分组、[恢复默认][取消][应用] 按钮行。

"用户改的归用户"保护记账：这里改的标题/轴标签/刻度/曲线样式，
重画一律不覆盖——见 plot_views 的 _snapshot_canvas /
_apply_text_guards / _settle_scale。边距改过 = 摘掉 tight
layout 引擎（fig.set_layout_engine(None)），否则每次 draw
引擎都把用户边距算回去。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog, QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton,
    QVBoxLayout, QWidget)

from xrd_toolkit.gui.panel_state import (
    _content, _curve_color, _log, _panel_param)

# 各视图画布上坐标轴的属性名（plot_views 的 builder 按视图挂其一）
_AXES_ATTRS = ("axes_1d", "axes_2d", "axes_profile", "axes_waterfall",
               "axes_heat")

# 各视图的默认外观（Customize [恢复默认] 与 plot_views 的
# _apply_text_guards 共用；未知视图 = 1D 积分图默认）
_VIEW_DEFAULTS = {
    "2D": ("{display}: diffraction image", "横向 (px)", "纵向 (px)"),
    "剖面": ("{display}: line profile", "Distance from center (px)",
             "Intensity (a.u.)"),
    "瀑布": ("{display}: 36-sector waterfall", "2θ (deg)",
             "Azimuthal sector (χ)"),
    "热图": ("{display}: intensity heatmap", "2θ (deg)", "Sample"),
}


def _default_texts(view: str, display: str) -> tuple:
    """该视图的默认 (标题, x 轴标签, y 轴标签)。"""
    title, xlabel, ylabel = _VIEW_DEFAULTS.get(view, (
        "{display}: full azimuthal integration", "2θ (deg)",
        "Intensity (a.u.)"))
    return title.format(display=display), xlabel, ylabel


def _open_customize_dialog(window: QMainWindow, key: str) -> None:
    """Customize 按钮：自绘轴属性对话框（替换 mpl 自带子图配置器）。

    内容 = 标题 / X 轴标签 / Y 轴标签 / 纵轴刻度（线性/对数）/
    图边距（左/下/右/上）——单图面板真正用得上的字段。mpl 自带
    的是英文技术术语（Left/Bottom/hspace/wspace/Export values），
    hspace/wspace 对单图无用，字段还挤；自绘版：表单标签左对齐、
    按节分组、[恢复默认][取消][应用] 按钮行（应用 = 生效并关闭，
    与"用户改的归用户"保护记账配套，见 _snapshot_canvas）。

    边距改动的坑：面板画布建在 tight_layout=True 的 Figure 上，
    布局引擎每次 draw 都会把 subplots_adjust 的边距算回去——应用
    时先把布局引擎摘掉（set_layout_engine(None)），边距由用户接管，
    不再被程序重排。标题/轴标签/刻度改了重画不覆盖：_apply_text_guards
    /_settle_scale 会认出"用户改过"（重画前快照与默认基准不符 =
    用户为准）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    content = _content(dock)
    # 各视图画布属性名不同（axes_1d/axes_2d/…），统一按注册表取
    # 第一个挂上的坐标轴
    ax = next((getattr(content, name) for name in _AXES_ATTRS
               if hasattr(content, name)), None)
    fig = getattr(content, "figure", None)
    if ax is None or fig is None:
        return   # 占位面板还没有图
    dlg = _build_customize_dialog(window, dock, ax, fig)
    if dlg.exec() != QDialog.Accepted:
        return   # 取消：图保持原样
    _apply_customize(window, dock, ax, fig, dlg)


def _build_customize_dialog(window: QMainWindow, dock, ax, fig) -> QDialog:
    """搭 Customize 对话框并预填当前轴状态（供 _open_customize_dialog
    与测试复用：测试可直改 _fields 再走 _apply_customize）。"""
    dlg = QDialog(window)
    dlg.setWindowTitle(f"Customize — {dock.panel_display}")
    dlg.setMinimumWidth(460)
    root = QVBoxLayout(dlg)
    # 表单统一左对齐（用户点名要的）：macOS 风格默认把表单内容
    # 整块水平居中（真机探针实测 formAlignment = AlignHCenter），
    # 标签/输入框全停在对话框中间——formAlignment 显式设左，标签
    # 列和输入框整块贴左；标签文本自身也设左对齐
    label_align = Qt.AlignLeft | Qt.AlignVCenter
    form_align = Qt.AlignLeft | Qt.AlignTop

    text_box = QGroupBox("标题与轴标签")
    text_form = QFormLayout(text_box)
    text_form.setLabelAlignment(label_align)
    text_form.setFormAlignment(form_align)
    title = QLineEdit(ax.get_title())
    title.setMinimumWidth(240)   # 标题输入框加长（列宽跟随变宽）
    xlabel = QLineEdit(ax.get_xlabel())
    ylabel = QLineEdit(ax.get_ylabel())
    text_form.addRow("标题", title)
    text_form.addRow("X 轴标签", xlabel)
    text_form.addRow("Y 轴标签", ylabel)
    root.addWidget(text_box)

    scale_box = QGroupBox("纵轴刻度")
    scale_form = QFormLayout(scale_box)
    scale_form.setLabelAlignment(label_align)
    scale_form.setFormAlignment(form_align)
    scale = QComboBox()
    scale.addItem("线性", "linear")
    scale.addItem("对数", "log")
    cur_scale = ax.get_yscale()
    idx = scale.findData(cur_scale)
    scale.setCurrentIndex(idx if idx >= 0 else 0)
    scale_form.addRow("刻度", scale)
    root.addWidget(scale_box)

    margin_box = QGroupBox("图边距")
    margin_form = QFormLayout(margin_box)
    margin_form.setLabelAlignment(label_align)
    margin_form.setFormAlignment(form_align)

    def _spin(value):
        s = QDoubleSpinBox()
        s.setRange(0.0, 1.0)   # 边距 = 占图宽的分数；tight layout 会算出
        # 0.96 这类大值，上限设 1 才装得下
        s.setDecimals(3)
        s.setSingleStep(0.005)
        s.setKeyboardTracking(False)
        s.setValue(value)
        return s

    sp = fig.subplotpars
    fields = {
        "left": _spin(sp.left), "bottom": _spin(sp.bottom),
        "right": _spin(sp.right), "top": _spin(sp.top),
    }
    margin_form.addRow("左边距", fields["left"])
    margin_form.addRow("下边距", fields["bottom"])
    margin_form.addRow("右边距", fields["right"])
    margin_form.addRow("上边距", fields["top"])
    root.addWidget(margin_box)

    # 对比面板专属：逐条自定义曲线颜色。参数坞的"曲线配色"只给
    # 整套色板，这里允许"某一条换色"（任务六的"自定义选取色彩"）。
    # 选择结果暂存 dlg._color_picks，点 [应用] 才写回 dock（取消 =
    # 图保持原样，与其余字段同规矩）。色块初值 = 自定义色，没有
    # 就按当前配色参数取该条槽色。
    view = dock.panel_key.split("|", 1)[0]
    if view == "对比" and getattr(dock, "compare_files", None):
        palette = _panel_param(window, dock, "曲线配色", "高对比")
        staged = dict(getattr(dock, "curve_colors", None) or {})
        color_box = QGroupBox("曲线颜色")
        color_form = QFormLayout(color_box)
        color_form.setLabelAlignment(label_align)
        color_form.setFormAlignment(form_align)
        swatches = {}
        # macOS 按钮样式会在纯背景色上叠一层高光渐变，把色块洗淡
        # （#2a78d6 → #6fadf4）；带 border 声明即改用纯色渲染（真机
        # 探针实测），细灰边也让浅色块在白底上有个轮廓。
        _swatch_qss = lambda c: f"background-color: {c}; border: 1px solid #999"
        for i, (_path, display) in enumerate(dock.compare_files):
            swatch = QPushButton()
            swatch.setObjectName(f"swatch_{i}")
            swatch.setFixedSize(30, 20)

            def pick(checked=False, display=display, swatch=swatch, i=i):
                initial = staged.get(display) or _curve_color(palette, i)
                picked = QColorDialog.getColor(
                    QColor(initial), window, f"选择 {display} 的颜色")
                if picked.isValid():
                    staged[display] = picked.name()
                    swatch.setStyleSheet(_swatch_qss(picked.name()))

            swatch.clicked.connect(pick)
            swatch.setStyleSheet(_swatch_qss(
                staged.get(display) or _curve_color(palette, i)))
            swatches[i] = swatch
            row = QWidget()
            row_lay = QHBoxLayout(row)
            row_lay.setContentsMargins(0, 0, 0, 0)
            row_lay.addWidget(QLabel(display), 1)
            row_lay.addWidget(swatch)
            color_form.addRow(row)

        def clear_colors():
            staged.clear()
            for i, swatch in swatches.items():
                swatch.setStyleSheet(_swatch_qss(_curve_color(palette, i)))

        clear_btn = QPushButton("恢复默认配色")
        clear_btn.setObjectName("clear_colors_btn")
        clear_btn.clicked.connect(clear_colors)
        color_form.addRow("", clear_btn)
        root.addWidget(color_box)
        dlg._color_picks = staged

    btn_row = QHBoxLayout()
    reset = QPushButton("恢复默认")
    cancel = QPushButton("取消")
    apply_btn = QPushButton("应用")
    apply_btn.setDefault(True)
    cancel.clicked.connect(dlg.reject)
    apply_btn.clicked.connect(dlg.accept)
    reset.clicked.connect(lambda: _reset_customize_fields(dock, dlg._fields))
    btn_row.addWidget(reset)
    btn_row.addStretch(1)
    btn_row.addWidget(cancel)
    btn_row.addWidget(apply_btn)
    root.addLayout(btn_row)

    dlg._fields = {"title": title, "xlabel": xlabel, "ylabel": ylabel,
                   "scale": scale, **fields}
    return dlg


def _reset_customize_fields(dock, fields) -> None:
    """[恢复默认]：各字段回到该面板视图的默认外观（1D/对比 = 积分
    图，2D/剖面/瀑布 = 各自默认；只改对话框里的值，点 [应用]
    才生效）。"""
    view = dock.panel_key.split("|", 1)[0]
    title, xlabel, ylabel = _default_texts(view, dock.panel_display)
    fields["title"].setText(title)
    fields["xlabel"].setText(xlabel)
    fields["ylabel"].setText(ylabel)
    fields["scale"].setCurrentIndex(fields["scale"].findData("linear"))
    for name, value in (("left", 0.125), ("bottom", 0.11),
                        ("right", 0.9), ("top", 0.88)):
        fields[name].setValue(value)


def _apply_customize(window: QMainWindow, dock, ax, fig, dlg) -> None:
    """把对话框字段写进轴 + 画布重画（[应用] 或测试直调）。

    刻度换了自动纵轴就按新刻度重算（手动纵轴不动，由用户管）；
    边距应用前先把 tight layout 引擎摘掉，否则 draw 时布局引擎
    会把用户边距算回去（见 _open_customize_dialog 的 docstring）。
    """
    f = dlg._fields
    ax.set_title(f["title"].text())
    ax.set_xlabel(f["xlabel"].text())
    ax.set_ylabel(f["ylabel"].text())
    scale = f["scale"].currentData()
    if scale != ax.get_yscale():
        ax.set_yscale(scale)
        if _panel_param(window, dock, "纵轴自动", True):
            ax.relim()
            ax.autoscale_view(scaley=True)
    fig.set_layout_engine(None)   # 边距由用户接管：tight layout 退场
    fig.subplots_adjust(left=f["left"].value(), bottom=f["bottom"].value(),
                        right=f["right"].value(), top=f["top"].value())
    picks = getattr(dlg, "_color_picks", None)
    if picks is not None:
        # 逐条自定义色：点 [应用] 才真正写回 dock，随后按新色重画
        # 整张对比图（_redraw_compare 自带重画与记账；惰性导入防
        # 模块环——plot_views 反向 import 本模块）
        if picks:
            dock.curve_colors = dict(picks)
        elif hasattr(dock, "curve_colors"):
            del dock.curve_colors
        from xrd_toolkit.gui.plot_views import _redraw_compare
        _redraw_compare(window, dock.panel_key)
    _content(dock).draw()
    _log(window, f"已应用 Customize 设置：{dock.windowTitle()}")
