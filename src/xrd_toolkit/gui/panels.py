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
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMdiArea,
    QMdiSubWindow, QPushButton, QVBoxLayout, QWidget)

from xrd_toolkit.gui.panel_state import _content, _log, _set_focus


# 面板状态属性的白名单：弹出/收回时整体搬家的"行李清单"。
# 不能用 vars() 整体拷：PySide6 包装对象 vars() 里混着信号实例
# （windowStateChanged/destroyed…），整体拷会把新容器的信号盖成
# 旧容器的绑定信号（探针验证过）。
_PANEL_ATTRS = (
    "panel_file", "panel_item", "panel_display", "figure_saved",
    "params_snapshot", "compare_files", "compare_gen", "compare_pending",
    "compare_data", "_dragged", "_canvas_pref", "hover_marker",
    "_pan_start", "_pan_limits", "last_tth", "last_intensity",
    "_last_canvas", "_settling", "panel_key",
    # Customize 对话框保护记账（重画不覆盖用户改动，见 _draw_1d）
    "_title_ours", "_title_display", "_xlabel_ours", "_ylabel_ours",
    "_yscale_ours", "_yscale_param",
)


def _copy_panel_attrs(src, dst) -> None:
    """按白名单把面板状态从旧容器搬到新容器（弹出/收回用）。"""
    for name in _PANEL_ATTRS:
        if hasattr(src, name):
            setattr(dst, name, getattr(src, name))


# ══ 中央：图面板区（QMdiArea 子窗口，互不牵连，可弹出）══════
class _PlotSubWindow(QMdiSubWindow):
    """绘图子窗口：× 关闭 = 面板从 plot_docks 移除（关闭即遗忘）。

    Qt 默认行为是"关闭 = 隐藏"（还留在 subWindowList 里），与
    "关闭即遗忘"的规则不符 → 重写 closeEvent 走统一关闭入口
    _close_panel（从登记表移除 + 焦点移交 + 销毁容器）。
    """

    def __init__(self, window: QMainWindow, key: str):
        super().__init__()
        self._window = window
        self.panel_key = key
        # 点窗口任何地方都选中该面板：由 _PanelClickTracker（应用级
        # 过滤器，见 create_window）统一处理——QWidget 的父过滤器
        # 收不到子部件事件，容器级过滤器盖不住内容区

    def closeEvent(self, event):
        _close_panel(self._window, self.panel_key)
        super().closeEvent(event)


class _FloatedWindow(QWidget):
    """弹出的独立窗口：面板内容整个搬进来，× 关闭同样"关闭即遗忘"。

    无父 = 顶层 OS 窗口，不随主窗口移动/关闭。收回主窗口时不走
    close()（那会触发 _close_panel 把面板登记抹掉），由调用方直接
    deleteLater() 销毁壳。
    """

    def __init__(self, window: QMainWindow, key: str):
        super().__init__()   # 无父 = 顶层独立窗口
        self._window = window
        self.panel_key = key
        self.content = None   # 面板内容（_content(dock) 从这里取）
        self.setAttribute(Qt.WA_DeleteOnClose)
        # 点窗口任何地方都选中该面板：同 _PlotSubWindow，
        # 由 _PanelClickTracker 统一处理

    def closeEvent(self, event):
        _close_panel(self._window, self.panel_key)
        super().closeEvent(event)


