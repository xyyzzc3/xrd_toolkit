"""相集合（PhaseSpec）/ 批处理策略 / 视图配置（2026-11-17）。

原位工作台要从"LMFP 三相专用"走向"任意 N 相"，先把三件事从引擎里
拆出来：

  * PhaseSpec —— 一相的结构、机器标签、显示名、角色（样品相/参考相）、
    晶胞/强度/是否计入样品分数；
  * BatchPolicy —— 种子、双起点、pending 重锁、每相跳变容差等策略；
  * ViewConfig —— 趋势图的分数对/交叉线、峰窗跟踪窗口、可选标注。

LMFP 三相只是一份把上述字段填好的预设（`lmfp_phase_set()`），通用
多相是同一结构的空白预设。引擎/界面只认这些数据结构，不再把
rich/poor/Al 写死在代码里。

注意：PhaseSpec 是"界面/批处理层"的数据结构；底层 `fit_pattern` 仍以
"一个主结构 + extra_phases" 实现。让所有样品相对等（每相自己的晶胞、
B、峰宽、坐标）是后续 L2 内核的事，本模块先把接口稳定下来。
"""
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from xrd_toolkit import paths
from xrd_toolkit.simulation import load_structure

# 角色
SAMPLE = "sample"
REFERENCE = "reference"
ROLE_LABELS = {SAMPLE: "样品相", REFERENCE: "参考相"}


@dataclass
class PhaseSpec:
    """一相的全部输入（结构对象在批处理里常驻，路径只作溯源）。"""

    structure: object
    label: str                       # 机器键：参数名/CSV 列
    path: Path | None = None
    display_name: str = ""           # 界面/CSV 显示名
    plot_name: str = ""              # 图内英文名（论文图默认英文）
    role: str = SAMPLE
    cell_free: bool = True
    width_free: bool = True          # main 相 size/mustrain 是否精修
    intensity: str = "structure"     # structure | lebail
    include_in_fraction: bool = True
    b_iso: float = 0.0
    size_um: float = 1e4
    mustrain: float = 0.0
    cell_bounds: dict = field(default_factory=dict)   # {"a": (lo,hi), ...}
    jump_tol: float | None = None
    color: str | None = None
    # 初始/固定晶胞（None = 用 CIF）；Al 参考相用实测 4.061
    cell0: tuple | None = None
    # L2 预留：每相自由参数（当前 fit_pattern 的 extra 路径不支持时忽略）
    free_extra: tuple = ()

    @property
    def lebail(self) -> bool:
        return self.intensity == "lebail"

    @lebail.setter
    def lebail(self, on):
        self.intensity = "lebail" if on else "structure"

    def display(self) -> str:
        return self.display_name or self.label

    def plot_label(self) -> str:
        return self.plot_name or self.display_name or self.label

    def is_sample(self) -> bool:
        return self.role == SAMPLE

    def is_reference(self) -> bool:
        return self.role == REFERENCE


@dataclass
class BatchPolicy:
    """批处理策略。LMFP 预设打开全部稳健项；通用预设默认最简。"""

    dual_start: str = "off"           # off | auto | always
    pending_frames: int = 0           # >0 = 连续 N 帧同新晶胞才重锁
    pending_tol: float = 0.02
    tie_rwp: float = 0.05
    max_rwp: float = 20.0
    cell_jump_default: float = 0.30
    first_nfev: int = 120
    frame_nfev: int = 120
    xtol: float = 1e-6
    ftol: float = 1e-6
    gtol: float = 1e-6
    background_model: str = "estimated"
    background_order: int = 10
    intensity_poly: int = 0
    joint_stage: bool = False      # True = 所有非线性参数一个联合阶段

    @classmethod
    def lmfp(cls):
        return cls(dual_start="auto", pending_frames=2, pending_tol=0.02,
                   tie_rwp=0.05, max_rwp=20.0, cell_jump_default=0.30,
                   first_nfev=120, frame_nfev=120,
                   xtol=1e-6, ftol=1e-6, gtol=1e-6,
                   background_model="estimated", intensity_poly=0,
                   joint_stage=True)

    @classmethod
    def generic(cls):
        return cls(dual_start="off", pending_frames=0,
                   cell_jump_default=0.30,
                   first_nfev=120, frame_nfev=120,
                   xtol=1e-6, ftol=1e-6, gtol=1e-6)


