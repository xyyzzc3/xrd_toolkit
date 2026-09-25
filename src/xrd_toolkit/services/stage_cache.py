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

另外有一份**台账** `outputs/_stage/index.json`（2026-09-25 加）：键是哈希、
反查不出来，所以"一次 [批量扣背景] = 一批产物"这件事要单独记一笔，界面
上的"阶段文件夹"（勾一整组去对比/热图）就靠它（见 record_batch /
list_batches / drop_batch）。

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
from xrd_toolkit.services.process import chain_desc, chain_parts

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


def key_of_1d(path, *, config: str, npt: int, tth_min=None, tth_max=None):
    """这个文件的 1D 产物键；文件不在 / 读不了 → None（查找不该抛）。

    界面建"1D 产物"分组时要用它：分组里的条目就是拿这把键取数（见
    load_by_key），所以条目得把键带在身上。
    """
    return _safe_key(path, config=config, npt=npt, tth_min=tth_min,
                     tth_max=tth_max)


def has_1d(path, *, config: str, npt: int, tth_min=None, tth_max=None) -> bool:
    """这个文件的 1D 产物**在不在**（只看文件在不在，不读内容）。

    给"批量超出画面板上限、剩下的只算不画"那条路用（界面 2026-09-25）：
    要先数清"哪些已经有产物、根本不用再算"，才能把这一批的总数报准
    （总数为 0 的批不该开进度条，也不该永远等不到 n）。
    """
    key = key_of_1d(path, config=config, npt=npt, tth_min=tth_min,
                    tth_max=tth_max)
    return has_key("1d", key)


def has_key(kind: str, key: str) -> bool:
    """这个产物键对应的文件还在不在（只看在不在，不读内容）。

    界面刷"阶段文件夹"时逐条核对用：产物被删了（用户手动清过
    outputs/）的条目就不该再出现在文件栏里。
    """
    return bool(key) and (_cache_dir(kind) / f"{key}.npz").exists()


def load_by_key(kind: str, key: str):
    """按产物键直接读（1D / bg 通用）。没有 / 坏了都返回 None。

    界面上的"产物条目"走这条：条目说的是**那一份产物**，与当前设置无关
    （用户 2026-09-25 定：照旧用，日志说一句）——所以不重算键、不比对
    设置，键是建分组时就记在条目上的。
    """
    if not key:
        return None
    return _read_curve(_cache_dir(kind) / f"{key}.npz")


def store_1d(path, tth, intensity, *, config: str, npt: int,
             tth_min=None, tth_max=None) -> Path:
    """写 1D 产物（原子）。返回产物路径。"""
    target = _cache_dir("1d") / (
        f"{cache_key(path, config=config, npt=npt, tth_min=tth_min, tth_max=tth_max)}.npz")
    return _write_curve(target, tth, intensity,
                        {"source": str(Path(path).name), "config": config,
                         "npt": int(npt), "engine": INTEGRATION_VERSION,
                         "kind": "1d", "created": time.time()})


# 处理产物在磁盘上的目录名**保持历史名字 "bg"**（第一版只有扣背景时起的）：
# 换名字意味着老产物、老台账要么迁移要么读两处，收益只是好看——不值。
# 代码里一律用这个常量，别再写字符串字面量。
PROC_KIND = "bg"


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


def proc_key_of(key_1d: str, settings: dict) -> str:
    """处理产物的键 = **1D 产物的键** + 处理链哈希（背景 + 平滑 + 裁剪）。

    先有 1D 才有处理（处理的是那条曲线），所以键里嵌 1D 的键——1D
    产物换代（换几何/换点数/升版本）时处理产物自然跟着作废。
    单独摘出来是因为"从 1D 产物处理"那条路要**用条目钉住的那把键**
    （而不是按当前设置重算一把），见 store_proc_by_key。
    """
    return hashlib.blake2b(
        (str(key_1d) + proc_settings_hash(settings)).encode("utf-8"),
        digest_size=10).hexdigest()


def proc_settings_hash(settings: dict) -> str:
    """处理产物键的一半：**背景设置哈希** + 链里其余开着的项（平滑 / 裁剪）。

    没有平滑与裁剪时**逐位等于 `bg_settings_hash`**——今天之前存的"扣背景
    产物"因此继续命中，不用迁移也不用重算（tests/test_stage_cache 里有一条
    对照测试钉着这件事）。链里没有开着的项就不参与哈希，是同一个道理的
    另一面：关掉平滑应当回到"未平滑"的那份产物，而不是又生成第三份。
    """
    base = bg_settings_hash(settings)
    extra = chain_parts(settings)
    if not extra:
        return base
    blob = base + json.dumps(extra, sort_keys=True, ensure_ascii=False)
    return hashlib.blake2b(blob.encode("utf-8"), digest_size=8).hexdigest()


