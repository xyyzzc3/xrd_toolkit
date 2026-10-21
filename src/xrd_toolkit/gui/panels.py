"""面板容器生命周期：MDI 子窗口 / 弹出窗口、关闭即遗忘、平铺缩放与抓手。

模块图见 panel_state 模块 docstring（本模块处于 plot_views 之下）。

内容：
  - 容器：_PlotSubWindow（MDI 子窗口）/ _FloatedWindow（弹出顶层
    窗口），× 关闭都走 _close_panel（登记表移除 + 焦点移交 + 销毁，
    关闭即遗忘）；
  - 中央绘图区：_build_center（QMdiArea + 底部模式条 + 横排/竖排
    + 总缩放控件）；
  - 抓手：_PanelGripFilter（四边 8px 抓取带 + 四角 24px 抓取区 +
    右下角可见把手；应用级覆盖光标——macOS 部件光标会被带过期
    坐标的合成事件打回箭头）/ _install_resize_grip；
  - 比例记忆：_PanelResizeFilter / _on_canvas_resized（画布尺寸
    一变就记"用户拖过"：每拖一次 = 记住当前画布比例；程序自己的
    布局变化不算）/ _panel_extra（面板壳尺寸）；
  - 摆位缩放：_tile_panels（按类型分层摆位置，绝不缩放）/
    _arrange / _apply_area_zoom（总缩放 50%–200%）/ _AreaZoomFilter
    （Ctrl+滚轮）/ _settle（消化排队中的 Qt 布局事件）；
  - 弹出收回：_toggle_pop_out（状态经 _PANEL_ATTRS 白名单搬家）。
"""
from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMdiArea,
    QMdiSubWindow, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from xrd_toolkit.gui.panel_state import (
    FOCUS_EMPTY_TEXT, _content, _log, _set_focus)
# 面板小部件（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.panel_widgets import (
    _COORD_MIN_W, _COORD_SLOT_W, _CoordReadout, _ElideLabel, _FocusElideLabel)
# 面板容器（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.panel_container import (
    _PANEL_ATTRS, _FloatedWindow, _PanelBarFilter, _PlotSubWindow,
    _apply_panel_chrome, _close_all_panels, _close_panel, _copy_panel_attrs,
    _sync_bar_tooltip)
# 抓手/尺寸记忆（2026-10-21 从本文件原样搬出；再导出）
from xrd_toolkit.gui.panel_grip import (
    _PanelGripFilter, _PanelResizeFilter, _dock_geo, _install_resize_grip,
    _on_canvas_resized, _panel_extra)

