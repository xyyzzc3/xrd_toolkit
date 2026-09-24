"""分阶段产物缓存：把"算出来的东西"落盘，跨会话/跨天不用重算。

为什么（用户 2026-09-24 原话）："现在点两个文件进行画图时，感觉就从原始
数据又跑了一遍，没有缓存，最终目标是要能一次处理 200 条左右数据，现在
80 条就卡顿"。**一次会话内本来就有缓存**（1D 曲线算完留在面板上，同文件
再点不重算）；真正贵的是**跨会话**——关掉程序第二天再开，81 张图全部重
积分。

产物按阶段分（与界面上的入口一一对应）：
    ① 校准 → 几何条目（已有：config_user.json；本模块不管）
    ② 1D   → 曲线 (2θ, 强度) 落盘 ← **唯一耗时的步骤，收益最大**
    ③ 扣背景 → 落盘，但**由 [批量扣背景] 显式生成**（不是每次改参数都
      写盘）：批量那一步把一张图的锚点用到整批（**只传 2θ 位置，强度到
      每张自己的曲线上重取**——绝对强度跨文件会错几倍），各存一份
    ④ 对比 / 热图 → 优先读 ③（有就用），其次 ②
  
  实时预览与落盘不冲突：产物键里含**背景设置哈希**（模式/窗口/拟合/
  锚点/截断/空扫指纹）——改任何一项 → 键变 → 当场重画（毫秒级）；设置
  没变 → 跨会话直接读产物。

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


def _read_curve(target: Path):
    """读一条曲线产物（1D 与 bg 共用）。没有 / 坏了都返回 None。

    坏产物（半截文件、形状不对）**删掉**再返回 None：留着它每次都会
    在读的这一步失败，而重建的代价只是重算一次。
    """
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


def _write_curve(target: Path, tth, intensity, meta: dict) -> Path:
    """写一条曲线产物（原子：先写 tmp 再 rename），1D 与 bg 共用。"""
    tth = np.asarray(tth, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    if tth.shape != intensity.shape or tth.size == 0:
        raise ValueError("曲线与强度形状不一致，不写缓存")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp.npz")
    meta = dict(meta, n_points=int(tth.size))
    np.savez(tmp, tth=tth, intensity=intensity,
             meta=json.dumps(meta, ensure_ascii=False))
    tmp.replace(target)          # 原子：读者要么见旧的、要么见新的
    return target


def _safe_key(path, *, config, npt, tth_min=None, tth_max=None):
    """算键；文件不在/读不了时返回 None（查找要算 miss，不能抛）。

    消费方（界面取数）会拿假路径或已删除的条目来查缓存，那是**正常情况**
    （没有缓存而已，去算就是），不该当异常——2026-09-24 对比测试因此红过。
    """
    try:
        return cache_key(path, config=config, npt=npt, tth_min=tth_min,
                         tth_max=tth_max)
    except OSError:
        return None


def load_1d(path, *, config: str, npt: int, tth_min=None, tth_max=None):
    """读 1D 产物；没有 / 坏了 / 文件不在都返回 None（当作没缓存）。"""
    key = _safe_key(path, config=config, npt=npt, tth_min=tth_min,
                    tth_max=tth_max)
    if key is None:
        return None
    return _read_curve(_cache_dir("1d") / f"{key}.npz")


def has_1d(path, *, config: str, npt: int, tth_min=None, tth_max=None) -> bool:
    """这个文件的 1D 产物**在不在**（只看文件在不在，不读内容）。

    给"批量超出画面板上限、剩下的只算不画"那条路用（界面 2026-09-25）：
    要先数清"哪些已经有产物、根本不用再算"，才能把这一批的总数报准
    （总数为 0 的批不该开进度条，也不该永远等不到 n）。
    """
    key = _safe_key(path, config=config, npt=npt, tth_min=tth_min,
                    tth_max=tth_max)
    return key is not None and (_cache_dir("1d") / f"{key}.npz").exists()


def store_1d(path, tth, intensity, *, config: str, npt: int,
             tth_min=None, tth_max=None) -> Path:
    """写 1D 产物（原子）。返回产物路径。"""
    target = _cache_dir("1d") / (
        f"{cache_key(path, config=config, npt=npt, tth_min=tth_min, tth_max=tth_max)}.npz")
    return _write_curve(target, tth, intensity,
                        {"source": str(Path(path).name), "config": config,
                         "npt": int(npt), "engine": INTEGRATION_VERSION,
                         "kind": "1d", "created": time.time()})


def bg_settings_hash(settings: dict) -> str:
    """背景设置 → 短哈希（bg 产物键的一半）。

    设置包括：模式 / 窗口宽度 / 锚点拟合方式 / 锚点列表（2θ 与强度都
    取，四舍五入到 1e-6）/ 负值截断 / 空扫图指纹。**任何一项变了就是
    另一份产物**——这正是"扣背景既实时可调、又能跨会话复用"的接缝：
    改参数 → 键变 → 当场重画（毫秒级），设置没变 → 读产物。
    """
    canon = {
        "mode": settings.get("mode"),
        "window_deg": round(float(settings.get("window_deg") or 0.0), 6),
        "anchor_method": settings.get("anchor_method"),
        "clip": bool(settings.get("clip")),
        "anchors": [[round(float(x), 6), round(float(y), 6)]
                    for x, y in (settings.get("anchors") or [])],
        "blank": settings.get("blank"),
    }
    blob = json.dumps(canon, sort_keys=True, ensure_ascii=False)
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=8).hexdigest()


def _bg_product(path, *, config: str, npt: int, tth_min=None, tth_max=None,
                settings: dict) -> Path:
    """bg 产物路径：1D 产物的键 + 背景设置哈希。

    先有 1D 才有扣背景（扣的是那条曲线），所以键里嵌 1D 的键——1D
    产物换代（换几何/换点数/升版本）时 bg 自然跟着作废。
    """
    base = cache_key(path, config=config, npt=npt, tth_min=tth_min,
                     tth_max=tth_max)
    key = hashlib.blake2b(
        (base + bg_settings_hash(settings)).encode("utf-8"),
        digest_size=10).hexdigest()
    return _cache_dir("bg") / f"{key}.npz"


def load_bg(path, *, config: str, npt: int, tth_min=None, tth_max=None,
            settings: dict):
    """读扣背景产物；没有 / 坏了 / 文件不在都返回 None（同 load_1d）。"""
    key = _safe_key(path, config=config, npt=npt, tth_min=tth_min,
                    tth_max=tth_max)
    if key is None:
        return None
    return _read_curve(_bg_product(path, config=config, npt=npt,
                                   tth_min=tth_min, tth_max=tth_max,
                                   settings=settings))


def store_bg(path, tth, intensity, *, config: str, npt: int, tth_min=None,
             tth_max=None, settings: dict) -> Path:
    """写扣背景产物（原子）。"""
    return _write_curve(
        _bg_product(path, config=config, npt=npt, tth_min=tth_min,
                    tth_max=tth_max, settings=settings),
        tth, intensity,
        meta={"source": str(Path(path).name), "config": config,
              "npt": int(npt), "engine": INTEGRATION_VERSION,
              "kind": "bg", "created": time.time(),
              "settings": settings})


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