@dataclass
class PeakWindow:
    """峰窗跟踪：一个 2θ 窗口里的模型无关指标（质心/面积/峰高）。"""

    name: str
    tth_min: float
    tth_max: float
    vs_phase: str | None = None       # 可与哪个 sample 相的分数画相关
    side_lo: tuple | None = None      # 两侧取背景的平带（默认窗口边缘）
    side_hi: tuple | None = None


@dataclass
class ViewConfig:
    """趋势/汇总视图的 LMFP 专属配置；通用预设留空。"""

    crossover_pair: tuple | None = None   # (label_a, label_b)，50/50 线
    peak_windows: list = field(default_factory=list)
    annotations: list = field(default_factory=list)  # [(start,end,label)]
    shade_phase: str | None = None    # 用该相分数最低点分充/放电底色
    show_wt: bool = False
    fraction_order: tuple = ()        # 堆叠面积顺序；空 = PhaseSpec 顺序


@dataclass
class PhaseSet:
    """一份原位预设：相 + 策略 + 视图配置。"""

    name: str
    phases: list
    policy: BatchPolicy = field(default_factory=BatchPolicy.generic)
    views: ViewConfig = field(default_factory=ViewConfig)

    def sample_phases(self) -> list:
        return [p for p in self.phases if p.is_sample()]

    def reference_phases(self) -> list:
        return [p for p in self.phases if p.is_reference()]

    def labels(self) -> list:
        return [p.label for p in self.phases]


def _safe_label(path, used):
    import re
    base = re.sub(r"[^0-9A-Za-z_]", "_", Path(path).stem) or "phase"
    label = base
    i = 2
    while label in used:
        label = f"{base}_{i}"
        i += 1
    used.add(label)
    return label


def spec_from_path(path, *, label=None, display_name="", plot_name="",
                   role=SAMPLE,
                   cell_free=True, width_free=True, intensity="structure",
                   include_in_fraction=True, b_iso=None,
                   size_um=1e4, mustrain=0.0, cell_bounds=None,
                   jump_tol=None, color=None, free_extra=()):
    """读一张 CIF → PhaseSpec（label 不给就用文件名洗出来）。"""
    path = Path(path)
    structure = load_structure(str(path))
    if b_iso is None:
        from xrd_toolkit.simulation import b_iso_from_cif
        b_iso = b_iso_from_cif(str(path)) or 0.0
    used = set()
    spec = PhaseSpec(
        structure=structure, label=label or _safe_label(path, used),
        path=path, display_name=display_name or "",
        plot_name=plot_name or "", role=role,
        cell_free=bool(cell_free), width_free=bool(width_free),
        intensity=str(intensity),
        include_in_fraction=bool(include_in_fraction), b_iso=float(b_iso),
        size_um=float(size_um), mustrain=float(mustrain),
        cell_bounds=dict(cell_bounds or {}),
        jump_tol=jump_tol, color=color,
        free_extra=tuple(free_extra or ()))
    return spec


def generic_phase_set(paths_list, *, name="通用多相"):
    """任意 CIF 列表 → PhaseSet（全部按样品相，用户可再改角色）。"""
    used = set()
    specs = []
    for path in paths_list:
        path = Path(path)
        structure = load_structure(str(path))
        specs.append(PhaseSpec(
            structure=structure, label=_safe_label(path, used),
            path=path, display_name="", role=SAMPLE))
    return PhaseSet(name=name, phases=specs, policy=BatchPolicy.generic(),
                    views=ViewConfig())


