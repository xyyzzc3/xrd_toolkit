"""GUI 主程序：主框架（可停靠面板式工作区）与事件循环（PySide6）。

入口：python -m xrd_toolkit.gui（__main__.py 转发到本文件的 main()）。
create_window() 与 main() 分离：测试里可以只建窗口、不进事件循环。

架构说明：
  - 外窗口（QMainWindow）管三块固定坞：文件（左）/ 参数（右）/
    日志（底）+ 顶部工具栏 + 状态行；
  - Qt 的中央区不接受停靠，所以中央再嵌一个内层 QMainWindow 专门
    管理图面板坞——嵌套是标准做法（PyCharm / Spyder 同款）；
  - 框架阶段的占位动作统一写日志区 + 状态行提示"尚未实现"；
    实现功能时只替换动作背后的处理，框架结构不变；
  - 已接线（1D 闭环）：交互模型 = 选文件只选中（不算）→ 勾选
    视图按钮打开面板并算该视图 → 点图面板设"编辑对象" → [应用]
    用当前参数重算焦点视图；计算走 gui/tasks.py 后台线程，界面不卡。

窗口上的公共接口（供后续页面接线与测试使用）：
  window.log(text)        写日志区 + 状态行
  window.add_files(paths) 把文件加进左侧列表
  window.plot_docks       {视图名: QDockWidget}
  window.params           参数面板控件字典
  window.config_name      当前选中的配置条目 key（如 lmfp1_lab6）
  window.config           完整条目 dict（label / geometry / beam_center）
  window.canvas_1d / axes_1d  1D 面板的 matplotlib 画布 / 坐标轴
"""
import sys
from pathlib import Path

import matplotlib

matplotlib.use("qtagg")   # 必须在导入 FigureCanvasQTAgg 之前选定 Qt 后端
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import QEvent, QObject, Qt, QSize, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QPlainTextEdit, QPushButton,
    QSpinBox, QToolBar, QVBoxLayout, QWidget, QDockWidget, QApplication)

from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG  # 几何配置注册表（下拉框数据源）
from xrd_toolkit.gui.tasks import BackgroundTask  # 后台线程任务（积分等耗时计算）
from xrd_toolkit.services.data_loader import load_diffraction_image
from xrd_toolkit.services.integrator import integrate_1d

FILE_FILTER = "衍射图像 (*.tif *.edf *.cbf);;所有文件 (*)"
VIEW_NAMES = ("2D", "剖面", "1D", "瀑布")   # 四个图面板（视图开关的顺序）


# ══ 日志 / 状态行 ═══════════════════════════════════════════
def _log(window: QMainWindow, text: str) -> None:
    """统一反馈出口：写日志区（累积）+ 状态行（常驻"就绪"，消息
    替换它 5 秒后自动恢复——状态栏永远有内容，不会隐形）。"""
    window.log_text.appendPlainText(text)
    window.status_text.setText(text)
    if window._status_timer is None:
        window._status_timer = QTimer(window)
        window._status_timer.setSingleShot(True)
        window._status_timer.timeout.connect(
            lambda: window.status_text.setText("就绪"))
    window._status_timer.start(5000)   # 重新计时


class _FocusMarker(QObject):
    """事件过滤器：点击图面板时把参数面板的编辑对象切到该视图。

    装在图面板控件上；只"监听"不"拦截"——eventFilter 返回 False，
    鼠标事件照常传给画布/标签。参数坞顶部的"编辑对象"标签随点击
    更新，[应用] 就作用在这个焦点视图上。
    """

    def __init__(self, window: QMainWindow, name: str):
        super().__init__(window)   # 挂在窗口上，随窗口销毁
        self._window = window
        self._name = name

    def eventFilter(self, obj, event):
        if event.type() == QEvent.MouseButtonPress:
            _set_focus(self._window, self._name)
        return False   # 不消费事件


def _set_focus(window: QMainWindow, name: str) -> None:
    """把参数面板的编辑对象切到某视图（点面板 / 计算完成时调用）。"""
    window.focus_view = name
    window.focus_label.setText(f"编辑对象：{name}")


