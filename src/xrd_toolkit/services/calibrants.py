"""标样（calibrant）注册表与几何小工具：常用清单 / 名称解析 / 完整环数。

为什么单独一层：几何校准里只有"理论 2θ 阶梯"依赖标样，距离、PONI、
倾斜角、像素尺寸都与标样无关。把标样相关的东西收在这里，引擎、指标、
GUI 和 CLI 共用同一份清单与同一条解析规则，避免各处再写
``get_calibrant("LaB6")``。

标样名称与 d 值全部来自 pyFAI 自带数据库（``get_calibrant``），本模块
不复制 d 值。常用清单只决定界面里先展示哪些、文件名怎么预选；命令行
仍可传任意 pyFAI 内置名称。
"""
from __future__ import annotations

import copy
import re
from functools import lru_cache

import numpy as np
from pyFAI.calibrant import Calibrant, get_calibrant

# 默认标样：与历史行为一致（内置条目 lmfp1_lab6 就是用它标的）。
DEFAULT_CALIBRANT = "LaB6"

# GUI 环数上限：再多只会拖慢画环/指标，且远超任何探测器能完整拍到的
# 环数；真需要更多请走 CLI（服务层只按标样反射总数夹，不设这个上限）。
MAX_GUI_RINGS = 64

# 常用标样清单：(pyFAI 名称, 界面标签, 文件名关键字)
#   - 顺序就是下拉框顺序；默认项排第一。
#   - 标签里写证书编号：LaB6 / Si 的不同 SRM 变体 d 值只差几十 ppm，
#     但对距离是几十 µm 级的系统差，选到具体变体才算溯源。
#   - 文件名关键字用于进校准时预选标样（只认完整词块，见
#     detect_calibrant_from_stem；`si` 不会误中 `series`）。
COMMON_CALIBRANTS = (
    ("LaB6", "LaB₆（NIST SRM 660c，默认）", ("lab6", "lab6c")),
    ("LaB6_SRM660a", "LaB₆（NIST SRM 660a）", ("lab6_srm660a", "lab6a")),
    ("LaB6_SRM660b", "LaB₆（NIST SRM 660b）", ("lab6_srm660b", "lab6b")),
    ("LaB6_SRM660c", "LaB₆（NIST SRM 660c）", ("lab6_srm660c",)),
    ("CeO2", "CeO₂（NIST SRM 674b）", ("ceo2", "ceria")),
    ("Si_SRM640", "Si（NIST SRM 640）", ("si", "silicon", "si_srm640")),
    ("Si_SRM640a", "Si（NIST SRM 640a）", ("si_srm640a",)),
    ("Si_SRM640b", "Si（NIST SRM 640b）", ("si_srm640b",)),
    ("Si_SRM640c", "Si（NIST SRM 640c）", ("si_srm640c",)),
    ("Si_SRM640d", "Si（NIST SRM 640d）", ("si_srm640d",)),
    ("Si_SRM640e", "Si（NIST SRM 640e）", ("si_srm640e",)),
    ("alpha_Al2O3_SRM676", "α-Al₂O₃（NIST SRM 676）",
     ("al2o3", "alumina", "corundum", "al2o3_srm676")),
    ("alpha_Al2O3_SRM676a", "α-Al₂O₃（NIST SRM 676a）",
     ("al2o3_srm676a",)),
    ("AgBh", "Ag 山嵛酸酯（AgBh，低角标样）",
     ("agbh", "behenate", "ag_behenate")),
    ("Ni", "Ni（镍）", ("ni", "nickel")),
    ("Au", "Au（金）", ("au", "gold")),
    ("TiO2", "TiO₂（二氧化钛）", ("tio2",)),
    ("ZnO", "ZnO（氧化锌）", ("zno",)),
)

_COMMON_BY_NAME = {name: (label, aliases)
                   for name, label, aliases in COMMON_CALIBRANTS}