def _close_panel(window: QMainWindow, key: str) -> None:
    """统一关闭面板：从登记表移除（关闭即遗忘）+ 焦点移交 + 销毁容器。

    幂等：面板已不在登记表里（如收回主窗口后旧壳被删除）直接返回。
    子窗口先从 MDI 摘下再 deleteLater（探针验证 closeEvent 里这组
    操作安全）；弹出窗口靠 WA_DeleteOnClose 自行销毁。焦点面板被
    关 → 编辑对象移给下一张还开着的图；一张不剩则清空。
    """
    dock = window.plot_docks.pop(key, None)
    if dock is None:
        return
    # 覆盖光标可能还挂在抓手过滤器名下（关面板时鼠标可能正停在
    # 抓取区上，来不及收 Leave）：主动撤销，别把方向光标留下
    grip_filter = getattr(_content(dock), "_grip_filter", None)
    if grip_filter is not None:
        grip_filter._release_cursor()
    if isinstance(dock, QMdiSubWindow):
        if dock in window.mdi.subWindowList():
            window.mdi.removeSubWindow(dock)
        dock.deleteLater()
    _log(window, f"已关闭面板：{dock.windowTitle()}")
    if window.focus_panel == key:
        window.focus_panel = None   # 先清空，绕过 _set_focus 的同键早退
        for next_key, next_dock in window.plot_docks.items():
            _set_focus(window, next_key, next_dock.windowTitle())
            break
        if window.focus_panel is None:
            window.focus_label.setText("编辑对象：未选中图面板")


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

    strip = QWidget()
    srow = QHBoxLayout(strip)
    srow.setContentsMargins(4, 2, 4, 2)
    srow.setSpacing(4)
    srow.addWidget(window.mode_label)
    srow.addStretch(1)
    srow.addWidget(arrange_box)
    srow.addWidget(zoom_box)

    center = QWidget()
    lay = QVBoxLayout(center)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(mdi, 1)
    lay.addWidget(strip)
    window.setCentralWidget(center)


