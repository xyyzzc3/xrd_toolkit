"""LaB₆ 仪器基线的落盘（2026-11-05）。

精修工作台的 [存为仪器基线] 原来只写窗口内存，关程序就没了；1D 页
W-H 的「LaB₆ 基线」也因此只能在本次运行内用。这里把同一份参数写成
一个小 JSON，下次启动自动读回。

文件默认放在 `config_user.json` 旁边（开发模式 = `src/xrd_toolkit/`，
打包后 = 用户数据目录）；测试/脚本可用 `XRD_INSTRUMENT_BASELINE`
整体指到临时文件（与 `XRD_OUTPUTS` / `XRD_USER_CONFIG` 同一套规矩）。

格式：
{
  "version": 1,
  "saved_at": "2026-11-05T18:00:00",
  "source": "LaB6_NIST660c.cif",
  "params": {"zero": ..., "u": ..., "v": ..., "w": ..., "x": ..., "y": ...},
  "wavelength": 0.1223
}

只认六个参数（zero/U/V/W/X/Y）；缺的/非有限的丢掉——一条坏数据
不该让整份基线失效。读不出来时返回 None，调用方按"没有基线"处理。
"""
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path

from xrd_toolkit import config

PARAM_NAMES = ("zero", "u", "v", "w", "x", "y")
_VERSION = 1


def baseline_path() -> Path:
    """基线的默认落盘位置（测试可用环境变量整体覆盖）。"""
    override = os.environ.get("XRD_INSTRUMENT_BASELINE")
    if override:
        return Path(override).expanduser()
    return config.USER_CONFIG_PATH.with_name("instrument_baseline.json")


def _clean_params(raw) -> dict:
    """只留六个有限浮点数；缺项/坏项直接丢掉。"""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for name in PARAM_NAMES:
        try:
            value = float(raw.get(name))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            out[name] = value
    return out


def save_baseline(record, path: Path = None) -> Path:
    """把一份基线原子写盘，返回实际路径。

    record 至少要有 `params`；`wavelength` / `source` 可选。写失败抛
    OSError，参数为空抛 ValueError——调用方负责转成状态行提示。
    """
    record = record or {}
    params = _clean_params(record.get("params"))
    if not params:
        raise ValueError("仪器基线没有可用的峰形参数")
    try:
        wavelength = float(record.get("wavelength", 0.0) or 0.0)
    except (TypeError, ValueError):
        wavelength = 0.0
    if not math.isfinite(wavelength):
        wavelength = 0.0

    path = Path(path) if path is not None else baseline_path()
    data = {
        "version": _VERSION,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "source": str(record.get("source") or ""),
        "params": params,
        "wavelength": wavelength,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8")
        os.replace(tmp, path)          # 同目录原子替换，断电不留半截
    finally:
        tmp.unlink(missing_ok=True)
    return path


def load_baseline(path: Path = None):
    """读回基线；文件不存在/损坏/没有有效参数 → None。"""
    path = Path(path) if path is not None else baseline_path()
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        # JSONDecodeError 与 UnicodeDecodeError 都是 ValueError 的子类：
        # 坏文件/二进制文件都不该让程序启动失败。
        print(f"警告：仪器基线 {path} 读取失败，已忽略（{err}）",
              file=sys.stderr)
        return None
    if not isinstance(raw, dict):
        return None
    params = _clean_params(raw.get("params"))
    if not params:
        return None
    try:
        wavelength = float(raw.get("wavelength", 0.0) or 0.0)
    except (TypeError, ValueError):
        wavelength = 0.0
    if not math.isfinite(wavelength):
        wavelength = 0.0
    return {
        "version": raw.get("version", _VERSION),
        "saved_at": str(raw.get("saved_at") or ""),
        "source": str(raw.get("source") or ""),
        "params": params,
        "wavelength": wavelength,
    }