def _build_center(window: QMainWindow) -> None:
    """中央绘图区 = QMdiArea：每张图一个子窗口，各拖各的互不牵连。

    旧方案用停靠分栏（内层 QMainWindow + QDockWidget）：图与图
    共用分栏把手，拽一张必然牵动邻居——这就是"图总是连在一起"
    的根源，参数上无解，换成子窗口才治本。底部细条放模式提示
    （左）+ 横排/竖排按钮（右，替代旧内层状态栏）。
    """
    mdi = QMdiArea()
    mdi.setObjectName("plot_area")
    # 滚动条策略必须显式设（默认策略下溢出区域的滚动条不出现，
    # 探针验证）；平铺只摆位置不缩放，放不下就靠滚动条看
    mdi.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    mdi.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    mdi.setStyleSheet("QMdiArea { background-color: #c8c8c8; }")
    window.mdi = mdi
    # Ctrl+滚轮 = 总缩放（Excel 习惯）：过滤器装在视口上管灰底/
    # 标题栏上的 Ctrl+滚轮，普通滚轮穿透给 QMdiArea 自己滚动；
    # 光标在图上方的那条路在 _wheel_zoom 的 mpl 层拦截
    mdi.viewport().installEventFilter(_AreaZoomFilter(window))
    window.plot_docks = {}
    # 当前面板布局方向（横排/竖排按钮设定）："row" = 一行 /
    # "column" = 一列
    window._layout_orient = "row"
    # 面板代数：每次（重新）开面板 +1；后台任务回调核对代数——
    # 面板关过重开后，旧代迟到结果不会串进新图（对比面板的
    # compare_gen 归零漏洞由它补上）
    window._panel_epoch = {}
    # 总缩放（50%–200%，每格 10%）：Ctrl+滚轮 / 底部 − + 按钮驱动，
    # 绘图区所有子窗口围绕视口中心整体同比缩放（像 Excel 缩放
    # 工作表）；弹出去的独立窗口不参与
    window._area_zoom = 1.0

    window.mode_label = QLabel("分析模式")   # 默认：分析工作台
    window.mode_label.setAlignment(Qt.AlignCenter)

    arrange_box = QWidget()
    row = QHBoxLayout(arrange_box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)
    window.arrange_buttons = {}   # 登记按钮（测试与后续接线用）
    for name in ("横排", "竖排"):
        btn = QPushButton(name)
        btn.setFlat(True)      # 扁平样式，像 Excel 角上的小按钮
        row.addWidget(btn)
        window.arrange_buttons[name] = btn
        btn.clicked.connect(
            lambda checked=False, n=name: _arrange(window, n))
    # [全关]：一键关掉所有图面板（用户 2026-09-26："打开的子窗口全部关闭"）
    btn_close_all = QPushButton("全部关闭")
    btn_close_all.setObjectName("close_all_panels")
    btn_close_all.setFlat(True)
    btn_close_all.setToolTip("关闭所有图面板与弹出的独立窗口。"
                             "未保存的图片不会另行提示；"
                             "关闭后日志里会列出没存过盘的图")
    btn_close_all.clicked.connect(lambda: _close_all_panels(window))
    window.close_all_btn = btn_close_all
    # 注意：**不再加进 arrange_box**——2026-10-07 起钉在横带最右端
    # （见下面 srow 的排布），离 [横排]/[竖排] 拉开距离防手滑

    # 总缩放控件（Excel 式 − 100% +）：放在横排/竖排右边
    zoom_box = QWidget()
    zrow = QHBoxLayout(zoom_box)
    zrow.setContentsMargins(0, 0, 0, 0)
    zrow.setSpacing(2)
    window.zoom_label = QLabel("100%")
    window.zoom_label.setMinimumWidth(40)
    window.zoom_label.setAlignment(Qt.AlignCenter)
    window.zoom_buttons = {}   # 登记按钮（测试用）
    for name in ("−", "+"):
        btn = QPushButton(name)
        btn.setFlat(True)
        window.zoom_buttons[name] = btn
        factor = 1.0 / 1.1 if name == "−" else 1.1
        btn.clicked.connect(
            lambda checked=False, f=factor:
            _apply_area_zoom(window, window._area_zoom * f))
    zrow.addWidget(window.zoom_buttons["−"])
    zrow.addWidget(window.zoom_label)
    zrow.addWidget(window.zoom_buttons["+"])

    # 实时坐标读数（2026-10-07 从状态行搬来）：看图时眼睛往下扫一点就到
    # （原来在窗口最底部的状态行里，太远）。固定宽度 + 中部省略：悬停读数
    # 长短变化不会把右边的按钮挤着左右跳（完整文字在悬停提示里）。
    # 等宽字体 + 中文回退：Windows 的 Consolas 没有中文字形，不写回退时
    # "2θ 3.335°"里的中文会是方框（2026-10-04 试用反馈）
    window.coord_label = _CoordReadout()
    window.coord_label.setObjectName("coord_label")
    window.coord_label.setFrameShape(QFrame.Shape.StyledPanel)
    window.coord_label.setStyleSheet(
        "font-family: Menlo, Consolas, 'Microsoft YaHei', "
        "'PingFang SC', monospace; padding: 1px 4px;")

    strip = QWidget()
    srow = QHBoxLayout(strip)
    srow.setContentsMargins(4, 2, 4, 2)
    srow.setSpacing(4)
    # 从左到右（用户 2026-10-07 定的排法）：模式提示 | 坐标读数（吃剩余宽）|
    # 横排/竖排 | 缩放 −100%+ | 最右：全部关闭
    # （2026-10-10：原来的"空白 + 固定 230 读数格"改成读数格占 stretch
    # ——读数多长都能显示全，见 _CoordReadout 的说明）
    srow.addWidget(window.mode_label)
    srow.addWidget(window.coord_label, 1)
    srow.addWidget(arrange_box)
    srow.addWidget(zoom_box)
    srow.addSpacing(8)   # 与缩放拉开一点：全关是"破坏性"按钮，别贴着
    srow.addWidget(btn_close_all)

    center = QWidget()
    lay = QVBoxLayout(center)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(mdi, 1)
    lay.addWidget(strip)
    window.setCentralWidget(center)

