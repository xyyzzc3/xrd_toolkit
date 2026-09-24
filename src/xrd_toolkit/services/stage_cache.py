"""分阶段产物缓存：把"算出来的东西"落盘，跨会话/跨天不用重算。

为什么（用户 2026-09-24 原话）："现在点两个文件进行画图时，感觉就从原始
数据又跑了一遍，没有缓存，最终目标是要能一次处理 200 条左右数据，现在
80 条就卡顿"。**一次会话内本来就有缓存**（1D 曲线算完留在面板上，同文件
再点不重算）；真正贵的是**跨会话**——关掉程序第二天再开，81 张图全部重
积分。

产物按阶段分（与界面上的入口一一对应）：
    ① 校准 → 几何条目（已有：config_user.json；本模块不管）
    ② 1D   → 曲线 (2θ, 强度) 落盘 ← **唯一耗时的步骤，收益最大**
    ③ 扣背景 → **默认不落盘**：它是实时可调的（改窗口/锚点立刻重画），
      冻成产物反而慢；导出时走导出链路现算
    ④ 对比 / 热图 → 读 ② 的产物（内存里已经这么做，跨会话靠本模块）

缓存键 = 文件指纹（名字 + 大小 + 修改时间 + 头部哈希，见 fingerprint）
+ 几何条目名 + 2θ 范围 + 点数 +
**积分实现版本号**（`integrator.INTEGRATION_VERSION`：算法或默认参数改了
要作废旧产物，否则会拿旧算法的结果冒充新的）。**命中必须写日志**——静默
复用会让人以为重算了、或者怀疑数据不对（调用方负责记，本模块只返回数据）。

存放：`outputs/_stage/1d/<key>.npz`（本地，不进 git）。可用环境变量
`XRD_STAGE_CACHE` 指到别处（测试隔离用）。界面上的 [清空缓存] 调
`clear()`。

写盘是原子的（tmp + rename）：中途崩了不会留下半截产物被下次误读。
"""
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import numpy as np

from xrd_toolkit.services.integrator import INTEGRATION_VERSION

# 产物根目录：项目根下的 outputs/_stage（脚本与界面的 outputs 习惯一致）
ROOT = Path(__file__).resolve().parents[3]
CACHE_ROOT = Path(os.environ.get("XRD_STAGE_CACHE", ROOT / "outputs" / "_stage"))


def _cache_dir(kind: str) -> Path:
    return CACHE_ROOT / kind


def fingerprint(path) -> tuple:
    """文件指纹：**名字** + 大小 + 修改时间（纳秒）+ 头部 64 KiB 的哈希。

    为什么不止 (size, mtime)——**实测踩过**：同一次拷进来的两张 LMFP
    大小与 mtime 完全相同（复制工具给了一样的时间戳），只靠这两项键
    就撞了：第二张的产物把第一张覆盖掉，下次两张又都"命中"同一份曲线
    ——**静默拿错数据**，比没有缓存糟得多（探针实测：3 张只落 2 个产物、
    重开时 3 次全命中）。

    名字区分同批文件（同一批数据里名字必然不同）；头部哈希防"重写后
    把 mtime 改回来"；都只读 64 KiB（17 MB 的 tif 读 0.4%），81 张
    加起来的开销可以忽略。
    """
    p = Path(path)
    st = p.stat()
    h = hashlib.blake2b(digest_size=8)
    with p.open("rb") as fh:
        h.update(fh.read(65536))
    return (p.name, int(st.st_size), int(st.st_mtime_ns), h.hexdigest())


def cache_key(path, *, config: str, npt: int, tth_min=None,
              tth_max=None) -> str:
    """1D 产物的键：文件指纹 + 几何条目 + 范围 + 点数 + 积分实现版本。"""
    name, size, mtime, head = fingerprint(path)
    parts = {
        "name": name,
        "size": size,
        "mtime_ns": mtime,
        "head": head,
        "config": config,
        "npt": int(npt),
        "tth_min": None if tth_min is None else round(float(tth_min), 6),
        "tth_max": None if tth_max is None else round(float(tth_max), 6),
        "engine": INTEGRATION_VERSION,
    }
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=10).hexdigest()


def load_1d(path, *, config: str, npt: int, tth_min=None, tth_max=None):
    """读 1D 产物；没有 / 坏了 / 版本不符都返回 None（当作没缓存）。

    坏产物（半截文件、形状不对）**删掉**再返回 None：留着它每次都会
    在读的这一步失败，而重建的代价只是一次积分。
    """
    target = _cache_dir("1d") / f"{cache_key(path, config=config, npt=npt, tth_min=tth_min, tth_max=tth_max)}.npz"
    if not target.exists():
        return None
    try:
        with np.load(target) as data:
            tth = np.asarray(data["tth"], dtype=float)
            intensity = np.asarray(data["intensity"], dtype=float)
        if tth.shape != intensity.shape or tth.size == 0:
            raise ValueError(f"形状不对：{tth.shape} / {intensity.shape}")
        return tth, intensity
    except Exception:                                    # noqa: BLE001
        target.unlink(missing_ok=True)                   # 坏产物清掉，别留着
        return None


def store_1d(path, tth, intensity, *, config: str, npt: int,
             tth_min=None, tth_max=None) -> Path:
    """写 1D 产物（原子：先写 tmp 再 rename）。返回产物路径。"""
    tth = np.asarray(tth, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    if tth.shape != intensity.shape or tth.size == 0:
        raise ValueError("曲线与强度形状不一致，不写缓存")
    target_dir = _cache_dir("1d")
    target_dir.mkdir(parents=True, exist_ok=True)
    key = cache_key(path, config=config, npt=npt, tth_min=tth_min,
                    tth_max=tth_max)
    target = target_dir / f"{key}.npz"
    tmp = target.with_suffix(".tmp.npz")
    meta = {"source": str(Path(path).name), "config": config, "npt": int(npt),
            "engine": INTEGRATION_VERSION, "created": time.time(),
            "n_points": int(tth.size)}
    np.savez(tmp, tth=tth, intensity=intensity,
             meta=json.dumps(meta, ensure_ascii=False))
    tmp.replace(target)          # 原子：读者要么见旧的、要么见新的
    return target


def describe() -> dict:
    """缓存概况（[清空缓存] 的提示与日志用）：文件数 + 字节数。"""
    total = files = 0
    if CACHE_ROOT.is_dir():
        for p in CACHE_ROOT.rglob("*"):
            if p.is_file():
                files += 1
                total += p.stat().st_size
    return {"files": files, "bytes": total,
            "root": str(CACHE_ROOT)}


def clear(kind: str = None) -> int:
    """删缓存（默认全删，给 kind 只删那一类）。返回删掉的文件数。

    只动 CACHE_ROOT 里的东西；目录不存在时静默返回 0（幂等）。
    """
    root = CACHE_ROOT if kind is None else _cache_dir(kind)
    if not root.is_dir():
        return 0
    n = sum(1 for p in root.rglob("*") if p.is_file())
    shutil.rmtree(root, ignore_errors=True)
    return n
