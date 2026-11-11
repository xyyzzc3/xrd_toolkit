"""掩膜检测参数按几何配置记忆（2026-11-11）。

孤立坏点/整环离群的阈值跟探测器与几何摆位走：换一批实验、换一条
几何配置，就应该用自己的一套参数。所以记忆的键是**几何配置名**
（`window.config_name`），不是全局一份。

存哪儿：与 `config_user.json` 同目录的 `mask_params.json`
（环境变量 `XRD_MASK_PARAMS` 可整体覆盖，测试隔离用，与
`XRD_USER_CONFIG` 同一套规矩）。文件损坏/条目非法时退回默认值，
只影响这一项，不拖垮启动。

值范围与 Mask 工作台的高级面板一致；写入前会夹紧并修正窗口奇偶。
"""
import json
import os
from pathlib import Path

from xrd_toolkit.config import USER_CONFIG_PATH

DEFAULTS = {
    "window": 15,          # 局部中值窗口（奇数 px）
    "ratio": 8.0,          # 亮斑倍数
    "max_area": 300,       # 连通域面积上限（px）
    "min_peak": 20000.0,   # 峰值下限（counts）
    "grow": 3,             # 芯外扩圈数（px）
    "n_sigma": 5.0,        # 整环离群阈值（仅整环离群用）
}


def _default_store() -> Path:
    return Path(USER_CONFIG_PATH).with_name("mask_params.json")


STORE = Path(os.environ.get("XRD_MASK_PARAMS", _default_store()))


def _clamp_int(value, lo, hi):
    return max(int(lo), min(int(hi), int(value)))


def _clean(raw) -> dict:
    """规范化一份参数：缺项补默认、非法值忽略、范围夹紧。"""
    out = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return out
    for key in DEFAULTS:
        try:
            value = float(raw.get(key, DEFAULTS[key]))
        except (TypeError, ValueError):
            continue
        if key in ("window", "max_area", "grow"):
            out[key] = int(round(value))
        else:
            out[key] = float(value)
    out["window"] = _clamp_int(out["window"], 3, 51)
    if out["window"] % 2 == 0:                     # 中值窗口必须是奇数
        out["window"] = min(51, out["window"] + 1)
    out["ratio"] = max(1.0, min(50.0, out["ratio"]))
    out["max_area"] = _clamp_int(out["max_area"], 10, 100000)
    out["min_peak"] = max(0.0, min(1.0e7, out["min_peak"]))
    out["grow"] = _clamp_int(out["grow"], 0, 10)
    out["n_sigma"] = max(1.0, min(20.0, out["n_sigma"]))
    return out


def load_all() -> dict:
    """全部几何的参数 {配置名: 参数}；坏文件/坏条目跳过。"""
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:                              # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}
    configs = data.get("configs")
    if not isinstance(configs, dict):
        # 兼容手写的"顶层就是配置名 → 参数"格式
        configs = data
    return {str(k): _clean(v) for k, v in configs.items()
            if isinstance(v, dict)}


def load(config_name) -> dict:
    """某条几何配置的参数；没存过退回默认值（返回副本）。"""
    name = str(config_name or "").strip()
    return dict(load_all().get(name, DEFAULTS))


def save(config_name, params) -> None:
    """保存某条几何配置的参数（整表原子重写）；空配置名直接忽略。"""
    name = str(config_name or "").strip()
    if not name:
        return
    data = load_all()
    data[name] = _clean(params)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "configs": data}
    tmp = STORE.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
        os.replace(tmp, STORE)
    finally:
        tmp.unlink(missing_ok=True)
