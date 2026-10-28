"""Mask 后的 2D 数据产物（2026-10-28）。

设计（讨论结论）：mask 是 2D→1D 之间的一个可选阶段。产物**不复制像素**，
而是一个小 manifest：

    raw_path   原始图像路径
    mask       bool 数组（packbits 压缩），True = 被遮掉
    label      mask 名（保存的文件名/用户标签）
    mask_hash  mask 内容的短哈希
    shape      图像形状
    ...

manifest 本身是一个真实文件（`.mask2d.npz`），所以现有“按路径取数/缓存”
的整条链路都能直接用：1D 缓存键、面板键、对比/热图/导出都把它当成一张
“源数据”即可。`data_loader.load_diffraction_image` 读到 manifest 时返回
`np.ma.MaskedArray`（原始像素 + mask），积分器自动排除被遮像素。

mask 像素**不被改写**（不填 0/NaN）：这样 2D 查看、导出、积分都保持
物理含义；要“看不见坏点”只是显示层的事。
"""
import hashlib
import json
import os
import time
from pathlib import Path

import numpy as np

from xrd_toolkit.services import stage_cache

MANIFEST_SUFFIX = ".mask2d.npz"
KIND = "masked2d"


def _dir() -> Path:
    """产物目录跟着 stage cache 走（XRD_STAGE_CACHE 隔离对脚本/测试生效）。"""
    return Path(stage_cache.CACHE_ROOT) / KIND


def _labels_path() -> Path:
    return _dir() / "labels.json"


def _read_labels() -> dict:
    """mask 标签的旁路映射：key → {label, mask_path}。

    标签只影响显示；写进 manifest 会改文件指纹、让 1D 缓存全部失效，
    所以单独放一个小 json。manifest 里的 label 是创建时的初始值。
    """
    try:
        data = json.loads(_labels_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_labels(data: dict) -> None:
    path = _labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)


def _merged_meta(meta: dict) -> dict:
    """manifest 元数据 + 旁路标签（标签以 labels.json 为准）。"""
    key = str(meta.get("key") or "")
    over = _read_labels().get(key) if key else None
    if isinstance(over, dict):
        meta = {**meta, **{k: over[k] for k in ("label", "mask_path")
                           if k in over}}
    return meta


def is_manifest(path) -> bool:
    return str(path).lower().endswith(MANIFEST_SUFFIX)


def mask_hash(mask) -> str:
    """mask 内容哈希（packbits 后算，4 MB bool → ~0.5 MB 输入）。"""
    arr = np.ascontiguousarray(np.asarray(mask, dtype=bool))
    return hashlib.blake2b(np.packbits(arr).tobytes(),
                           digest_size=10).hexdigest()