# ══ 左侧：文件列 ═══════════════════════════════════════════
class NarrowList(QListWidget):
    """最小宽度可收窄的列表。

    QListWidget 的 minimumSizeHint 内部写死约 270px（按"能显示
    条目"设计），且 dock 布局只认这个 hint、不认 setMinimumWidth。
    覆盖 hint 后文件列才能收到按钮行决定的真实最窄宽度。
    """

    def minimumSizeHint(self):
        return QSize(100, 120)


def _build_file_dock(window: QMainWindow) -> QDockWidget:
    """文件坞：打开（多选）/ 保存 + 勾选式文件列表。"""
    dock = QDockWidget("文件", window)
    dock.setObjectName("file_dock")
    dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

    content = QWidget()
    lay = QVBoxLayout(content)
    # 按钮排 2 列网格：[打开] 占满第一行（长按钮），[保存][删除] 第二行。
    # 一行三个按钮的最小宽度 ≈ 3×80+间距，把文件列锁在 270；
    # 网格最宽的一行只有两个按钮 ≈ 184，文件列才能和参数列一样收窄
    btns = QGridLayout()
    btn_open = QPushButton("打开")
    btn_save = QPushButton("保存")
    btn_delete = QPushButton("删除")
    btns.addWidget(btn_open, 0, 0, 1, 2)   # 打开占满第一行
    btns.addWidget(btn_save, 1, 0)
    btns.addWidget(btn_delete, 1, 1)
    lay.addLayout(btns)

    window.file_list = NarrowList()   # 覆盖了 minimumSizeHint，可以收窄
    # 支持按住 Shift/Ctrl 选多行，配合"删除"批量移除
    window.file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
    lay.addWidget(window.file_list)

    def open_dialog():
        paths, _ = QFileDialog.getOpenFileNames(
            window, "选择数据文件", "data", FILE_FILTER)
        if paths:
            window.add_files(paths)

    def delete_selected():
        items = window.file_list.selectedItems()
        if not items:
            _log(window, "未选中要删除的文件")
            return
        for it in items:
            window.file_list.takeItem(window.file_list.row(it))
        _log(window, f"已删除 {len(items)} 个文件")

    btn_open.clicked.connect(open_dialog)
    btn_save.clicked.connect(lambda: _log(window, "保存 — 尚未实现"))
    btn_delete.clicked.connect(delete_selected)

    dock.setWidget(content)
    window.addDockWidget(Qt.LeftDockWidgetArea, dock)
    return dock


def add_files(window: QMainWindow, paths) -> None:
    """把文件加进左侧列表（勾选式；单选看单张、多选批量）。

    添加后把当前项移到最后一个新文件：打开文件立即"选中"，自动
    触发积分（_on_file_selected），不用再手动点一下。注意程序化
    addItem 不会自动设当前项——不设的话 currentItemChanged 永不
    触发，链子断了。
    """
    from pathlib import Path
    for p in paths:
        p = Path(p)
        item = QListWidgetItem(p.name)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(Qt.Checked)          # 默认勾上
        item.setData(Qt.UserRole, str(p))       # 全路径藏在 UserRole
        window.file_list.addItem(item)
    if paths:
        window.file_list.setCurrentRow(window.file_list.count() - 1)
    _log(window, f"已添加 {len(paths)} 个文件")


# ══ 文件 → 1D 闭环：选文件 → 后台积分 → 1D 面板出图 ══════════
def _collect_geometry(window: QMainWindow) -> dict:
    """从参数面板收集积分几何。

    PONI/倾斜角取当前配置条目的标定值（面板暂不暴露），像素/波长/
    距离取面板输入框——对应 integrate_pattern 里 --pixel/--wavelength/
    --dist 覆盖配置值的语义。
    """
    geom = window.config["geometry"]
    return dict(
        pixel_size_m=window.params["像素尺寸 (µm)"].value() * 1e-6,
        wavelength_m=window.params["波长 (Å)"].value() * 1e-10,
        dist_m=window.params["初始距离 (mm)"].value() * 1e-3,
        poni1_m=geom["poni1_m"],
        poni2_m=geom["poni2_m"],
        rot1_deg=geom["rot1_deg"],
        rot2_deg=geom["rot2_deg"],
    )


