"""手动选点的判环尺度扫描：同一环号横跨两个半径时重判环、挑环位偏差最小的一套。

2026-10-22 从 calib.py 原样搬出（只改结构，不动逻辑）。
calib 再导出。
"""
import numpy as np

from xrd_toolkit.gui.calib_metrics import _attach_metrics
from xrd_toolkit.gui.calib_model import (
    SNAP_TOL_DEG, SOURCE_IMPROVE_MIN_PX, _result_dev)
from xrd_toolkit.gui.calib_panel import _geom_px_keys
from xrd_toolkit.services.calibrants import DEFAULT_CALIBRANT
from xrd_toolkit.services.integrator import (
    refine_geometry_from_points, snap_ring)


# 判环可疑时按哪些距离尺度重试（见 _reindex_by_scale）。距离是这套几何里
# **整体缩放环位置**的那个量：它错一个比例，低角侧的真实环就会被压到"第 0
# 环"以下、判环全挤在一起。
_MANUAL_SCALE_PROBE = (0.85, 0.90, 0.95, 1.0, 1.05, 1.10, 1.20)
# 同一环号被点到的两个半径差超过这个比例 = 判环挤在一起了
# （同一个环上点两次半径只差几个百分点，不会误报）
_SUSPICIOUS_RADIUS_RATIO = 1.15

def _ring_radius(p, center_px) -> float:
    """点到束心的半径（px）——判"同一个环号是不是横跨了两个半径"。"""
    return float(np.hypot(float(p[0]) - float(center_px[0]),
                          float(p[1]) - float(center_px[1])))

def _rings_look_suspicious(points, rings, center_px) -> bool:
    """同一个环号被点在明显不同的半径上 → 判环很可能挤在一起了。

    用户 2026-09-30 报的正是这个：第 0 青环里套了两个真实环，点它们都判成
    环 0（半径差一倍）。这是"几何尺度偏了"的火警，触发下面的尺度扫描。
    """
    by_ring = {}
    for p, k in zip(points, rings):
        by_ring.setdefault(int(k), []).append(_ring_radius(p, center_px))
    for radii in by_ring.values():
        if len(radii) >= 2 \
                and max(radii) / max(min(radii), 1e-9) > _SUSPICIOUS_RADIUS_RATIO:
            return True
    return False

def _rings_at_scale(points, geom, center_px, scale,
                    calibrant=DEFAULT_CALIBRANT, n_rings: int = 16) -> list:
    """把距离乘 scale 后重新判环；有任何一点判不到环就返回空表（那套不能用）。

    geom 的形状约定与 _attach_metrics 相同（GUI 交出来的是米制键
    poni1_m/poni2_m），判环要的却是 px 键——先归一化再读，两种形状都收。
    别忘了这一步（2026-10-05 踩过：只认 px 键 → 手动校准一触发尺度扫描
    就 KeyError: 'poni1_px'，正好把这个兜底功能该干活的时候打死了）。
    """
    g = _geom_px_keys(geom) if "poni1_m" in geom else dict(geom)
    g["dist_m"] = float(geom["dist_m"]) * float(scale)
    px = float(g["pixel_size_m"])
    out = []
    for x, y in points:
        k = snap_ring(
            float(x), float(y), pixel_size_m=px,
            wavelength_m=float(g["wavelength_m"]), dist_m=float(g["dist_m"]),
            poni1_m=float(g["poni1_px"]) * px, poni2_m=float(g["poni2_px"]) * px,
            rot1_deg=float(g.get("rot1_deg") or 0.0),
            rot2_deg=float(g.get("rot2_deg") or 0.0), tol_deg=SNAP_TOL_DEG,
            calibrant=calibrant, max_rings=n_rings)
        if k is None:
            return []
        out.append(int(k))
    return out

def _reindex_by_scale(points, rings, geom, center0_px, image,
                      calibrant=DEFAULT_CALIBRANT, n_rings: int = 16):
    """判环可疑时：扫几个尺度重判环、各拟合一次，挑**环位偏差最小**的那套。

    返回 (rings, 说明, dist0_m)；不需要扫 / 扫不动时返回 (rings, "", 原距离)
    ——不猜、不动。换了判环就**连赢家那套的起点距离一起交出来**：只换判环、
    起点还用原来那个错的，重拟合会从错的初值出发落回另一支更差的解——真数据
    实测 0.31 px vs 7.55 px，日志写的和最终拿到的对不上（2026-10-05 查修）。

    为什么扫尺度：判环是"拿当前几何把点击点换算成 2θ、再找最近的理论环"，
    而低角侧没有更低的环可判——距离错一个比例时最里面几个真实环全被压到
    第 0 环。距离是唯一整体缩放环位置的量，所以按比例扫它、每个候选重新判环
    再拟合，用 16 个环的环位偏差挑解（单看 2~3 个点会过拟合，指标才有分辨力）。

    门槛：只有比原尺度（1.0）好 **0.05 px 以上**才换——与"当前配置要不要
    换"同一个噪声口径（calib_model.SOURCE_IMPROVE_MIN_PX），免得在噪声里跳。
    """
    if image is None or not _rings_look_suspicious(points, rings, center0_px):
        return rings, "", float(geom["dist_m"])

    def _fit(scale, screen=True):
        cand = _rings_at_scale(points, geom, center0_px, scale,
                               calibrant=calibrant, n_rings=n_rings)
        if not cand:
            return None
        if screen and _rings_look_suspicious(points, cand, center0_px):
            # 便宜的先筛一遍（几步判环，~10 ms）：环号还是挤在一起的那套
            # 不可能对，别花 200 ms 去拟合它（用户 2026-10-01："按优化来"）
            return None
        try:
            res = refine_geometry_from_points(
                points, cand, pixel_size_m=float(geom["pixel_size_m"]),
                wavelength_m=float(geom["wavelength_m"]),
                dist0_m=float(geom["dist_m"]) * float(scale),
                center0_px=center0_px, calibrant=calibrant,
                max_rings=n_rings)
        except Exception:                              # noqa: BLE001
            return None
        _attach_metrics(res, image, geom, initial=geom, calibrant=calibrant,
                        n_rings=n_rings)
        dev = _result_dev(res)
        return None if dev is None else (dev, scale, cand)

    # 基准（1.0）**不筛**：它就是"原判法"，得量出它的环位偏差当比较基准
    base = _fit(1.0, screen=False)
    if base is None:
        return rings, "", float(geom["dist_m"])   # 指标算不出来 = 没有判据：老实地不动
    best = base
    for s in _MANUAL_SCALE_PROBE:
        if abs(s - 1.0) < 1e-9:
            continue
        got = _fit(s)
        if got is not None and got[0] <= best[0] - SOURCE_IMPROVE_MIN_PX:
            best = got
    if best[1] == base[1]:
        return rings, "", float(geom["dist_m"])
    dev, scale, cand = best
    return cand, (f"判环可疑（同一环号横跨了两个半径）：按距离尺度 {scale:g} 重判"
                  f"更合理——环位偏差 {dev:.2f} px，原判法 {base[0]:.2f} px"), \
        float(geom["dist_m"]) * float(scale)
