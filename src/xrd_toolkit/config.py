import json
import sys
from pathlib import Path

# 项目根目录：基于本文件位置定位，无论从哪里启动程序都能准确找到根目录
# __file__ = "本文件自己的路径"；parents[2] = config.py → xrd_toolkit → src → 项目根目录
BASE_DIR = Path(__file__).resolve().parents[2]

# ══ 几何配置注册表 ══════════════════════════════════════════════════
# 每批实验的仪器摆位不同、几何各自标定，因此按批次登记为独立条目：
# 每个 key 对应一批实验的标定几何 + 束心。导入时合并进 CONFIGS，
# 消费方只认 CONFIGS。
#
# 注册表分两层：
#   1. 内置条目 BUILTIN_CONFIGS（下方大括号）：进 git 的人工登记表。
#      登记 = CLI（calibrate_integrate.py）标定后打印条目模板 →
#      人工核对 → 粘贴进下方大括号、改新 key。脚本不自动写配置
#      文件，配置登记必须人工复核，避免错误数据进入仓库。
#   2. 用户条目 USER_CONFIGS：GUI 校准工作台 [保存为配置] 直接写入
#      同目录的 config_user.json（本地文件，不进 git）。用户在结果
#      区看完成绩（距离/残差）自己点保存，这一步点击就是复核。
#      文件损坏或条目无效时跳过并打印警告，不拖垮程序启动。
#      内置条目同名时以内置为准（人工登记的不可被用户文件覆盖）。
#
# 消费方（view_diffraction / integrate_pattern / sector_waterfall）：
#     用 --config 指定条目（内置与用户条目一视同仁）；交互模式（不带
#     --file）下，选完数据文件后会再显示配置菜单选择一次。服务层
#     （integrator.py）不依赖本文件，几何参数由调用方显式传入。
#
# 命名约定：key = 材料简写 + 批次编号 + 标样简写（lmfp1_lab6 = lmfp
# 第 1 批、LaB₆ 标样标定）——以批次为主，后缀标注校准标样便于溯源。
# 隐私：label 只写批次级信息，不含数据集运行号或未公开样品名等实验
# 细节（仓库公开）。LaB₆ 是公开标样（NIST SRM 660），可以提及。
BUILTIN_CONFIGS = {
    # ── lmfp1_lab6 ──
    # lmfp 第 1 批实验的几何。LaB₆ 标样（NIST SRM 660）与本批样品同时
    # 测量、专门用于几何校准，标定结果适用于整个 lmfp 批次；key 以
    # 批次材料为主（lmfp1），后缀标样（lab6）便于溯源。
    # 标定方式：手动初值圆心 (1024, 1024)，3 次精修取平均。距离对初值
    # 不敏感（1595.80 mm，与自动定位初值一致）；PONI/倾斜角与自动定位
    # 初值存在近似简并、两组解残差相当——统一采用手动初值解，自动定位
    # （取点拟合 fit_center_from_rings，FFT find_ring_center 兜底）仅
    # 保留作新数据的标定初值。
    "lmfp1_lab6": {
        # 批次备注（中文）：几何所属批次与标定方式
        "label": "lmfp 第 1 批（LaB₆ 标样标定）",
        # 探测器几何（7 个键）：参考标定值
        "geometry": dict(
            pixel_size_m=200e-6,          # 像素 200 µm
            wavelength_m=0.1223e-10,      # λ = 0.1223 Å（同步辐射硬 X 光，不是 Cu Kα！）
            dist_m=1.59580,               # 探测器距离 1595.80 mm
            poni1_m=1045.2 * 200e-6,      # PONI 横向（米）
            poni2_m=1022.0 * 200e-6,      # PONI 纵向（米）
            rot1_deg=-0.005,              # 倾斜角 1（度）
            rot2_deg=-0.163,              # 倾斜角 2（度）
        ),
        # 环圆心 = 直射束落点 B（像素坐标，(行, 列)）。
        # 注意：B ≠ PONI（法线垂足）——探测器有倾斜时两者差约 23 px。
        # B 随倾斜角变化，是标定得到的量，因此按条目存储、随几何走。
        # view_diffraction 画图默认用 B（校准值，同一仪器通用）；
        # 取点拟合/FFT 自动定位仅保留给校准脚本作初值。
        "beam_center": (1022.0, 1022.3),
    },
}

# 用户配置文件：GUI 校准工作台 [保存为配置] 的落盘位置（config.py
# 同目录，不进 git——个人标定记录本地保留，与内置人工登记表分开）。
USER_CONFIG_PATH = Path(__file__).with_name("config_user.json")