def _compute_integration(path_str: str, geom: dict, npt: int) -> tuple:
    """后台线程里运行的纯计算：读图 → 全角度积分。

    不碰任何界面控件；异常由 tasks.BackgroundTask 转成 error 信号
    送回主线程。
    """
    image = load_diffraction_image(path_str)
    tth, intensity = integrate_1d(image, npt=npt, **geom)
    return tth, intensity


def _on_file_selected(window: QMainWindow, current, previous) -> None:
    """文件列表选中变化 → 只登记当前文件，不计算。

    算哪个视图由工具栏的视图开关决定（勾选 = 打开面板并计算），
    文件选择本身保持轻快——这也是"反应迟钝"问题的根源：以前每次
    点选都触发一次完整积分。
    """
    if current is None:
        window.file_label.setText("未打开文件")
        return
    path = Path(current.data(Qt.UserRole))
    window.file_label.setText(path.name)
    _log(window, f"已选中 {path.name}（勾选视图按钮开始计算）")


def _run_view(window: QMainWindow, name: str) -> None:
    """按视图名对当前文件跑对应的计算（后台线程）。

    1D = 全角度积分；其余视图尚未接线（面板保持占位）。
    """
    if name != "1D":
        _log(window, f"{name} 视图尚未接线（面板占位）")
        return
    item = window.file_list.currentItem()
    if item is None:
        _log(window, "没有选中的文件")
        return
    path = Path(item.data(Qt.UserRole))
    geom = _collect_geometry(window)
    npt = int(window.params["输出点数"].value())
    window.status_text.setText(f"正在积分 {path.name}…")
    _log(window, f"开始积分 {path.name}（后台线程）")
    _spawn(window, path, geom, npt)


def _apply_params(window: QMainWindow) -> None:
    """[应用] 按钮：用当前参数重算焦点视图。

    焦点视图 = 参数坞顶部"编辑对象"（点图面板或计算完成时设定）；
    没选焦点时提示用户先点图。
    """
    if window.focus_view is None:
        _log(window, "先点击要更新的图面板（如 1D），再点 [应用]")
        return
    _log(window, f"[应用] 重算焦点视图：{window.focus_view}")
    _run_view(window, window.focus_view)


def _spawn(window: QMainWindow, path: Path, geom: dict, npt: int) -> None:
    """启动后台积分任务；引用挂在 window._tasks 防垃圾回收，结束移除。

    task 变量在闭包外定义、闭包内只引用：done/error 回调在任务结束
    时才被调用，那时 task 早已完成赋值。
    """
    task = None

    def done(result):
        window._tasks.remove(task)
        _on_integration_done(window, path, result)

    def error(msg):
        window._tasks.remove(task)
        _on_integration_error(window, path, msg)

    task = BackgroundTask(_compute_integration, str(path), geom, npt,
                          on_done=done, on_error=error)
    window._tasks.append(task)
    task.start()


def _on_integration_done(window: QMainWindow, path: Path, result) -> None:
    """积分完成（主线程）：结果仍属于当前文件才画，过期结果丢弃。

    用户可能等待期间点了别的文件（A 没算完就点了 B）——A 的结果
    回来时列表当前项已是 B，画 A 会让面板和选中状态对不上，直接
    丢弃并如实记日志。
    """
    item = window.file_list.currentItem()
    current = item.data(Qt.UserRole) if item else None
    if current != str(path):
        _log(window, f"已忽略 {path.name} 的过期结果（当前文件已切换）")
        return
    tth, intensity = result
    _set_focus(window, "1D")   # 最新生成的图成为参数面板的编辑对象
    _draw_1d(window, path, tth, intensity)
    _log(window, f"积分完成：{path.name}（{len(tth)} 点，"
                 f"2θ {tth.min():.3f}~{tth.max():.3f}°）")


