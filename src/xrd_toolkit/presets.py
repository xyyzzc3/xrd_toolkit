"""项目预设（2026-11-02 起步，2026-11-10 扩成可命名预设层）。

v0.2.0 的定位是"实验室项目工具"：默认几何、第二相、原位三相模型和默认
目录都是 LMFP 这一套数据的配置。以前这些字面量散落在 GUI / simulation
里，看起来像"软件只会做 LMFP"；现在集中到这里。

v0.2.1 B1（2026-11-10）起，预设不再只有内置的一份：
  * 内置预设（LMFP 三相）写在本文件，不可删/不可覆盖；
  * 用户预设写在 `presets_user.json`（默认与 config_user.json 同目录；
    测试/脚本可用 XRD_USER_PRESETS 整体覆盖），可新增/覆盖/删除；
  * 文件里同时记住最后使用的预设名（`active`），下次启动自动接上。

预设只覆盖"换项目时要改的输入"：几何配置、第二相 CIF、原位帧目录、
原位 mask 文件名、相库目录。它**不改变**原位三相引擎的物理模型
（仍假定 rich/poor/Al），也不做任意 N 相批量。
"""
import json
import os
import sys
from pathlib import Path

from xrd_toolkit import paths

DEFAULT_PRESET_NAME = "LMFP 三相"

# 预设字段的默认值（缺字段的旧文件/手写文件按这里补齐）
DEFAULTS = {
    # 几何配置 key（config.CONFIGS 的条目）
    "geometry": "lmfp1_lab6",
    # 物相筛选时视为"第二相"、不参与主相候选的 CIF 文件名
    "second_phase_cif": "Al_metal.cif",
    # 原位工作台的默认帧目录与默认 mask 文件名
    "insitu_dir": "~/Data/XRD/lmfpdata",
    "insitu_mask_name": "lmfp1_lab6_mask.npy",
    # 相库目录；空字符串 = 用 paths.structures_dir()（随包参考 CIF）
    "phase_library": "",
}
_FIELDS = tuple(DEFAULTS)

BUILTIN_PRESETS = {
    DEFAULT_PRESET_NAME: {"name": DEFAULT_PRESET_NAME, **DEFAULTS},
}
# 旧代码直接读 PROJECT_PRESET；保留为内置预设的副本（不随用户选择变化）
PROJECT_PRESET = dict(BUILTIN_PRESETS[DEFAULT_PRESET_NAME])


def _default_user_presets_path() -> Path:
    """用户预设文件的默认位置（拆成函数便于测试/打包判断）。

    开发模式 = 本文件同目录（与 config_user.json 并排）；
    打包后   = 用户数据目录（包目录只读，升级即换）。
    """
    if paths.FROZEN:
        return paths.OUTPUTS_DIR / "presets_user.json"
    return Path(__file__).with_name("presets_user.json")


# 环境变量 XRD_USER_PRESETS 可整体覆盖（测试/脚本隔离用）
USER_PRESETS_PATH = Path(os.environ.get(
    "XRD_USER_PRESETS", _default_user_presets_path()))


def _clean_preset(raw, fallback_name="") -> dict:
    """校验并规范化一份预设：只留五个字段，缺项补默认值。

    非法结构抛 ValueError；字段类型不对时用默认值（不因为一个手写
    JSON 的笔误把整份预设丢掉）。名称从 raw["name"] 或 fallback_name
    取；两者都没有时抛错。
    """
    if not isinstance(raw, dict):
        raise ValueError("预设必须是 JSON 对象")
    name = str(raw.get("name") or fallback_name or "").strip()
    if not name:
        raise ValueError("预设缺名称")
    out = {"name": name}
    for key in _FIELDS:
        value = raw.get(key, DEFAULTS[key])
        out[key] = value.strip() if isinstance(value, str) else DEFAULTS[key]
    return out


def _read_state(path=None) -> dict:
    """读预设文件 → {"active": 名字, "presets": {名字: 预设}}。

    文件不存在 = 空用户层（active 用内置默认）；JSON 损坏/顶层类型不对
    = 警告并忽略整个文件；单条预设无效 = 警告并跳过该条。同时兼容
    旧式"顶层直接是 {名字: 预设}"的文件（没有 active，按内置默认）。
    """
    path = Path(path) if path is not None else USER_PRESETS_PATH
    empty = {"active": DEFAULT_PRESET_NAME, "presets": {}}
    if not path.is_file():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"警告：预设文件 {path} 读取失败，已忽略（{err}）",
              file=sys.stderr)
        return empty
    if not isinstance(raw, dict):
        print(f"警告：预设文件 {path} 顶层不是对象，已忽略",
              file=sys.stderr)
        return empty

    if isinstance(raw.get("presets"), dict):
        presets_raw = raw["presets"]
        active = str(raw.get("active") or DEFAULT_PRESET_NAME).strip()
    else:
        # 旧式/手写格式：顶层就是名字 → 预设
        presets_raw = raw
        active = DEFAULT_PRESET_NAME

    presets = {}
    for name, item in presets_raw.items():
        key = str(name).strip()
        try:
            preset = _clean_preset(item, fallback_name=key)
        except ValueError as err:
            print(f"警告：预设 {key!r} 无效，已跳过（{err}）",
                  file=sys.stderr)
            continue
        if not key:
            continue
        preset["name"] = key
        if key in BUILTIN_PRESETS:
            print(f"警告：用户预设 {key!r} 与内置预设重名，已跳过",
                  file=sys.stderr)
            continue
        presets[key] = preset
    if active not in presets and active not in BUILTIN_PRESETS:
        active = DEFAULT_PRESET_NAME
    return {"active": active, "presets": presets}


