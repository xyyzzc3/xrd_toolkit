"""面板抓手与尺寸记忆：四边/四角抓取、比例记忆、画布尺寸回调。

2026-10-21 从 panels.py 原样搬出（只改结构，不动逻辑）。
panels 再导出，plot_panels 与 app 照旧经 gui_panels 取用。
"""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from xrd_toolkit.gui.panel_state import _content


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

    def __init__(self, window: QMainWindow, key: str, content, grip,
                 get_dock=None):
        super().__init__(content)   # 父 = 内容：防 Python GC 静默失效
        self._window = window
        self._key = key
        self._content = content
        self._grip = grip
        # 容器现查：普通图面板住 window.plot_docks，校准面板不进那张表
        # （它有自己的 window.calib_dock，见 calib_panel）。认的是"这一刻
        # 谁是这个内容的容器"，所以传来的是一条取用函数而不是容器本身
        self._get_dock = get_dock or (lambda: window.plot_docks.get(key))
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
        dock = self._get_dock()
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
                self._drag = (gpos.x(), gpos.y(),
                              *_dock_geo(self._get_dock), zone)
                return True   # 吃下：不让画布当平移起点
            return False
        # MouseButtonRelease
        if self._drag is not None:
            self._drag = None
            return True
        return False

def _dock_geo(get_dock):
    """当前容器几何 (x, y, w, h)；容器已关就原地踏步（拖拽兜底）。"""
    dock = get_dock()
    if dock is None:
        return (0, 0, 0, 0)
    g = dock.geometry()
    return (g.x(), g.y(), g.width(), g.height())

def _install_resize_grip(window: QMainWindow, key: str, content,
                         get_dock=None) -> None:
    """给面板内容装四边/四角抓手 + 右下角可见把手。

    把手 = 半透明小三角标签（child of 内容）：光标变斜向箭头、
    按住拖 = 拉伸右下角；位置随内容尺寸变化由 _PanelGripFilter
    钉住。过滤器必须装到事件落点控件上：QWidget 的父过滤器收不
    到子部件事件（探针实证：子部件 accept 后不向上传播，而
    matplotlib 画布会 accept 鼠标按下）——所以 1D 面板装画布、
    占位面板装内容本体、把手自己再装一份；顶边抓取带落在自绘
    标题栏（面板最上面那一行）上，也挂一份（按钮是它的子部件，
    按到按钮仍各司其职，不会误拉伸）。mouseTracking 打开：悬停换光标需
    要鼠标移动事件（按住拖动期间的移动事件有隐式鼠标抓取，把手
    不开也照常拖）。光标机制：过滤器挂应用级覆盖光标
    （QApplication.setOverrideCursor，macOS 上部件级 setCursor 会
    被带过期坐标的合成事件打回原形，见 _PanelGripFilter 类
    docstring）；把手自带 SizeFDiag 光标，按住把手拖 = 右下角
    拉伸（按压位置会换算回内容坐标判区）。

    `get_dock` = "现在谁装着这个内容"：默认查 window.plot_docks（普通
    图面板）；校准面板不住那张表，自己传 `lambda: window.calib_dock`
    ——用户 2026-10-01："校准的子窗口没有 1D 图右下角那个标准的缩放"。
    内容的画布认 `content.canvas`：普通面板由 _PlotPanel 挂上，校准
    面板在建面板时补挂（见 calib_panel._open_calib_panel）。
    """
    grip = QLabel("▙", content)
    grip.setCursor(Qt.SizeFDiagCursor)
    grip.setStyleSheet("color: #808080; background: transparent;")
    grip.setFixedSize(18, 18)
    grip.move(max(content.width() - 20, 0), max(content.height() - 20, 0))
    content.setMouseTracking(True)
    filt = _PanelGripFilter(window, key, content, grip, get_dock=get_dock)
    canvas = getattr(content, "canvas", None)
    # content 必挂：接自己的 Resize 事件重定位把手（1D 面板它的
    # 鼠标事件全被画布挡着，只有 Resize 会来，无害）；画布另挂
    # 一份接真实鼠标落点（见 docstring 的原因说明）
    targets = [content, grip]
    if canvas is not None:
        canvas.setMouseTracking(True)
        targets.append(canvas)
    bar = getattr(content, "slim_bar", None)
    if bar is not None:
        # 内容上边 8px 抓取带落在自绘标题栏那一行上：也挂一份（按钮
        # 是它的子部件，按到按钮仍各司其职，不会误拉伸）。这一行还
        # 挂着"拖动移动"过滤器（plot_panels._build_slim_bar）——后
        # 装的过滤器先收到事件，这里的挂载发生在建栏之后，所以边缘
        # 带先被抓手吃掉，其余区域才轮到"拖 = 移动面板"
        bar.setMouseTracking(True)
        targets.append(bar)
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
    """面板"壳"尺寸 = 容器尺寸 − 画布尺寸（自绘标题栏一行 + 边框）。

    布局稳定后直接量；刚创建/未布局时量出来是垃圾值（探针实测
    子窗口刚 setWidget 后宽 0..60、高 10..300 都有）→ 改用
    sizeHint 差值兜底（创建时 sizeHint 差值与稳定后的实测一致，
    同旧 _dock_extra 的验证结论）。返回 (宽, 高)；占位面板没有
    画布 → (0, 0)。弹出窗口的壳是 OS 标题栏（不进 widget 几何），
    量出来 = (0, 0)——正常，弹出状态本就不需要壳。

    2026-09-24 面板壳瘦身：这里现在是 26 px 的自绘标题栏（原先
    原生标题栏 36 + 工具栏 47 = 83 px），同样的画布能多拿 57 px。
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