class _PanelGripFilter(QObject):
    """四边抓取带 + 四角抓取区（右下角有可见把手 ▙）的事件过滤器。

    macOS 原生样式画的 MDI 子窗口边框几乎不可见（用户实测：没有
    拉伸光标、抓不到边）→ 不靠边框了，自己给图装"抓手"：内容四
    边各留 8px 抓取带、四角各留 24px 抓取区，悬停换方向光标，按住
    左键拖 = 直接改容器（子窗口/弹出窗口）几何。装到事件落点控件
    上（画布/占位内容/把手，见 _install_resize_grip 的原因说明）；
    不消费普通区域的鼠标事件——平移/悬停取点照旧。拖完画布尺寸
    变化照常走 _on_canvas_resized 记"拖过"。位置判定统一换算到
    内容坐标：把手/画布的事件都按同一套几何算。

    光标用 QApplication.setOverrideCursor（全局覆盖光标）而不是
    各部件 setCursor：每部件光标在 macOS 上不可靠——真机探针抓
    到"设上后被带过期坐标的合成事件打回箭头"（窗口激活/布局变
    动时系统会补发幽灵鼠标事件，坐标还是旧位置，判出来不在抓
    取区就把光标改回去了；旧版怪罪 QToolBar 丢光标其实是同一个
    现象）。覆盖光标 = 应用级、设上立刻生效、不走部件光标矩形那
    套机制；同一时刻最多挂一个（换区先弹后挂），鼠标离开抓手落
    点控件（Leave）或面板关闭时主动撤销。带过期坐标的事件一律
    忽略：不动光标状态。
    """

    CORNER, EDGE = 24, 8
    _CURSORS = {
        "left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor,
        "top": Qt.SizeVerCursor, "bottom": Qt.SizeVerCursor,
        "topleft": Qt.SizeFDiagCursor, "bottomright": Qt.SizeFDiagCursor,
        "topright": Qt.SizeBDiagCursor, "bottomleft": Qt.SizeBDiagCursor,
    }

    def __init__(self, window: QMainWindow, key: str, content, grip):
        super().__init__(content)   # 父 = 内容：防 Python GC 静默失效
        self._window = window
        self._key = key
        self._content = content
        self._grip = grip
        self._zone = None      # 当前悬停区（控制光标）
        self._drag = None      # (起点全局坐标, 起点几何, 抓取区名)
        self._cursor_override = False   # 覆盖光标是否挂在本过滤器名下

    @classmethod
    def _zone_at(cls, w, h, x, y):
        """内容坐标 → 抓取区名；不在任何区 → None（角优先于边）。"""
        c, e = cls.CORNER, cls.EDGE
        if x < c and y < c:
            return "topleft"
        if x > w - c and y < c:
            return "topright"
        if x < c and y > h - c:
            return "bottomleft"
        if x > w - c and y > h - c:
            return "bottomright"
        if x < e:
            return "left"
        if x > w - e:
            return "right"
        if y < e:
            return "top"
        if y > h - e:
            return "bottom"
        return None

    def _apply_drag(self, gpos):
        """拖拽中：按起点几何 + 全局位移算新几何（增量法，子窗口的
        MDI 坐标与弹出窗口的屏幕坐标都适用）。"""
        sx, sy, gx, gy, gw, gh, z = self._drag
        dx, dy = gpos.x() - sx, gpos.y() - sy
        x, y, w, h = gx, gy, gw, gh
        if "left" in z:
            x, w = gx + dx, gw - dx
        elif "right" in z:
            w = gw + dx
        if "top" in z:
            y, h = gy + dy, gh - dy
        elif "bottom" in z:
            h = gh + dy
        # 下限：别缩到看不见（内容自身最小 ~65×57，留够余地）
        w, h = max(w, 120), max(h, 100)
        dock = self._window.plot_docks.get(self._key)
        if dock is not None:
            dock.setGeometry(x, y, w, h)

    def _set_zone_cursor(self, zone):
        """换应用级覆盖光标：换区先弹旧栈再挂新的（同一时刻最多一个）。

        挂 = QApplication.setOverrideCursor：应用级、设上立刻生效，
        不走各部件光标矩形那套机制（macOS 上部件光标会被带过期坐
        标的合成事件打回原形，见类 docstring）。
        """
        if self._cursor_override:
            QApplication.restoreOverrideCursor()
            self._cursor_override = False
        self._zone = zone
        if zone is not None:
            QApplication.setOverrideCursor(QCursor(self._CURSORS[zone]))
            self._cursor_override = True

    def _release_cursor(self):
        """撤销本过滤器挂的覆盖光标（Leave / 面板关闭时调用）。"""
        if self._cursor_override:
            QApplication.restoreOverrideCursor()
            self._cursor_override = False
        self._zone = None

    def eventFilter(self, obj, event):
        et = event.type()
        if et == QEvent.Type.Resize and obj is self._content:
            # 把手钉在右下角（内容变尺寸时跟随）
            self._grip.move(self._content.width() - self._grip.width() - 2,
                            self._content.height() - self._grip.height() - 2)
            return False
        if et == QEvent.Type.Leave:
            # 鼠标离开抓手落点控件：撤销覆盖光标（离开后可能直接
            # 跨到别的面板，让它重新判）
            self._release_cursor()
            return False
        if et not in (QEvent.Type.MouseMove, QEvent.Type.Enter,
                      QEvent.Type.MouseButtonPress,
                      QEvent.Type.MouseButtonRelease):
            return False
        # 事件可能落在子部件（画布/把手）上：统一换算到内容坐标判区
        gpos = event.globalPosition().toPoint()
        pos = self._content.mapFromGlobal(gpos)
        inside = (0 <= pos.x() < self._content.width()
                  and 0 <= pos.y() < self._content.height())
        zone = (self._zone_at(self._content.width(), self._content.height(),
                              pos.x(), pos.y()) if inside else None)
        if et in (QEvent.Type.MouseMove, QEvent.Type.Enter):
            if self._drag is not None:
                if et == QEvent.Type.MouseMove:
                    self._apply_drag(gpos)
                return True   # 拖拽中：吃下事件，不传给画布平移
            if not inside:
                # 过期坐标的合成事件（窗口激活/布局变动时 macOS 补发
                # 的幽灵事件）：忽略，不动光标状态——否则判出来的区
                # 是 None，会把刚设上的方向光标打回箭头
                return False
            if zone != self._zone or et == QEvent.Type.Enter:
                self._set_zone_cursor(zone)
            return False   # 悬停不拦截：画布取点照旧
        if et == QEvent.Type.MouseButtonPress:
            if (inside and zone is not None
                    and event.button() == Qt.LeftButton):
                self._drag = (gpos.x(), gpos.y(), *_dock_geo(self._window,
                                                             self._key),
                              zone)
                return True   # 吃下：不让画布当平移起点
            return False
        # MouseButtonRelease
        if self._drag is not None:
            self._drag = None
            return True
        return False