def _on_integration_error(window: QMainWindow, path: Path, msg: str) -> None:
    """积分失败（主线程）：报错进日志区，不崩溃。"""
    _log(window, f"积分失败：{path.name} — {msg}")


def _draw_1d(window: QMainWindow, path: Path, tth, intensity) -> None:
    """在 1D 面板画出积分曲线（只允许主线程调用）。

    x 轴范围跟随参数坞的 2θ 上下限（看图范围，不参与计算）；
    面板显示的是全范围数据，用户可以自由改范围重画。
    """
    ax = window.axes_1d
    ax.clear()
    ax.plot(tth, intensity, "b-", lw=0.8)
    lo = window.params["2θ 下限 (°)"].value()
    hi = window.params["2θ 上限 (°)"].value()
    if lo < hi:
        ax.set_xlim(lo, hi)
    ax.set_xlabel("2θ (deg)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.set_title(f"{path.name}: full azimuthal integration")
    ax.grid(alpha=0.3)
    window.canvas_1d.draw()


# ══ 右侧：参数面板 ═════════════════════════════════════════
def _build_param_dock(window: QMainWindow) -> QDockWidget:
    """参数坞：两个分组（QGroupBox）——数据参数 / 图像参数。

    数据参数 = 参与计算的（几何配置 + 积分区间 + 点数），改它们
    会改变积分/校准的结果；图像参数 = 只看图不参与计算的（对比度
    + 剖面线角度）。对照 CLI：数据参数来自 integrate/calibrate 脚本，
    图像参数来自 view_diffraction 的 --vmin/--vmax/--angle。
    """
    dock = QDockWidget("参数", window)
    dock.setObjectName("param_dock")
    dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)

    content = QWidget()
    lay = QVBoxLayout(content)

    # 编辑对象：参数坞当前作用在哪个图面板上。点图面板（_FocusMarker）
    # 或某视图计算完成（_on_integration_done）时更新；[应用] 重算它。
    window.focus_label = QLabel("编辑对象：未选中图面板")
    window.focus_label.setStyleSheet("color: gray;")
    window.focus_label.setWordWrap(True)
    lay.addWidget(window.focus_label)

    # ── 数据参数组 ──
    data_box = QGroupBox("数据参数")
    form = QFormLayout(data_box)

    # 几何配置选择器：下拉框只显示短 key（如 lmfp1_lab6）——长备注
    # 挤进下拉框会撑宽参数坞；完整批次备注放在下方灰色说明行
    # （随选择更新）+ 悬停提示。key 藏在 itemData 里给程序用。
    # 选中即把该条目的标定几何填进下方三个输入框；完整条目（含
    # beam_center）挂在 window.config，后续 2D/剖面接线时直接取用。
    # 注意顺序：先填条目、设默认，再连接信号——建坞阶段日志区还没
    # 建好，信号此刻触发会去写一个还不存在的控件；默认值改由
    # create_window 收尾时显式调用 _apply_config 应用。
    window.config_combo = QComboBox()
    for name, entry in CONFIGS.items():
        window.config_combo.addItem(name, name)
        # 悬停提示：完整批次备注（下拉框里只放短 key）
        window.config_combo.setItemData(
            window.config_combo.count() - 1, entry["label"], Qt.ToolTipRole)

    window.config_label = QLabel(CONFIGS[DEFAULT_CONFIG]["label"])
    window.config_label.setWordWrap(True)          # 长备注自动换行，不撑宽
    window.config_label.setStyleSheet("color: gray;")

    field = QWidget()   # 下拉框 + 说明行装进一个字段（表格行内竖排）
    box = QVBoxLayout(field)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(2)
    box.addWidget(window.config_combo)
    box.addWidget(window.config_label)

    window.config_combo.setCurrentIndex(
        window.config_combo.findData(DEFAULT_CONFIG))
    window.config_combo.currentIndexChanged.connect(
        lambda i: _apply_config(window, i))
    form.addRow("几何配置", field)

    window.params = {}
    def add_float(form, name, lo, hi, value, decimals=2):
        box = QDoubleSpinBox()
        box.setRange(lo, hi)
        box.setValue(value)
        box.setDecimals(decimals)
        window.params[name] = box
        form.addRow(name, box)
        return box

    add_float(form, "像素尺寸 (µm)", 0.0, 10000.0, 200.0, decimals=1)
    add_float(form, "波长 (Å)", 0.0, 10.0, 0.1223, decimals=4)
    add_float(form, "初始距离 (mm)", 0.0, 10000.0, 1600.0, decimals=1)
    add_float(form, "2θ 下限 (°)", 0.0, 90.0, 1.0)
    add_float(form, "2θ 上限 (°)", 0.0, 90.0, 8.0)

    npt = QSpinBox()
    npt.setRange(100, 100000)
    npt.setValue(3000)
    window.params["输出点数"] = npt
    form.addRow("输出点数", npt)

    # [恢复默认] + [应用] 并排：[恢复默认] 只把参数复位（几何回到
    # 当前配置条目、区间/点数回到初值），不计算；[应用] 才重算焦点视图
    data_defaults = {
        "2θ 下限 (°)": 1.0,
        "2θ 上限 (°)": 8.0,
        "输出点数": 3000,
    }
    btn_reset_data = QPushButton("恢复默认")
    btn_reset_data.setObjectName("reset_data_btn")
    btn_apply = QPushButton("应用")
    btn_apply.setObjectName("apply_btn")
    btn_apply.clicked.connect(lambda: _apply_params(window))

    def reset_data():
        _apply_config(window, window.config_combo.currentIndex())
        for name, value in data_defaults.items():
            window.params[name].setValue(value)
        _log(window, "数据参数已恢复默认（未计算，点 [应用] 生效）")

    btn_reset_data.clicked.connect(reset_data)

    btn_row = QHBoxLayout()
    btn_row.addWidget(btn_reset_data)
    btn_row.addWidget(btn_apply)
    form.addRow(btn_row)

    lay.addWidget(data_box)

    # ── 图像参数组 ──
    img_box = QGroupBox("图像参数")
    form2 = QFormLayout(img_box)

    # 自动对比度（默认开）：vmin/vmax 按数据 1%/99.9% 分位数自定，
    # 与 view_diffraction 的默认行为一致；取消勾选后手填两个输入框
    # （对应 --vmin/--vmax 的"指定时覆盖自动值"语义）。自动模式下
    # 输入框置灰，信号一响立刻同步。
    auto = QCheckBox("自动对比度")
    auto.setChecked(True)
    window.params["自动对比度"] = auto
    form2.addRow(auto)

    add_float(form2, "对比度下限", 0.0, 1e9, 1.0, decimals=1)
    add_float(form2, "对比度上限", 0.0, 1e9, 100000.0, decimals=1)

    def sync_contrast(checked):
        window.params["对比度下限"].setEnabled(not checked)
        window.params["对比度上限"].setEnabled(not checked)

    auto.toggled.connect(sync_contrast)
    sync_contrast(True)   # 初始状态：自动开 → 两个输入框置灰

    angle = add_float(form2, "剖面角度 (°)", -180.0, 180.0, 0.0, decimals=1)
    angle.setSingleStep(5.0)   # 步进 5°，对应 view_diffraction 的 --angle

    # 看图参数：改动即时重画，不需要 [应用]（2D/剖面面板接线后生效）
    hint = QLabel("改动即时生效（2D/剖面接线后）")
    hint.setStyleSheet("color: gray;")
    hint.setWordWrap(True)
    form2.addRow(hint)

    img_defaults = {
        "对比度下限": 1.0,
        "对比度上限": 100000.0,
        "剖面角度 (°)": 0.0,
    }
    btn_reset_img = QPushButton("恢复默认")
    btn_reset_img.setObjectName("reset_image_btn")

    def reset_image():
        auto.setChecked(True)   # 触发 sync_contrast，输入框自动置灰
        for name, value in img_defaults.items():
            window.params[name].setValue(value)
        _log(window, "图像参数已恢复默认")

    btn_reset_img.clicked.connect(reset_image)
    form2.addRow(btn_reset_img)

    lay.addWidget(img_box)

    dock.setWidget(content)
    window.addDockWidget(Qt.RightDockWidgetArea, dock)
    return dock


