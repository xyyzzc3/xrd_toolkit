"""元素周期表选择器（2026-11-11，用户第 11 条：元素筛选改为点击周期表）。

物相工作台的「元素」原先只有一个输入框（手输 `Fe Co Ni`）。这个模块
给同一件事一个点得着的入口：18 列标准布局，点一格 = 勾上/取消，确定
后把符号按原子序数排好写回输入框（输入框仍保留，手输/脚本不受影响）。

2026-11-17 起每个元素有**两种状态**（用户："周期表选中包含两种，一定有
和可能有"）：点一下 = 一定有（实心蓝），再点 = 可能有（浅蓝），再点 =
取消。筛选资格取两组的并集（相的元素必须全落在并集内）；"一定有"用于
结果标注（一个都不含的相标"可能为衬底/杂质"，用到"可能有"里的元素时
标出"含 X"）。另加 [确定并筛相]：选完元素直接开始筛相，少跑一趟。

只做界面：不读相库、不判断某元素在不在库里——筛选仍由
`simulation.phase_search.phase_candidates` 按元素集合完成。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    QWidget)

# 118 个元素，按原子序数排列（Z 即下标 + 1）
SYMBOLS = tuple("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co
Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb
Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re
Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es
Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og
""".split())

_ORDER = {symbol: z for z, symbol in enumerate(SYMBOLS, start=1)}

# 标准 18 列布局：period → {group: symbol}。镧系/锕系单独两行（9/10 行）。
_PERIODS = {
    1: {1: "H", 18: "He"},
    2: {1: "Li", 2: "Be", 13: "B", 14: "C", 15: "N", 16: "O", 17: "F",
        18: "Ne"},
    3: {1: "Na", 2: "Mg", 13: "Al", 14: "Si", 15: "P", 16: "S", 17: "Cl",
        18: "Ar"},
    4: dict(enumerate("K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr"
                      .split(), start=1)),
    5: dict(enumerate("Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe"
                      .split(), start=1)),
    6: {1: "Cs", 2: "Ba", 4: "Hf", 5: "Ta", 6: "W", 7: "Re", 8: "Os",
        9: "Ir", 10: "Pt", 11: "Au", 12: "Hg", 13: "Tl", 14: "Pb",
        15: "Bi", 16: "Po", 17: "At", 18: "Rn"},
    7: {1: "Fr", 2: "Ra", 4: "Rf", 5: "Db", 6: "Sg", 7: "Bh", 8: "Hs",
        9: "Mt", 10: "Ds", 11: "Rg", 12: "Cn", 13: "Nh", 14: "Fl",
        15: "Mc", 16: "Lv", 17: "Ts", 18: "Og"},
    9: dict(enumerate("La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu"
                      .split(), start=3)),
    10: dict(enumerate("Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr"
                       .split(), start=3)),
}


def atomic_number(symbol) -> int:
    """元素符号 → 原子序数；不认识的符号排最后（999）。"""
    return _ORDER.get(str(symbol).strip().capitalize(), 999)


def sort_elements(symbols) -> list:
    """把元素符号集合按原子序数排好（去重、规范大小写）。"""
    cleaned = {str(s).strip().capitalize() for s in symbols if str(s).strip()}
    return sorted(cleaned, key=atomic_number)


# 元素格的三种状态：点一下一定有、再点可能有、再点取消
STATE_OFF = 0
STATE_REQUIRED = 1
STATE_POSSIBLE = 2
_STATE_TEXT = {STATE_OFF: "未选", STATE_REQUIRED: "一定有",
               STATE_POSSIBLE: "可能有"}


def _element_button(symbol: str) -> QPushButton:
    button = QPushButton(symbol)
    button.setObjectName(f"element_{symbol}")
    button.setProperty("element", True)
    button.setProperty("elementState", "off")
    button.setToolTip(f"{symbol}（Z = {atomic_number(symbol)}）：点一下 = "
                      "一定有，再点 = 可能有，再点 = 取消")
    button.setFocusPolicy(Qt.NoFocus)
    return button


