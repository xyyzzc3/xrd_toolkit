"""GUI 主程序：窗口组装（文件坞 / 参数坞 / 日志坞 / 工具栏 / 保存关窗流程）。

入口：python -m xrd_toolkit.gui（__main__.py 转发到 main()）。
create_window() 与 main() 分离：测试里可以只建窗口、不进事件循环。

模块图（2026-09-18 从 3622 行单文件拆分，调用方向永远从上往下，
循环导入无路可走）：
    app.py（本模块：窗口组装与保存/关窗流程）
      → plot_views.py   视图注册表 + 出图调度（2D/剖面/1D/瀑布全接
                         线）+ 悬停取点 + 手势（扩展点：
                         _VIEW_BUILDERS/_VIEW_RUNNERS 两张注册表）
      → file_dock.py    左侧文件坞：文件栏（树：原始数据 + 各阶段产物
                         分组）+ 导入/拖放 + 选择工具
      → sources.py      条目的"数据来源"：条目是原始文件还是哪个阶段的
                         产物、该去哪儿取数（跨组读勾选集合的地方只认它）
      → calib.py        校准工作台：参数坞第 2 页表单 + 中央校准图
                         面板 + 自动/手动校准后台任务
      → panels.py        面板容器生命周期：MDI 子窗口/弹出窗口、
                         关闭即遗忘、抓手、平铺、总缩放
      → customize.py     Customize 自绘轴属性对话框
      → panel_state.py   共享层：_log/_content/参数快照/焦点回放/
                         几何收集/自动显示区间
      → tasks.py         后台任务运行器（耗时计算挪出界面线程）

窗口上的公共接口（供面板与测试使用）：
  window.log(text)        写日志区 + 状态行
  window.add_files(paths) 把文件加进左侧文件栏（默认不勾选，见 file_dock）
  window.drop_folder(path) 扫描文件夹加入文件栏（与 [打开文件夹] 同逻辑）
  window.refresh_groups() 重建文件栏里的产物分组（批量处理/算完 1D/
                          清空缓存之后调；实现见 file_dock）
  window.mdi              QMdiArea（绘图区，所有图子窗口的父场地）
  window.plot_docks       {面板键: QMdiSubWindow 或 _FloatedWindow}，
                          键 = f"{视图}|{路径}"，重复文件改名条目再补
                          |显示名 区分；已关闭的面板不在登记表里
  window.focus_panel      参数面板编辑对象 = 面板键（点窗口任意处/
                          计算完成时设定）
  window.params           参数面板控件字典
  window.config_name      当前选中的配置条目 key（如 lmfp1_lab6）
  window.config           完整条目 dict（label / geometry / beam_center）
  各视图面板的画布/坐标轴在面板内容上：_content(dock).axes_1d
  （2D/剖面/瀑布 = axes_2d / axes_profile / axes_waterfall）

兼容再导出：拆分前全部函数都住在 app 模块里，测试等外部代码继续
经 gui_app 访问它们（下方 import 即再导出）。mock.patch 的目标请
指到实现所在的新模块（tests 里已改为 gui_views / gui_customize）。
"""
import re
import sys
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("qtagg")   # 必须在导入 FigureCanvasQTAgg 之前选定 Qt 后端
# 图标题取自文件显示名（重复文件改名可输入中文）→ 字体回退链补上
# macOS 中文字体，缺字形时逐字体回退，标题不会渲染成方框。
# 注意要设 font.family 直接给列表：实测 qtagg 后端下 font.sans-serif
# 列表不触发回退（Agg 可以），中文仍会变方框
# 中文字体**按平台给**（2026-10-04 试用反馈：Windows 上图内中文是方框
# ——原列表全是 Mac 字体 + DejaVu（无中文字形））。matplotlib ≥3.6 按
# 这个列表逐字回退，所以只写"该系统一定有"的那几个；跨平台混着写会在
# 另一个平台上白报 "Font family not found"。
if sys.platform == "darwin":
    _cjk_fonts = ["PingFang SC", "Hiragino Sans GB", "Arial Unicode MS"]
else:                                  # Windows（Linux 桌面同理可加 Noto）
    _cjk_fonts = ["Microsoft YaHei", "SimHei"]
matplotlib.rcParams["font.family"] = ["DejaVu Sans"] + _cjk_fonts
from PySide6.QtCore import (QEvent, QLibraryInfo, QObject, Qt, QSize,
                            QTranslator, QUrl)
from PySide6.QtGui import (QAction, QDesktopServices, QIcon, QKeySequence,
                            QShortcut)
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QInputDialog, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QMdiSubWindow,  # 兼容再导出：测试 isinstance 用
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QStackedWidget, QToolBar,
    QVBoxLayout, QWidget, QDockWidget, QApplication)

from xrd_toolkit import config, paths
from xrd_toolkit.cli import SUPPORTED_EXTS   # 文件夹导入的格式白名单（与 CLI 菜单一致）
from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG, entry_display
# ── 兼容再导出（见模块 docstring）：测试继续经本模块访问 ──
from xrd_toolkit.gui.plot_export import (
    _ask_save_options, _build_export_dialog, _checked_1d_results,
    _run_export, _save_figures, _write_csv_summary, _write_export)
from xrd_toolkit.gui.file_dock import (
    FILE_FILTER, FileTree, _ask_duplicate, _ask_rename, _build_file_dock,
    _dropped_items, _on_file_selected, _refresh_file_label, _scan_folder,
    _sync_current_to_checks, _sync_select_label, _unique_display_name,
    add_files, refresh_open_marks, refresh_product_groups)
# 校准相关按职责分了三块（2026-09-23 拆分）：页面与流程在 calib，
# 中央校准图面板在 calib_panel，配置条目进出在 config_ops。这里全部
# 再导出（见模块 docstring 的"兼容再导出"），外部照旧经 gui_app 取用。
from xrd_toolkit.gui.calib import (
    _build_calib_form, _enter_calib, _exit_calib, _start_auto_calib,
    _start_manual_calib)
from xrd_toolkit.gui.calib_panel import (
    _CalibSubWindow, _clear_calib_points, _close_calib_panel,
    _draw_calib_image, _on_calib_click, _open_calib_panel,
    _undo_calib_point)
from xrd_toolkit.gui.config_ops import _sync_del_config_btn
from xrd_toolkit.gui.customize import (
    _apply_customize, _build_customize_dialog, _open_customize_dialog)
from xrd_toolkit.gui.panels import (
    _apply_area_zoom, _build_center, _close_panel, _ElideLabel,
    _FloatedWindow, _FocusElideLabel, _PlotSubWindow, _toggle_pop_out)
from xrd_toolkit.gui.panel_state import (
    DATA_PARAM_DEFAULTS, FOCUS_EMPTY_TEXT,
    _apply_auto_contrast, _apply_auto_heatlim, _apply_auto_ylim,
    _apply_config, _bg_geom_sig, _collect_geometry, _content,
    _family_label, _GRADIENT_FAMILY_ORDER, _log,
    _note_user_edit, _param_box_set, _pending_names, _proc_settings,
    _reload_config_combo, _set_focus)