def _apply_config(window: QMainWindow, index: int) -> None:
    """把下拉框选中的配置条目应用到参数面板。

    几何值来自 config.py 注册表（标定值，不是占位默认值）。只同步
    参数坞里已有的三个输入框（像素/波长/距离）；PONI、倾斜角等其余
    几何键随完整条目一起挂在 window.config 上，留给后续图面板接线。
    """
    key = window.config_combo.itemData(index)
    cfg = CONFIGS[key]
    geom = cfg["geometry"]
    window.config_name = key
    window.config = cfg   # 完整条目（label / geometry / beam_center）
    window.config_label.setText(cfg["label"])   # 说明行随选择更新
    window.params["像素尺寸 (µm)"].setValue(geom["pixel_size_m"] * 1e6)
    window.params["波长 (Å)"].setValue(geom["wavelength_m"] * 1e10)
    window.params["初始距离 (mm)"].setValue(geom["dist_m"] * 1e3)
    _log(window, f"已加载几何配置 {key}（{cfg['label']}）")


# ══ 底部：日志区（两层之一，状态行在 _build_status 里）════════
def _build_log_dock(window: QMainWindow) -> QDockWidget:
    """日志坞：累积输出（校准结果 / 积分统计都滚在这里），可收起。"""
    dock = QDockWidget("日志", window)
    dock.setObjectName("log_dock")
    window.log_text = QPlainTextEdit()
    window.log_text.setReadOnly(True)
    window.log_text.setMaximumBlockCount(5000)   # 只留最近 5000 行，防涨爆
    dock.setWidget(window.log_text)
    window.addDockWidget(Qt.BottomDockWidgetArea, dock)
    return dock


