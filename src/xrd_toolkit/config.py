from pathlib import Path

# 项目根目录：基于本文件位置定位，无论从哪里启动程序都能准确找到根目录
# __file__ = "本文件自己的路径"；parents[2] = config.py → xrd_toolkit → src → 项目根目录
BASE_DIR = Path(__file__).resolve().parents[2]

# ══ 几何配置注册表 ══════════════════════════════════════════════════
# 每批实验的仪器摆位不同、几何各自标定，因此按批次登记为独立条目：
# 每个 key 对应一批实验的标定几何 + 束心。
#
# 消费方（view_diffraction / integrate_pattern / sector_waterfall）：
#     用 --config 指定条目；交互模式（不带 --file）下，选完数据文件后
#     会再显示配置菜单选择一次。服务层（integrator.py）不依赖本文件，
#     几何参数由调用方显式传入。
# 生产方（calibrate_integrate.py）：
#     标定完成后打印一段可直接复制的条目模板，人工核对后粘贴到下方
#     大括号中并改为新 key（如 lmfp2_lab6）。脚本不自动写配置文件，
#     配置登记必须人工复核，避免错误数据进入仓库。
#
# 命名约定：key = 材料简写 + 批次编号 + 标样简写（lmfp1_lab6 = lmfp
# 第 1 批、LaB₆ 标样标定）——以批次为主，后缀标注校准标样便于溯源。
# 隐私：label 只写批次级信息，不含数据集运行号或未公开样品名等实验
# 细节（仓库公开）。LaB₆ 是公开标样（NIST SRM 660），可以提及。
CONFIGS = {
    # ── lmfp1_lab6 ──
    # lmfp 第 1 批实验的几何。LaB₆ 标样（NIST SRM 660）与本批样品同时
    # 测量、专门用于几何校准，标定结果适用于整个 lmfp 批次；key 以
    # 批次材料为主（lmfp1），后缀标样（lab6）便于溯源。
    # 标定方式：手动初值圆心 (1024, 1024)，3 次精修取平均。距离对初值
    # 不敏感（1595.80 mm，与自动定位初值一致）；PONI/倾斜角与自动定位
    # 初值存在近似简并、两组解残差相当——统一采用手动初值解，自动定位
    # （find_ring_center）仅保留作新数据的标定初值。
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
        # find_ring_center 自动定位仅保留给校准脚本作初值。
        "beam_center": (1022.0, 1022.3),
    },
}

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
