"""参数坞的排版小工具：页面骨架、小节标题、数值行、成对上下限、横排容器。

2026-10-22 从 builder.py 的 _build_param_dock 里原样搬出（只改结构，不动
逻辑）：原先它们是嵌套函数，靠闭包读 window.params；现在显式传 window。
builder 再导入，调用点保持同名。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QScrollArea,
    QVBoxLayout, QWidget)

from xrd_toolkit.gui.param_dock.helpers import _fit_max_width


def make_page():
    """一页 = 滚动表单（装参数）+ 底部按钮行。

    返回 (页面, 表单, 底部行的布局)：参数一律加到表单上，按钮加到
    返回的布局里（固定最下方，不随滚动走）。"""
    page = QWidget()
    pv = QVBoxLayout(page)
    pv.setContentsMargins(0, 0, 0, 0)
    pv.setSpacing(0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)   # 条目随滚动区宽度自动重排
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    fields = QWidget()
    form = QFormLayout(fields)
    form.setContentsMargins(2, 1, 2, 1)
    scroll.setWidget(fields)
    pv.addWidget(scroll, 1)
    return page, form, pv


def add_caption(form, text):
    """全宽灰字小节标题（横跨标签/字段两列）：**上面一条细分隔线**。

    2026-10-09 加线加粗：用户"对比和热图的参数区没有很明显区分"
    （根子是「对比」那半页当时连标题都没有）——标题补上之后顺手把
    小节边界做明显：细线 + 半粗。全页统一，原图页三节、处理页四节
    也一起吃这套。
    """
    cap = QLabel(text)
    cap.setStyleSheet("color: gray; font-weight: 600;")
    line = QFrame()
    line.setFixedHeight(1)
    # 裸 QFrame 上给 color 画不出来（实测）——固定 1px 高 + 背景色
    line.setStyleSheet("background-color: #d5dae0;")
    row = QWidget()
    box = QVBoxLayout(row)
    box.setContentsMargins(0, 3, 0, 1)
    box.setSpacing(3)
    box.addWidget(line)
    box.addWidget(cap)
    form.addRow(row)
    return cap


def add_float(window, form, name, lo, hi, value, decimals=2,
              label=None, suffix="", tooltip="", readonly=False,
              step=None):
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setValue(value)
    box.setDecimals(decimals)
    if step is not None:
        # 滚轮/上下箭头的步长；QDoubleSpinBox 默认 1.0，对 0.008 这种
        # 量程 0–1 的参数等于废掉（2026-10-24 用户："滚轮和按钮很无效"）
        box.setSingleStep(step)
    if suffix:
        box.setSuffix(suffix)   # 单位跟在数字后（标签不再带括号单位）
    if tooltip:
        box.setToolTip(tooltip)
    if readonly:
        # 只读摘要：几何值由所选配置条目决定（_apply_config 填），
        # 用户改不了——要改去校准页。setReadOnly 只挡用户输入，
        # 程序 setValue 照常工作，快照回放不受影响。
        box.setReadOnly(True)
        box.setButtonSymbols(QDoubleSpinBox.NoButtons)
        box.setStyleSheet("color: gray;")
    _fit_max_width(box)          # 上限 = 完整显示量程最长文本所需
    window.params[name] = box
    form.addRow(label if label is not None else name, box)
    return box


def add_range(window, form, lo_name, hi_name, lo, hi, lo_value, hi_value,
              label, suffix="", decimals=1, tooltip="", step=None):
    """成对的上下限并排一行：左框 ~ 右框，共用一个行标签。

    window.params 的键保持原来的 lo_name/hi_name 不动（快照回放、
    测试都按这些键找控件），只改显示排版。两框各自限宽到"量程最长
    文本能完整显示"（_fit_max_width）——写死 84 时，上到 1e9 的量程
    放不下（"1000000000.0" 要 117px），拉宽坞也补不回来。
    """
    lo_box = QDoubleSpinBox()
    lo_box.setRange(lo, hi)
    lo_box.setValue(lo_value)
    lo_box.setDecimals(decimals)
    hi_box = QDoubleSpinBox()
    hi_box.setRange(lo, hi)
    hi_box.setValue(hi_value)
    hi_box.setDecimals(decimals)
    for box in (lo_box, hi_box):
        if suffix:
            box.setSuffix(suffix)
        if tooltip:
            box.setToolTip(tooltip)
        if step is not None:
            box.setSingleStep(step)
        _fit_max_width(box, 84)
    window.params[lo_name] = lo_box
    window.params[hi_name] = hi_box
    field = QWidget()
    row = QHBoxLayout(field)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    row.addWidget(lo_box, 1)   # 两框平分行宽
    row.addWidget(QLabel("–"))
    row.addWidget(hi_box, 1)
    form.addRow(label, field)
    return lo_box, hi_box


def bg_group(rows):
    """把若干控件行打包成一个可整体显隐的竖直容器。

    三个模式各有一个容器（空扫/自动/锚点），按模式只放出一个——
    参数坞窄，全部摊开既挤又让人不知道该填哪个。
    """
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    for row in rows:
        lay.addWidget(row)
    return box


def bg_row(*widgets):
    """一行横排（窄排版：内边距 0、间距 2）。"""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    for w, stretch in widgets:
        lay.addWidget(w, stretch)
    return row