def _write(path: Path, mask: np.ndarray, meta: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    packed = np.packbits(np.asarray(mask, dtype=bool))
    np.savez_compressed(path, mask=packed,
                        meta=np.array(json.dumps(meta, ensure_ascii=False)))
    return path


def create(raw_path, mask, *, label: str = "", mask_path=None) -> dict:
    """为 (raw_path, mask) 建/复用 manifest；返回 meta（含 path）。

    键 = 原始文件指纹（名字/大小/mtime/头部）+ mask 内容哈希。同一张图
    同一 mask 重复应用 → 同一个文件（覆盖写，不堆重复条目）；mask 改了
    → 新键、新文件，旧产物保留。
    """
    arr = np.asarray(mask, dtype=bool)
    if arr.ndim != 2:
        raise ValueError(f"mask 必须是 2D（现在是 {arr.ndim}D）")
    raw = Path(raw_path).expanduser().resolve()
    if is_manifest(raw):
        raise ValueError("不能对 Mask 后的 2D 再建 Mask 2D；请用原始图")
    name, size, mtime, head = stage_cache.fingerprint(raw)
    mhash = mask_hash(arr)
    blob = json.dumps({"name": name, "size": size, "mtime_ns": mtime,
                       "head": head, "mask": mhash}, sort_keys=True)
    key = hashlib.blake2b(blob.encode("utf-8"), digest_size=10).hexdigest()
    path = _dir() / f"{key}{MANIFEST_SUFFIX}"
    if path.exists():
        try:
            old = meta_of(path)
            if (old.get("mask_hash") == mhash
                    and old.get("raw_path") == str(raw)):
                # 同一 raw+mask：manifest 不重写（保住 1D 缓存），
                # 只把新的显示标签写进旁路映射。
                new_tag = {"label": str(label or ""),
                           "mask_path": "" if mask_path is None
                           else str(mask_path)}
                if (new_tag["label"] != str(old.get("label") or "")
                        or new_tag["mask_path"]
                        != str(old.get("mask_path") or "")):
                    labels = _read_labels()
                    labels[key] = new_tag
                    _write_labels(labels)
                    old.update(new_tag)
                old["path"] = str(path)
                return old
        except Exception:                              # noqa: BLE001
            pass
    meta = {"kind": KIND, "key": key, "raw_path": str(raw),
            "mask_hash": mhash, "label": str(label or ""),
            "mask_path": "" if mask_path is None else str(mask_path),
            "masked_pixels": int(arr.sum()),
            "total_pixels": int(arr.size),
            "shape": [int(x) for x in arr.shape],
            "created": time.time(), "path": str(path)}
    _write(path, arr, meta)
    return meta


def load_manifest(path) -> tuple:
    """读 manifest → (raw_path, mask, meta)。文件/原始图不在时抛 OSError。"""
    p = Path(path)
    with np.load(p, allow_pickle=False) as z:
        packed = np.asarray(z["mask"], dtype=np.uint8)
        meta = json.loads(str(z["meta"].item()))
    shape = tuple(int(x) for x in meta.get("shape") or ())
    if len(shape) != 2:
        raise ValueError(f"manifest 缺少图像形状：{p}")
    mask = np.unpackbits(packed)[:shape[0] * shape[1]].astype(bool)
    mask = mask.reshape(shape)
    raw = Path(meta.get("raw_path") or "")
    if not raw.is_file():
        raise OSError(f"masked 2D 的原始图不在了：{raw}")
    return str(raw), mask, _merged_meta(meta)


def meta_of(path) -> dict:
    """只读 manifest 元数据（列表/文件栏用，不解包 mask）+ 旁路标签。"""
    p = Path(path)
    with np.load(p, allow_pickle=False) as z:
        meta = json.loads(str(z["meta"].item()))
    return _merged_meta(meta)


def list_products() -> list:
    """全部 manifest 的元数据（坏文件跳过）；按创建时间旧→新。"""
    out = []
    d = _dir()
    if not d.is_dir():
        return out
    for p in sorted(d.glob(f"*{MANIFEST_SUFFIX}")):
        try:
            meta = meta_of(p)
        except Exception:                              # noqa: BLE001
            continue
        meta.setdefault("path", str(p))
        out.append(meta)
    out.sort(key=lambda m: float(m.get("created") or 0.0))
    return out


def export_npz(path, target) -> tuple:
    """把 Mask 2D 导出成独立的 `.npz`：image + mask + meta。

    与内部 manifest 不同，这个文件**自带像素**，换机器/不给原始图也能用；
    坏点仍是 mask（不填 0/NaN），读法：
        z = np.load("x.npz"); image = z["image"]; mask = z["mask"]
    返回 (target, shape, masked_pixels)。
    """
    from xrd_toolkit.services.data_loader import load_diffraction_image
    raw_path, mask, meta = load_manifest(path)
    image = np.asarray(load_diffraction_image(raw_path), dtype=float)
    if image.shape != mask.shape:
        raise ValueError(
            f"mask 形状 {mask.shape} 与原始图 {image.shape} 不一致")
    target = Path(target)
    if target.suffix.lower() != ".npz":
        target = target.with_suffix(".npz")
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        target, image=image, mask=np.asarray(mask, dtype=bool),
        meta=np.array(json.dumps(meta, ensure_ascii=False)))
    return target, image.shape, int(mask.sum())


def delete(path) -> bool:
    """删掉一条 masked 2D manifest（原始图/ mask 文件都不动）。"""
    p = Path(path)
    try:
        key = str(meta_of(p).get("key")
                  or p.name.replace(MANIFEST_SUFFIX, ""))
    except Exception:                                  # noqa: BLE001
        key = ""
    try:
        p.unlink()
        ok = True
    except FileNotFoundError:
        ok = False
    if key:
        labels = _read_labels()
        if labels.pop(key, None) is not None:
            _write_labels(labels)
    return ok