def _dock_geo(window: QMainWindow, key: str):
    """当前容器几何 (x, y, w, h)；面板已关就原地踏步（拖拽兜底）。"""
    dock = window.plot_docks.get(key)
    if dock is None:
        return (0, 0, 0, 0)
    g = dock.geometry()
    return (g.x(), g.y(), g.width(), g.height())


def _install_resize_grip(window: QMainWindow, key: str, content) -> None:
    """给面板内容装四边/四角抓手 + 右下角可见把手。

    把手 = 半透明小三角标签（child of 内容）：光标变斜向箭头、
    按住拖 = 拉伸右下角；位置随内容尺寸变化由 _PanelGripFilter
    钉住。过滤器必须装到事件落点控件上：QWidget 的父过滤器收不
    到子部件事件（探针实证：子部件 accept 后不向上传播，而
    matplotlib 画布会 accept 鼠标按下）——所以 1D 面板装画布、
    占位面板装内容本体、把手自己再装一份；顶边抓取带落在工具栏
    条上（含坐标标签），也各挂一份（按钮是它的子部件，按到按钮
    仍各司其职，不会误拉伸）。mouseTracking 打开：悬停换光标需
    要鼠标移动事件（按住拖动期间的移动事件有隐式鼠标抓取，把手
    不开也照常拖）。光标机制：过滤器挂应用级覆盖光标
    （QApplication.setOverrideCursor，macOS 上部件级 setCursor 会
    被带过期坐标的合成事件打回原形，见 _PanelGripFilter 类
    docstring）；把手自带 SizeFDiag 光标，按住把手拖 = 右下角
    拉伸（按压位置会换算回内容坐标判区）。
    """
    grip = QLabel("▙", content)
    grip.setCursor(Qt.SizeFDiagCursor)
    grip.setStyleSheet("color: #808080; background: transparent;")
    grip.setFixedSize(18, 18)
    grip.move(max(content.width() - 20, 0), max(content.height() - 20, 0))
    content.setMouseTracking(True)
    filt = _PanelGripFilter(window, key, content, grip)
    canvas = getattr(content, "canvas", None)
    # content 必挂：接自己的 Resize 事件重定位把手（1D 面板它的
    # 鼠标事件全被画布挡着，只有 Resize 会来，无害）；画布另挂
    # 一份接真实鼠标落点（见 docstring 的原因说明）
    targets = [content, grip]
    if canvas is not None:
        canvas.setMouseTracking(True)
        targets.append(canvas)
    toolbar = getattr(content, "toolbar", None)
    if toolbar is not None:
        # 内容上边 8px 抓取带落在工具栏条上：也挂一份（按钮是它的
        # 子部件，按到按钮仍各司其职，不会误拉伸）
        toolbar.setMouseTracking(True)
        targets.append(toolbar)
        loc = getattr(toolbar, "locLabel", None)
        if loc is not None:
            # 坐标标签占住工具栏右侧大半：不挂它顶边拖拽会在这里断
            loc.setMouseTracking(True)
            targets.append(loc)
    for target in targets:
        target.installEventFilter(filt)
    content._resize_grip = grip
    content._grip_filter = filt   # 关面板时用来撤销覆盖光标