def _apply_area_zoom(window: QMainWindow, new: float) -> None:
    """总缩放：绘图区所有子窗口围绕视口中心整体同比缩放（50%–200%）。

    像 Excel 缩放工作表：所有图一起变大变小、相对位置不变，每张
    图自己的比例记忆（_canvas_pref）是缩放无关值（见
    _on_canvas_resized），总缩放不碰它。只动 MDI 里的子窗口——
    弹出去的独立窗口各管各的（与平铺同理）。先滚动归零再动手：
    QMdiArea 在滚动状态下会把滚动偏移混进子窗口 move 坐标
    （平铺踩过的同一个坑，见 _tile_panels）。面板 _settling 举
    旗：程序性尺寸变化不记成"用户拖过"。
    """
    new = min(2.0, max(0.5, new))
    if abs(new - window._area_zoom) < 1e-9:
        return
    mdi = window.mdi
    mdi.horizontalScrollBar().setValue(0)
    mdi.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    subs = [d for d in window.plot_docks.values()
            if isinstance(d, QMdiSubWindow)]
    k = new / window._area_zoom
    cx = mdi.viewport().width() / 2   # 滚动已归零：锚点 = 视口中心
    cy = mdi.viewport().height() / 2
    for d in subs:
        d._settling = True
        d.move(round(cx + (d.x() - cx) * k), round(cy + (d.y() - cy) * k))
        d.resize(max(60, round(d.width() * k)),
                 max(40, round(d.height() * k)))
    window._area_zoom = new
    _settle(window)
    for d in subs:
        canvas = getattr(_content(d), "canvas", None)
        if canvas is not None:
            d._last_canvas = (canvas.width(), canvas.height())
        d._settling = False
    window.zoom_label.setText(f"{round(new * 100)}%")
    _log(window, f"总缩放 {round(new * 100)}%")

class _AreaZoomFilter(QObject):
    """装在 QMdiArea 视口上的事件过滤器：Ctrl+滚轮 = 总缩放。

    普通滚轮不管（穿透给 QMdiArea 自己滚动看图）；Ctrl+滚轮吃下
    、每格 10%（Excel 的 Ctrl+滚轮缩放工作表习惯）。管灰底/标题
    栏上的 Ctrl+滚轮；光标在图上方的那条路在 _wheel_zoom 的
    mpl 层拦截（画布把滚轮事件吃进 mpl 事件，到不了这里）。
    """

    def __init__(self, window: QMainWindow):
        super().__init__(window.mdi.viewport())   # 以视口为父：不被 GC
        self._window = window

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.Type.Wheel
                and event.modifiers() & Qt.ControlModifier):
            delta = event.angleDelta().y()
            if delta == 0:
                return True   # Ctrl+横向滚轮：吃掉，别误触缩放
            factor = 1.1 if delta > 0 else 1.0 / 1.1
            _apply_area_zoom(self._window,
                             self._window._area_zoom * factor)
            return True
        return False

def _tile_panels(window: QMainWindow, subs, orient: str) -> None:
    """平铺 = 纯摆位置：按类型分层，图保持各自大小，绝不缩放。

    缩放数学题整个退役：旧实现为了"贴满视口"要算统一系数、量壳、
    防压扁——用户拍板"宁可滚动也不压扁"后这些全部不需要（与用户
    讨论定稿：默认图永远 500×300、拖过的图永远保持拖成比例，
    平铺根本不碰尺寸，"拖过 = 永远按拖成比例"从此不需要任何
    保护代码，_layouting 旗标也随之退役）。

    摆图前先把滚动归零：QMdiArea 在滚动状态下会把滚动偏移混进
    子窗口 move 坐标（探针实证：横滚 628 时再排列，桌面多出
    628px 灰区、图整体向右下漂移，每排一次漂一次）——归零后
    坐标精确落在 (4,4) 起步，排完视图自然从左上角开始展示。
    平铺不碰总缩放（各管各的）。

    分组：按面板键第一段（2D/剖面/1D/瀑布/对比各算一类），类型
    顺序 = 开图先后（plot_docks 的键序）。横排 = 每类一行（顶
    对齐，行内从左往右）；竖排 = 每类一列（左对齐，列内从上往
    下）。行比视口宽 / 层总高比视口高 → QMdiArea 滚动条兜底
    （两向 AsNeeded 策略本来就开着）。
    """
    if not subs:
        return
    window.mdi.horizontalScrollBar().setValue(0)
    window.mdi.verticalScrollBar().setValue(0)
    QApplication.processEvents()
    groups = {}
    for d in subs:
        groups.setdefault(d.panel_key.split("|", 1)[0], []).append(d)
    if orient == "column":
        # 竖排：每类一列，列内从上往下
        x = 4
        for row in groups.values():
            col_w = max(d.width() for d in row)
            y = 4
            for d in row:
                d.move(x, y)
                y += d.height() + 8
            x += col_w + 8
    else:
        # 横排：每类一行，行内从左往右
        y = 4
        for row in groups.values():
            x = 4
            row_h = max(d.height() for d in row)
            for d in row:
                d.move(x, y)
                x += d.width() + 8
            y += row_h + 8
    _log(window, f"已{'竖排' if orient == 'column' else '横排'}"
                 f" {len(subs)} 个面板（按类型分组排布，保持各自大小）")

def _settle(window: QMainWindow) -> None:
    """消化排队中的 Qt 布局事件（_layouting 举着时处理函数会跳过）。"""
    for _ in range(5):
        QApplication.processEvents()

