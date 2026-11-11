"""元素周期表选择器（2026-11-11，用户第 11 条：元素筛选改为点击周期表）。

物相工作台的「元素」原先只有一个输入框（手输 `Fe Co Ni`）。这个模块
给同一件事一个点得着的入口：18 列标准布局，点一格 = 勾上/取消，确定
后把符号按原子序数排好写回输入框（输入框仍保留，手输/脚本不受影响）。

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


def _element_button(symbol: str) -> QPushButton:
    button = QPushButton(symbol)
    button.setObjectName(f"element_{symbol}")
    button.setCheckable(True)
    button.setProperty("element", True)
    button.setToolTip(f"{symbol}（Z = {atomic_number(symbol)}）")
    button.setFocusPolicy(Qt.NoFocus)
    return button


class PeriodicTableDialog(QDialog):
    """点选元素；[确定] 后由调用方读 `selected_elements()`。

    `selected` 是初始勾选（物相工作台输入框里已有的元素）。不在表里的
    符号被忽略，不做报错——输入框允许手输，这里是它的图形入口。
    """

    def __init__(self, selected=(), parent=None):
        super().__init__(parent)
        self.setWindowTitle("元素周期表")
        self.setMinimumWidth(760)
        self.setStyleSheet(
            'QPushButton[element="true"] {'
            ' min-width: 28px; min-height: 22px; padding: 1px 2px;'
            ' font-size: 11px; }'
            'QPushButton[element="true"]:checked {'
            ' background-color: #2f6fd0; color: white; }')

        root = QVBoxLayout(self)
        hint = QLabel("点元素 = 勾选，再点一下取消；留空 = 不按元素筛选")
        hint.setStyleSheet("color: gray;")
        root.addWidget(hint)

        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self._buttons = {}
        for period, groups in _PERIODS.items():
            for group, symbol in groups.items():
                button = _element_button(symbol)
                button.toggled.connect(self._refresh_summary)
                self._buttons[symbol] = button
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
        self.summary.setStyleSheet("color: gray;")
        root.addWidget(self.summary)

        buttons = QHBoxLayout()
        clear_btn = QPushButton("清空")
        clear_btn.setToolTip("取消全部勾选")
        clear_btn.clicked.connect(self._clear)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        ok_btn = QPushButton("确定")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        buttons.addWidget(ok_btn)
        root.addLayout(buttons)

        self.set_selected(selected)

    # ── 勾选状态 ─────────────────────────────────────────────
    def set_selected(self, symbols) -> None:
        """按符号集合重设勾选（不认识的忽略）。"""
        wanted = {str(s).strip().capitalize()
                  for s in symbols if str(s).strip()}
        for symbol, button in self._buttons.items():
            button.setChecked(symbol in wanted)
        self._refresh_summary()

    def selected_elements(self) -> list:
        """勾上的元素，按原子序数排序（Z 顺序）。"""
        return [symbol for symbol in SYMBOLS
                if self._buttons[symbol].isChecked()]

    def _clear(self) -> None:
        for button in self._buttons.values():
            button.setChecked(False)

    def _refresh_summary(self) -> None:
        chosen = self.selected_elements()
        if chosen:
            self.summary.setText(f"已选 {len(chosen)} 个：{' '.join(chosen)}")
        else:
            self.summary.setText("未选元素（不按元素筛选）")
