"""原位多相批量中央工作台（2026-10-30；2026-11-18 统一为通用页面）。

打开方式：工具栏 [原位]。与 [掩膜]、[精修]、[物相] 同一套中央 MDI
子窗口：进入时收起参数坞和日志坞，退出只隐藏，选中的目录/参数/结果
都留着。

一个页面、一份通用流程（引擎：`simulation.insitu_generic`）：

    选帧目录与掩膜 → 相表（默认空；[加 CIF…] 或顶部「方案」下拉）
    → [开始]：后台准备（读相表 + 定仪器，数秒）
    → 逐帧精修：读图 → 积分 → 全谱拟合 → 结果进表
    → 右侧四个视图：趋势 / 汇总 / 单帧拟合 / 峰窗跟踪
    → 结束写 CSV 与 fit_summary.txt

「方案」下拉只替换相表/策略/视图/仪器来源（通用（默认）/ LMFP 三相 /
载入的方案 .json），不再另开界面。方案文件格式见
`simulation.phase_set`（v2 多记 instrument 与 table_columns）。

分数口径：f_<相> = A_相 / ΣA_样品相，是**相对散射强度分数**；表里
另有 `wt <相>%`（Rietveld 重量分数，只对晶态相；参考相单列、不进
样品分母）。单帧拟合双击表格行；[保存当前图…] 按脚本同尺寸重画。
"""
import csv
import math
import time
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDoubleSpinBox, QFileDialog,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QMdiSubWindow, QMenu, QProgressBar,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from xrd_toolkit import paths
from xrd_toolkit.config import get_config
from xrd_toolkit.gui.figure_appearance import (
    apply_figure_appearance, edit_figure_appearance)
from xrd_toolkit.gui.insitu_phase_table import (
    InsituPhaseTable, ROLE_SAMPLE)
from xrd_toolkit.gui.panel_state import _log
from xrd_toolkit.gui.panel_widgets import _ElideLabel
from xrd_toolkit.gui.plot_export import save_figure_with_options
from xrd_toolkit.gui.tasks import BackgroundTask, user_error_text
from xrd_toolkit.presets import (
    DEFAULT_PRESET_NAME, PROJECT_PRESET, resolve_mask_ref)

# 结果表列（2026-11-18 起跟随方案）：引擎列名 → 表头；方案里只记列名，
# 没登记的按规则展开（wt_x → "wt x%"，rich_a → "rich a" …）。
_COLUMN_LABELS = {
    "frame": "帧", "file": "文件", "status": "状态", "rwp": "Rwp %",
    "rp": "Rp %", "usable": "usable",
    "f_rich": "f_rich", "wt_rich": "wt rich%", "wt_poor": "wt poor%",
    "rich_a": "rich a", "rich_c": "rich c",
    "poor_a": "poor a", "poor_c": "poor c",
    "t_fit_s": "拟合 s", "t_total_s": "总 s",
}


def _column_label(key):
    key = str(key)
    if key in _COLUMN_LABELS:
        return _COLUMN_LABELS[key]
    if key.startswith("f_") and key.endswith("_esd"):
        return f"{key[:-4]} esd"
    if key.startswith("wt_"):
        return f"wt {key[3:]}%"
    if "_" in key:
        return key.replace("_", " ")
    return key


def _cell_text(key, value):
    """结果表单元格格式（沿用原来按列的小数位）。"""
    if isinstance(value, str):
        return value
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number != number:
        return "—"
    if key == "frame":
        return str(int(number))
    if key in ("rwp", "rp"):
        return f"{number:.2f}"
    if key.startswith("f_") or key.endswith("_esd"):
        return f"{number:.3f}"
    if key.startswith("wt_"):
        return f"{number:.2f}"
    if key.startswith("t_"):
        return f"{number:.1f}"
    return f"{number:.4f}"


def _fmt(value, digits=3):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{value:.{digits}f}" if np.isfinite(value) else "—"


def _dual_start_note(row):
    """双起点溯源提示（2026-11-27）：悬停 Rwp 看两个起点各是多少。"""
    start = str(row.get("start_used") or "").strip()
    if not start:
        return ""
    try:
        dual = int(row.get("dual_start", 0) or 0)
    except (TypeError, ValueError):
        dual = 0
    if not dual:
        return ""
    seed = row.get("rwp_seed")
    cif = row.get("rwp_cif")
    try:
        seed_v = float(seed)
        cif_v = float(cif)
    except (TypeError, ValueError):
        return f"采用 {start} 起点"
    if not (math.isfinite(seed_v) and math.isfinite(cif_v)):
        return f"采用 {start} 起点"
    return (f"种子起点 Rwp {seed_v:.3f}% → CIF 起点 {cif_v:.3f}%；"
            f"采用 {start.upper()}")


def _start_suffix(row):
    """状态行尾巴：CIF 起点胜出时提醒一句（不占表格列）。"""
    return "  （CIF 起点）" if str(
        row.get("start_used") or "").strip() == "cif" else ""


def compact_table_columns(columns, phases):
    """通用方案的紧凑结果表列（2026-11-19 手测：41 列太挤）。

    基础四列 + 每条样品相的分数/重量分数/晶胞 a、c + 拟合耗时；
    参考相（Al 等）不占列。方案显式给了 `table_columns` 时不用这套
    （LMFP 预设仍是它自己那 12 列），右键「显示全部列」可看引擎全列。
    """
    available = set(str(k) for k in (columns or ()))
    keys = [k for k in ("frame", "file", "status", "rwp") if k in available]
    for phase in phases or ():
        is_sample = getattr(phase, "is_sample", None)
        if callable(is_sample) and not is_sample():
            continue
        label = getattr(phase, "label", "")
        if not label:
            continue
        for key in (f"f_{label}", f"wt_{label}",
                    f"{label}_a", f"{label}_c"):
            if key in available:
                keys.append(key)
    if "t_fit_s" in available:
        keys.append("t_fit_s")
    return keys


def _prepare_worker(config_name, scheme, mask_path, saved_baseline,
                    max_rwp, first_nfev, frame_nfev, npt,
                    tth_min, tth_max):
    """后台准备：按方案字典读结构/定仪器/读 mask，返回 batch。

    scheme 是 `phase_set_to_dict()` 的形状；CIF 解析与 LaB₆ 定仪器都
    留在后台线程（2026-11-18 起界面只拼字典）。
    """
    try:
        from xrd_toolkit.simulation.insitu_generic import prepare_batch
    except ImportError as err:
        raise RuntimeError(str(err)) from err
    return prepare_batch(
        config_name, scheme, mask_path=mask_path,
        saved_baseline=saved_baseline, max_rwp=max_rwp,
        first_nfev=first_nfev, frame_nfev=frame_nfev, npt=npt,
        tth_min=tth_min, tth_max=tth_max)


