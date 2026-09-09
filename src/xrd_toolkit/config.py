from pathlib import Path

# 项目根目录：基于本文件位置定位，无论从哪里启动程序都能准确找到根目录
# __file__ = "本文件自己的路径"；parents[2] = config.py → xrd_toolkit → src → 项目根目录
BASE_DIR = Path(__file__).resolve().parents[2]

# 任务三对 LaB₆ 标样校准得到的几何参数（3 次精修取平均，2026-09-09）。
# 这是全项目唯一一份——脚本统一从这里 import（integrate_pattern.py、
# sector_waterfall.py），要改波长 / 距离 / 中心等参数只改这一处，
# 不要再在脚本里各自复制一份（之前就踩过"两处参数不同步"的坑）。
CALIBRATED = dict(
    pixel_size_m=200e-6,          # 像素 200 µm
    wavelength_m=0.1223e-10,      # λ = 0.1223 Å（同步辐射硬 X 光，不是 Cu Kα！）
    dist_m=1.59579,               # 探测器距离 1595.79 mm
    poni1_m=1045.17 * 200e-6,     # PONI 横向（米）
    poni2_m=1022.03 * 200e-6,     # PONI 纵向（米）
    rot1_deg=-0.0054,             # 倾斜角 1（度）
    rot2_deg=-0.1632,             # 倾斜角 2（度）
)