def _proc_product(path, *, config: str, npt: int, tth_min=None, tth_max=None,
                  settings: dict) -> Path:
    """处理产物路径：按**当前设置**算出的 1D 键 + 处理链哈希。"""
    base = cache_key(path, config=config, npt=npt, tth_min=tth_min,
                     tth_max=tth_max)
    return _cache_dir(PROC_KIND) / f"{proc_key_of(base, settings)}.npz"


def load_proc(path, *, config: str, npt: int, tth_min=None, tth_max=None,
             settings: dict):
    """读处理产物（背景 → 平滑 → 裁剪）；没有 / 坏了 / 文件不在都返回 None。"""
    key = _safe_key(path, config=config, npt=npt, tth_min=tth_min,
                    tth_max=tth_max)
    if key is None:
        return None
    return _read_curve(_proc_product(path, config=config, npt=npt,
                                     tth_min=tth_min, tth_max=tth_max,
                                     settings=settings))


def store_proc(path, tth, intensity, *, config: str, npt: int, tth_min=None,
               tth_max=None, settings: dict, source: str = "") -> Path:
    """写处理产物（原子）。元数据里记下**整条链**与背景设置。"""
    return _write_curve(
        _proc_product(path, config=config, npt=npt, tth_min=tth_min,
                      tth_max=tth_max, settings=settings),
        tth, intensity,
        meta={"source": source or str(Path(path).name), "config": config,
              "npt": int(npt), "engine": INTEGRATION_VERSION,
              "kind": PROC_KIND, "created": time.time(),
              "chain": chain_desc(settings), "settings": settings})


# ══ 产物台账（界面上的"阶段文件夹"靠它）══════════════════════
# 为什么要有这份索引：产物键是**哈希**（文件指纹 + 几何 + 设置），算不回
# 去——界面上问"这张图有哪些扣背景产物"用键答不出来。而扣背景产物一次
# 批量产出 200 个，散在 bg/ 目录里，除了键没有任何归属信息。台账把"一次
# [批量扣背景] = 一批"这件事记下来：批的标签（时间/锚点数/窗口）、用的
# 几何与设置、批里每张图的源路径与产物键。界面上一个批 = 一个"文件夹"。
#
# 写在 `outputs/_stage/index.json`（原子重写）。它是**台账不是产物**：
# 丢了只是界面上看不到分组（产物还在、还能命中），所以坏文件当空表读，
# 不删也不报错。整批一次写（不是每张一次）：200 张逐张重写 JSON 既慢又
# 可能半路留半截。
def _index_path() -> Path:
    return CACHE_ROOT / "index.json"


def _read_index() -> dict:
    """读台账；没有 / 坏了 / 不是对象都返回空表。"""
    p = _index_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _write_index(data: dict) -> None:
    """原子写台账（tmp + rename，同 _write_curve）。"""
    p = _index_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True,
                              indent=1), encoding="utf-8")
    tmp.replace(p)


def record_batch(kind: str, batch: str, *, label: str, items, config: str,
                 npt: int, tth_min=None, tth_max=None, settings: dict = None,
                 note: str = "") -> int:
    """记一批产物（界面上 = 一个新的"阶段文件夹"）。返回记下的条目数。

    items = [(源文件路径, 产物键), ...]；产物键 = 产物 npz 的 stem，界面
    拿它去 bg/ 目录里核对产物还在不在。同一 batch id 再记一次 = 覆盖
    （同一套设置在同一个时间戳上下标 → 幂等，不会长出重复的分组）。
    """
    data = _read_index()
    node = data.setdefault(kind, {}).setdefault(batch, {})
    node.update({
        "label": label,
        "created": time.time(),
        "config": config,
        "npt": int(npt),
        "tth_min": None if tth_min is None else round(float(tth_min), 6),
        "tth_max": None if tth_max is None else round(float(tth_max), 6),
        "settings": settings or {},
        "note": note,
        "items": {str(Path(p).resolve()): {"key": str(k), "source": Path(p).name}
                  for p, k in items},
    })
    _write_index(data)
    return len(node["items"])


def list_batches(kind: str = "bg", prune: bool = False) -> list:
    """列台账里的批次（新的在前），每项含 id / label / created / 设置 / items。

    prune=True 顺手清掉"产物文件已经不在了"的条目（用户在文件管理器里删了
    outputs/、或 [清空缓存] 只清了某一类）——返回的是清过的表，但**不写盘**；
    要把清理落盘，调用方拿返回值再调 `write_batches(kind, batches)`。
    产物不存在不是脏数据，是"这一条已经没用了"，界面上不该再显示。
    """
    out = []
    for bid, node in (_read_index().get(kind) or {}).items():
        node = dict(node)
        items = dict(node.get("items") or {})
        if prune:
            items = {p: meta for p, meta in items.items()
                     if (_cache_dir(kind) / f"{meta.get('key')}.npz").exists()}
        node["items"] = items
        node["id"] = bid
        out.append(node)
    out.sort(key=lambda n: n.get("created") or 0, reverse=True)
    return out