# 条目 geometry 必须包含的 7 个键（与内置条目一致，单位见各键注释）
_GEOMETRY_KEYS = ("pixel_size_m", "wavelength_m", "dist_m",
                  "poni1_m", "poni2_m", "rot1_deg", "rot2_deg")


def _validate_user_entry(name, entry):
    """校验并规范化一条用户配置条目。

    非法条目抛 ValueError（报错信息说明原因）；合法条目返回规范化
    副本：geometry 只保留 7 个标准键（多余键丢弃，如控制点），
    beam_center 列表 → (row, col) 元组，residual_deg 可选保留
    （诊断量，消费方忽略）。
    """
    if not isinstance(entry, dict):
        raise ValueError(f"条目 {name!r} 必须是 dict")
    label = entry.get("label")
    if not isinstance(label, str) or not label.strip():
        raise ValueError(f"条目 {name!r} 缺非空 label（批次备注）")
    geom = entry.get("geometry")
    if not isinstance(geom, dict):
        raise ValueError(f"条目 {name!r} 缺 geometry dict")
    for key in _GEOMETRY_KEYS:
        value = geom.get(key)
        if not isinstance(value, (int, float)):
            raise ValueError(f"条目 {name!r} 的 geometry 缺数值键 {key}")
    beam = entry.get("beam_center")
    if (not isinstance(beam, (list, tuple)) or len(beam) != 2
            or not all(isinstance(v, (int, float)) for v in beam)):
        raise ValueError(f"条目 {name!r} 的 beam_center 必须是 [row, col] 两个数字")
    out = {
        "label": label.strip(),
        "geometry": {key: float(geom[key]) for key in _GEOMETRY_KEYS},
        "beam_center": (float(beam[0]), float(beam[1])),
    }
    if isinstance(entry.get("residual_deg"), (int, float)):
        out["residual_deg"] = float(entry["residual_deg"])
    return out