class _PanelResizeFilter(QObject):
    """装在画布上的事件过滤器：画布尺寸一变就记"用户拖过面板"。

    挂在画布而不是容器上：弹出/收回换容器不用重挂。MDI 子窗口
    自由缩放——用户拖边 = 画布跟着变，把当前画布尺寸记成新偏好
    比例；程序自己的布局变化（开局/平铺/弹出收回）不算拖动。
    真正的逻辑在 _on_canvas_resized。以画布为父对象：installEventFilter
    不接管所有权，无父的过滤器对象会被 Python 垃圾回收、静默失效
    （探针验证过）。
    """

    def __init__(self, window: QMainWindow, key: str, canvas):
        super().__init__(canvas)
        self._window = window
        self._key = key

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            _on_canvas_resized(self._window, self._key)
        return False


def _on_canvas_resized(window: QMainWindow, key: str) -> None:
    """画布尺寸变化 → 记比例记忆（每拖一次 = 记住当前画布比例）。

    规则（与用户讨论定稿）：
      - 手动缩放完全自由：拖成什么样就什么样，不弹回任何比例
        （旧的普通拖/Shift 拖之分随停靠分栏一起退场）；
      - 每拖一次 = _canvas_pref 记成当前画布尺寸、_dragged = True
        ——之后开新图不动它、横排/竖排平铺按它等比摆放，这就是
        "拖过就永远按拖成的比例缩放"的记忆载体；
      - 比例记忆是缩放无关值：总缩放 80% 时拖成 400×240 的画布，
        记 500×300——Ctrl+滚轮回到 100% 时面板正好是拖成的比例，
        不会把总缩放误记成"用户拖过"（记忆 ÷ 当前总缩放）；
      - 程序自己的布局变化不算拖动：_layouting（平铺）与面板级
        _settling（开局/弹出/收回）举旗期间直接跳过；旗外还有
        _last_canvas 预期值兜底——与预期一致的迟到事件同样跳过，
        不误标"拖过"。子窗口不随主窗口缩放，无需再区分"拖图"与
        "拖窗口"（旧规则里那一大段启发式随分栏一起删掉）。
    """
    dock = window.plot_docks.get(key)
    if dock is None:
        return   # 面板已关：静默丢弃
    canvas = getattr(_content(dock), "canvas", None)
    if canvas is None:
        return   # 占位面板没有画布，没有比例记忆
    now = (canvas.width(), canvas.height())
    if (getattr(window, "_layouting", False)
            or getattr(dock, "_settling", False)
            or now == getattr(dock, "_last_canvas", None)):
        return
    dock._last_canvas = now
    z = getattr(window, "_area_zoom", 1.0)
    dock._canvas_pref = (now[0] / z, now[1] / z)
    dock._dragged = True


def _panel_extra(dock) -> tuple:
    """面板"壳"尺寸 = 容器尺寸 − 画布尺寸（标题栏 + 工具栏 + 边框）。

    布局稳定后直接量；刚创建/未布局时量出来是垃圾值（探针实测
    子窗口刚 setWidget 后宽 0..60、高 10..300 都有）→ 改用
    sizeHint 差值兜底（创建时 sizeHint 差值与稳定后的实测一致，
    同旧 _dock_extra 的验证结论）。返回 (宽, 高)；占位面板没有
    画布 → (0, 0)。弹出窗口的壳是 OS 标题栏（不进 widget 几何），
    量出来 = (0, 0)——正常，弹出状态本就不需要壳。
    """
    content = _content(dock)
    canvas = getattr(content, "canvas", None)
    if canvas is None:
        return 0, 0
    live = (dock.width() - canvas.width(), dock.height() - canvas.height())
    if 0 <= live[0] <= 60 and 10 <= live[1] <= 300:   # 合理区间外的 = 垃圾
        return live
    fallback = (dock.sizeHint().width() - content.sizeHint().width(),
                dock.sizeHint().height() - content.sizeHint().height())
    if 0 <= fallback[0] <= 60 and 10 <= fallback[1] <= 300:
        return fallback
    return 0, 0


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
                 f" {len(subs)} 个面板（按类型分层，保持各自大小）")


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
        floated.setWindowTitle(dock.windowTitle())
        floated.content = content
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