def write_batches(kind: str, batches) -> None:
    """把 list_batches(prune=True) 的结果落盘（只改这个 kind 的表）。"""
    data = _read_index()
    if not batches:
        data.pop(kind, None)
    else:
        data[kind] = {n["id"]: {k: v for k, v in n.items() if k != "id"}
                      for n in batches}
    _write_index(data)


def drop_batch(kind: str, batch: str) -> int:
    """删掉一批产物（台账 + 盘上的 npz）。返回删掉的产物文件数。

    界面上的"删除这一组"：一次 [批量扣背景] 的结果整组作废（比一张一张
    猜哪个是这一批的产物可靠）。同一张图若被别的批引用（同文件另一套
    设置），那是**另一个键、另一个文件**，不受影响。
    """
    data = _read_index()
    node = (data.get(kind) or {}).pop(batch, None) or {}
    if data.get(kind) == {}:
        data.pop(kind, None)
    _write_index(data)
    n = 0
    for meta in (node.get("items") or {}).values():
        target = _cache_dir(kind) / f"{meta.get('key')}.npz"
        if target.exists():
            target.unlink()
            n += 1
    return n


def store_proc_by_key(key_1d: str, tth, intensity, *, settings: dict,
                     source: str = "") -> Path:
    """按**已有的 1D 产物键**存一份处理产物（原子）。

    给"1D 产物条目也能扣背景"这条路用（用户 2026-09-25 问起）：条目钉住的
    是**那一份** 1D 曲线，扣背景就该挂在那一份的键下面——不按当前设置重算
    键，否则"勾的那条"与"扣的那条"可能是两个设置下的曲线（设置变了以后
    尤其明显）。理由与 load_by_key 相同：产物条目说的是"那一份"。
    """
    target = _cache_dir(PROC_KIND) / f"{proc_key_of(key_1d, settings)}.npz"
    return _write_curve(target, tth, intensity,
                        {"source": source or f"1d:{key_1d}", "kind": PROC_KIND,
                         "engine": INTEGRATION_VERSION, "created": time.time(),
                         "base_key": str(key_1d),
                         "chain": chain_desc(settings), "settings": settings})


def drop_keys(kind: str, keys) -> int:
    """删掉指定的若干份产物（界面上"删这一条 / 删这一组"），返回删掉的文件数。

    1D 产物与 bg 产物通用。bg 那边**台账条目一并摘掉**（引用这些键的那几条）
    ——不摘的话批次里会留着"产物已不在"的空条目：界面靠 refresh 时的 prune
    才看不见它，可一旦那一批只剩空条目，它就该整条消失，而不是留个空壳。
    """
    keys = {str(k) for k in keys if k}
    if not keys:
        return 0
    n = 0
    for key in keys:
        target = _cache_dir(kind) / f"{key}.npz"
        if target.exists():
            target.unlink()
            n += 1
    if kind == "bg":
        data = _read_index()
        changed = False
        for bid, node in list((data.get("bg") or {}).items()):
            items = {p: meta for p, meta in (node.get("items") or {}).items()
                     if str(meta.get("key")) not in keys}
            if len(items) != len(node.get("items") or {}):
                changed = True
                if items:
                    node["items"] = items
                else:
                    data["bg"].pop(bid, None)   # 这一批空了 → 整条摘掉
        if changed:
            if not data.get("bg"):
                data.pop("bg", None)
            _write_index(data)
    return n


def meta_by_key(kind: str, key: str) -> dict:
    """读一份产物的元数据；没有 / 坏了都返回空表。

    产物自己带着"我是怎么来的"（`chain`、`settings`、`created`）——导出
    文件头、以后的产物管理界面都读它，不必回头猜。
    """
    target = _cache_dir(kind) / f"{key}.npz"
    if not key or not target.exists():
        return {}
    try:
        with np.load(target) as data:
            meta = json.loads(str(data["meta"]))
        return meta if isinstance(meta, dict) else {}
    except Exception:                                    # noqa: BLE001
        return {}


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
    台账跟着清：整清 = 连 index.json 一起没；只清一类 = 摘掉那类的
    批次（别的类的分组不受影响）。index.json 自己不算进返回的个数
    （数的是产物文件）。
    """
    root = CACHE_ROOT if kind is None else _cache_dir(kind)
    n = sum(1 for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0
    shutil.rmtree(root, ignore_errors=True)
    if kind is None:
        _index_path().unlink(missing_ok=True)
    else:
        data = _read_index()
        if data.pop(kind, None) is not None:
            _write_index(data)
    return n