from xrd_toolkit.gui.panel_state import _proc_curve
from xrd_toolkit.gui.plot_compare import (
    _plot_compare, _plot_heatmap, _refresh_compare, _refresh_compare_family,
    _refresh_heat, _refresh_heat_view, _refresh_heatmap_view)
from xrd_toolkit.gui.plot_export import _ask_save_options
from xrd_toolkit.gui.plot_panels import (
    _home_key_reset, _hover_leave, _hover_motion, _magnifier_on,
    _open_plot_panel, _pan_motion, _pan_press, _pan_release, _sync_bar_active,
    _wheel_zoom)
from xrd_toolkit.gui.plot_views import (
    _apply_params, _proc_batch_apply, _proc_keep_this, _proc_save,
    _compute_integration, _draw_1d, _open_source_group, _open_source_view,
    _plot_view, _refresh_1d_display, _refresh_image_display,
    _refresh_heatmap_names, _refresh_profile_angle, _refresh_proc,
    _refresh_waterfalls,
    _retarget_product_panel_to_source, _spawn_task,
    apply_recipe, chain_label_of, recipe_text)
from xrd_toolkit.gui import sources as gui_sources
from xrd_toolkit.gui import watchdog
from xrd_toolkit.services import recipes as recipe_store
# 自动基线窗口的**默认值**（0.2°，见 background 里的来历）：控件初始值、
# 悬停提示都读它，免得"引擎默认 / 面板默认 / 恢复默认 / 控件初始值"四处
# 各写一个字面量、改一个漏一个（2026-09-28 之前控件就是写死的 1.0）
from xrd_toolkit.services.background import AUTO_WINDOW_DEG

# 参数坞背景/配方 helper（2026-10-21 从本文件原样搬出，只改结构）
from xrd_toolkit.gui.param_dock.helpers import (
    _apply_selected_recipe, _bg_anchor_count, _bg_anchor_file,
    _bg_edit_dock, _connect_pending_hooks, _delete_selected_recipe,
    _fit_max_width, _on_bg_mode, _on_choose_blank, _on_clear_anchors,
    _on_pick_anchor, _recipe_combo_fill, _save_recipe_dialog,
    _selected_recipe, _sync_bg_rows, _sync_recipe_rows, _update_bg_count)
from xrd_toolkit.gui.param_dock.pages import (
    _build_check_summary, _build_plot_type_row, _cut_text, _on_cut_add,
    _on_cut_clear, _on_cut_toggled, _select_plot_type, _sync_cut_label,
    _sync_data_row, _sync_geom_row, _sync_smooth_rows)
from xrd_toolkit.gui.param_dock.builder import _build_param_dock

VIEW_NAMES = ("2D", "剖面", "1D", "瀑布")   # 四个图面板（作图按钮的顺序）