def lmfp_phase_set(rich_path=None, poor_path=None, al_path=None):
    """内置「LMFP 三相」预设：rich/poor 样品相 + Al 参考相。"""
    from xrd_toolkit.simulation.insitu import (
        AL_A, CELL_BOUNDS, DEFAULT_AL, DEFAULT_POOR, DEFAULT_RICH)
    rich = spec_from_path(
        rich_path or DEFAULT_RICH, label="rich", display_name="富锂相",
        plot_name="Li-rich phase", role=SAMPLE,
        cell_free=True, width_free=False, intensity="structure",
        include_in_fraction=True,
        b_iso=0.0, size_um=0.093, mustrain=0.0,
        cell_bounds={k: CELL_BOUNDS[k] for k in ("a", "b", "c")},
        jump_tol=0.12, color="#1f5fa8")
    poor = spec_from_path(
        poor_path or DEFAULT_POOR, label="poor", display_name="贫锂相",
        plot_name="Li-poor phase", role=SAMPLE,
        cell_free=True, width_free=False, intensity="structure",
        include_in_fraction=True,
        b_iso=0.5, size_um=0.1, mustrain=0.0,
        cell_bounds={k: CELL_BOUNDS[f"LMFP_poor:{k}"]
                     for k in ("a", "b", "c")},
        jump_tol=0.25, color="#e08a1e")
    al = spec_from_path(
        al_path or DEFAULT_AL, label="Al", display_name="Al 参考相",
        plot_name="Al reference", role=REFERENCE,
        cell_free=False, intensity="lebail", include_in_fraction=False,
        b_iso=0.5, size_um=0.1, mustrain=0.0, jump_tol=0.30,
        color="#2a9d8f")
    al.cell0 = (AL_A, AL_A, AL_A)   # 与 LMFP 引擎一致，固定参考
    views = ViewConfig(
        crossover_pair=("rich", "poor"), shade_phase="rich",
        peak_windows=[PeakWindow("(200) doublet", 1.18, 1.55,
                                 vs_phase="poor",
                                 side_lo=(1.18, 1.28),
                                 side_hi=(1.50, 1.56))],
        show_wt=True, fraction_order=("rich", "poor"))
    return PhaseSet(name="LMFP 三相", phases=[rich, poor, al],
                    policy=BatchPolicy.lmfp(), views=views)


# ── 方案文件（2026-11-17）：PhaseSet/ViewConfig 持久化 ──────────────
def phase_set_to_dict(phase_set):
    """序列化为 JSON 友好 dict；结构只存 CIF 路径，不内嵌原子坐标。"""
    out = {
        "version": 1,
        "name": phase_set.name,
        "policy": asdict(phase_set.policy),
        "views": asdict(phase_set.views),
        "phases": [],
    }
    for phase in phase_set.phases:
        item = {f.name: getattr(phase, f.name) for f in fields(phase)
                if f.name != "structure"}
        item["path"] = str(phase.path)
        out["phases"].append(item)
    return out


def phase_set_from_dict(data):
    """从方案文件重建 PhaseSet；CIF 重新读（路径必须存在）。"""
    from xrd_toolkit.simulation import load_structure
    phases = []
    for item in data.get("phases") or []:
        path = Path(str(item.get("path", ""))).expanduser()
        if not path.is_file():
            raise ValueError(f"方案里的 CIF 不存在：{path}")
        values = {f.name: item[f.name] for f in fields(PhaseSpec)
                  if f.name in item and f.name not in ("structure", "path")}
        values["path"] = path
        values["structure"] = load_structure(str(path))
        phases.append(PhaseSpec(**values))
    policy = BatchPolicy(**(data.get("policy") or {}))
    views_raw = dict(data.get("views") or {})
    windows = [PeakWindow(**w) for w in views_raw.pop("peak_windows", [])]
    views = ViewConfig(peak_windows=windows, **views_raw)
    return PhaseSet(name=str(data.get("name") or "insitu 方案"),
                    phases=phases, policy=policy, views=views)


def save_phase_set(phase_set, path):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(phase_set_to_dict(phase_set),
                               ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load_phase_set(path):
    data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    return phase_set_from_dict(data)