class InSituWorkbench(QWidget):
    """原位多相批量工作台（默认通用，方案就地替换）。"""

    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.setObjectName("insitu_workbench")

        try:
            from xrd_toolkit.simulation.insitu import LAB6_TIF, frames_in_dir
            self._frames_in_dir = frames_in_dir
        except ImportError:
            LAB6_TIF = None
            self._frames_in_dir = lambda *a, **k: []

        preset = getattr(main_window, "preset", None) or PROJECT_PRESET
        # 预设没写帧目录 = 真的"未设置"（用户预设不再偷偷落回 LMFP 默认）；
        # 内置预设自带目录，目录不存在时退到仓库样例（保持"开箱能看到东西"）。
        preset_dir = str(preset.get("insitu_dir") or "").strip()
        self._data_dir_set = bool(preset_dir)
        if preset_dir:
            self.data_dir = Path(preset_dir).expanduser()
            if (not self.data_dir.is_dir()
                    and preset.get("name") == DEFAULT_PRESET_NAME):
                repo_data = paths.ROOT / "data"
                self.data_dir = (repo_data if repo_data.is_dir()
                                 else Path.home())
        else:
            self.data_dir = Path.home()
        # 仓库样例只在开发机有；打包后由用户选自己的 LaB₆ 标样帧
        self.lab6_path = (Path(LAB6_TIF)
                          if LAB6_TIF is not None and Path(LAB6_TIF).is_file()
                          else None)
        # 预设的 mask 只在预设自己的几何下自动带；其它配置按不遮
        self.mask_path = self._mask_for_preset(
            preset, self.main.config_name)
        # 当前方案（2026-11-18）：相表在界面上，策略/视图/仪器跟方案走。
        from xrd_toolkit.simulation.phase_set import (
            BatchPolicy, InstrumentSpec, ViewConfig)
        self._scheme_name = "通用多相"
        self._scheme_policy = BatchPolicy.generic()
        self._scheme_views = ViewConfig()
        self._scheme_instrument = InstrumentSpec(mode="baseline")
        self._scheme_adapter = ""
        self._preview_phase_set = None   # 视图在没有批次时用（含相位颜色/窗口）
        self._preset_index = 0        # 下拉当前应用过的项（取消替换时还原）
        self._table_keys = ["frame", "file", "status", "rwp", "rp"]
        # 结果表列（2026-11-19）：通用方案默认紧凑列，右键可切诊断列。
        self._all_keys = []
        self._compact_keys = []
        self._auto_compact = False
        self._show_all_columns = False
        self.outdir = paths.default_outputs_dir() / "insitu"
        self.frames = []
        self._use_checked = False       # 帧来源 = 文件栏勾选
        self.batch = None
        # 批次开始那一刻的输入快照：报告用（跑完再改下拉框也不会写错）
        self._batch_meta = None
        self._task = None
        self._stop = False
        self._index = 0
        # 单帧拟合视图（2026-11-02）：double-click 表格行时填；只在当前
        # 会话有效（FitResult 曲线没有持久化）
        self._fit_frame = None
        self._fit_res = None
        self._fit_status = ""
        self._fit_file = ""
        self._fit_frich = float("nan")
        self._fit_main_label = "phase"
        # 模型无关视图的按帧缓存：每次新帧/新批次清空
        self._mi_rows = None
        self._mi_len = -1
        self._appearances = [{}, {}, {}, {}]   # 四视图各自的图外观

        self._build_ui()
        self._refresh_mask_label()
        self._refresh_data_label()
        self._refresh_instrument_label()
        self._draw_trends()

    # ── 项目预设 ─────────────────────────────────────────────
    @staticmethod
    def _mask_for_preset(preset, config_name):
        """预设的 mask 只在预设几何下自动带；文件不存在 → 不遮。

        引用可以是文件名（去 outputs/masks 找）或完整路径（见
        `presets.resolve_mask_ref`）；掩膜工程 `.mask.npz` 与通用掩膜
        `.npy/.edf` 都收（2026-11-17 起，应用内以工程为准）。
        """
        ref = str((preset or {}).get("insitu_mask_name") or "").strip()
        if not ref or config_name != (preset or {}).get("geometry"):
            return None
        path = resolve_mask_ref(ref)
        return path if path is not None and path.is_file() else None

    def apply_preset(self, preset):
        """应用项目预设：换帧目录与 mask（不动 rich/poor/Al 三相定义）。"""
        raw = str(preset.get("insitu_dir") or "").strip()
        self._data_dir_set = bool(raw)
        self._use_checked = False
        self.data_dir = (Path(raw).expanduser() if raw else Path.home())
        self.mask_path = self._mask_for_preset(
            preset, self.main.config_name)
        self._refresh_data_label()
        self._refresh_mask_label()
        self._set_status(
            f"已应用预设「{preset.get('name', '')}」："
            f"{len(self.frames)} 帧")

    def _suggested_mask_path(self):
        """当前几何在 outputs/masks 里现成的掩膜（只提醒，不自动选）。"""
        name = str(getattr(self.main, "config_name", "") or "").strip()
        if not name:
            return None
        folder = paths.default_outputs_dir() / "masks"
        for filename in (f"{name}_mask.npy", f"{name}_mask.edf",
                         f"{name}.mask.npz", f"{name}_mask.mask.npz"):
            path = folder / filename
            if path.is_file():
                return path
        return None

    def _refresh_mask_label(self):
        if self.mask_path is not None:
            self.mask_label.setText(f"掩膜：{self.mask_path.name}")
            self.mask_label.setToolTip(str(self.mask_path))
        else:
            hint = self._suggested_mask_path()
            if hint is not None:
                # 2026-11-19 手测：忘选掩膜时 Rwp 从 ~3% 掉到 ~8%，
                # 这里只提醒，不替用户改输入。
                self.mask_label.setText(
                    f"掩膜：不遮（建议选 {hint.name}）")
                self.mask_label.setToolTip(
                    f"{hint}\n热像素不遮会明显抬高 Rwp；点 [选掩膜…] 选它")
            else:
                self.mask_label.setText("掩膜：不遮")
                self.mask_label.setToolTip("")

    # ── 界面组装 ─────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("数据"))
        # 长路径用省略号标签，并给数据/掩膜各留上限；否则长路径会把
        # 右上角的 mask 灰字压到按钮底下（2026-11-03 用户报“遮盖灰字”）。
        self.data_label = _ElideLabel("", Qt.ElideMiddle)
        self.data_label.setStyleSheet("color: gray;")
        self.data_label.setMinimumWidth(0)
        self.data_label.setMaximumWidth(420)
        top.addWidget(self.data_label, 1)
        self.data_btn = QPushButton("选文件夹…")
        self.data_btn.clicked.connect(self._choose_data)
        top.addWidget(self.data_btn)
        self.checked_btn = QPushButton("用勾选的原始数据")
        self.checked_btn.setObjectName("insitu_use_checked_btn")
        self.checked_btn.setToolTip(
            "把文件栏里勾选的原始图按文件名排序当作帧序列"
            "（不用先进文件夹；只认原始图，同几何/同形状）")
        self.checked_btn.clicked.connect(self._use_checked_frames)
        top.addWidget(self.checked_btn)
        # 一个入口、一份通用页面：方案只替换相表/策略/视图，不另开界面。
        top.addWidget(QLabel("方案"))
        self.preset_combo = QComboBox()
        self.preset_combo.setObjectName("insitu_preset_combo")
        self.preset_combo.addItem("通用（默认）", "generic")
        self.preset_combo.addItem("LMFP 三相", "lmfp")
        self.preset_combo.setToolTip(
            "通用 = 自己加相；LMFP 三相 = 用内置三相当场替换相表、策略和"
            "视图（页面不变）。方案只管相与拟合策略，数据目录与掩膜仍按"
            "项目方案/本页选择。")
        self.preset_combo.activated.connect(self._on_preset_changed)
        top.addWidget(self.preset_combo)
        self.save_scheme_btn = QPushButton("存方案…")
        self.save_scheme_btn.setToolTip(
            "把当前相表 + 策略 + 视图 + 仪器来源存成 .json 方案文件")
        self.save_scheme_btn.clicked.connect(self._save_scheme)
        top.addWidget(self.save_scheme_btn)
        self.load_scheme_btn = QPushButton("载方案…")
        self.load_scheme_btn.setToolTip(
            "载入方案文件并当场替换相表；载入过的方案会留在下拉里")
        self.load_scheme_btn.clicked.connect(self._load_scheme)
        top.addWidget(self.load_scheme_btn)
        top.addStretch(1)
        self.mask_label = _ElideLabel("掩膜：不遮", Qt.ElideMiddle)
        self.mask_label.setStyleSheet("color: gray;")
        self.mask_label.setMinimumWidth(170)
        self.mask_label.setMaximumWidth(260)
        # 必须用 Preferred：_ElideLabel 默认 Ignored，布局会把它的宽度
        # 算成 0，再被 minimumWidth 撑回 170，压到后面的按钮底下。
        self.mask_label.setSizePolicy(QSizePolicy.Preferred,
                                      QSizePolicy.Preferred)
        top.addWidget(self.mask_label)
        self.mask_btn = QPushButton("选掩膜…")
        self.mask_btn.clicked.connect(self._choose_mask)
        top.addWidget(self.mask_btn)
        self.mask_clear_btn = QPushButton("清除")
        self.mask_clear_btn.clicked.connect(self._clear_mask)
        top.addWidget(self.mask_clear_btn)
        root.addLayout(top)

        # 相表（全宽）：通用页面默认空表，LMFP 预设一键填三行。
        phase_row = QHBoxLayout()
        phase_row.setSpacing(6)
        self.phase_table = InsituPhaseTable()
        self.phase_table.setMaximumHeight(180)
        self.phase_table.cellDoubleClicked.connect(
            self._on_phase_double_clicked)
        phase_row.addWidget(self.phase_table, 1)
        phase_side = QVBoxLayout()
        phase_side.setSpacing(4)
        for text, slot, tip in (
                ("加 CIF…", self._add_phases, "加相（默认样品相）"),
                ("移除", self._remove_phase, "移除选中的相"),
                ("切换角色", self._toggle_phase_role,
                 "样品相 ↔ 参考相（参考相默认固定晶胞 + Le Bail + 不计入分数）"),
                ("改名…", self._rename_phase,
                 "改界面显示名与图内名称（图例、布拉格刻度行）"),
                ("细节…", self._edit_phase_details,
                 "每相细节：B、尺寸、应变、晶胞范围、跳变容差、颜色"),
                ("上移", lambda: self.phase_table.move_selected(-1),
                 "上移一位（第一条样品相走完整结构精修）"),
                ("下移", lambda: self.phase_table.move_selected(1),
                 "下移一位")):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            phase_side.addWidget(btn)
        phase_side.addStretch(1)
        phase_row.addLayout(phase_side)
        root.addLayout(phase_row)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(250)
        left_scroll.setMaximumWidth(340)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(2, 2, 8, 2)
        left_lay.setSpacing(6)

        left_lay.addWidget(_section("仪器"))
        # 仪器来源三选一（2026-11-19 手测：通用页原先只能跟着方案走，
        # 没有已保存基线时也切不到标样帧路径）。
        self.instrument_mode = QComboBox()
        self.instrument_mode.addItem("用已保存基线", "baseline")
        self.instrument_mode.addItem("标样帧现拟合", "lab6")
        self.instrument_mode.addItem("不固定（只精修零位）", "none")
        self.instrument_mode.setToolTip(
            "仪器峰形来源：已保存基线 = 之前存下的 LaB₆ 参数；"
            "标样帧现拟合 = 每次跑批先从标样图定仪器（LMFP 方案用）；"
            "不固定 = 只精修零位，峰宽全交给样品。")
        self.instrument_mode.currentIndexChanged.connect(
            self._on_instrument_mode_changed)
        left_lay.addLayout(_labeled_row("来源", self.instrument_mode))
        self.instrument_label = QLabel("—")
        self.instrument_label.setStyleSheet("color: gray;")
        self.instrument_label.setWordWrap(True)
        self.instrument_label.setMinimumWidth(0)
        self.instrument_label.setSizePolicy(QSizePolicy.Ignored,
                                            QSizePolicy.Preferred)
        left_lay.addWidget(self.instrument_label)
        self.lab6_btn = QPushButton("换标样帧…")
        self.lab6_btn.setToolTip("LMFP 方案用标样帧现拟合仪器峰形（zero/U/V/W/X/Y）")
        self.lab6_btn.clicked.connect(self._choose_lab6)
        left_lay.addWidget(self.lab6_btn)
        self.instrument_params_label = QLabel("")
        self.instrument_params_label.setStyleSheet("color: gray;")
        self.instrument_params_label.setWordWrap(True)
        self.instrument_params_label.setMinimumWidth(0)
        self.instrument_params_label.setSizePolicy(QSizePolicy.Ignored,
                                                   QSizePolicy.Preferred)
        left_lay.addWidget(self.instrument_params_label)

        # 背景（2026-11-19 B4）：原来写死"自动基线 0.2° + 0.25° 分段修正"，
        # 方案里虽然记着 background_model 却没有界面、引擎也没读。现在
        # 三个模型可选，默认仍是原来的自动基线（LMFP 金标准口径不变）。
        left_lay.addWidget(_section("背景"))
        self.bg_model = QComboBox()
        self.bg_model.addItem("自动基线", "estimated")
        self.bg_model.addItem("Chebyshev 联解", "chebyshev")
        self.bg_model.addItem("不扣背景", "none")
        self.bg_model.setToolTip(
            "自动基线：先估计一条背景再拟合（默认，最稳）；"
            "Chebyshev 联解：背景和峰一起解，适合背景起伏大的数据；"
            "不扣背景只适合背景已经扣干净的数据。")
        self.bg_model.currentIndexChanged.connect(self._sync_bg_controls)
        left_lay.addLayout(_labeled_row("模型", self.bg_model))
        self.bg_window = QDoubleSpinBox()
        self.bg_window.setRange(0.05, 2.0)
        self.bg_window.setSingleStep(0.05)
        self.bg_window.setDecimals(2)
        self.bg_window.setValue(0.2)
        self.bg_window.setSuffix(" °")
        self.bg_window.setToolTip(
            "自动基线的滑动窗口宽度：越小越贴峰底，越大越平缓")
        left_lay.addLayout(_labeled_row("窗口", self.bg_window))
        self.bg_order = QSpinBox()
        self.bg_order.setRange(2, 30)
        self.bg_order.setValue(10)
        self.bg_order.setToolTip(
            "Chebyshev 阶数：越高越灵活，也越容易和峰抢强度")
        left_lay.addLayout(_labeled_row("阶数", self.bg_order))
        self._sync_bg_controls()

        left_lay.addWidget(_section("参数"))
        self.max_rwp = QDoubleSpinBox()
        self.max_rwp.setRange(2.0, 50.0)
        self.max_rwp.setDecimals(1)
        self.max_rwp.setSingleStep(1.0)
        self.max_rwp.setValue(20.0)
        self.max_rwp.setSuffix(" %")
        left_lay.addLayout(_labeled_row("最大 Rwp", self.max_rwp))
        from xrd_toolkit.simulation.insitu import FIRST_NFEV, FRAME_NFEV
        self.first_nfev = QSpinBox()
        self.first_nfev.setRange(20, 2000)
        self.first_nfev.setValue(FIRST_NFEV)
        left_lay.addLayout(_labeled_row("首帧迭代", self.first_nfev))
        self.frame_nfev = QSpinBox()
        self.frame_nfev.setRange(10, 1000)
        self.frame_nfev.setValue(FRAME_NFEV)
        left_lay.addLayout(_labeled_row("后续帧迭代", self.frame_nfev))
        self.tth_min = QDoubleSpinBox()
        self.tth_min.setRange(0.0, 20.0)
        self.tth_min.setDecimals(2)
        self.tth_min.setSingleStep(0.1)
        self.tth_min.setValue(1.0)
        left_lay.addLayout(_labeled_row("2θ 下限", self.tth_min))
        self.tth_max = QDoubleSpinBox()
        self.tth_max.setRange(1.0, 40.0)
        self.tth_max.setDecimals(2)
        self.tth_max.setSingleStep(0.1)
        self.tth_max.setValue(8.0)
        left_lay.addLayout(_labeled_row("2θ 上限", self.tth_max))
        self.npt = QSpinBox()
        self.npt.setRange(200, 20000)
        self.npt.setSingleStep(100)
        self.npt.setValue(3000)
        left_lay.addLayout(_labeled_row("输出点数", self.npt))
        self.limit = QSpinBox()
        self.limit.setRange(0, 9999)
        self.limit.setSpecialValueText("全部")
        self.limit.setValue(0)
        self.limit.setToolTip("只跑前 N 帧；0 = 全部（先跑 3 帧看流程）")
        self.limit.valueChanged.connect(
            lambda _v: self._refresh_data_label())
        left_lay.addLayout(_labeled_row("帧数上限", self.limit))

        left_lay.addWidget(_section("输出"))
        self.out_label = QLabel(str(self.outdir))
        self.out_label.setStyleSheet("color: gray;")
        self.out_label.setWordWrap(True)
        self.out_label.setMinimumWidth(0)
        self.out_label.setSizePolicy(QSizePolicy.Ignored,
                                     QSizePolicy.Preferred)
        left_lay.addWidget(self.out_label)
        self.out_btn = QPushButton("选目录…")
        self.out_btn.clicked.connect(self._choose_outdir)
        left_lay.addWidget(self.out_btn)

        self.start_btn = QPushButton("开始")
        self.start_btn.clicked.connect(self._start)
        left_lay.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.setToolTip("当前帧算完后停，已完成的帧保留")
        self.stop_btn.clicked.connect(self._stop_run)
        left_lay.addWidget(self.stop_btn)
        self.appearance_btn = QPushButton("图外观…")
        self.appearance_btn.setToolTip(
            "改当前标签页的标题/轴标签/Y 轴对数/网格/范围；重画和保存都保留")
        self.appearance_btn.clicked.connect(self._edit_appearance)
        left_lay.addWidget(self.appearance_btn)
        self.save_btn = QPushButton("保存当前图…")
        self.save_btn.setToolTip(
            "保存当前标签页的图（趋势 / 汇总 / 单帧拟合 / 峰窗跟踪）")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save_view)
        left_lay.addWidget(self.save_btn)
        self.copy_report_btn = QPushButton("复制这一帧报告")
        self.copy_report_btn.setToolTip(
            "复制单帧拟合的完整参数报告（先在表格里双击一帧）")
        self.copy_report_btn.setEnabled(False)
        self.copy_report_btn.clicked.connect(self._copy_fit_report)
        left_lay.addWidget(self.copy_report_btn)
        hint = QLabel("双击表格行看单帧拟合；f_相 是相对散射强度分数，"
                      "看趋势用；wt 相% 是晶态相的 Rietveld 重量分数"
                      "（不计入分数的相单列）。多数帧只跑一次（种子收敛"
                      "就采用），通常几秒/帧，两相区更慢；晶胞只在相占"
                      "多数时可信。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        hint.setMinimumWidth(0)
        hint.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        left_lay.addWidget(hint)
        left_lay.addStretch(1)
        left_scroll.setWidget(left)
        split.addWidget(left_scroll)

        right = QSplitter(Qt.Vertical)
        self.view_tabs = QTabWidget()
        self.view_tabs.setObjectName("insitu_view_tabs")
        # figsize 与脚本产物一致：画布缩小显示，[保存当前图…] 仍按
        # 同一套尺寸/字体出报告图（2026-11-02）。
        self.fig = Figure(figsize=(11.0, 7.0))
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.summary_fig = Figure(figsize=(12.5, 8.0))
        self.summary_canvas = FigureCanvasQTAgg(self.summary_fig)
        self.fit_fig = Figure(figsize=(11.5, 7.6))
        self.fit_canvas = FigureCanvasQTAgg(self.fit_fig)
        self.mi_fig = Figure(figsize=(15.0, 4.6))
        self.mi_canvas = FigureCanvasQTAgg(self.mi_fig)
        for canvas in (self.canvas, self.summary_canvas,
                       self.fit_canvas, self.mi_canvas):
            canvas.setSizePolicy(QSizePolicy.Expanding,
                                 QSizePolicy.Expanding)
        self.view_tabs.addTab(self.canvas, "趋势")
        self.view_tabs.addTab(self.summary_canvas, "汇总")
        self.view_tabs.addTab(self.fit_canvas, "单帧拟合")
        self.view_tabs.addTab(self.mi_canvas, "峰窗跟踪")
        self.view_tabs.setCurrentIndex(0)
        self.view_tabs.currentChanged.connect(self._on_view_changed)
        right.addWidget(self.view_tabs)

        self.table = QTableWidget(0, len(self._table_keys))
        self.table.setHorizontalHeaderLabels(
            [_column_label(k) for k in self._table_keys])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setFrameShape(QFrame.NoFrame)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.table.setToolTip("双击一行看这一帧的三相拟合")
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        # 表头短名的图例（2026-11-19 手测：13 列时机器标签太长太挤）。
        self.table_legend = QLabel("")
        self.table_legend.setStyleSheet("color: gray;")
        self.table_legend.setWordWrap(True)
        self.table_legend.setMinimumWidth(0)
        self.table_legend.setSizePolicy(QSizePolicy.Ignored,
                                        QSizePolicy.Preferred)
        self.table_legend.setVisible(False)
        table_box = QWidget()
        table_lay = QVBoxLayout(table_box)
        table_lay.setContentsMargins(0, 0, 0, 0)
        table_lay.setSpacing(2)
        table_lay.addWidget(self.table_legend)
        table_lay.addWidget(self.table, 1)
        right.addWidget(table_box)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        right.setSizes([420, 220])
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([290, 760])

        # 底栏：状态文字 + 帧进度条（2026-11-17 用户："原位十几分钟只有
        # 一行字"）。总帧数一开始就知道，直接给 k/n 确定进度。
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)
        self.status = QLabel("选帧目录，点 [开始]")
        self.status.setStyleSheet("color: gray;")
        self.status.setSizePolicy(QSizePolicy.Ignored,
                                  QSizePolicy.Preferred)
        status_row.addWidget(self.status, 1)
        self.frame_progress = QProgressBar()
        self.frame_progress.setObjectName("insitu_progress")
        self.frame_progress.setFixedWidth(170)
        self.frame_progress.setMaximumHeight(14)
        self.frame_progress.setTextVisible(True)
        self.frame_progress.setFormat("%v/%m")
        self.frame_progress.setVisible(False)
        status_row.addWidget(self.frame_progress)
        root.addLayout(status_row)

    # ── 目录与文件 ──────────────────────────────────────────
    def _refresh_data_label(self):
        if self._use_checked:
            if self.frames:
                self.data_label.setText(f"文件栏勾选（{len(self.frames)} 帧）")
                self.data_label.setToolTip(
                    "\n".join(str(p) for p in self.frames))
            else:
                self.data_label.setText("（文件栏没有勾选原始数据）")
                self.data_label.setToolTip("")
            return
        if not self._data_dir_set:
            self.frames = []
            self.data_label.setText("（未选择帧目录）")
            self.data_label.setToolTip("")
            return
        limit = int(self.limit.value()) if hasattr(self, "limit") else 0
        try:
            self.frames = list(
                self._frames_in_dir(self.data_dir, limit, 1))
        except OSError:
            self.frames = []
        self.data_label.setText(
            f"{self.data_dir}（{len(self.frames)} 帧）")
        self.data_label.setToolTip(str(self.data_dir))

    # ── 方案（相表 + 策略 + 视图 + 仪器） ─────────────────────
    def _on_preset_changed(self, _index):
        """下拉选中 = 当场替换页面内容（不再另开界面）。"""
        if self.phase_table.dirty and not self.phase_table.is_empty():
            from PySide6.QtWidgets import QMessageBox
            answer = QMessageBox.question(
                self, "替换方案",
                "当前相表有未保存的修改，替换会丢掉这些改动。继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                self._sync_preset_combo()
                return
        # 正在跑批就先暂停（2026-11-18 用户："切就暂停"）：换方案会让
        # 在途帧回调落到新相表上，旧代码会在这里把界面卡死。
        self._pause_run("已暂停")
        data = self.preset_combo.currentData()
        if data == "generic":
            self._apply_generic_scheme()
        elif data == "lmfp":
            from xrd_toolkit.simulation.phase_set import lmfp_phase_set
            self._apply_phase_set(lmfp_phase_set())
        elif isinstance(data, tuple) and data[0] == "scheme":
            self._load_scheme_path(Path(data[1]))
        self._preset_index = self.preset_combo.currentIndex()

    def _sync_preset_combo(self):
        self.preset_combo.blockSignals(True)
        self.preset_combo.setCurrentIndex(self._preset_index)
        self.preset_combo.blockSignals(False)

    def _apply_generic_scheme(self):
        from xrd_toolkit.simulation.phase_set import (
            BatchPolicy, InstrumentSpec, ViewConfig)
        self._scheme_name = "通用多相"
        self._scheme_policy = BatchPolicy.generic()
        self._scheme_views = ViewConfig()
        self._scheme_instrument = InstrumentSpec(mode="baseline")
        self._scheme_adapter = ""
        self._preview_phase_set = None
        self.phase_table.load([])
        self._reset_run()
        self._set_table_columns([])
        self._apply_policy_to_controls()
        self._refresh_instrument_label()
        self._set_status("方案：通用（相表为空，先 [加 CIF…]）")

    def _apply_phase_set(self, pset):
        from xrd_toolkit.simulation.phase_set import spec_to_dict
        self._scheme_name = pset.name
        self._scheme_policy = pset.policy
        self._scheme_views = pset.views
        self._scheme_instrument = pset.instrument
        self._scheme_adapter = pset.adapter
        self._preview_phase_set = pset
        self.phase_table.load([spec_to_dict(p) for p in pset.phases])
        lab6 = str(pset.instrument.lab6_tif or "")
        self.lab6_path = (
            Path(lab6) if lab6 and Path(lab6).is_file() else None)
        self._reset_run()
        self._set_table_columns(list(pset.views.table_columns))
        self._apply_policy_to_controls()
        self._refresh_instrument_label()
        self._set_status(f"方案：{pset.name}（{len(pset.phases)} 相）")

    def _sync_bg_controls(self, *_args):
        """背景模型切换：窗口只对自动基线有意义，阶数只对 Chebyshev 有意义。"""
        model = self.bg_model.currentData() or "estimated"
        self.bg_window.setEnabled(model == "estimated")
        self.bg_order.setEnabled(model == "chebyshev")

    def _apply_policy_to_controls(self):
        self.max_rwp.setValue(float(self._scheme_policy.max_rwp))
        self.first_nfev.setValue(int(self._scheme_policy.first_nfev))
        self.frame_nfev.setValue(int(self._scheme_policy.frame_nfev))
        index = self.bg_model.findData(
            str(self._scheme_policy.background_model or "estimated"))
        self.bg_model.setCurrentIndex(max(0, index))
        self.bg_order.setValue(int(self._scheme_policy.background_order))
        self.bg_window.setValue(float(self._scheme_policy.background_window))
        self._sync_bg_controls()

    def _current_policy(self):
        from dataclasses import replace
        return replace(
            self._scheme_policy,
            max_rwp=float(self.max_rwp.value()),
            first_nfev=int(self.first_nfev.value()),
            frame_nfev=int(self.frame_nfev.value()),
            background_model=str(self.bg_model.currentData() or "estimated"),
            background_order=int(self.bg_order.value()),
            background_window=float(self.bg_window.value()))

    def _instrument_for_run(self):
        from dataclasses import replace
        if self._scheme_instrument.mode == "lab6":
            return replace(
                self._scheme_instrument,
                lab6_tif=str(self.lab6_path) if self.lab6_path else "")
        return self._scheme_instrument

    @staticmethod
    def _instrument_params_text(params):
        """zero/U/V/W/X/Y → 一行灰字（2026-11-18：和精修页一样能看见）。"""
        bits = []
        for name in ("zero", "u", "v", "w", "x", "y"):
            try:
                value = float((params or {}).get(name))
            except (TypeError, ValueError):
                continue
            if np.isfinite(value):
                bits.append(f"{name} {value:.4g}")
        return ("参数：" + "，".join(bits)) if bits else ""

    def _on_instrument_mode_changed(self, *_args):
        """左栏「来源」下拉：换仪器峰形来源（baseline/lab6/none）。"""
        from dataclasses import replace
        mode = str(self.instrument_mode.currentData() or "baseline")
        self._scheme_instrument = replace(self._scheme_instrument, mode=mode)
        self._refresh_instrument_label()
        if mode == "lab6" and self.lab6_path is None:
            self._set_status("标样帧现拟合：先点 [换标样帧…] 选一张标样图")

    def _refresh_instrument_label(self):
        mode = self._scheme_instrument.mode
        index = self.instrument_mode.findData(mode)
        if index >= 0 and index != self.instrument_mode.currentIndex():
            self.instrument_mode.blockSignals(True)
            self.instrument_mode.setCurrentIndex(index)
            self.instrument_mode.blockSignals(False)
        # 跑过的批次带着实际用上的参数；没跑时看方案来源。
        batch_instrument = getattr(self.batch, "instrument", None)
        if isinstance(batch_instrument, dict) and batch_instrument:
            self.instrument_params_label.setText(
                self._instrument_params_text(batch_instrument))
        elif mode == "lab6":
            self.instrument_params_label.setText("参数：运行准备后显示")
        else:
            self.instrument_params_label.setText("")
        if mode == "lab6":
            name = self.lab6_path.name if self.lab6_path else "未选"
            self.instrument_label.setText(f"标样帧现拟合：{name}")
            self.instrument_label.setToolTip(
                str(self.lab6_path) if self.lab6_path else
                "LMFP 方案需要一张 LaB₆ 标样帧来定仪器峰形")
            self.lab6_btn.setVisible(True)
            return
        self.lab6_btn.setVisible(False)
        self.instrument_label.setToolTip("")
        if mode == "none":
            self.instrument_label.setText("不固定（只精修零位）")
            return
        record = getattr(self.main, "instrument_baseline", None) or {}
        try:
            wavelength = float(record.get("wavelength"))
        except (TypeError, ValueError):
            wavelength = float("nan")
        try:
            current = float(
                get_config(self.main.config_name)["geometry"]["wavelength_m"]
            ) * 1e10
        except (KeyError, TypeError, ValueError):
            current = float("nan")
        if wavelength == wavelength and abs(wavelength - current) <= 0.01:
            self.instrument_label.setText(
                f"已保存基线（λ {wavelength:.4f} Å）")
            self.instrument_params_label.setText(
                self._instrument_params_text(record.get("params") or {}))
        else:
            self.instrument_label.setText("无可用基线（只精修零位）")

    def _sample_phase_list(self):
        pset = self._phase_set()
        phases = getattr(pset, "phases", ()) if pset is not None else ()
        out = []
        for phase in phases:
            is_sample = getattr(phase, "is_sample", None)
            if callable(is_sample) and not is_sample():
                continue
            out.append(phase)
        return out

    def _header_label(self, key, compact=False):
        """紧凑列用短表头（f1/wt1/a1/c1），完整名进 tooltip/图例。"""
        if compact:
            for index, phase in enumerate(self._sample_phase_list(), start=1):
                label = str(getattr(phase, "label", ""))
                if not label:
                    continue
                if key == f"f_{label}":
                    return f"f{index}"
                if key == f"wt_{label}":
                    return f"wt{index}%"
                if key == f"{label}_a":
                    return f"a{index}"
                if key == f"{label}_c":
                    return f"c{index}"
        return _column_label(key)

    def _update_table_legend(self, compact):
        """紧凑列表头短名的图例：P1/P2 分别是哪条样品相。"""
        legend = getattr(self, "table_legend", None)
        if legend is None:
            return
        phases = self._sample_phase_list() if compact else []
        if not phases:
            legend.setText("")
            legend.setVisible(False)
            return
        bits = []
        for index, phase in enumerate(phases, start=1):
            display = getattr(phase, "display", None)
            name = (display() if callable(display)
                    else (getattr(phase, "display_name", "")
                          or getattr(phase, "label", "")))
            bits.append(f"P{index} = {name}")
        legend.setText("；".join(bits))
        legend.setToolTip(
            "f = 相对散射强度分数，wt = Rietveld 重量分数，"
            "a / c = 晶胞轴长（Å）；表头悬停可看完整列名")
        legend.setVisible(True)

    def _set_table_columns(self, keys, *, compact=False):
        """结果表列：方案给了就按方案（LMFP 紧凑 12 列），空 = 基础列。"""
        keys = [str(k) for k in (keys or ())]
        self._table_keys = keys or ["frame", "file", "status", "rwp", "rp"]
        self.table.setColumnCount(len(self._table_keys))
        self.table.setHorizontalHeaderLabels(
            [self._header_label(k, compact) for k in self._table_keys])
        for col, key in enumerate(self._table_keys):
            item = self.table.horizontalHeaderItem(col)
            if item is not None:
                item.setToolTip(f"{_column_label(key)}（{key}）")
        self._update_table_legend(compact)

    def _rebuild_table_columns(self):
        """右键「诊断列」/取消：换列并重填已有行（2026-11-19）。"""
        keys = (self._all_keys if self._show_all_columns
                else (self._compact_keys or self._all_keys))
        compact = bool(self._auto_compact and not self._show_all_columns)
        self._set_table_columns(keys, compact=compact)
        rows = list(getattr(self.batch, "rows", []) or [])
        self.table.setRowCount(0)
        for row in rows:
            self._append_row(row)
        self._set_status(
            f"结果表：{'诊断列' if self._show_all_columns else '紧凑列'}"
            f"（{len(self._table_keys)} 列）")

    def _reset_run(self):
        self.batch = None
        self._batch_meta = None
        self._all_keys = []
        self._compact_keys = []
        self._auto_compact = False
        self._show_all_columns = False
        self.table.setRowCount(0)
        if hasattr(self, "table_legend"):
            self.table_legend.setVisible(False)
        self._clear_fit_view()
        self._mi_rows = None
        self._mi_len = -1
        self._appearances = [{}, {}, {}, {}]
        self._draw_trends()
        self._draw_summary()
        self._draw_model_independent()

    # ── 相表按钮 / 方案文件 ───────────────────────────────────
    def _add_phases(self):
        start = (paths.phase_library_dir()
                 if paths.phase_library_dir().is_dir()
                 else paths.structures_dir())
        files, _ = QFileDialog.getOpenFileNames(
            self, "加 CIF（默认样品相，可再切换角色）", str(start),
            "CIF (*.cif);;所有文件 (*)")
        added = self.phase_table.add_paths(files)
        if added:
            self._set_status(f"加了 {added} 相")

    def _remove_phase(self):
        if self.phase_table.remove_selected():
            self._set_status("移除了一相")

    def _toggle_phase_role(self):
        if self.phase_table.toggle_role():
            self._set_status("已切换角色（参考相默认固定晶胞 + Le Bail + 不进分母）")
        else:
            self._set_status("先点一下相名选中那一行，再点 [切换角色]")

    def _rename_phase(self):
        if not self.phase_table.rename_selected(self):
            return
        self._sync_phase_names()
        self._set_status("已改名：图例与布拉格刻度行跟着换")

    def _on_phase_double_clicked(self, row, col):
        if col == 1 and row >= 0:
            self._edit_phase_details()

    def _edit_phase_details(self):
        if not self.phase_table.edit_selected_details(self):
            return
        self._sync_phase_details()
        self._set_status("相细节已更新（数值下一次开始生效；颜色立即生效）")

    def _sync_phase_details(self):
        """颜色立即生效（重画视图）；其余数值等下一次 [开始] 才进批次。"""
        specs = {p["label"]: p for p in self.phase_table.phases()}
        for pset in (getattr(self.batch, "phase_set", None),
                     self._preview_phase_set):
            if pset is None:
                continue
            for phase in pset.phases:
                spec = specs.get(phase.label)
                if spec is not None:
                    phase.color = spec.get("color") or None
        self._draw_trends()
        self._draw_summary()
        self._draw_model_independent()

    def _sync_phase_names(self):
        """相表里的显示名/图内名 → 批次/预览方案，然后重画视图。"""
        names = {}
        for phase in self.phase_table.phases():
            label = str(phase.get("label") or "")
            if label:
                names[label] = (str(phase.get("display_name") or ""),
                                str(phase.get("plot_name") or ""))
        for pset in (getattr(self.batch, "phase_set", None),
                     self._preview_phase_set):
            if pset is None:
                continue
            for phase in pset.phases:
                pair = names.get(phase.label)
                if pair is not None:
                    phase.display_name, phase.plot_name = pair
        self._draw_trends()
        self._draw_summary()
        self._draw_model_independent()
        self._draw_fit()

    def _save_scheme(self):
        phases = self.phase_table.phases()
        if not phases:
            self._set_status("相表为空，没有可保存的方案")
            return
        start = str(Path.home() / "Data" / "XRD" / "insitu_scheme.json")
        name, _ = QFileDialog.getSaveFileName(
            self, "保存原位方案", start, "方案 (*.json);;所有文件 (*)")
        if not name:
            return
        path = Path(name)
        if path.suffix.lower() != ".json":
            path = path.with_suffix(".json")
        try:
            from xrd_toolkit.simulation.phase_set import (
                PhaseSet, save_phase_set, spec_from_path)
            specs = []
            for phase in phases:
                fields = {k: v for k, v in phase.items() if k != "path"}
                specs.append(spec_from_path(phase["path"], **fields))
            pset = PhaseSet(
                name=path.stem, phases=specs,
                policy=self._current_policy(), views=self._scheme_views,
                instrument=self._instrument_for_run(),
                adapter=self._scheme_adapter)
            save_phase_set(pset, path)
        except Exception as err:                    # noqa: BLE001
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "保存方案失败", str(err))
            return
        self.phase_table.dirty = False
        self._add_scheme_item(path, name=path.stem, select=True)
        self._set_status(f"方案已保存：{path}")
        _log(self.main, f"原位方案已保存：{path}")

    def _load_scheme(self):
        start = str(Path.home() / "Data" / "XRD")
        name, _ = QFileDialog.getOpenFileName(
            self, "载入原位方案", start, "方案 (*.json);;所有文件 (*)")
        if not name:
            return
        self._load_scheme_path(Path(name))

    def _load_scheme_path(self, path):
        self._pause_run("已暂停")
        try:
            from xrd_toolkit.simulation.phase_set import load_phase_set
            pset = load_phase_set(path)
        except Exception as err:                    # noqa: BLE001
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "载入方案失败", str(err))
            return
        self._apply_phase_set(pset)
        self._add_scheme_item(Path(path), name=Path(path).stem, select=True)
        self.phase_table.dirty = False
        self._set_status(
            f"已载入方案：{Path(path).name}（{len(pset.phases)} 相）")
        _log(self.main, f"原位方案已载入：{path}")

    def _add_scheme_item(self, path, name=None, select=False):
        key = ("scheme", str(path))
        idx = self.preset_combo.findData(key)
        if idx < 0:
            self.preset_combo.addItem(str(name or Path(path).stem), key)
            idx = self.preset_combo.count() - 1
        if select:
            self.preset_combo.blockSignals(True)
            self.preset_combo.setCurrentIndex(idx)
            self.preset_combo.blockSignals(False)
            self._preset_index = idx

    def _choose_data(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选择原位帧目录",
            str(self.data_dir) if self._data_dir_set else str(Path.home()))
        if not chosen:
            return
        self.data_dir = Path(chosen)
        self._data_dir_set = True
        self._use_checked = False
        self._refresh_data_label()
        self._set_status(f"已选目录：{len(self.frames)} 帧")

    def _use_checked_frames(self):
        """帧来源改成文件栏里勾选的原始数据（按文件名排序）。

        2026-11-11 用户："原位用的文件夹，是不是也可以用文件栏的数据"——
        可以：只认原始图（.tif），同几何/同形状由引擎逐帧积分时自己把关；
        1D/处理产物不进这条批量（三相引擎要的是原始帧）。
        """
        from xrd_toolkit.gui import sources as gui_sources
        picked = sorted(
            (Path(src.path) for src in gui_sources.checked_sources(self.main)
             if src.kind == gui_sources.RAW),
            key=lambda p: p.name)
        limit = int(self.limit.value()) if hasattr(self, "limit") else 0
        if limit > 0:
            picked = picked[:limit]
        self._use_checked = True
        self.frames = picked
        self._refresh_data_label()
        if picked:
            self._set_status(f"用文件栏勾选的原始数据：{len(picked)} 帧")
            _log(self.main, f"原位批量数据源：文件栏勾选 {len(picked)} 帧")
        else:
            self._set_status("文件栏里没有勾选原始数据（.tif）")

    def _choose_mask(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择掩膜文件", str(self.data_dir),
            "掩膜 (*.mask.npz *.npy *.edf);;所有文件 (*)")
        if not path:
            return
        self.mask_path = Path(path)
        self._refresh_mask_label()

    def _clear_mask(self):
        self.mask_path = None
        self._refresh_mask_label()

    def _choose_lab6(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 LaB₆ 标样帧", str(self.data_dir),
            "衍射图 (*.tif *.tiff *.edf *.cbf);;所有文件 (*)")
        if not path:
            return
        self.lab6_path = Path(path)
        self._refresh_instrument_label()

    def _choose_outdir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选择输出目录", str(self.outdir))
        if not chosen:
            return
        self.outdir = Path(chosen)
        self.out_label.setText(str(self.outdir))

    # ── 运行 ────────────────────────────────────────────────
    def _start(self):
        if self._task is not None:
            self._set_status("上一轮还在跑，请稍候")
            return
        self._refresh_data_label()
        if not self.frames:
            self._set_status("先选一个包含 .tif 的文件夹，或点 [用勾选的原始数据]")
            return
        phases = self.phase_table.phases()
        if not phases:
            self._set_status("相表是空的：先 [加 CIF…] 至少加一张相")
            return
        if not any(p.get("role") == ROLE_SAMPLE for p in phases):
            self._set_status("至少要有一相是「样品相」（不能全是参考相）")
            return
        if (self._scheme_instrument.mode == "lab6"
                and self.lab6_path is None):
            self._set_status("先选一张 LaB₆ 标样帧（用来定仪器峰形）")
            return
        self._stop = False
        self._index = 0
        self.batch = None
        self._batch_meta = {
            "data_dir": (str(self.data_dir)
                         if self._data_dir_set and not self._use_checked
                         else ""),
            "source": "checked" if self._use_checked else "folder",
            "mask": str(self.mask_path) if self.mask_path else "",
            "scheme": self._scheme_name,
            "phases": [str(p.get("path") or "") for p in phases],
            "lab6": str(self.lab6_path) if self.lab6_path else "",
        }
        self.table.setRowCount(0)
        self._set_table_columns(
            list(getattr(self._scheme_views, "table_columns", ()) or ()))
        self._clear_fit_view()
        self._mi_rows = None
        self._mi_len = -1
        self._appearances = [{}, {}, {}, {}]   # 四视图各自的图外观
        self.copy_report_btn.setEnabled(False)
        self.view_tabs.setCurrentIndex(0)
        self._draw_trends()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.save_btn.setEnabled(False)
        self._show_progress(len(self.frames), 0)
        if self._scheme_instrument.mode == "lab6":
            self._set_status("准备中（读结构 + LaB₆ 定仪器）…")
        else:
            self._set_status("准备中（读结构 + 仪器基线）…")
        where = ("文件栏勾选" if self._use_checked else str(self.data_dir))
        _log(self.main, f"原位批量：{where}（{len(self.frames)} 帧，"
                        f"方案「{self._scheme_name}」）")
        if self.mask_path is None:
            hint = self._suggested_mask_path()
            if hint is not None:
                _log(self.main,
                     f"原位批量：未选掩膜；{hint.name} 可用，建议选上"
                     "（热像素会明显抬高 Rwp）")

        task = None

        def done(batch):
            self.main._tasks.remove(task)
            self._task = None
            self.batch = batch
            self._on_prepared()

        def error(message):
            self.main._tasks.remove(task)
            self._task = None
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self._hide_progress()
            self._set_status(f"准备失败：{message}")
            _log(self.main, f"原位批量准备失败：{message}")

        from dataclasses import asdict
        policy = self._current_policy()
        scheme = {
            "version": 2,
            "name": self._scheme_name,
            "adapter": self._scheme_adapter,
            "policy": asdict(policy),
            "views": asdict(self._scheme_views),
            "instrument": asdict(self._instrument_for_run()),
            "phases": phases,
        }
        task = BackgroundTask(
            _prepare_worker, self.main.config_name, scheme,
            (str(self.mask_path) if self.mask_path else None),
            getattr(self.main, "instrument_baseline", None),
            float(policy.max_rwp), int(policy.first_nfev),
            int(policy.frame_nfev), int(self.npt.value()),
            float(self.tth_min.value()), float(self.tth_max.value()),
            on_done=done, on_error=error)
        self._task = task
        self.main._tasks.append(task)
        task.start()

    def _on_prepared(self):
        self.outdir.mkdir(parents=True, exist_ok=True)
        # 列：方案指定了就用方案的顺序；没指定用通用紧凑列（2026-11-19：
        # 引擎 41 列全塞进表里太挤）；右键可以切回全部列。
        try:
            all_keys = list(self.batch.columns())
        except AttributeError:                 # 测试里的假 batch
            all_keys = []
        wanted = list(getattr(
            getattr(getattr(self.batch, "phase_set", None), "views", None),
            "table_columns", ()) or ())
        scheme_keys = ([k for k in wanted if k in all_keys] if all_keys
                       else wanted)
        phases = getattr(
            getattr(self.batch, "phase_set", None), "phases", ())
        auto = ([] if scheme_keys
                else compact_table_columns(all_keys, phases))
        self._all_keys = list(all_keys)
        self._compact_keys = scheme_keys or auto
        self._auto_compact = bool(auto)
        self._show_all_columns = False
        self._set_table_columns(self._compact_keys or all_keys,
                                compact=self._auto_compact)
        self._refresh_instrument_label()
        self._set_status("已就绪，开始逐帧精修…")
        self._start_next()

    def _start_next(self):
        if self._stop:
            self._finish("已停止")
            return
        if self._index >= len(self.frames):
            self._finish("完成")
            return
        path = self.frames[self._index]
        self._set_progress(self._index)   # 已完成帧数（本帧正在算）
        self._set_status(
            f"第 {self._index + 1}/{len(self.frames)} 帧：{path.name}")
        task = None

        def done(row):
            self.main._tasks.remove(task)
            self._task = None
            self._on_frame(row)

        def error(message):
            self.main._tasks.remove(task)
            self._task = None
            self._set_status(f"第 {self._index + 1} 帧失败：{message}")
            _log(self.main, f"原位批量第 {self._index + 1} 帧失败：{message}")
            self._finish("出错停止")

        task = BackgroundTask(
            self.batch.fit_frame, path, on_done=done, on_error=error)
        self._task = task
        self.main._tasks.append(task)
        task.start()

    def _on_frame(self, row):
        self._index += 1
        self._set_progress(self._index)
        self._append_row(row)
        # 任意新帧都会让模型无关缓存失效；趋势图始终跟着更新
        self._mi_rows = None
        self._mi_len = -1
        self._appearances = [{}, {}, {}, {}]   # 四视图各自的图外观
        self._draw_trends()
        if self.view_tabs.currentIndex() != 0:
            self._refresh_active_view()
        self._write_csv()
        label = self._main_label()
        fraction = row.get(f"f_{label}", row.get("f_rich", float("nan")))
        weight = row.get(f"wt_{label}", row.get("wt_rich", float("nan")))
        self._set_status(
            f"第 {row['frame']}/{len(self.frames)} 帧：{row['status']}  "
            f"Rwp {_fmt(row['rwp'])}%  f_{label} {_fmt(fraction)}  "
            f"wt {_fmt(weight, 1)}%" + _start_suffix(row))
        self._start_next()

    def _stop_run(self):
        self._stop = True
        self.stop_btn.setEnabled(False)
        self._set_status("正在停止（当前帧算完后停）…")

    def _pause_run(self, reason="已暂停"):
        """暂停跑批（2026-11-18）：丢弃在途回调，界面立刻恢复可操作。

        换方案/载入方案时调用。已完成的帧先写一次 CSV——换方案会清表，
        但产物目录里留着这一轮的进度。正在跑的那一帧照常算完，结果丢弃
        （回调已清空），不会再往新相表里塞旧方案的行，也不会把界面卡在
        "停止按钮亮着但点不动"的状态。
        """
        task = self._task
        if task is None:
            return False
        self._task = None
        self._stop = False
        task.abandon()
        try:
            self.main._tasks.remove(task)   # 收尾回调已被丢弃，这里自己摘账
        except ValueError:
            pass
        wrote = bool(self.batch is not None and self.batch.rows)
        if wrote:
            self._write_csv()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._hide_progress()
        detail = "已完成的帧先写入 CSV" if wrote else "在途计算的结果丢弃"
        self._set_status(f"{reason}：{detail}")
        _log(self.main, f"原位批量{reason}（换方案）：{detail}")
        return True

    def _finish(self, reason):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self._task = None
        self._hide_progress()
        if self.batch is not None and self.batch.rows:
            self._write_csv()
            try:
                from xrd_toolkit.simulation.insitu import write_summary
                write_summary(self.outdir, self.batch.rows,
                              self.batch.mask is not None,
                              phase_set=self._phase_set())
            except OSError as err:                 # noqa: BLE001
                _log(self.main, f"摘要写入失败：{user_error_text(err)}")
        usable = (sum(1 for r in (self.batch.rows if self.batch else [])
                      if r.get("usable")))
        total = len(self.batch.rows) if self.batch else 0
        self.save_btn.setEnabled(total > 0)
        self._set_status(f"{reason}：{total} 帧，usable {usable}")
        _log(self.main, f"原位批量{reason}：{total} 帧，usable {usable}")

    # ── 结果表与趋势图 ──────────────────────────────────────
    def _append_row(self, row):
        table = self.table
        i = table.rowCount()
        table.insertRow(i)
        status = str(row.get("status", ""))
        note = _dual_start_note(row)
        for col, key in enumerate(self._table_keys):
            item = QTableWidgetItem(_cell_text(key, row.get(key, "")))
            if key not in ("file", "status"):
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if key == "status" and (status.startswith("error")
                                    or status.startswith("high_rwp")):
                item.setForeground(QBrush(QColor("#c0392b")))
            elif key == "status" and status != "ok":
                item.setForeground(QBrush(QColor("#b7791f")))
            if note and key in ("rwp", "status"):
                item.setToolTip(note)
            table.setItem(i, col, item)
        table.scrollToBottom()

    # ── 四个视图：趋势 / 汇总 / 单帧拟合 / 模型无关 ──────────
    def _on_view_changed(self, _index):
        self._refresh_active_view()

    def _active_fig(self):
        index = self.view_tabs.currentIndex()
        return [self.fig, self.summary_fig, self.fit_fig,
                self.mi_fig][max(0, min(3, index))]

    def _edit_appearance(self):
        index = max(0, min(3, self.view_tabs.currentIndex()))
        opts = edit_figure_appearance(
            self, self._active_fig(), self._appearances[index])
        if opts is None:
            return
        self._appearances[index] = opts
        self._refresh_active_view()
        self._set_status("图外观已应用（保存当前图也用这套外观）")

    def _refresh_active_view(self):
        index = self.view_tabs.currentIndex()
        if index == 0:
            self._draw_trends()
        elif index == 1:
            self._draw_summary()
        elif index == 2:
            self._draw_fit()
        else:
            self._draw_model_independent()

    def _phase_set(self):
        """视图用的方案：真实批次自带；没有就用手上这份预览。"""
        return (getattr(self.batch, "phase_set", None)
                or getattr(self, "_preview_phase_set", None))

    def _bragg_labels(self):
        """机器标签 → 图内显示名（布拉格刻度行用，2026-11-18）。"""
        labels = {}
        pset = self._phase_set()
        if pset is not None:
            for phase in pset.phases:
                labels[str(phase.label)] = phase.plot_label()
            return labels
        for phase in self.phase_table.phases():
            label = str(phase.get("label") or "")
            if not label:
                continue
            labels[label] = str(
                phase.get("plot_name") or phase.get("display_name")
                or label)
        return labels

    def _main_label(self):
        """结构精修相（第一条样品相）的标签：状态行/单帧标题用它。"""
        pset = self._phase_set()
        if pset is not None and pset.sample_phases():
            return pset.sample_phases()[0].label
        for phase in self.phase_table.phases():
            if phase.get("role") == ROLE_SAMPLE:
                return phase.get("label") or "phase"
        return "phase"

    def _draw_trends(self):
        from xrd_toolkit.simulation.insitu_multi_plots import (
            draw_multi_trends)
        rows = (self.batch.rows if self.batch is not None else [])
        draw_multi_trends(self.fig, rows, self._phase_set())
        apply_figure_appearance(self.fig, self._appearances[0])
        self.canvas.draw_idle()

    def _draw_summary(self):
        from xrd_toolkit.simulation.insitu_multi_plots import (
            draw_multi_summary)
        rows = (self.batch.rows if self.batch is not None else [])
        draw_multi_summary(self.summary_fig, rows, self._phase_set())
        apply_figure_appearance(self.summary_fig, self._appearances[1])
        self.summary_canvas.draw_idle()

    def _fit_title(self):
        total = len(self.frames) or self._fit_frame
        label = getattr(self, "_fit_main_label", "phase")
        return (f"{self._fit_file} — frame {self._fit_frame}/{total}, "
                f"f_{label}={_fmt(self._fit_frich)}, "
                f"Rwp={self._fit_res.rwp:.2f}% ({self._fit_status})")

    def _draw_fit(self):
        from xrd_toolkit.simulation.plots import draw_fit
        self.fit_fig.clear()
        if self._fit_res is None:
            ax = self.fit_fig.add_subplot(111)
            ax.set_axis_off()
            ax.text(0.5, 0.55, "Single-frame fit", ha="center", va="center",
                    fontsize=15, color="#666666", transform=ax.transAxes)
            ax.text(0.5, 0.44, "Double-click a table row to see that frame",
                    ha="center", va="center", fontsize=10,
                    color="#888888", transform=ax.transAxes)
        else:
            draw_fit(self.fit_fig, self._fit_res, self._fit_title(),
                     bragg="auto", bragg_labels=self._bragg_labels())
        apply_figure_appearance(self.fit_fig, self._appearances[2])
        self.fit_canvas.draw_idle()

    def _draw_model_independent(self):
        """峰窗跟踪（原模型无关 (200) 检查的通用版）。"""
        from xrd_toolkit.simulation.insitu_multi_plots import (
            draw_window_tracker)
        rows = (self.batch.rows if self.batch is not None else [])
        draw_window_tracker(self.mi_fig, rows, self._phase_set())
        apply_figure_appearance(self.mi_fig, self._appearances[3])
        self.mi_canvas.draw_idle()

    def _clear_fit_view(self):
        self._fit_frame = None
        self._fit_res = None
        self._fit_status = ""
        self._fit_file = ""
        self._fit_frich = float("nan")
        self.copy_report_btn.setEnabled(False)
        self._draw_fit()

    def _frame_entry(self, frame):
        if self.batch is None:
            return None
        # 快照：后台线程可能正往 fitted 追加当前帧
        for entry in list(self.batch.fitted):
            if entry[0] == frame:
                return entry
        return None

    def _show_frame_fit(self, frame):
        entry = self._frame_entry(frame)
        if entry is None:
            self._set_status(f"第 {frame} 帧没有拟合结果（看状态列）")
            return
        i, path, res, fraction, status = entry
        self._fit_frame = i
        self._fit_res = res
        self._fit_status = status
        self._fit_file = Path(path).name
        self._fit_frich = float(fraction)
        self._fit_main_label = self._main_label()
        self.copy_report_btn.setEnabled(True)
        self.view_tabs.setCurrentWidget(self.fit_canvas)
        self._draw_fit()
        record = next((r for r in (self.batch.rows if self.batch else [])
                       if int(r.get("frame", -1)) == int(i)), {})
        self._set_status(
            f"第 {i} 帧：{status}  Rwp {res.rwp:.2f}%  "
            f"f_{self._fit_main_label} {_fmt(fraction)}"
            + _start_suffix(record))

    def _on_cell_double_clicked(self, row, _col):
        if self.batch is None or row < 0 or row >= len(self.batch.rows):
            return
        self._show_frame_fit(self.batch.rows[row]["frame"])

    def _copy_fit_report(self):
        if self._fit_res is None:
            self._set_status("先在表格里双击一帧")
            return
        QApplication.clipboard().setText(self._fit_res.report())
        self._set_status("已复制单帧拟合报告")
        _log(self.main, f"已复制第 {self._fit_frame} 帧拟合报告")

    # ── 导出与复制 ──────────────────────────────────────────
    @property
    def csv_path(self):
        return Path(self.outdir) / "insitu_fit.csv"

    def _write_csv(self):
        rows = (self.batch.rows if self.batch is not None else [])
        if not rows:
            return
        try:
            Path(self.outdir).mkdir(parents=True, exist_ok=True)
            with open(self.csv_path, "w", newline="",
                      encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        except OSError as err:                     # noqa: BLE001
            _log(self.main, f"CSV 写入失败：{user_error_text(err)}")

    def _save_view(self):
        """按脚本同尺寸/同布局重画一张再存——画布会被 Qt 拉成窗口大小，
        直接存画布会得到低分辨率、挤在一起的图（2026-11-02 实测）。"""
        from xrd_toolkit.simulation.insitu_multi_plots import (
            draw_multi_summary, draw_multi_trends, draw_window_tracker)
        from xrd_toolkit.simulation.plots import draw_fit

        index = self.view_tabs.currentIndex()
        if index == 0:
            if self.batch is None or not self.batch.rows:
                self._set_status("还没有趋势图")
                return
            fig = Figure(figsize=(11.0, 7.0))
            draw_multi_trends(fig, self.batch.rows, self._phase_set())
            apply_figure_appearance(fig, self._appearances[0])
            name = "insitu_trends"
        elif index == 1:
            if self.batch is None or not self.batch.rows:
                self._set_status("还没有汇总图")
                return
            fig = Figure(figsize=(12.5, 8.0))
            draw_multi_summary(fig, self.batch.rows, self._phase_set())
            apply_figure_appearance(fig, self._appearances[1])
            name = "insitu_summary"
        elif index == 2:
            if self._fit_res is None:
                self._set_status("先在表格里双击一帧")
                return
            fig = Figure(figsize=(11.5, 7.6))
            draw_fit(fig, self._fit_res, self._fit_title(), bragg="auto",
                     bragg_labels=self._bragg_labels())
            apply_figure_appearance(fig, self._appearances[2])
            name = f"insitu_fit_frame_{self._fit_frame:03d}"
        else:
            if self.batch is None or not self.batch.rows:
                self._set_status("还没有峰窗数据")
                return
            fig = Figure(figsize=(12.0, 8.0))
            draw_window_tracker(fig, self.batch.rows, self._phase_set())
            apply_figure_appearance(fig, self._appearances[3])
            name = "insitu_peak_windows"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        saved = save_figure_with_options(
            self.main, fig, f"{name}_{stamp}", title="保存当前图",
            default_dir=self.outdir)
        if saved is not None:
            self._set_status(f"已保存 {saved.name}")

    def _table_tsv(self, selected_only=False):
        model = self.table.selectionModel()
        rows = (sorted({i.row() for i in model.selectedRows()})
                if selected_only and model is not None
                else list(range(self.table.rowCount())))
        lines = ["\t".join(
            (self.table.horizontalHeaderItem(col).text()
             if self.table.horizontalHeaderItem(col) is not None
             else str(col))
            for col in range(self.table.columnCount()))]
        for row in rows:
            values = []
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                values.append(item.text() if item is not None else "")
            lines.append("\t".join(values))
        return "\n".join(lines)

    def _copy_table(self, selected_only=False):
        text = self._table_tsv(selected_only)
        n = max(0, len(text.splitlines()) - 1)
        if n == 0:
            self._set_status("结果表没有可复制的行")
            return 0
        QApplication.clipboard().setText(text)
        _log(self.main, f"已复制原位结果 {n} 行")
        return n

    def _table_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            row = self.table.currentRow()
        frame = None
        if (self.batch is not None and 0 <= row < len(self.batch.rows)):
            frame = self.batch.rows[row]["frame"]
        has_fit = frame is not None and self._frame_entry(frame) is not None

        menu = QMenu(self.table)
        act_view = menu.addAction("查看这一帧拟合")
        act_save = menu.addAction("保存这一帧拟合图…")
        act_report = menu.addAction("复制这一帧报告")
        act_view.setEnabled(has_fit)
        act_save.setEnabled(has_fit)
        act_report.setEnabled(has_fit)
        menu.addSeparator()
        act_sel = menu.addAction("复制选中行")
        act_all = menu.addAction("复制全部")
        menu.addSeparator()
        act_cols = menu.addAction("诊断列（esd / 计时 / 起点）")
        act_cols.setCheckable(True)
        act_cols.setChecked(bool(self._show_all_columns))
        act_cols.setEnabled(bool(self._all_keys))
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act_view:
            self._show_frame_fit(frame)
        elif chosen is act_save:
            self._show_frame_fit(frame)
            if self._fit_res is not None:
                self._save_view()
        elif chosen is act_report:
            self._show_frame_fit(frame)
            self._copy_fit_report()
        elif chosen is act_sel:
            self._copy_table(True)
        elif chosen is act_all:
            self._copy_table(False)
        elif chosen is act_cols:
            self._show_all_columns = bool(act_cols.isChecked())
            self._rebuild_table_columns()

    def _set_status(self, text):
        self.status.setText(text)

    # ── 帧进度条（2026-11-17）───────────────────────────────
    def _show_progress(self, total: int, value: int = 0) -> None:
        bar = getattr(self, "frame_progress", None)
        if bar is None:
            return
        bar.setRange(0, max(1, int(total)))
        bar.setValue(int(value))
        bar.setVisible(True)

    def _set_progress(self, value: int) -> None:
        bar = getattr(self, "frame_progress", None)
        if bar is not None and bar.isVisible():
            bar.setValue(int(value))

    def _hide_progress(self) -> None:
        bar = getattr(self, "frame_progress", None)
        if bar is not None:
            bar.setVisible(False)


def _section(text):
    label = QLabel(text)
    label.setStyleSheet("color: gray; font-weight: 600;")
    return label


def _labeled_row(text, widget):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)
    row.addWidget(QLabel(text))
    row.addWidget(widget, 1)
    return row


class _InSituSubWindow(QMdiSubWindow):
    """原位工作台的中央子窗口：× 关闭 = 退出原位模式，内容不销毁。"""

    def __init__(self, window):
        super().__init__()
        self._window = window
        self.panel_key = "InSitu|workbench"

    def closeEvent(self, event):
        window = self._window
        if (not getattr(window, "_closing", False)
                and getattr(window, "insitu_btn", None) is not None
                and window.insitu_btn.isChecked()):
            window.insitu_btn.setChecked(False)    # toggled → 退出原位模式
        event.ignore()
        self.hide()
