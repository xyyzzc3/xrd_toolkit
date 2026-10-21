"""面板小部件：省略号标签、坐标读数、焦点省略标签。

2026-10-21 从 panels.py 原样搬出（只改结构，不动逻辑）。
panels 再导出，plot_panels 与 app 照旧经 gui_panels 取用。
"""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy


_COORD_MIN_W = 50

# 横带里坐标读数的槽宽（px）：偏好 230（2026-10-07 用户"坐标现在显示不完全"
# 后从 180 加大：单曲线读数去掉冗余的"面板名："前缀后约 150px，瀑布的
# "165°：2θ …" 也放得下；**带文件名的对比读数**仍可能中部省略、悬停看全），
# 空间紧时允许压到 50。**下限必须留得低**：横带最小宽会顶大"窗口的最小宽度"——
# 实测下限 180 时整个窗口最小宽涨到能堵住"进校准拉宽参数坞"（1200 默认
# 宽时参数坞要能到 ~335=校准页内容宽；50 是量出来的：60 还差 4px，
# 见 calib.CALIB_PANEL_RESERVE_PX 与 TestCalibFlow 两条；2026-10-07
# 全量套件逮到 280 vs 335）
_COORD_SLOT_W = 230

class _ElideLabel(QLabel):
    """宽度受限的单行标签：文字超宽时打省略号，绝不撑宽父布局。

    用在参数坞的长名称行（编辑对象标题 / 几何配置说明 / 锚点计数 / 裁剪
    清单）与绘图区横带的实时坐标读数：窄了缩略显示，悬停有完整提示
    （tooltip 跟着文本走）；布局给多宽就显示多宽（Ignored = 不按文字宽度
    反过来要地方）。text() 仍返回完整文字（缩略只是显示层面，程序与测试
    读 text() 不受影响）。坐标读数用它，读数长短变化时右边的按钮不会被
    挤着左右跳。

    min_cap：**minimumSizeHint 的封顶值**（px）。默认 None = 按全文要宽度；
    传了值就封顶——参数坞的最小宽度是照各控件 minimumSizeHint 量的，长
    动态文本不封顶会把坞顶宽/把页面顶出滚动区（ScrollBarAlwaysOff 直接
    裁掉右缘，2026-10-08 实测：21 个锚点的计数让处理页内容 301 > 坞最小
    280）。

    2026-10-07 从 app.py 搬来（坐标读数进了绘图区横带，而横带建在
    panels——app 反过来从本模块取它，兼容导出照旧）。
    """

    def __init__(self, text: str = "", mode=Qt.ElideMiddle, parent=None,
                 min_cap: int = None):
        super().__init__(text, parent)
        self._full = text
        self._mode = mode
        self._min_cap = min_cap
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(text)

    def minimumSizeHint(self):
        base = super().minimumSizeHint()
        if self._min_cap is None:
            return base
        return QSize(min(base.width(), self._min_cap), base.height())

    def setText(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._refresh()

    def text(self) -> str:
        return self._full

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self.width() <= 0:
            super().setText(self._full)   # 布局还没定宽：先显示全量
            return
        super().setText(self.fontMetrics().elidedText(
            self._full, self._mode, self.width()))

class _CoordReadout(_ElideLabel):
    """横带里的坐标读数格（见 _COORD_SLOT_W 的尺寸说明）。

    **上限 230 px 已去掉（2026-10-10）**：用户"对比图的坐标显示还是有问题
    ……挪位置以前怎么没这个问题"——10-07 读数从底部状态栏（整条全宽）搬进
    绘图区横带时，给它定了固定上限 230，对比读数（文件名 + 2θ + 强度）
    必然被省略号裁掉。现在读数格**吃掉横带的剩余宽度**（行里 stretch=1），
    面板多宽就显示多宽、窄了压到 _COORD_MIN_W 才省略——一劳永逸。
    sizeHint 仍固定 = 槽宽（不随文本变）：悬停读数长短变化不会把右边的
    按钮挤着左右跳（宽度只由剩余空间决定，与文本无关）。
    """

    def __init__(self, parent=None):
        super().__init__("", parent=parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.setMinimumWidth(_COORD_MIN_W)

    def sizeHint(self):
        return QSize(_COORD_SLOT_W, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(_COORD_MIN_W, super().minimumSizeHint().height())

class _FocusElideLabel(_ElideLabel):
    """坞顶"编辑对象"那一行用的省略号标签：minimumSizeHint 封顶 160。

    封顶的来由（2026-10-07）：QLabel 的 minimumSizeHint 会按全文宽度要
    地方，而参数坞的最小宽度是照它量的——空态文案加了指路（31 字）之后，
    坞最小宽从 ~280 跳到 405，两条"最小宽 < 320"的测试当场红。它按设计
    会自己打省略号（坞拖宽自动多显示几个字，悬停看全），不需要全文宽度。
    2026-10-08 封顶机制上提为 _ElideLabel 的 min_cap 参数，本类保留
    原名额与 160 的取值。
    """

    def __init__(self, text: str = "", mode=Qt.ElideMiddle, parent=None):
        super().__init__(text, mode, parent, min_cap=160)