class PeriodicTableDialog(QDialog):
    """点选元素（一定有 / 可能有）；[确定] 后由调用方读两组。

    `selected` = 初始的"一定有"，`possible` = 初始的"可能有"（物相工作台
    两个输入框里已有的元素）。不在表里的符号被忽略，不做报错——输入框
    允许手输，这里是它的图形入口。

    `allow_screen=True` 时多一颗 [确定并筛相]：点它 = accept + 把
    `screen_requested` 置 True，调用方据此直接开始筛相。
    """

    def __init__(self, selected=(), possible=(), parent=None, *,
                 allow_screen=False):
        super().__init__(parent)
        self.setWindowTitle("元素周期表")
        self.setMinimumWidth(760)
        self.screen_requested = False
        self.setStyleSheet(
            'QPushButton[element="true"] {'
            ' min-width: 28px; min-height: 22px; padding: 1px 2px;'
            ' font-size: 11px; }'
            'QPushButton[element="true"][elementState="required"] {'
            ' background-color: #2f6fd0; color: white; }'
            'QPushButton[element="true"][elementState="possible"] {'
            ' background-color: #dce9fb; color: #1f4f9a;'
            ' border: 1px solid #7aa7e8; }')

        root = QVBoxLayout(self)
        hint = QLabel(
            "点元素：一下 = <b>一定有</b>（深蓝，样品里确定的元素），"
            "再点 = <b>可能有</b>（浅蓝，杂质/衬底里可能出现的元素），"
            "再点 = 取消。\n"
            "筛相时取两组的并集：相里的元素必须全在并集内；漏选的元素"
            "会把真正的相挡在候选外（被挡的相近相会单独提示）。"
            "「一定有」还用来标注结果——一个都不含的相标「可能为衬底/"
            "杂质」。不确定就留空（= 不按元素筛选）。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self._buttons = {}
        self._states = {}
        for period, groups in _PERIODS.items():
            for group, symbol in groups.items():
                button = _element_button(symbol)
                button.clicked.connect(
                    lambda _checked=False, s=symbol: self._cycle(s))
                self._buttons[symbol] = button
                self._states[symbol] = STATE_OFF
                grid.addWidget(button, period - 1, group - 1)
        # 镧系/锕系在主表里的占位（不放可点的元素，避免重复）
        for row, text, tip in ((5, "57–71", "镧系：La–Lu（见下方一行）"),
                               (6, "89–103", "锕系：Ac–Lr（见下方一行）")):
            label = QLabel(text)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet("color: gray; font-size: 10px;")
            label.setToolTip(tip)
            grid.addWidget(label, row, 2)
        for row, text in ((8, "镧系"), (9, "锕系")):
            label = QLabel(text)
            label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            label.setStyleSheet("color: gray;")
            grid.addWidget(label, row, 0, 1, 2)
        root.addWidget(grid_host)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("color: gray;")
        root.addWidget(self.summary)

        buttons = QHBoxLayout()
        clear_btn = QPushButton("清空")
        clear_btn.setToolTip("取消全部选择（一定有 + 可能有）")
        clear_btn.clicked.connect(self._clear)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        ok_btn = QPushButton("确定")
        ok_btn.setDefault(not allow_screen)
        ok_btn.clicked.connect(self.accept)
        buttons.addWidget(ok_btn)
        if allow_screen:
            screen_btn = QPushButton("确定并筛相")
            screen_btn.setObjectName("periodic_screen_btn")
            screen_btn.setDefault(True)
            screen_btn.setToolTip(
                "把两组元素写回物相页，并直接开始筛相"
                "（需要先在物相页选好一条 1D 曲线）")
            screen_btn.clicked.connect(self._accept_and_screen)
            buttons.addWidget(screen_btn)
        root.addLayout(buttons)

        self.set_selected(selected, possible)

    # ── 选择状态 ─────────────────────────────────────────────
    def set_selected(self, symbols, possible=()) -> None:
        """按两组符号重设状态（不认识的忽略）；`possible` 省略 = 全算一定有。"""
        required = {str(s).strip().capitalize()
                    for s in symbols if str(s).strip()}
        optional = {str(s).strip().capitalize()
                    for s in possible if str(s).strip()}
        for symbol in self._buttons:
            if symbol in required:
                self._set_state(symbol, STATE_REQUIRED)
            elif symbol in optional:
                self._set_state(symbol, STATE_POSSIBLE)
            else:
                self._set_state(symbol, STATE_OFF)
        self._refresh_summary()

    def required_elements(self) -> list:
        """「一定有」的元素，按原子序数排序（Z 顺序）。"""
        return [symbol for symbol in SYMBOLS
                if self._states[symbol] == STATE_REQUIRED]

    def possible_elements(self) -> list:
        """「可能有」的元素，按原子序数排序（Z 顺序）。"""
        return [symbol for symbol in SYMBOLS
                if self._states[symbol] == STATE_POSSIBLE]

    def selected_elements(self) -> list:
        """两组的并集（兼容旧调用方），按原子序数排序。"""
        return [symbol for symbol in SYMBOLS
                if self._states[symbol] != STATE_OFF]

    def _cycle(self, symbol: str) -> None:
        self._set_state(symbol, (self._states[symbol] + 1) % 3)
        self._refresh_summary()

    def _set_state(self, symbol: str, state: int) -> None:
        self._states[symbol] = int(state)
        button = self._buttons[symbol]
        button.setProperty(
            "elementState",
            {STATE_OFF: "off", STATE_REQUIRED: "required",
             STATE_POSSIBLE: "possible"}[int(state)])
        button.style().unpolish(button)
        button.style().polish(button)
        button.update()
        button.setToolTip(
            f"{symbol}（Z = {atomic_number(symbol)}）：{_STATE_TEXT[int(state)]}"
            " · 点一下 = 一定有，再点 = 可能有，再点 = 取消")

    def _clear(self) -> None:
        for symbol in self._buttons:
            self._set_state(symbol, STATE_OFF)
        self._refresh_summary()

    def _accept_and_screen(self) -> None:
        self.screen_requested = True
        self.accept()

    def _refresh_summary(self) -> None:
        required = self.required_elements()
        possible = self.possible_elements()
        if not required and not possible:
            self.summary.setText("未选元素（不按元素筛选）")
            return
        parts = []
        if required:
            parts.append(f"一定有 {len(required)} 个：{' '.join(required)}")
        if possible:
            parts.append(f"可能有 {len(possible)} 个：{' '.join(possible)}")
        self.summary.setText("　｜　".join(parts))