# 常用别名（文件名里的 lab6/ceo2/si…）也允许命令行直接输：精确 pyFAI
# 名称优先，找不到时才走别名，最后才报错。大小写不敏感。
_ALIAS_TO_NAME = {alias: name
                  for name, _label, aliases in COMMON_CALIBRANTS
                  for alias in aliases}


def calibrant_label(name) -> str:
    """界面上显示的名字：常用标样给友好标签，其他原样返回。"""
    label = _COMMON_BY_NAME.get(str(name), (None, None))[0]
    return label or str(name)


def calibrant_short_label(name) -> str:
    """日志/报错里的短名字：去掉标签里的括号说明（LaB₆、CeO₂、Si…）。"""
    label = calibrant_label(name)
    return label.split("（", 1)[0]


@lru_cache(maxsize=None)
def calibrant_tooltip(name) -> str:
    """下拉框悬停说明：证书编号、晶胞、前三条 d 值（按名字缓存）。"""
    try:
        cal = resolve_calibrant(name)
        # 先摸一下 dspacing 触发懒加载——config（证书/晶胞）是在那之后
        # 才被填上的；反过来读会拿到 None，tooltip 就少了证书编号。
        d = np.asarray(cal.dspacing, dtype=float)[:3]
        cfg = cal.config
        lines = [calibrant_label(name)]
        if cfg is not None:
            if cfg.reference:
                lines.append(f"参考：{cfg.reference}")
            if cfg.cell:
                lines.append(f"晶胞：{cfg.cell}")
        if d.size:
            lines.append("前三条 d：" + "、".join(f"{v:.4f} Å" for v in d))
        lines.append("请按标样证书选到具体变体（不同 SRM 的 d 值差几十 ppm）。")
        return "\n".join(lines)
    except Exception:                                    # noqa: BLE001
        return str(name)


def resolve_calibrant(calibrant, wavelength_m: float = None) -> Calibrant:
    """名称或 Calibrant 对象 → 设好波长的**新** Calibrant 对象。

    pyFAI 的 calibrant 波长一经设置不可再改（见 integrator 的备注），
    传入对象时先 copy，免得改到调用方手里那个实例。
    """
    if isinstance(calibrant, Calibrant):
        cal = copy.copy(calibrant)
    else:
        name = str(calibrant)
        try:
            cal = get_calibrant(name)
        except KeyError:
            # 精确名称不认 → 试常用小写别名（lab6/ceo2/si…）；再不行报错。
            alias = _ALIAS_TO_NAME.get(name.lower())
            if alias is None:
                common = "、".join(n for n, _label, _aliases
                                  in COMMON_CALIBRANTS)
                raise ValueError(
                    f"未知标样 {name!r}（pyFAI 内置名称区分大小写；"
                    f"常用小写别名 lab6/ceo2/si 也认）。"
                    f"常用标样：{common}") from None
            cal = get_calibrant(alias)
    if wavelength_m is not None:
        cal.wavelength = wavelength_m
    return cal


def calibrant_2theta(calibrant, wavelength_m: float,
                     n_rings: int = None) -> np.ndarray:
    """标样前 n_rings 条反射的理论 2θ（度）。

    布拉格定律：2d·sinθ = λ  →  2θ = 2·asin(λ / 2d)

    参数：
        calibrant     pyFAI 标样名称（如 "LaB6"、"CeO2"）或 Calibrant 对象
        wavelength_m  X 光波长（米）
        n_rings       取前几条反射；None = 全部（LaB6 有 1441 条，GUI
                      不会这么用）

    返回：
        np.ndarray，2θ（度）。d 太小、λ/(2d)>1 的尾部反射会被丢掉
        （布拉格定律无解），所以长度可能小于 n_rings。
    """
    cal = resolve_calibrant(calibrant, wavelength_m)
    ds = np.asarray(cal.dspacing, dtype=float)
    if n_rings is not None:
        ds = ds[:max(0, int(n_rings))]
    if ds.size == 0:
        return np.empty(0)
    ratio = (wavelength_m * 1e10) / (2.0 * ds)
    # d 值按从大到小排，无解的只可能出现在尾部；只留可解的（顺序不变，
    # 环号仍然是从 0 起的连续前缀）。
    ok = ratio <= 1.0
    return np.degrees(2.0 * np.arcsin(ratio[ok]))


