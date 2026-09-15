from pathlib import Path

# 项目根目录：基于本文件位置定位，无论从哪里启动程序都能准确找到根目录
# __file__ = "本文件自己的路径"；parents[2] = config.py → xrd_toolkit → src → 项目根目录
BASE_DIR = Path(__file__).resolve().parents[2]

# ══ 几何配置注册表（2026-09-16 新增）════════════════════════════════
# 背景：以前所有数据来自同一批实验，几何参数只有一份。以后会有多批实验：
# 每批仪器摆位不同、几何各自标定，所以改成"名册"式——CONFIGS 里每个条目
# （key = 批次名）装一份该批的标定几何 + 束心。
#
# 消费方（view_diffraction / integrate_pattern / sector_waterfall）：
#     用 --config 点名取用；交互模式下（不带 --file）选完数据文件后会
#     再弹一个配置菜单选一次。服务层（integrator.py）不碰这里，保持
#     "谁用谁传"的干净接口。
# 生产方（calibrate_integrate.py）：
#     标定完会打印一段可直接复制的条目模板，粘进下面的大括号里、改成
#     新 key（如 lab6_exp2）即可。脚本不会自己写文件——配置登记永远是
#     人看一眼、亲手贴进去，防止坏数据混进仓库。
#
# 命名约定：key 用"样品简写_exp编号"风格（如 lab6_exp1、lab6_exp2）。
# 隐私注意：label 只写批次级信息，不写数据集运行号 / 未公开样品名等
# 实验细节（仓库要公开）。LaB₆ 是公开标样（NIST SRM 660），可以提。
CONFIGS = {
    # ── lab6_exp1 ──
    # 对 LaB₆ 标样校准得到的几何参数：手动初值圆心 (1024, 1024)，
    # 3 次精修取平均。距离对初值不敏感（1595.80 mm，与自动定位初值的
    # 结果一致）；PONI/倾斜角与自动定位初值存在近似简并、两组解残差
    # 相当——统一采用手动初值解，自动定位（find_ring_center）仅作
    # 新数据的便捷初值。
    "lab6_exp1": {
        # 给人看的备注（中文）：这组几何是哪批实验、怎么标定出来的
        "label": "LaB₆ 标样几何标定",
        # 几何体检表：7 个键，数值为参考标定值（行为与之前一致）
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
        # 注意：B ≠ PONI（法线垂足）——探测器有倾斜时两者差 ~23 px。
        # 放在每个条目里而不是全局一份：B 依赖倾斜角，是标定出来的量，
        # 必须跟着几何走。view_diffraction 画图默认用 B（校准值，同一台
        # 仪器通用）；find_ring_center 自动定位只保留给校准脚本做初值
        # （2026-09-16 拍板）。
        "beam_center": (1022.0, 1022.3),
    },
}

# 默认条目：消费脚本不带 --config 且非交互模式时用这个。PyCharm 的运行
# 配置也全靠这个默认值，所以它的数值必须和今天的一致，不能动。
DEFAULT_CONFIG = "lab6_exp1"


def get_config(name=None):
    """按名字取出一个配置条目；name 为空时用 DEFAULT_CONFIG。

    参数：
        name : str 或 None
            条目 key（如 "lab6_exp1"）；None → 取 DEFAULT_CONFIG

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
