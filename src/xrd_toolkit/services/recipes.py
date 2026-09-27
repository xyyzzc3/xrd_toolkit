"""命名配方：把一套背景处理设置（+ 锚点位置）存下来，给别的批次复用。

为什么要有它（用户 2026-09-27："配方可以保存用也挺好，两批数据用同一个
锚点可行吗"）——**可行**：锚点回答的是"2θ 的哪几个位置是纯背景"，那是
**装置**的性质（空气散射 / 光路 / 探测器），不随样品变；背景有多高则是每
张图自己的事。所以配方里存的是**锚点位置**（连同模式 / 窗口 / 平滑 / 裁剪），
强度在应用时按目标图自己的曲线重新取——这与 [批量处理] 一直在做的口径
完全一致（见 plot_views._proc_batch_apply 的说明）。

存哪儿：`outputs/recipes.json`（与产物缓存同在 outputs/ 下，整个目录拷走
即可带走）。环境变量 `XRD_RECIPES` 可以指到别处（测试隔离用，与
stage_cache 的 XRD_STAGE_CACHE 同一套规矩）。

**跨批次复用的前提**（界面上会提示）：装置没换。换了毛细管/距离/曝光，
背景形状仍是角度的函数，锚点位置照用；但换了**几何条目**或**2θ 区间**，
锚点可能落到数据之外——应用时范围外的锚点会被丢掉并记一行日志，不静默
钳到端点值。

格式（一个 JSON 对象，键 = 配方名）：:

    {"<名字>": {"mode", "window_deg", "anchor_method", "clip",
                "anchors": [[2θ, 强度], ...],   # 强度只是参考值
                "smooth_deg", "smooth_method", "smooth_order",
                "cut_ranges": [[起, 止], ...], "created": 时间戳}}

坏文件当空表读（与产物台账一个态度：丢了只是少几条配方，不是数据）。
"""
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STORE = Path(os.environ.get("XRD_RECIPES", ROOT / "outputs" / "recipes.json"))


def _norm(recipe: dict) -> dict:
    """规范化一份配方：只留认识的键，数值定型（写盘与比较都用它）。"""
    out = {
        "mode": recipe.get("mode") or "off",
        "window_deg": round(float(recipe.get("window_deg") or 0.0), 6),
        "anchor_method": recipe.get("anchor_method") or "pchip",
        "clip": bool(recipe.get("clip")),
        "anchors": [[round(float(x), 6), round(float(y), 6)]
                    for x, y in (recipe.get("anchors") or [])],
        "smooth_deg": round(float(recipe.get("smooth_deg") or 0.0), 6),
        "smooth_method": recipe.get("smooth_method") or "boxcar",
        "smooth_order": int(recipe.get("smooth_order") or 3),
        "cut_ranges": [[round(float(lo), 6), round(float(hi), 6)]
                       for lo, hi in (recipe.get("cut_ranges") or [])
                       if float(hi) > float(lo)],
        "created": recipe.get("created") or time.time(),
    }
    return out


def load_all() -> dict:
    """全部配方 {名字: 配方}；没有/坏了/不是对象都返回空表。"""
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): _norm(v) for k, v in data.items() if isinstance(v, dict)}


def save(name: str, recipe: dict) -> None:
    """存/覆盖一份命名配方（整表原子重写）。"""
    name = str(name).strip()
    if not name:
        raise ValueError("配方名不能为空")
    data = load_all()
    data[name] = _norm({**recipe, "created": time.time()})
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(STORE)


def delete(name: str) -> bool:
    """删一份配方；本来就没有返回 False。"""
    data = load_all()
    if str(name) not in data:
        return False
    data.pop(str(name))
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
                   encoding="utf-8")
    tmp.replace(STORE)
    return True


def names() -> list:
    """配方名列表（按名字排序，界面下拉用）。"""
    return sorted(load_all())