@lru_cache(maxsize=None)
def _ring_total_name(name: str) -> int:
    return len(resolve_calibrant(name).dspacing)


def ring_total(calibrant) -> int:
    """标样的反射总数（用于夹环号上界）。"""
    if isinstance(calibrant, str):
        return _ring_total_name(calibrant)
    return len(resolve_calibrant(calibrant).dspacing)


def clamp_ring_count(calibrant, n_rings: int) -> int:
    """把请求的环数夹到 [1, 反射总数]；None → 全部反射。"""
    total = ring_total(calibrant)
    if total <= 0:
        return 0
    if n_rings is None:
        return total
    return min(max(int(n_rings), 1), total)


def complete_ring_count(calibrant, *, wavelength_m: float,
                        pixel_size_m: float, dist_m: float,
                        beam_center_rc, image_shape,
                        margin_px: float = 4.0) -> int:
    """当前几何下**完整落在图像内**的环数（GUI 环数默认值）。

    为什么用"完整环"当默认：完全落在图像里的环对 pyFAI 取点最可靠
    （半环/四分之一环在部分方向没有约束）；这条规则也正好复现历史
    行为——LaB₆ 在当前 2048×2048、1595.8 mm 几何下是 16 环（第 16 环
    r≈1001.8 px，第 17 环 r≈1029.6 px，图像半宽约 1022 px）。

    只用正圆近似 + 边缘余量：真实环在倾斜下是椭圆，本数据倾斜
    −0.163° 只差几像素，4 px 余量足够；倾斜很大时用户可在界面里手改
    环数。束心取 ``beam_center_rc``（行, 列，索引坐标）；拿不到几何时
    退回历史默认 16（再夹到标样反射总数）。
    """
    total = max(1, ring_total(calibrant))
    fallback = min(16, total)
    if beam_center_rc is None or image_shape is None:
        return fallback
    h, w = image_shape
    row, col = float(beam_center_rc[0]), float(beam_center_rc[1])
    r_edge = min(row, h - 1 - row, col, w - 1 - col) - float(margin_px)
    if r_edge <= 0:
        # 束心在图像外/贴着边（或测试用的极小图）：一条完整环都装不下。
        # 别返回 1（那会让界面画/判不了几条环），退回历史默认 16；
        # 用户仍可在界面里手改。
        return fallback
    tth = calibrant_2theta(calibrant, wavelength_m)
    if tth.size == 0:
        return fallback
    radius = dist_m * np.tan(np.radians(tth)) / float(pixel_size_m)
    # 2θ>90° 的反射落在背向探测器上（tan 为负），不能算"完整环"。
    ok = np.isfinite(radius) & (radius > 0) & (radius <= r_edge)
    n = int(np.sum(ok))
    return n if n > 0 else fallback


def detect_calibrant_from_stem(stem: str):
    """按文件名预选标样（如 lab6-00024.tif → LaB6）；认不出返回 None。

    关键字按**完整词块**匹配（前后不能紧跟字母数字）：`si` 不会误中
    `series`，`au` 不会误中 `sample`；`si_srm640a` 这种带下划线的
    变体名也能整段匹配。多个关键字都中时取最长的那个（`lab6_srm660a`
    优先于 `lab6`）。这只是预选，用户随时能改。
    """
    s = str(stem).lower()
    hits = []
    for name, _label, aliases in COMMON_CALIBRANTS:
        for alias in aliases:
            pat = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"
            if re.search(pat, s):
                hits.append((len(alias), name))
    if not hits:
        return None
    return max(hits)[1]