class _MainWindow(QMainWindow):
    """顶层窗口：接受拖入文件或文件夹（拖到窗口任意位置）。

    文件 → 加进文件列表；文件夹 → 扫描其中的数据文件。至少拖进
    一个支持类型的文件或一个目录才"接住"（光标变 +）；子控件默认
    不接拖放（日志区显式关掉了文本拖放），事件会冒泡到顶层窗口
    ——一个入口覆盖整个窗口面。drop_callback / drop_folder 由
    create_window 接到 add_files / _scan_folder 上。
    """

    def changeEvent(self, event):                 # noqa: N802（Qt 命名）
        """从最小化/显示桌面回来（"最小化 → 恢复"）的那一步：对一次三个
        坞的显隐（背景见 _resync_docks）。

        **只在真的"最小化 → 不最小化"那一步动手**：2026-10-04 写这版时
        条件写成了"任何状态变化、只要当前不是最小化"，于是进/出 macOS
        全屏（也是 WindowStateChange、也不最小化）会顺带对一次显隐
        （用户 2026-10-16："全屏会导致参数栏隐藏"——坞是入口按钮露出来的、
        当时意图没记上，一对就被收起）。现在按 docstring 的原意收窄成
        最小化恢复的那一步；意图记账同时补齐（见 _switch_entrance /
        _clear_entrance / calib._enter_calib），两层都不再漏。
        """
        super().changeEvent(event)
        if event.type() != QEvent.WindowStateChange:
            return
        now = self.isMinimized()
        was = getattr(self, "_was_minimized", False)
        self._was_minimized = now
        if was and not now:
            _resync_docks(self)

    def __init__(self):
        super().__init__()
        self.drop_callback = None
        self.drop_folder = None
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if (self.drop_callback or self.drop_folder) and _dropped_items(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        files, dirs = [], []
        for p in _dropped_items(event):
            (dirs if p.is_dir() else files).append(p)
        if not files and not dirs:
            event.ignore()
            return
        if files and self.drop_callback:
            self.drop_callback([str(p) for p in files])
        for d in dirs:
            if self.drop_folder:
                self.drop_folder(str(d))
        event.acceptProposedAction()

class _PanelClickTracker(QObject):
    """应用级事件过滤器：点任何面板窗口内任何位置都选中该面板。

    与用户讨论定稿：选中子窗口 = 选中参数，不必非点图本体（标题
    栏、边框、图、工具栏都算）。为什么必须是应用级：探针实证
    QWidget 的父过滤器收不到子部件事件（子部件 accept 后事件不
    向上传播，matplotlib 画布恰恰会 accept 鼠标按下）——只有挂
    在 QApplication 上的过滤器能看到一切。收到鼠标按下后从落点
    逐级向上找面板容器（_PlotSubWindow / _FloatedWindow），且该
    容器必须登记在本窗口 plot_docks 里（测试会同时开多个窗口，
    各窗口的过滤器只认自己登记的面板，防止串窗）。只"监听"不
    "拦截"：返回 False，事件照常传递。
    """

    def __init__(self, window: QMainWindow):
        super().__init__(window)   # 挂在窗口上，随窗口销毁
        # 刻意不存 window 引用：QApplication 的过滤器表 → 过滤器 →
        # window 会形成引用环，关掉的窗口永不回收（探针实测全套件
        # 30 个窗口全存活）。窗口就是父对象，用时 self.parent() 取。

    def eventFilter(self, obj, event):
        if event.type() != QEvent.MouseButtonPress:
            return False
        window = self.parent()
        if not isinstance(window, QMainWindow):
            return False   # 窗口已销毁：过滤器随父摘除前的兜底
        w = obj if isinstance(obj, QWidget) else None
        while w is not None:
            if isinstance(w, (_PlotSubWindow, _FloatedWindow)):
                key = w.panel_key
                if window.plot_docks.get(key) is w:
                    _set_focus(window, key, w.windowTitle())
                break
            w = w.parentWidget()
        return False   # 不消费事件

# _ElideLabel 已搬到 panels.py（2026-10-07：坐标读数进了绘图区横带，
# 横带建在 panels；这里 import 进来，兼容再导出照旧）

# ══ 底部：日志区（两层之一，状态行在 _build_status 里）════════
def _build_log_dock(window: QMainWindow) -> QDockWidget:
    """日志坞：累积输出（校准结果 / 积分统计都滚在这里），可收起。"""
    dock = QDockWidget("日志", window)
    dock.setObjectName("log_dock")
    window.log_text = QPlainTextEdit()
    window.log_text.setReadOnly(True)
    window.log_text.setMaximumBlockCount(5000)   # 只留最近 5000 行，防涨爆
    # 文本编辑器默认接受拖放：不关掉的话，文件拖到日志区会被它
    # 吞掉（塞成文字），到不了顶层窗口的"拖入文件"入口
    window.log_text.setAcceptDrops(False)
    dock.setWidget(window.log_text)
    window.addDockWidget(Qt.BottomDockWidgetArea, dock)
    return dock

def _build_status(window: QMainWindow) -> None:
    """状态行（两层之二）：左侧常驻文字（"就绪"/瞬时消息）+ 右侧
    进度条 | 竖分隔线 | 当前文件。用常驻 QLabel 而不是 showMessage——
    后者超时清空后状态栏会变成看不见的细条。

    实时**坐标读数 2026-10-07 搬去绘图区横带**（panels._build_center：
    看图时眼睛够不着窗口最底部）——本条只剩"消息 + 进度 + 当前文件"，
    竖分隔线改分隔进度条与文件信息。"""
    window.status_text = QLabel("就绪")
    window.statusBar().addWidget(window.status_text)
    # 批量进度条（[1D] 等一次勾多张时出现）：开面板、后台积分、只算不画
    # 都走它（见 plot_views._progress_show/_batch_step）。平时藏起来，
    # 不占状态栏的地方；宽度固定，出现时右侧那几项不会左右跳。
    window.batch_progress = QProgressBar()
    window.batch_progress.setObjectName("batch_progress")
    window.batch_progress.setFixedWidth(150)
    window.batch_progress.setMaximumHeight(14)
    window.batch_progress.setTextVisible(True)
    window.batch_progress.setFormat("%v/%m")
    window.batch_progress.setVisible(False)
    window.statusBar().addPermanentWidget(window.batch_progress)
    sep = QFrame()
    sep.setFrameShape(QFrame.Shape.VLine)
    sep.setFrameShadow(QFrame.Shadow.Sunken)
    window.statusBar().addPermanentWidget(sep)
    window.file_label = QLabel("未打开任何文件")
    window.statusBar().addPermanentWidget(window.file_label)

# ══ 顶部：工具栏 ═══════════════════════════════════════════
def _set_app_icon(window: QMainWindow) -> None:
    """把随包的 icon.png 设成应用图标（2026-10-04 试用反馈）。

    Windows 的任务栏/窗口用的是**运行时窗口的图标**：打包时设的 exe
    图标只在资源管理器里显示，运行起来仍是 Qt 的默认图标。这里显式设上
    （macOS 不受影响——它用 .app 里的 icns）。

    **每个进程只设一次**（图标是 application 级属性）：第一版每建一个
    窗口都调，而 setWindowIcon 会同步派发事件（走事件过滤器做枚举转换）
    ——全套件几百个窗口，把测试套件从 29 分钟拖过看门狗线、连着误杀
    两轮（2026-10-04 的转储拍到主线程就停在这条调用里）。
    """
    app = QApplication.instance()
    if app is None or not app.windowIcon().isNull():
        return                     # 进程级属性：设过一次就不再设
    icon_path = paths.shipped_file("icon.png", "packaging/icon.png")
    if icon_path is None:
        return
    app.setWindowIcon(QIcon(str(icon_path)))

def _resync_docks(window: QMainWindow) -> None:
    """把三个坞的显隐对齐到工具栏按钮（幂等；见 _MainWindow.changeEvent）。

    2026-10-04 试用反馈：Windows 上"点右下角显示桌面"、再点任务栏图标
    回来之后，文件/参数/日志三个坞都不见了，要再点一次才出来。本机复现
    不了，这里做**防御**：窗口状态一变就把三个坞对回**用户意图**并重绘。

    为什么不用按钮的勾选状态当依据：坞的显隐是**双向同步**的
    （dock.visibilityChanged → btn.setChecked），坞被程序/系统藏起来时
    按钮会跟着取消勾选——按按钮恢复等于"恢复成不显示"（写这版时测试
    当场逮到）。所以意图只在用户**真点击**按钮时记录（clicked 信号；
    setChecked 不触发它），系统把坞藏了也改不动它。

    **程序自己按用户动作显隐坞的三处也记账**（2026-10-16 补：入口选了
    曝坞、退出入口收坞、进校准曝坞——此前只有按钮点击记，入口露出来的
    坞意图一直是开局那个 False，一对显隐就被收走），见 _switch_entrance /
    _clear_entrance / calib._enter_calib。
    """
    for name, dock in (("文件", window.file_dock),
                       ("参数", window.param_dock),
                       ("日志", window.log_dock)):
        want = getattr(window, "_dock_intent", {}).get(name, True)
        if dock.isVisible() != want:
            dock.setVisible(want)
    window.update()

def _open_with_system(window: QMainWindow, path, what: str,
                      hint: str = "") -> None:
    """用系统默认程序打开一个随包文件（使用说明进浏览器、许可进文本编辑器）。

    path 为 None（随包文件缺失，理论上只会在手改过的包里发生）时记一行
    日志并给出去哪找——不弹错误框吓人。
    """
    if path is None:
        _log(window, f"{what}读不到（随包文件缺失）{hint}")
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

def _open_manual(window: QMainWindow) -> None:
    """[帮助 → 使用说明]：打开随包的《使用说明》HTML（系统浏览器）。"""
    _open_with_system(
        window, paths.shipped_file("使用说明.html", "docs/使用说明.html"),
        "使用说明", "；项目主页 docs/ 下有一份")

def _open_notices(window: QMainWindow) -> None:
    """打开第三方许可声明（随包分发的那一份）。"""
    _open_with_system(
        window,
        paths.shipped_file("THIRD_PARTY_NOTICES.txt",
                           "packaging/THIRD_PARTY_NOTICES.txt"),
        "第三方许可声明")

def _show_about(window: QMainWindow) -> None:
    """「关于 XRD Toolkit」：名称 / 版本 / 作者 / 许可 + 两个打开入口。

    2026-10-03 加（用户："按照行业规范、市场规范来做"）：桌面软件都有
    这个入口——版本号、许可、说明书从这找。macOS 上经 MenuRole 归入
    应用菜单（顺带补齐 Cmd+Q 等原生项），Windows 上在 [帮助] 里。
    """
    from xrd_toolkit import __version__
    box = QMessageBox(window)
    box.setWindowTitle("关于 XRD Toolkit")
    box.setText(f"<b>XRD Toolkit</b> {__version__}")
    box.setInformativeText(
        "二维 XRD 衍射数据处理：LaB₆ 几何校准、方位角积分、"
        "背景扣除、批量对比与热图。\n\n"
        "© 2026 Chenze Bian · MIT 许可\n"
        "随附的第三方组件（Qt/PySide6、pyFAI、NumPy、SciPy、"
        "Matplotlib 等）各按其许可分发")
    manual_btn = box.addButton("使用说明", QMessageBox.ActionRole)
    lic_btn = box.addButton("第三方许可", QMessageBox.ActionRole)
    box.addButton(QMessageBox.Close)
    box.exec()
    if box.clickedButton() is manual_btn:
        _open_manual(window)
    elif box.clickedButton() is lic_btn:
        _open_notices(window)

def _build_menu(window: QMainWindow) -> None:
    """菜单栏：帮助 → 使用说明 / 关于（2026-10-03，按桌面软件惯例）。

    0.1 打包分发之后，"这是什么、哪一版、谁做的、许可怎么算、说明书在哪"
    必须有地方可看；F1 开说明书是 Windows 侧的通用习惯（QKeySequence.
    HelpContents），macOS 上系统还会给帮助菜单配搜索框。

    **Windows/Linux 上这条菜单栏是隐藏的**（2026-10-07 用户："windows 是在
    功能栏的上方多出一行，仅有帮助一个选项"——它只装一个「帮助」，白占
    窗口里一整条）。帮助全走工具栏的 [帮助▾]（同一批 QAction，见
    _build_toolbar）。**macOS 保留**：那里的菜单栏是系统级的（屏幕顶部
    全局那条），不占窗口，也是 Apple 惯例里「关于」该住的地方
    （AboutRole 靠它搬到应用菜单）。隐藏后 F1 照常生效（动作显式挂到
    窗口上保底，离屏实测过）。
    """
    help_menu = window.menuBar().addMenu("帮助")
    act_manual = help_menu.addAction("使用说明")
    act_manual.setShortcut(QKeySequence.HelpContents)      # F1
    act_manual.triggered.connect(lambda: _open_manual(window))
    act_about = help_menu.addAction("关于 XRD Toolkit")
    # macOS：这句让系统把它挪进应用菜单的「关于」位（原生惯例）
    act_about.setMenuRole(QAction.AboutRole)
    act_about.triggered.connect(lambda: _show_about(window))
    window.menu_actions = {"manual": act_manual, "about": act_about}
    if sys.platform != "darwin":
        window.menuBar().setVisible(False)
        # F1 保底：动作既在（隐藏的）菜单里、也在 [帮助▾] 菜单里，这里再
        # 挂到窗口这一级——实测"隐藏菜单栏"后它仍能触发
        window.addAction(act_manual)

_MASK_TIP = ("Mask 工作台：画/自动遮罩 beamstop/模块缝/坏点，"
             "生成 Mask 后的 2D 数据（原图 + mask），再去做 1D")


def _open_mask_workbench(window: QMainWindow) -> None:
    """打开/前置中央 Mask 工作台（工具栏 [Mask]）。

    内容（MaskWorkbench）常驻主窗口；子窗口只是显示容器。× 关闭 =
    退出 Mask 模式（参数坞/日志坞恢复），内容不销毁，下次原样继续。
    模块级延迟导入避开 app ↔ mask_editor 的环。
    """
    from xrd_toolkit.gui.mask_editor import MaskWorkbench, _MaskSubWindow
    if getattr(window, "mask_editor", None) is None:
        window.mask_editor = MaskWorkbench(window)
    dock = getattr(window, "mask_dock", None)
    if dock is None:
        dock = _MaskSubWindow(window)
        dock.setObjectName("mask_workbench")
        dock.setWidget(window.mask_editor)
        window.mdi.addSubWindow(dock)
        window.mask_dock = dock
        dock.setWindowTitle("Mask 工作台")
        area = window.mdi.viewport().size()
        dock.resize(max(640, area.width() - 40),
                    max(480, area.height() - 40))
        dock.move(12, 12)
    dock.show()
    dock.raise_()


def _enter_mask_mode(window: QMainWindow) -> None:
    """进入 Mask 工作台：收起参数/日志坞，中央换成 Mask 工作台。"""
    if getattr(window, "_mask_active", False):
        return
    # 校准与 Mask 互斥：从校准进来先把校准退掉（它会恢复分析态）
    calib = getattr(window, "calib_btn", None)
    if calib is not None and calib.isChecked():
        calib.setChecked(False)
    window._mask_active = True
    window._mask_prev_entrance = getattr(window, "_last_entrance", None)
    window._mask_dock_saved = {
        name: window._dock_intent.get(name, False)
        for name in ("参数", "日志")}
    window._dock_intent["参数"] = False
    window._dock_intent["日志"] = False
    _resync_docks(window)
    # 五个分析入口熄灯（参数页不动，退出时按 _mask_prev_entrance 复原）
    _highlight_entrance(window, None)
    window.mode_label.setText("Mask 模式")
    _open_mask_workbench(window)
    # Home 键：工作台自己的 [复位视图] 接管；主窗口那条先让开，
    # 否则两个 WindowShortcut 同键 = Qt 判定歧义、两条都不触发
    main_home = getattr(window, "home_shortcut", None)
    if main_home is not None:
        main_home.setEnabled(False)
    window.mask_editor.home_sc.setEnabled(True)
    window.mask_btn.setText("退出 Mask")
    window.mask_btn.setToolTip("退出 Mask 工作台，回到分析模式")
    _log(window, "进入 Mask 模式")


def _exit_mask_mode(window: QMainWindow) -> None:
    """退出 Mask 工作台：隐藏子窗口（状态保留）+ 恢复参数/日志坞。"""
    if not getattr(window, "_mask_active", False):
        return
    window._mask_active = False
    dock = getattr(window, "mask_dock", None)
    if dock is not None:
        dock.hide()
    # Home 键还给主窗口（工作台隐藏后它的快捷键不该再抢）
    editor = getattr(window, "mask_editor", None)
    if editor is not None:
        editor.home_sc.setEnabled(False)
    main_home = getattr(window, "home_shortcut", None)
    if main_home is not None:
        main_home.setEnabled(True)
    saved = getattr(window, "_mask_dock_saved", None) or {}
    for name, want in saved.items():
        window._dock_intent[name] = want
    window._mask_dock_saved = None
    _resync_docks(window)
    prev = getattr(window, "_mask_prev_entrance", None)
    if prev is not None:
        _highlight_entrance(window, prev)
    window.mode_label.setText("分析模式")
    window.mask_btn.setText("Mask")
    window.mask_btn.setToolTip(_MASK_TIP)
    _log(window, "退出 Mask 模式")


def _on_mask_mode(window: QMainWindow, on: bool) -> None:
    if on:
        _enter_mask_mode(window)
    else:
        _exit_mask_mode(window)


def _build_toolbar(window: QMainWindow) -> None:
    """工具栏 = 五个入口 + [Mask] 独立编辑器 + 面板开关 + [帮助]。

    分组与顺序（用户 2026-10-07 两轮讨论定稿，2026-10-27 加 Mask）：
    `[校准][Mask] │ [原图] [1D] [处理] [对比] │ [文件][参数][日志] │
    [帮助▾]`——左边一格是"一次性设置"（校准 / mask），右边四个是每天
    干活的循环，按数据流排：**原图**（直接
    看/直接算原图：2D / 剖面 / 瀑布）→ **1D**（积分主台）→ **处理**
    （链加工）→ **对比**（多文件汇总）。

    入口（位置 A = 窗口顶部，用户 2026-09-24 定稿）：点一个 = 参数坞翻到
    那一页（见 _switch_entrance）；出图动作由各页底部的产出按钮负责
    （1D：[重算这张图][出图…]；处理：[存成产物]（合并了原 [批量处理]）；
    对比：[出对比][出热图]；原图：[出图…]）；存图走面板自己的 [保存图片…]
    （[导出图片…] 已于 2026-10-01 删除）。

    类型行拆分（2026-10-07）：「原图」页只剩 2D / 剖面 / 瀑布 三个类型
    按钮；1D / 对比 / 热图各回自己的家（兼容别名：window.view_buttons
    ["1D"] / compare_btn / heat_btn 指向那三颗产出按钮）。
    """
    tb = QToolBar("主工具栏", window)
    tb.setMovable(False)
    window.addToolBar(tb)

    # 五个入口：可勾选（当前页高亮）。[校准] 另有一层含义——进出校准
    # 工作台（开/关校准面板），那条走 toggled → _on_mode（calib.py 的
    # [返回分析模式] 也走同一条路：setChecked(False)）
    window.entrance_buttons = {}
    # 退出校准翻回哪一页：None = 还没选过任何一个入口（开局就是这样，
    # 退出校准就回到"什么都没选"，见 _clear_entrance / _on_mode）
    window._last_entrance = None
    btn_calib = QPushButton("校准")
    btn_calib.setCheckable(True)
    btn_calib.setToolTip(_ENTRANCE_TIPS["校准"][0])
    tb.addWidget(btn_calib)
    window.entrance_buttons["校准"] = btn_calib
    btn_calib.clicked.connect(lambda checked=False: _switch_entrance(window, "校准"))
    # [Mask] 与 [校准] 并列：都属"一次性设置"，但它开的是中央工作台
    # （不进 entrance_buttons / PARAM_PAGES）。可勾选：点亮 = Mask 模式，
    # 再点一次 = 退出（参数坞/日志坞恢复原样）。
    window._mask_active = False
    window._mask_prev_entrance = None
    window._mask_dock_saved = None
    btn_mask = QPushButton("Mask")
    btn_mask.setCheckable(True)
    btn_mask.setToolTip(_MASK_TIP)
    btn_mask.toggled.connect(lambda on: _on_mask_mode(window, on))
    tb.addWidget(btn_mask)
    window.mask_btn = btn_mask
    tb.addSeparator()      # 设置 │ 干活的循环（见 docstring 的分组说明）
    for name in ("原图", "1D", "处理", "对比"):
        btn = QPushButton(name)
        btn.setCheckable(True)
        btn.setToolTip(_ENTRANCE_TIPS[name][0])
        tb.addWidget(btn)
        window.entrance_buttons[name] = btn
        btn.clicked.connect(lambda checked=False, n=name:
                            _switch_entrance(window, n))

    # 兼容：旧名字 [校准] 开关（测试与 calib.py 都按 window.calib_btn 找）
    # 文字与提示由 _highlight_entrance 统一换（选中时变「退出校准」）
    window.calib_btn = window.entrance_buttons["校准"]
    window.calib_btn.toggled.connect(lambda on: _on_mode(window, on))

    tb.addSeparator()

    # 面板开关：[文件][参数][日志] 三个勾选按钮，收起/展开对应坞。
    # 全收起来 = 中央只剩绘图区，看图视野最大。双向同步：按钮点
    # 击 → 坞显隐；坞被标题栏 × 关掉 → 按钮自动弹起（visibilityChanged
    # 信号），下次点按钮还能再展开
    window.panel_toggles = {}   # 登记按钮（测试用）
    # 用户意图（见 _resync_docks）：只在**真点击**时记录；参数的初始状态
    # 是收起（"开界面时右边参数栏是隐藏的"），所以它的默认意图是 False
    window._dock_intent = {"文件": True, "参数": False, "日志": True}
    for name, dock in (("文件", window.file_dock),
                       ("参数", window.param_dock),
                       ("日志", window.log_dock)):
        btn = QPushButton(name)
        btn.setCheckable(True)
        btn.setChecked(window._dock_intent[name])
        tb.addWidget(btn)
        window.panel_toggles[name] = btn
        dock.visibilityChanged.connect(btn.setChecked)
        btn.toggled.connect(dock.setVisible)
        btn.clicked.connect(          # 只认真点击：setChecked 不触发 clicked
            lambda checked=False, n=name:
            window._dock_intent.__setitem__(n, checked))

    # 帮助：**挨着 [日志]，不再只是菜单栏里那一条**（用户 2026-10-07：
    # "把帮助放到日志旁边，不在外面了"）。点开一个小菜单（使用说明 /
    # 关于）。与菜单栏那两个 QAction **同一批对象**——两条入口，一份行为。
    # 菜单栏那条保留：macOS 的「关于」必须住在系统应用菜单里（AboutRole），
    # F1 快捷键也归它管，删了会破坏平台惯例。
    tb.addSeparator()
    # 用 QPushButton（不是 QToolButton）：工具栏里其余按钮都是 QPushButton，
    # 同一个外观（QToolButton 在 macOS 上是无边框的，混在一排里像少了壳）
    btn_help = QPushButton("帮助")
    btn_help.setObjectName("help_btn")
    btn_help.setToolTip("使用说明（F1）与关于本软件")
    help_menu = QMenu(btn_help)
    help_menu.addAction(window.menu_actions["manual"])
    help_menu.addAction(window.menu_actions["about"])
    btn_help.setMenu(help_menu)       # 单击即弹菜单（按钮带一个小三角）
    tb.addWidget(btn_help)
    window.help_btn = btn_help

    # 开局谁都不选（参数坞建好才动得了，所以放在这里）：不点亮任何入口
    # + 收起参数坞。用户 2026-09-25 定：开界面时上面什么都不选、右边
    # 参数栏是隐藏的——"选到谁才放谁的参数"。
    _clear_entrance(window)

def _clear_entrance(window: QMainWindow) -> None:
    """"什么都没选"状态：入口全不点亮 + 收起参数坞。

    两个调用点：开局（_build_toolbar 收尾）与"没选过入口就退出校准"
    （_on_mode(False)）。参数坞的显隐与 [参数] 开关双向同步
    （见 _build_toolbar），所以收坞 = 按钮自动弹起，不用额外接线。
    """
    window._last_entrance = None
    _highlight_entrance(window, None)
    # 收起时停在分析侧的默认页：用户手动点 [参数] 展开时看到的是分析页，
    # 而不是上一次停在的校准页（页号唯一来源就在入口切换这一处）
    window.param_stack.setCurrentIndex(window.PARAM_PAGES["1D"])
    window.param_dock.setVisible(False)
    # 收起是**用户意图**（退出入口）：记进意图表，_resync_docks 才不会
    # 在下一步（最小化恢复）把它顶回来（见 _switch_entrance 的同款记账）
    if hasattr(window, "_dock_intent"):
        window._dock_intent["参数"] = False

def _switch_entrance(window: QMainWindow, name: str) -> None:
    """切到某个入口：参数坞翻页 + 五个按钮高亮。

    [校准] 是入口 + 工作台开关：切进去 = 进校准（开校准面板，见
    _on_mode），切出去 = 退校准（翻回最近用过的分析入口）。其余四个
    是纯翻页——页底部的产出按钮才负责出图。

    点入口先让参数坞露出来：开局它是收起的（_clear_entrance），
    "选到谁才放谁的参数"；[校准] 的拉宽要量坞的宽度，所以必须先可见。

    再点已亮着的 [校准] = 退出（Qt 先弹起勾选，那一刻 toggled 已经把
    _on_mode(False) 跑完了）。这里要**认出来并就此打住**：否则 clicked
    又把勾按回去，退出 + 重进在同一个点击里跑完，看着像"点了没反应"，
    还会把选到一半的选点清空（2026-09-26 用户报"没有退出校准的按钮"，
    实为这个陷阱；面板的关闭即遗忘把选点也带走了）。
    """
    pages = getattr(window, "PARAM_PAGES", {})
    if name not in pages or name not in window.entrance_buttons:
        return
    # 点分析入口 = 离开 Mask 工作台（先退出，再翻页）
    if getattr(window, "_mask_active", False) and name != "校准":
        window.mask_btn.setChecked(False)
    if name == "校准" and not window.calib_btn.isChecked():
        return                      # 这一次点击是"退出"，已由 toggled 处理
    if name != "校准" and not window.entrance_buttons[name].isChecked():
        # 再点一次已亮着的分析入口 = 退出（用户 2026-10-01：按钮此刻正写着
        # 「退出某某」，按下就该退）。四个分析入口没有"关掉这个功能"一说，
        # 退出即**回到开局那个"什么都没选"**：入口全熄灭 + 收起参数坞
        # （[参数] 按钮随时能再展开）；产物/图一张都不动。
        _clear_entrance(window)
        _log(window, f"{_exit_text(name)}（收起参数面板；[参数] 按钮可以再展开）")
        return
    window.param_dock.setVisible(True)
    # 入口"露坞"也是**用户意图**（选到谁看谁的参数）：记进意图表——不记
    # 的话它一直是开局那个 False，_resync_docks 一跑（用户 2026-10-16：
    # 进全屏）就把坞收回去（当年那条件宽、全屏也算"状态变化"）
    window._dock_intent["参数"] = True
    if name != "校准":
        window._last_entrance = name
    if name == "校准":
        window.calib_btn.setChecked(True)    # 幂等；进入走 toggled
    else:
        if window.calib_btn.isChecked():
            window.calib_btn.setChecked(False)   # → toggled → _on_mode(False)
        window.param_stack.setCurrentIndex(pages[name])
    _highlight_entrance(window, name)

# 入口按钮的**两副面孔**（用户 2026-10-01）：没选中的写入口名，选中的
# 那个改写「退出某某」——文字自己就是"怎么回去"的说明，再点一次就退出
# （见 _switch_entrance 的退出分支）。5707a6f 那版 [校准] 就是这个手感，
# 2026-09-26 改成坞顶常驻的 [返回分析模式] 之后丢了；这次五个入口一起给。
# 表里两句话：没选中时的说明、选中时（"再点一下就退出"）的说明。
_ENTRANCE_TIPS = {
    "校准": ("进入校准工作台：标样数据定几何（束心 / 距离 / 倾斜角）",
             "退出校准工作台，回到分析模式"),
    "1D": ("1D 参数：积分范围 / 输出点数 / 曲线显示",
           "再点一次退出「1D」页（收起参数面板）"),
    "处理": ("处理参数：裁剪 / 背景扣除 / 平滑（从上到下）；[存成产物] 勾了=批量、没勾=当前这张",
           "再点一次退出「处理」页（收起参数面板）"),
    "对比": ("对比参数：归一化 / 配色 / 堆叠",
           "再点一次退出「对比」页（收起参数面板）"),
    "原图": ("本页画单文件视图：2D / 剖面 / 瀑布——都直接对原图，不经过 1D 产物",
           "再点一次退出「原图」页（收起参数面板）"),
}

def _exit_text(name: str) -> str:
    """退出态的入口文字：拉丁名两侧留空格（"退出 1D"），中文名不加（"退出校准"）。"""
    return f"退出 {name}" if name.isascii() else f"退出{name}"

def _highlight_entrance(window: QMainWindow, name: str) -> None:
    """入口高亮：只有当前那一个勾着，且它的按钮写「退出某某」。

    name=None = 谁都不勾（"什么都没选"的开局态，按钮文字复位成入口名）。
    文字与提示都在这一处换：进/退/换页、开局清空，全走这里，不会各写各的。
    """
    for key, btn in getattr(window, "entrance_buttons", {}).items():
        active = key == name
        btn.setChecked(active)
        btn.setText(_exit_text(key) if active else key)
        off_tip, on_tip = _ENTRANCE_TIPS.get(
            key, (key, f"再点一次{_exit_text(key)}"))
        btn.setToolTip(on_tip if active else off_tip)

def _on_mode(window: QMainWindow, calibrating: bool) -> None:
    """模式开关：勾选 = 校准工作台，弹起 = 分析工作台。

    进入：参数坞翻到校准页（页 0 的分析参数原样保留），勾选的第一个
    文件开校准面板；没勾文件只记日志提示（不崩）。退出：翻回分析页
    + 关校准面板（校准状态清零，关闭即遗忘）。

    2026-09-24 起入口是**五个页签式按钮**（[校准][1D][处理]
    [对比][原图]，见 _build_toolbar / _switch_entrance）。退出校准有
    三条路，都汇到这里的 setChecked(False)：再点一次已亮着的 [校准]
    （见 _switch_entrance 的早退）、点别的入口、按坞顶常驻的
    [返回分析模式]。按钮文字由 _highlight_entrance 统一换（选中时写
    「退出校准」——用户 2026-10-01 要的"两副面孔"，五个入口一致）。
    """
    if calibrating:
        if getattr(window, "_mask_active", False):
            window.mask_btn.setChecked(False)   # 校准与 Mask 互斥
        window.mode_label.setText("校准模式")
        _log(window, "进入校准模式")
        window.param_stack.setCurrentIndex(window.PARAM_PAGES["校准"])
        _enter_calib(window)
    else:
        window.mode_label.setText("分析模式")
        # 翻回最近用过的分析入口；一个都没选过（开局直接进校准、
        # 或点了坞顶的 [返回分析模式]）→ 回到"什么都没选"：
        # 入口不点亮、参数坞收起（用户 2026-09-25 定）
        last = getattr(window, "_last_entrance", None)
        # 页号总归要翻回分析侧的默认页（坞是收起的，看不见；但用户手动
        # 点 [参数] 展开时该看到分析页，而不是停留在校准页）
        window.param_stack.setCurrentIndex(
            window.PARAM_PAGES.get(last, window.PARAM_PAGES["1D"]))
        _exit_calib(window)     # 先还原坞宽（收起后量不到）
        if last is None:
            _clear_entrance(window)
        else:
            # 高亮跟着回来：再点一次已亮着的 [校准] 退出时，走的是这条路
            # （没有别的入口在替它高亮——以前靠 _switch_entrance 补，
            # 现在那条路在校准分支上直接返回了）
            _highlight_entrance(window, last)
        _log(window, "回到分析模式")

# ══ 保存：勾选已输出的图 → 选分辨率/格式 → 逐个选文件名存图 ══

def _confirm_close(window: QMainWindow, n_unsaved: int) -> str:
    """关窗时询问未保存的图怎么处理；返回 "save" / "discard" / "cancel"。

    窗口从未显示过（测试等非交互场景）不弹框，直接按"不保存"关，
    免得模态对话框把测试挂死；真实使用中窗口显示过，正常弹框。
    """
    if not window.isVisible():
        return "discard"
    ans = QMessageBox.question(
        window, "关闭 XRD Toolkit",
        f"有 {n_unsaved} 张图尚未保存，要保存后再关闭吗？",
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save)
    return {QMessageBox.Save: "save", QMessageBox.Discard: "discard"}.get(
        ans, "cancel")

# ══ 主窗口组装 ════════════════════════════════════════════
def create_window() -> QMainWindow:
    """组装主框架：文件坞 + 参数坞 + 日志坞 + 工具栏 + 中央面板区。"""
    window = _MainWindow()
    window.setWindowTitle("XRD Toolkit")
    window.resize(1200, 800)
    # 关窗即销毁 C++ 对象：窗口自己的 dict 里挂满了捕获自己的闭包
    # （点选/作图/保存等回调），Python 引用计数环永远归不了零；而
    # shiboken 的 C++→Python 绑定映射在 C++ 对象存活期间持有包装
    # 器，gc 视之为可达也收不掉（探针实证：裸窗口能回收、带闭包环
    # 的窗口不能）。close 时先销毁 C++ 对象 → 绑定映射解除 → 闭包
    # 环变成无根环，gc 即可收走。取消/保存失败走 event.ignore()，
    # 窗口留在程序里不受影响。
    window.setAttribute(Qt.WA_DeleteOnClose)
    # 拖文件进窗口任意位置 = 加进文件列表；拖文件夹 = 扫描加入
    # （拖放事件冒泡到顶层窗口）
    window.drop_callback = lambda paths: add_files(window, paths)
    window.drop_folder = lambda folder: _scan_folder(window, folder)

    window._status_timer = None   # _log 里的状态栏恢复计时器（懒创建）
    window.log = lambda text: _log(window, text)
    window.add_files = lambda paths, **kw: add_files(window, paths, **kw)
    # 布局/同步旗标（各自用途见 _on_canvas_resized / _tile_panels /
    # _draw_1d / _on_limits_changed 的注释）
    window._layouting = False          # 程序自己在平铺/布局（不算用户拖动）
    window._setting_limits = False     # 程序自己在画图设范围（不算用户改动）
    window._hover_panel_key = None     # 鼠标最后停留过的面板（Home 键认它）

    # Home 键 = 复位"你正在看的那张图"（回到生成它时的视图，见
    # plot_panels._home_key_reset）。三条设计要点：
    #   ① 绑在**窗口**上、作用域 WindowShortcut——不然鼠标停在文件列表
    #      上时按 Home 会被 Qt 的树控件吃掉（树的 Home = 跳到第一行），
    #      那正是用户抱怨的"乱跳"；
    #   ② 焦点在输入框里时 Home 到不了这里：QLineEdit/QAbstractSpinBox
    #      会把 Home 认成"光标到行首"，所以在数值框里打字不会被打断；
    #   ③ 只动视图、不动任何设置（见 _SlimToolbar._reset_view）。
    home_sc = QShortcut(QKeySequence(Qt.Key_Home), window)
    home_sc.setContext(Qt.WindowShortcut)
    home_sc.activated.connect(lambda: _home_key_reset(window))
    window.home_shortcut = home_sc   # 挂住引用，别被回收

    # 后台任务簿：进行中的积分任务挂在这里防垃圾回收（结束回调里
    # 移除）；关窗口时逐一 discard（等待后台函数返回，防线程悬空）
    window._tasks = []
    # 每面板的最新任务：同面板连点两次时，先开的晚到会被丢弃
    window._latest_task = {}
    # 自动对比度的图像缓存（路径 → 图像数组，上限 3 张）：同一文件
    # 反复点焦点不重复解码大 tif
    window._image_cache = {}
    # 焦点面板：参数面板"编辑对象"指向的图面板（点图/计算完成时更新）
    window.focus_panel = None
    # Mask 工作台内容与中央子窗口（2026-10-30 起不再是独立窗口）：
    # 子窗口关闭只隐藏，内容不销毁，下次点 [Mask] 原样继续。Mask 本身是
    # Mask 后的 2D 产物（services/masked2d），不是窗口级开关。
    window.mask_editor = None
    window.mask_dock = None

    # 背景扣除的窗级数据（不是快照参数，所以不随面板走、免疫面板
    # 弹出/收回——panels.py 的 _PANEL_ATTRS 白名单只搬少量 dock 属性）：
    # bg_anchors = {文件路径: [(2θ, 强度), ...]}，锚点按文件各记各的；
    # bg_blank = 空扫曲线 {"path", "tth", "intensity"}，整批实验共用一条
    window.bg_anchors = {}
    window.bg_blank = None
    # 裁剪清单（窗口级，不是快照参数：快照只认控件值，列表放不进控件）——
    # 编辑它的是「处理」页的 [添加]/[清空]，实时重画时再拷进各面板快照
    window.cut_list = []
    # 锚点计数标签的刷新入口（plot_views 里点选锚点后回调，避免
    # plot_views 反向 import app）
    window._bg_count_refresh = _update_bg_count
    # 背景扣除专用行的显隐同步入口（panel_state 回放面板快照后回调，
    # 同样避免反向 import）
    window._bg_rows_sync = _sync_bg_rows
    # 「配方」小节的显示刷新入口：_refresh_proc 记完"本图配方"之后回调
    # （顺序见 _sync_recipe_rows 的说明——用户报"套用无效"的根因之一）
    window._recipe_rows_sync = lambda: _sync_recipe_rows(window)
    # 文件栏"正在打开"记号的刷新入口（面板开/关时回调，用户 2026-09-28 第 5 条）
    window.mark_open_rows = lambda: refresh_open_marks(window)
    # 焦点切换入口：当前编辑对象那块面板的标题栏深一档（plot_panels
    # 的 _sync_bar_active；panel_state 只负责回调，不反向 import）
    window._on_focus_changed = (
        lambda prev, cur: _sync_bar_active(window, prev, cur))

    _build_center(window)
    # 点任何面板窗口内任何位置都选中该面板（应用级过滤器，原因见
    # _PanelClickTracker）。每个窗口装一个；窗口销毁时过滤器随父
    # 对象销毁，Qt 自动把它从应用事件分发里摘掉
    QApplication.instance().installEventFilter(_PanelClickTracker(window))
    window.file_dock = _build_file_dock(window)
    # 产物分组刷新入口（文件栏里的"阶段文件夹"）：批量处理 / 1D 批量
    # 算完 / 清空缓存之后要重建。挂成窗口回调而不是让 plot_views 反向
    # import 文件坞（同 _bg_count_refresh 的老规矩）
    window.refresh_groups = lambda: refresh_product_groups(window)
    # 单张 1D 算完的即时刷新（plot_views._on_integration_done）：产物照常
    # 列出，但**不**触发"新产物 = 勾选清零"——那条规矩是给"一批活干完"
    # （批量出图/批量处理）定的；单张看一眼就把勾选清掉，用户连"再点一次
    # [1D] 看缓存"都做不了（2026-10-05 实测撞上，见 plot_views 那处注释）
    window.refresh_groups_quiet = (
        lambda: refresh_product_groups(window, quiet=True))
    # 文件栏按需开图（双击条目 / 右键 [打开 1D 图] / [打开整组]）：
    # 勾选超过上限的那批只算不画，看哪张点哪张。挂回调而不是让
    # file_dock 反向 import plot_views（同 refresh_groups 的老规矩）
    window.open_view_source = (
        lambda source, name="1D": _open_source_view(window, name, source))
    window.open_view_group = (
        lambda sources, name="1D": _open_source_group(window, name, sources))
    window.param_dock = _build_param_dock(window)
    window.log_dock = _build_log_dock(window)
    # 日志"两边缩"（用户 2026-10-07）：两个底角归左右区 → 文件栏/参数栏
    # 各贯通到底拿到全高，日志只占底部中间那一段（"夹在他俩中间"）；
    # 只开日志、或只开一边时，角落归属自动失效，日志随之铺满/半铺。
    # 默认（不设）两个底角归底部区，日志横贯整宽——就是改之前的样子。
    window.setCorner(Qt.BottomLeftCorner, Qt.LeftDockWidgetArea)
    window.setCorner(Qt.BottomRightCorner, Qt.RightDockWidgetArea)
    _build_status(window)
    _build_menu(window)
    _build_toolbar(window)
    _set_app_icon(window)

    # 点选文件 → 只登记当前文件（此时各坞已就绪，可安全接信号）
    window.file_list.currentItemChanged.connect(
        lambda cur, prev: _on_file_selected(window, cur, prev))

    # 关窗口：有未保存的图先询问（保存 / 不保存 / 取消），再等后台
    # 任务收尾——直接销毁运行中的线程 Qt 会 abort
    def on_close(event):
        window._closing = True
        unsaved = [d for d in window.plot_docks.values()
                   if getattr(_content(d), "figure", None) is not None
                   and not getattr(d, "figure_saved", False)]
        if unsaved:
            choice = _confirm_close(window, len(unsaved))
            if choice == "cancel":
                window._closing = False
                event.ignore()   # 取消 → 留在程序里
                return
            if choice == "save" and not _save_figures(window):
                window._closing = False
                event.ignore()   # 保存流程被取消 → 留在程序里
                return
        for task in window._tasks:
            task.discard()
        # 弹出的顶层窗口不随主窗口关，逐一关掉（close → 遗忘登记；
        # 遍历拷贝：close 会把面板从 plot_docks 里摘掉）
        for d in list(window.plot_docks.values()):
            if isinstance(d, _FloatedWindow):
                d.close()
        event.accept()

    window.closeEvent = on_close

    # 初始尺寸：文件列/参数列默认收到最窄（各自内容的最小宽度），
    # 需要时用户自己拖宽；日志高 140（都可拖动）
    for d in (window.file_dock, window.param_dock):
        window.resizeDocks([d], [d.minimumSizeHint().width()], Qt.Horizontal)
    window.resizeDocks([window.log_dock], [140], Qt.Vertical)

    # 启动即应用默认配置条目：参数坞初值来自注册表（如初始距离
    # 1595.80 mm），而不是写死的占位默认值；[删除] 按钮随选中条目
    # 同步置灰（默认条目是内置的 → 初始不可删）
    _apply_config(window, window.config_combo.currentIndex())
    _sync_del_config_btn(window)
    # 出图按钮上的"勾选 N 个"与原图页那句构成说明：开局填一次（此时一条
    # 都没勾），之后每次勾选变化由 _sync_select_label 刷新。放在最后：
    # 按钮与那句说明都归参数坞，要等它建好
    _sync_select_label(window)

    # 卡死现场记录（用户 2026-10-01 报"出图按钮卡死"、本地复现不出来）：
    # 界面线程停摆超过阈值就把所有线程的栈写进 outputs/hang-*.txt。只记录、
    # 不干预——现场越干净越好（见 watchdog 模块说明）
    watchdog.start(window)

    window.log("程序已就绪")
    return window

def _install_chinese_translations(app: QApplication) -> None:
    """装入 Qt 内置中文翻译（docs/UI_COPY.zh-CN.md §4.5）。

    标准按钮（确定/取消/是/否）与标准对话框由 Qt 自己画，不装翻译就是
    英文。qtbase_zh_CN.qm 随 PySide6 发行；找不到就静默跳过（翻译文件
    缺了不该让程序起不来）。translator 必须挂在 app 上活到进程结束。
    """
    translator = QTranslator(app)
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load("qtbase_zh_CN", path):
        app.installTranslator(translator)

def main() -> int:
    """程序入口：创建 QApplication → 建窗口 → 进入事件循环。

    事件循环（app.exec()）是 GUI 程序的"心跳"：程序停在这里，
    不断接收鼠标/键盘/重绘事件并分发出去，直到窗口被关闭。
    """
    app = QApplication(sys.argv)
    _install_chinese_translations(app)
    window = create_window()
    window.show()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