def _read_user_config(path):
    """读用户配置文件：JSON dict {key: 条目}。

    文件不存在 → {}；JSON 损坏 → 警告（stderr）并忽略整个文件；
    单个条目无效 → 警告并跳过该条目（不让一条坏数据毁掉全部用户
    配置）。本函数只读不写。
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as err:
        print(f"警告：用户配置文件 {path} 读取失败，已忽略（{err}）",
              file=sys.stderr)
        return {}
    if not isinstance(raw, dict):
        print(f"警告：用户配置文件 {path} 顶层不是对象，已忽略",
              file=sys.stderr)
        return {}
    entries = {}
    for name, entry in raw.items():
        try:
            entries[name] = _validate_user_entry(name, entry)
        except ValueError as err:
            print(f"警告：用户配置条目无效，已跳过（{err}）", file=sys.stderr)
    return entries


def _merge_configs(builtin, user):
    """合并两层注册表：内置在前、用户条目追加在后；重名内置优先。"""
    merged = dict(builtin)
    for name, entry in user.items():
        if name not in merged:
            merged[name] = entry
    return merged


USER_CONFIGS = _read_user_config(USER_CONFIG_PATH)
CONFIGS = _merge_configs(BUILTIN_CONFIGS, USER_CONFIGS)


def _write_user_configs():
    """把内存里的 USER_CONFIGS 原子落盘（保存/删除共用）。

    先写临时文件再替换：os.replace 在同一文件系统上是原子的，
    中途断电不会留半截文件。
    """
    tmp = USER_CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(USER_CONFIGS, ensure_ascii=False, indent=2),
        encoding="utf-8")
    tmp.replace(USER_CONFIG_PATH)


def save_user_config(name, entry):
    """GUI [保存为配置]：把校准结果存成命名用户条目。

    校验条目 → 原子写入 config_user.json → 立即合并进内存的
    USER_CONFIGS / CONFIGS（GUI 下拉框无需重启即可见到）。

    参数：
        name   条目 key（如 "lmfp2_lab6"）
        entry  dict，结构同 _validate_user_entry 的输入

    返回：
        True = 新增条目；False = 覆盖了同名的已有用户条目。

    报错：
        ValueError：与内置条目重名（人工登记的不可覆盖）或条目结构
        无效。
    """
    if name in BUILTIN_CONFIGS:
        raise ValueError(f"key {name!r} 与内置条目重名，换一个名字")
    validated = _validate_user_entry(name, entry)
    is_new = name not in USER_CONFIGS
    USER_CONFIGS[name] = validated
    CONFIGS[name] = validated
    _write_user_configs()
    return is_new


def remove_user_config(name):
    """GUI [删除]：把一条用户配置条目从注册表与磁盘移除。

    参数：
        name   条目 key（如 "lmfp2_lab6"）

    返回：
        True = 删除了条目；False = 用户条目里没有该 key（无操作）。

    报错：
        ValueError：与内置条目重名——内置注册表是人工登记维护的，
        不从 GUI 改动。
    """
    if name in BUILTIN_CONFIGS:
        raise ValueError(f"key {name!r} 是内置条目（人工登记的注册表），"
                         "不可从 GUI 删除")
    if name not in USER_CONFIGS:
        return False
    del USER_CONFIGS[name]
    CONFIGS.pop(name, None)
    _write_user_configs()
    return True

# 默认条目：消费脚本未指定 --config 且非交互模式时使用。PyCharm 的
# 运行配置依赖该默认值，修改前需同步检查。
DEFAULT_CONFIG = "lmfp1_lab6"


def get_config(name=None):
    """按名字取出一个配置条目；name 为空时用 DEFAULT_CONFIG。

    参数：
        name : str 或 None
            条目 key（如 "lmfp1_lab6"）；None → 取 DEFAULT_CONFIG

    返回：
        dict，包含 "label" / "geometry" / "beam_center" 三个键。

    报错：
        名字不存在时抛 ValueError（英文报错，列出全部可选名字）。脚本里
        用 try/except 接住后交给 parser.error()，就变成 argparse 风格的
        报错（打印用法 + 退出码 2），而不是一长串 Python traceback。
    """
    if name is None:
        name = DEFAULT_CONFIG
    if name not in CONFIGS:
        raise ValueError(
            f"Unknown config '{name}'. Available configs: {', '.join(CONFIGS)}")
    return CONFIGS[name]


def config_entry_template(key_hint="lmfp2_lab6", label_hint="（改成实际批次备注）",
                          geometry=None, beam_center_rc=None):
    """生成内置 CONFIGS 条目模板文本（CLI 标定完成后打印，人工粘贴登记用）。

    scripts/calibrate_integrate.py 打印的模板就是这个函数的输出（逐字
    一致由 test_calibration.py 守护）。GUI 校准工作台不再走复制粘贴：
    它调 save_user_config 把结果直接存成用户条目（config_user.json）。
    本函数只生成文本、不写任何文件——内置条目登记必须人工复核
    （见文件头说明）。

    参数：
        key_hint       新条目 key 提示（默认与 CLI 相同）
        label_hint     批次备注提示（默认与 CLI 相同）
        geometry       dict，需含 pixel_size_m / wavelength_m（米）、
                       dist_m（米）、poni1_px / poni2_px（像素）、
                       rot1_deg / rot2_deg（度），可选 residual_deg
                       （度，写进注释行）。GUI 用校准结果 + 参数坞的
                       像素/波长输入合并出这个 dict
        beam_center_rc  (row, col) 束心像素坐标（直射束落点 B）

    返回：
        str，可直接粘贴进 config.py 的条目模板（多行文本）。
    """
    lines = []
    lines.append("===== CONFIGS entry for config.py (copy-paste ready) =====")
    residual = geometry.get("residual_deg")
    if residual is None:
        lines.append("# refined residual: ??? deg (diagnostic, not stored)")
    else:
        lines.append(f"# refined residual: {residual:.4f} deg (diagnostic, not stored)")
    # CLI 里像素/波长取自命令行输入（µm / Å），这里从米制几何反推
    pixel_um = geometry["pixel_size_m"] * 1e6
    wl_angstrom = geometry["wavelength_m"] * 1e10
    lines.append(
        f'    "{key_hint}": {{   # rename key to "<material><n>_<standard>" (e.g. lmfp2_lab6)')
    lines.append(f'        "label": "{label_hint}",')
    lines.append('        "geometry": dict(')
    lines.append(f"            pixel_size_m={pixel_um:g}e-6,")
    lines.append(f"            wavelength_m={wl_angstrom:g}e-10,")
    lines.append(f"            dist_m={geometry['dist_m']:.5f},")
    lines.append(f"            poni1_m={geometry['poni1_px']:.3f} * {pixel_um:g}e-6,")
    lines.append(f"            poni2_m={geometry['poni2_px']:.3f} * {pixel_um:g}e-6,")
    lines.append(f"            rot1_deg={geometry['rot1_deg']:.4f},")
    lines.append(f"            rot2_deg={geometry['rot2_deg']:.4f},")
    lines.append('        ),')
    row, col = beam_center_rc
    lines.append(
        f'        "beam_center": ({row:.2f}, {col:.2f}),   # (row, col) px = direct beam spot')
    lines.append('    },')
    return "\n".join(lines)