def _build_status(window: QMainWindow) -> None:
    """状态行（两层之二）：左侧常驻文字（"就绪"/瞬时消息）+
    右侧常驻当前文件。用常驻 QLabel 而不是 showMessage——
    后者超时清空后状态栏会变成看不见的细条。"""
    window.status_text = QLabel("就绪")
    window.statusBar().addWidget(window.status_text)
    window.file_label = QLabel("未打开文件")
    window.statusBar().addPermanentWidget(window.file_label)


# ══ 顶部：工具栏 ═══════════════════════════════════════════
def _build_toolbar(window: QMainWindow) -> None:
    """工具栏 = [校准] 模式开关 + 视图开关 + 日志开关。"""
    tb = QToolBar("主工具栏", window)
    tb.setMovable(False)
    window.addToolBar(tb)

    # 模式开关：[校准] 可勾选。按下 = 校准工作台（几何参数 +
    # 点图微调束心），弹起 = 默认的分析工作台。原来的 [图像] 按钮
    # 已删：outputs 摊成四个视图开关后，它只剩空壳
    btn_calib = QPushButton("校准")
    btn_calib.setCheckable(True)   # 默认弹起 = 分析工作台
    tb.addWidget(btn_calib)
    btn_calib.toggled.connect(lambda on: _on_mode(window, on))

    tb.addSeparator()

    # 视图开关：[2D][剖面][1D][瀑布]——勾选 = 对应图面板的开关
    window.view_buttons = {}   # 登记按钮，供面板可见性同步使用
    for name in VIEW_NAMES:
        btn = QPushButton(name)
        btn.setCheckable(True)
        tb.addWidget(btn)
        window.view_buttons[name] = btn
        btn.toggled.connect(lambda on, n=name: _toggle_plot(window, n, on))

    tb.addSeparator()

    # 日志开关：日志坞可被关闭，用这个勾选按钮重新打开
    btn_log = QPushButton("日志")
    btn_log.setCheckable(True)
    btn_log.setChecked(True)
    tb.addWidget(btn_log)
    window.log_dock.visibilityChanged.connect(btn_log.setChecked)
    btn_log.toggled.connect(window.log_dock.setVisible)