def _write_state(state, path=None) -> None:
    """原子写入预设文件（与 config / 仪器基线同一套先写临时文件再替换）。"""
    path = Path(path) if path is not None else USER_PRESETS_PATH
    payload = {
        "version": 1,
        "active": str(state.get("active") or DEFAULT_PRESET_NAME),
        "presets": {
            name: {key: preset.get(key, DEFAULTS[key]) for key in _FIELDS}
            for name, preset in (state.get("presets") or {}).items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def preset_names(path=None) -> list:
    """全部预设名：内置在前，用户预设按名字排序。"""
    state = _read_state(path)
    return list(BUILTIN_PRESETS) + sorted(state["presets"])


def is_builtin(name) -> bool:
    return str(name) in BUILTIN_PRESETS


def raw_preset(name, path=None):
    """按名字取原始预设 dict；不存在返回 None。"""
    name = str(name)
    if name in BUILTIN_PRESETS:
        return dict(BUILTIN_PRESETS[name])
    return _read_state(path)["presets"].get(name)


def resolve_preset(raw) -> dict:
    """补齐默认值并把几何 key 校验成 CONFIGS 里存在的条目。

    返回的 dict 带 `warnings`（运行时提示，不写回文件）；几何不存在时
    退回 DEFAULT_CONFIG，避免一个删掉的配置条目让预设整体不可用。
    """
    from xrd_toolkit.config import CONFIGS, DEFAULT_CONFIG
    preset = _clean_preset(raw)
    warnings = []
    if preset["geometry"] not in CONFIGS:
        fallback = (DEFAULT_CONFIG if DEFAULT_CONFIG in CONFIGS
                    else next(iter(CONFIGS), DEFAULT_CONFIG))
        warnings.append(
            f"几何配置 {preset['geometry']!r} 不存在，已改用 {fallback}")
        preset["geometry"] = fallback
    preset["warnings"] = warnings
    return preset


def preset_phase_library(preset) -> Path:
    """预设的相库目录；空/不存在时退回随包参考 CIF 目录。"""
    raw = str((preset or {}).get("phase_library") or "").strip()
    path = Path(raw).expanduser() if raw else paths.structures_dir()
    return path if path.is_dir() else paths.structures_dir()


def get_preset(name=None, path=None) -> dict:
    """按名字取规范化后的预设；名字不存在时退回内置默认。"""
    target = str(name or "").strip()
    raw = raw_preset(target, path) if target else None
    if raw is None:
        raw = dict(BUILTIN_PRESETS[DEFAULT_PRESET_NAME])
    return resolve_preset(raw)


def active_preset_name(path=None) -> str:
    """上次使用的预设名；文件不存在/记录失效时退回内置默认。"""
    return _read_state(path)["active"]


def set_active_preset(name, path=None) -> None:
    """记住当前预设（下次启动直接接上）。名字必须是已有预设。"""
    name = str(name).strip()
    if name not in preset_names(path):
        raise ValueError(f"预设 {name!r} 不存在")
    state = _read_state(path)
    state["active"] = name
    _write_state(state, path)


def save_user_preset(name, preset, path=None) -> bool:
    """保存/覆盖一份用户预设；返回 True = 新增，False = 覆盖。

    内置预设不可覆盖（抛 ValueError）；名称为空也抛 ValueError。
    """
    name = str(name).strip()
    if not name:
        raise ValueError("预设名称不能为空")
    if name in BUILTIN_PRESETS:
        raise ValueError(f"{name!r} 是内置预设，不能覆盖；换个名字")
    state = _read_state(path)
    clean = _clean_preset(dict(preset, name=name), fallback_name=name)
    clean["name"] = name
    is_new = name not in state["presets"]
    state["presets"][name] = clean
    _write_state(state, path)
    return is_new


def remove_user_preset(name, path=None) -> bool:
    """删除一份用户预设；返回是否真的删了。

    内置预设抛 ValueError；删除当前预设时 active 退回内置默认。
    """
    name = str(name).strip()
    if name in BUILTIN_PRESETS:
        raise ValueError(f"{name!r} 是内置预设，不能删除")
    state = _read_state(path)
    if name not in state["presets"]:
        return False
    del state["presets"][name]
    if state["active"] == name:
        state["active"] = DEFAULT_PRESET_NAME
    _write_state(state, path)
    return True