def _arrange(window: QMainWindow, mode: str) -> None:
    """一键重排主窗口内的面板：横排 = 每类一行 / 竖排 = 每类一列。

    纯摆位置、绝不缩放（默认图保持 500×300、拖过的保持拖成比例，
    放不下靠滚动条看——"宁可滚动也不压扁"）；只排主窗口内的子窗
    口，弹出去的独立窗口不碰。每次点击都重新确立布局（之前怎么
    摆的都归位），方向记在 _layout_orient 上。
    """
    subs = [d for d in window.plot_docks.values()
            if isinstance(d, QMdiSubWindow)]
    if not subs:
        _log(window, "没有打开的面板")
        return
    window._layout_orient = "column" if mode == "竖排" else "row"
    _tile_panels(window, subs, window._layout_orient)

def _toggle_pop_out(window: QMainWindow, key: str) -> None:
    """[弹出]/[收回]：面板内容搬到独立 OS 窗口，或搬回 MDI 子窗口。

    弹出（探针验证顺序）：先建浮动窗口、把内容改挂过去（addWidget
    自带改挂），再删旧子窗口——顺序反了内容会被连带销毁。状态经
    白名单 _copy_panel_attrs 搬家（vars() 整体拷会砸坏 PySide6
    信号）。壳尺寸（标题栏 + 边框）在弹出时量好记下；收回时按
    "内容尺寸 + 壳"×（当前总缩放 / 弹出时总缩放）恢复——内容的
    画布在弹出时已带当时的缩放，直接乘当前缩放会双重缩（探针
    实证：80% 弹出 100% 收回会落位 406 而不是 508）。
    收回：浮动壳用 deleteLater 而不是 close()——close() 会触发
    _close_panel 把面板登记抹掉；子窗口回到主窗口左上角。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return
    content = _content(dock)
    btn = getattr(content, "popout_btn", None)
    if isinstance(dock, QMdiSubWindow):
        # ── 弹出：MDI 子窗口 → 顶层窗口 ──
        dock._settling = True
        cw, ch = content.width(), content.height()
        floated = _FloatedWindow(window, key)
        floated.content = content      # 先挂内容：标题同步（_sync_bar_tooltip）
        floated.setWindowTitle(dock.windowTitle())   # 才找得到自绘栏

        box = QVBoxLayout(floated)
        box.setContentsMargins(0, 0, 0, 0)
        box.addWidget(content)   # 先改挂内容，再删旧子窗口
        floated._shell = (dock.width() - cw, dock.height() - ch)
        floated._pop_zoom = window._area_zoom   # 收回时折算用（见 docstring）
        floated._object_name = dock.objectName()
        _copy_panel_attrs(dock, floated)
        window.plot_docks[key] = floated
        if dock in window.mdi.subWindowList():
            window.mdi.removeSubWindow(dock)
        dock.deleteLater()
        floated.resize(cw, ch)
        floated.show()
        _settle(window)
        canvas = getattr(content, "canvas", None)
        if canvas is not None:
            floated._last_canvas = (canvas.width(), canvas.height())
        floated._settling = False
        if btn is not None:
            btn.setText("收回")
        _log(window, f"已弹出面板：{floated.windowTitle()}")
    else:
        # ── 收回：顶层窗口 → MDI 子窗口 ──
        floated = dock
        floated._settling = True
        cw, ch = content.width(), content.height()
        ew, eh = getattr(floated, "_shell", (0, 0))
        sub = _PlotSubWindow(window, key)
        sub.setObjectName(getattr(floated, "_object_name", "plot_1D"))
        window.mdi.addSubWindow(sub)
        sub.setWidget(content)   # 先改挂内容，再删旧壳（同弹出）
        sub.setWindowTitle(floated.windowTitle())
        _apply_panel_chrome(sub, content)   # 收回：壳换回自绘标题栏
        _copy_panel_attrs(floated, sub)
        window.plot_docks[key] = sub
        floated.deleteLater()   # 不用 close()：close 会触发 _close_panel 抹掉登记
        z = window._area_zoom
        k = z / getattr(floated, "_pop_zoom", 1.0)   # 内容带弹出时缩放，折算回当前
        sub.resize(max(round((cw + ew) * k), 60),   # 按当前总缩放落位：
                   max(round((ch + eh) * k), 40))   # 和周围的图大小一致
        sub.move(round(16 * z), round(16 * z))
        sub.show()
        _settle(window)
        canvas = getattr(content, "canvas", None)
        if canvas is not None:
            sub._last_canvas = (canvas.width(), canvas.height())
        sub._settling = False
        if btn is not None:
            btn.setText("弹出")
        _log(window, f"已收回面板：{sub.windowTitle()}")