def _on_mode(window: QMainWindow, calibrating: bool) -> None:
    """模式开关：勾选 = 校准工作台，弹起 = 分析工作台（框架阶段只换占位提示）。"""
    if calibrating:
        window.mode_label.setText("校准模式 — 待实现")
        _log(window, "进入校准模式")
    else:
        window.mode_label.setText("分析模式 — 待实现")
        _log(window, "回到分析模式")


# ══ 中央：可停靠图面板区（内层 QMainWindow）═════════════════
def _build_center(window: QMainWindow) -> None:
    """内层 QMainWindow 托管图面板坞；外层窗口管文件/参数/日志坞。"""
    inner = QMainWindow(window)
    # PySide6 怪癖：QMainWindow(parent) 构造后仍带 Qt::Window 标志，
    # 不会被当成子部件嵌入（isWindow() 为真、永不随主窗口显示）。
    # 显式改成 Qt.Widget 后才是真正的嵌入式内层窗口。
    inner.setWindowFlags(Qt.Widget)
    inner.setObjectName("plot_area")
    inner.setDockOptions(QMainWindow.AnimatedDocks
                         | QMainWindow.AllowNestedDocks
                         | QMainWindow.AllowTabbedDocks)
    window.inner = inner
    window.plot_docks = {}

    window.mode_label = QLabel("分析模式 — 待实现")   # 默认：分析工作台
    window.mode_label.setAlignment(Qt.AlignCenter)
    inner.setCentralWidget(window.mode_label)
    window.setCentralWidget(inner)

    # 绘图区右下角：横排/竖排按钮（像 Excel 底部右角的视图按钮）。
    # 内层窗口自带状态栏，正贴绘图区下边缘；addPermanentWidget
    # 靠右排列，按钮组就落在右下角。
    inner.statusBar().setSizeGripEnabled(False)   # 去掉拖角手柄，更整洁
    arrange_box = QWidget()
    row = QHBoxLayout(arrange_box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    for name in ("横排", "竖排"):
        btn = QPushButton(name)
        btn.setFlat(True)      # 扁平样式，更像 Excel 角上的小按钮
        row.addWidget(btn)
        btn.clicked.connect(
            lambda checked=False, n=name: _arrange(window, n))
    inner.statusBar().addPermanentWidget(arrange_box)


def _build_view_widget(window: QMainWindow, name: str) -> QWidget:
    """按视图名创建面板内容：1D = matplotlib 画布，其余暂为占位标签。

    画布与坐标轴挂在 window.canvas_1d / window.axes_1d 上，供
    _draw_1d 使用；以后 2D/剖面/瀑布接线时在此函数里加分支。
    每个面板都装 _FocusMarker：点它即成为参数面板的编辑对象。
    """
    if name == "1D":
        fig = Figure(figsize=(5, 3), tight_layout=True)
        canvas = FigureCanvasQTAgg(fig)
        window.canvas_1d = canvas
        window.axes_1d = fig.add_subplot(111)
        widget = canvas
    else:
        placeholder = QLabel(f"{name} — 面板占位")
        placeholder.setAlignment(Qt.AlignCenter)
        widget = placeholder
    widget.installEventFilter(_FocusMarker(window, name))
    return widget


def _toggle_plot(window: QMainWindow, name: str, on: bool) -> None:
    """视图开关：勾选 = 打开面板（首次打开顺带计算该视图），
    取消勾选 = 隐藏面板（内容保留，重新勾选不再重算；要重算点
    [应用]）。

    面板是 QDockWidget：拖标题栏换位、拖出窗口浮动、× 关闭。
    用户用 × 关掉面板时 visibilityChanged 会把按钮勾掉，两者同步。
    """
    dock = window.plot_docks.get(name)
    if dock is None:
        dock = QDockWidget(name, window.inner)
        dock.setObjectName(f"plot_{name}")
        dock.setWidget(_build_view_widget(window, name))
        window.plot_docks[name] = dock
        window.inner.addDockWidget(Qt.RightDockWidgetArea, dock)
        dock.show()   # 显式显示：不依赖停靠系统自动显示
        dock.visibilityChanged.connect(
            lambda v: _sync_toggle(window, name, v))
        _log(window, f"打开{name}面板")
        _run_view(window, name)   # 勾选视图 = 选择要算的内容
    else:
        dock.setVisible(on)


def _sync_toggle(window: QMainWindow, name: str, visible: bool) -> None:
    """面板可见性与工具栏勾选按钮保持同步（用户点 × 关面板时）。"""
    window.view_buttons[name].setChecked(visible)


def _arrange(window: QMainWindow, mode: str) -> None:
    """一键重排所有已打开的面板：横排成一行 / 竖排成一列。

    用 splitDockWidget 依次把每个面板排到前一个旁边（横）或下面
    （竖），再用 resizeDocks 均分空间。每次点击都会重新确立布局，
    之前怎么摆的都归位。
    """
    docks = [d for d in window.plot_docks.values() if d.isVisible()]
    if not docks:
        _log(window, "没有打开的面板")
        return
    for d in docks:
        window.inner.addDockWidget(Qt.RightDockWidgetArea, d)
        d.show()
    orient = Qt.Vertical if mode == "竖排" else Qt.Horizontal
    for prev, d in zip(docks, docks[1:]):
        window.inner.splitDockWidget(prev, d, orient)
    size = (window.inner.height() if mode == "竖排"
            else window.inner.width()) // len(docks)
    window.inner.resizeDocks(docks, [max(size, 120)] * len(docks), orient)
    _log(window, f"已{mode} {len(docks)} 个面板")


# ══ 主窗口组装 ════════════════════════════════════════════
def create_window() -> QMainWindow:
    """组装主框架：文件坞 + 参数坞 + 日志坞 + 工具栏 + 中央面板区。"""
    window = QMainWindow()
    window.setWindowTitle("XRD Toolkit")
    window.resize(1200, 800)

    window._status_timer = None   # _log 里的状态栏恢复计时器（懒创建）
    window.log = lambda text: _log(window, text)
    window.add_files = lambda paths: add_files(window, paths)

    # 后台任务簿：进行中的积分任务挂在这里防垃圾回收（结束回调里
    # 移除）；关窗口时逐一 discard（等待后台函数返回，防线程悬空）
    window._tasks = []
    # 焦点视图：参数面板"编辑对象"指向的图面板（点图/计算完成时更新）
    window.focus_view = None

    _build_center(window)
    file_dock = _build_file_dock(window)
    param_dock = _build_param_dock(window)
    window.log_dock = _build_log_dock(window)
    _build_status(window)
    _build_toolbar(window)

    # 点选文件 → 只登记当前文件（此时各坞已就绪，可安全接信号）
    window.file_list.currentItemChanged.connect(
        lambda cur, prev: _on_file_selected(window, cur, prev))

    # 关窗口前等后台任务收尾：直接销毁运行中的线程 Qt 会 abort
    def on_close(event):
        for task in window._tasks:
            task.discard()
        event.accept()

    window.closeEvent = on_close

    # 初始尺寸：文件列/参数列默认收到最窄（各自内容的最小宽度），
    # 需要时用户自己拖宽；日志高 140（都可拖动）
    for d in (file_dock, param_dock):
        window.resizeDocks([d], [d.minimumSizeHint().width()], Qt.Horizontal)
    window.resizeDocks([window.log_dock], [140], Qt.Vertical)

    # 启动即应用默认配置条目：参数坞初值来自注册表（如初始距离
    # 1595.80 mm），而不是写死的占位默认值
    _apply_config(window, window.config_combo.currentIndex())

    window.log("主框架已就绪")
    return window


def main() -> int:
    """程序入口：创建 QApplication → 建窗口 → 进入事件循环。

    事件循环（app.exec()）是 GUI 程序的"心跳"：程序停在这里，
    不断接收鼠标/键盘/重绘事件并分发出去，直到窗口被关闭。
    """
    app = QApplication(sys.argv)
    window = create_window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
