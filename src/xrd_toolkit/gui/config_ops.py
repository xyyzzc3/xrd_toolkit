"""几何配置条目的进出口：加载/保存 .poni、删条目、保存为配置。

从 calib.py 拆出来（纯搬迁）：这些动的是**文件与注册表**
（config_user.json / .poni），与校准页的界面无关。
"""
import re
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QFileDialog, QMainWindow, QMessageBox

from xrd_toolkit import config
from xrd_toolkit.gui.calib_model import _calib_state, _result_by_name
from xrd_toolkit.gui.panel_state import _log, _reload_config_combo


def _import_poni(window: QMainWindow) -> None:
    """[加载参数]：读 .poni 交换格式几何文件 → 存成用户配置条目。

    .poni 是 pyFAI 生态通用的几何交换格式（别的工具/命令行标定的
    结果常以这种文件交付）。导入 = 解析出几何 → 照 GUI 配置条目的
    形状存进本地 config_user.json（与 [保存为配置] 同源，重启仍
    在）→ 下拉框重建并自动选中（_apply_config 立即生效）。pyFAI
    只在点击时导入：CLI 用户与纯测试环境不为此多背启动依赖。
    """
    from xrd_toolkit.gui.calib import (_borrow_entry, _calib_sync)   # 破循环：见本模块说明
    path_str, _ = QFileDialog.getOpenFileName(
        window, "选择 .poni 几何文件", "data",
        "pyFAI 几何 (*.poni);;所有文件 (*)")
    if not path_str:
        return
    p = Path(path_str)
    try:
        import pyFAI
        ai = pyFAI.load(str(p))
    except Exception as err:
        _log(window, f".poni 读取失败 {p.name}（{err}）")
        return
    # 必备几何字段缺一不可（探测器库不认识旧型号时 pixel 可能缺失）
    missing = [field for field, val in (
        ("dist", ai.dist), ("poni1", ai.poni1), ("poni2", ai.poni2),
        ("rot1", ai.rot1), ("rot2", ai.rot2),
        ("wavelength", ai.wavelength),
        ("pixel", getattr(ai, "pixel1", None) or getattr(ai, "pixel2", None)),
    ) if val is None]
    if missing:
        _log(window, f".poni 缺少几何字段：{', '.join(missing)}，无法导入")
        return
    pixel = float(ai.pixel1)   # 配置只有单一像素尺寸：非方像素取 pixel1
    if float(ai.pixel2) != pixel:
        _log(window, "注意：.poni 像素非方形（pixel1≠pixel2），配置只"
                     "存单一像素尺寸，已取 pixel1")
    # 束心 = getFit2D 的直射束落点（含倾斜修正）：正是配置条目的 B
    # 语义（B ≠ PONI，探测器有倾斜时两者差可达 23 px，见 config.py
    # 注释）——不能直接用 poni/pixel 投影。约定核实过：pyFAI 里
    # centerX = 列、centerY = 行，与内置 lmfp1_lab6 条目的实测值吻合。
    fit2d = ai.getFit2D()
    entry = {
        "label": p.stem,
        "geometry": {
            "pixel_size_m": pixel,
            "wavelength_m": float(ai.wavelength),
            "dist_m": float(ai.dist),
            "poni1_m": float(ai.poni1),
            "poni2_m": float(ai.poni2),
            "rot1_deg": float(np.degrees(ai.rot1)),
            "rot2_deg": float(np.degrees(ai.rot2)),
        },
        "beam_center": (float(fit2d.centerY), float(fit2d.centerX)),
    }
    # key = 文件名清洗（只留字母数字下划线）；数字开头补前缀，
    # 撞名依次补 _poni1/_poni2…（注册表含内置，循环避开全部重名）
    key = re.sub(r"[^A-Za-z0-9_]", "_", p.stem)
    if not key or key[0].isdigit():
        key = "poni_" + key
    base, n = key, 1
    while key in config.CONFIGS:
        key = f"{base}_poni{n}"
        n += 1
    try:
        is_new = config.save_user_config(key, entry)
    except ValueError as err:
        _log(window, f".poni 导入失败（{err}）")
        return
    _reload_config_combo(window, key)
    # 导入的几何同时成为"当前配置"的起点（新批次的导入通道），并把像素
    # 确认清掉——像素值换了必须重新确认
    window.calib_pixel_ok_m = None
    _borrow_entry(window, key)
    _log(window, f"已导入 .poni → 配置条目 {key}"
                 f"（{'新增' if is_new else '覆盖同名条目'}，已自动选中，"
                 f"重启后仍在）")
    _calib_sync(window)


def _save_poni(window: QMainWindow) -> None:
    """[保存参数]：把当前选中的几何配置写成标准 .poni 文件。

    保存内容 = 探测器距离 / 中心点 / 像素尺寸 / 波长 / 倾斜角。
    中心点在 .poni 标准里就是 poni1/poni2 米制坐标（像素束心含
    显示语义、不含倾斜修正，不属于几何量——加载回来时由
    getFit2D 重算，往返探测已验证自洽）。作业规格里的"掩膜文件
    路径"是可选项：引擎尚未支持掩膜，且 pyFAI .poni 格式本身没
    有掩膜字段，故不写。保存成功记日志（列出保存内容，供核对）。
    默认文件名 = {配置名}.poni、默认目录 outputs/，同 [加载参数]
    共用一套读写口径（pyFAI 只在点击时导入，CLI/测试不为启动背
    依赖）。
    """
    cfg = window.config   # 当前选中条目（label / geometry / beam_center）
    geom = cfg["geometry"]
    default = str(Path("outputs") / f"{window.config_name}.poni")
    path_str, _ = QFileDialog.getSaveFileName(
        window, "保存几何参数（.poni）", default,
        "pyFAI 几何 (*.poni);;所有文件 (*)")
    if not path_str:
        return   # 用户取消
    if not path_str.lower().endswith(".poni"):
        path_str += ".poni"
    try:
        from pyFAI.geometry import Geometry
        g = Geometry(
            dist=float(geom["dist_m"]),
            poni1=float(geom["poni1_m"]),
            poni2=float(geom["poni2_m"]),
            rot1=float(np.radians(geom["rot1_deg"])),
            rot2=float(np.radians(geom["rot2_deg"])),
            pixel1=float(geom["pixel_size_m"]),
            pixel2=float(geom["pixel_size_m"]),
            wavelength=float(geom["wavelength_m"]))
        Path(path_str).parent.mkdir(parents=True, exist_ok=True)
        g.save(path_str)
    except Exception as err:
        _log(window, f".poni 保存失败（{err}）")
        return
    _log(window, f"已保存几何参数 → {path_str}"
                 f"（距离 {geom['dist_m'] * 1e3:.2f} mm，"
                 f"中心 poni1={geom['poni1_m']:.6g} m, "
                 f"poni2={geom['poni2_m']:.6g} m，"
                 f"像素 {geom['pixel_size_m'] * 1e6:.1f} µm，"
                 f"波长 {geom['wavelength_m'] * 1e10:.4f} Å，"
                 f"倾斜 rot1={geom['rot1_deg']:.4f}°, "
                 f"rot2={geom['rot2_deg']:.4f}°）")


def _sync_del_config_btn(window: QMainWindow) -> None:
    """[删除] 按钮置灰同步：选中内置条目时不可删（人工登记注册表）。

    下拉框当前索引变化时由连接调用；_reload_config_combo 重建下拉
    框后索引不变不触发信号，调用方（_delete_config / _calib_sync）
    再显式补一次。
    """
    btn = getattr(window, "del_config_btn", None)
    combo = getattr(window, "config_combo", None)
    if btn is None or combo is None:
        # 版面还没建全（校准页先于分析页建，此刻没有下拉框）——安全忽略，
        # 分析页建好后会自己再同步一次
        return
    idx = combo.currentIndex()
    btn.setEnabled(idx >= 0 and
                   combo.itemData(idx) not in config.BUILTIN_CONFIGS)


def _delete_config(window: QMainWindow) -> None:
    """[删除]：把当前选中的用户配置条目从注册表与磁盘移除。

    只删用户条目（.poni 导入 / [保存为配置] 产生）——内置条目是
    config.py 人工登记的注册表，按钮置灰 + 处理函数双保险拒绝。
    删除前弹确认框；删后下拉框重建并切回默认条目（删除的对象是
    "当前选中"条目，删完当前选中已不存在）。
    """
    combo = window.config_combo
    key = combo.itemData(combo.currentIndex())
    if key in config.BUILTIN_CONFIGS:
        _log(window, f"内置条目 {key} 不可删除（人工登记的注册表）")
        return
    entry = config.USER_CONFIGS.get(key)
    if entry is None:
        _log(window, f"用户条目 {key} 不存在，无需删除")
        return
    answer = QMessageBox.question(
        window, "删除配置",
        f"删除用户配置条目 {key}（{entry['label']}）？\n"
        "删除后不可恢复（内置条目不受影响）。",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
    if answer != QMessageBox.Yes:
        return
    try:
        removed = config.remove_user_config(key)
    except ValueError as err:
        _log(window, f"删除失败（{err}）")
        return
    if not removed:
        _log(window, f"用户条目 {key} 不存在，无需删除")
        return
    _reload_config_combo(window, config.DEFAULT_CONFIG)
    _sync_del_config_btn(window)
    _log(window, f"已删除配置条目 {key}（{entry['label']}），"
                 f"已切回默认条目 {config.DEFAULT_CONFIG}")


def _suggest_config_key(current_key: str) -> str:
    """从当前配置 key 递推新条目建议名：lmfp1_lab6 → lmfp2_lab6。

    懒前缀匹配**第一个**数字段 +1（新批次编号顺延，命名约定见
    config.py；后缀里再出现数字不受影响，如 "_lab6" 的 6 不动）；
    对不上该模式就退回通用提示名。
    """
    m = re.match(r"^(.*?)(\d+)(.*)$", current_key)
    if m:
        return f"{m.group(1)}{int(m.group(2)) + 1}{m.group(3)}"
    return "lab6_calib"


def _confirm_overwrite(window: QMainWindow, key: str) -> bool:
    """用户条目重名确认：覆盖返回 True（用户自己拍板，点击即复核）。"""
    return QMessageBox.question(
        window, "覆盖已有配置",
        f"用户配置 {key} 已存在。用这次的校准结果覆盖它吗？"
    ) == QMessageBox.Yes


def _save_calib_config(window: QMainWindow) -> None:
    """[存为配置]：把**当前配置**的几何 → 命名用户条目（本地落盘）。

    校验 key/label → 与内置条目撞名拒绝、与已存用户条目撞名弹确认
    覆盖 → config.save_user_config 落盘 → 下拉框重建并自动选中新
    条目（_apply_config 把几何填进参数坞，保存即生效）。

    血缘一并写入：method = 这份几何是哪个动作产出的（raw/auto/manual/
    refined/custom）、derived_from = 这一批的起点借自哪条、created =
    保存时间。

    保存前查**像素尺寸确认**那道门（与开始校准同一道，见 calib.
    _initial_ready）：条目会被别的批次、CLI 脚本原样拿去用，而像素
    填错时拟合会把距离同比例凑回来——不确认就存，等于把一份"看着
    正常、距离存疑"的几何发出去。
    """
    from xrd_toolkit.gui.calib import _initial_ready   # 破循环：见模块说明
    state = _calib_state(window)
    if state.get("current_geom") is None:
        _log(window, "还没有可保存的几何（先选一条配置或用 [编辑…] 填）")
        return
    if not _initial_ready(window):
        return   # 只提示、不保存（消息由 _initial_ready 记进日志）
    key = window.calib_key_edit.text().strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        _log(window, "key 无效：只允许字母/数字/下划线，且以字母或"
                     "下划线开头（如 lmfp2_lab6）")
        return
    if key in config.BUILTIN_CONFIGS:
        _log(window, f"key {key} 与内置条目重名（内置条目人工登记，"
                     "不可覆盖），换一个名字")
        return
    label = window.calib_label_edit.text().strip()
    if not label:
        _log(window, "请先填写批次备注（label）")
        return
    if key in config.USER_CONFIGS and not _confirm_overwrite(window, key):
        return
    state = _calib_state(window)
    g = state["current_geom"]
    pixel = g["pixel_size_m"]
    name = state["slots"]["current"]
    item = _result_by_name(state, name) if name else None
    entry = {
        "label": label,
        "geometry": {
            "pixel_size_m": pixel,
            "wavelength_m": g["wavelength_m"],
            "dist_m": g["dist_m"],
            "poni1_m": g["poni1_m"],
            "poni2_m": g["poni2_m"],
            "rot1_deg": g["rot1_deg"],
            "rot2_deg": g["rot2_deg"],
        },
        "beam_center": tuple(
            g.get("beam_center_rc") or window.config["beam_center"]),
        # 血缘：哪个动作产出的（raw/auto/manual/refined/custom）、
        # 这一批的起点借自哪条、何时保存
        "method": item["kind"] if item else
                  ("custom" if state.get("custom") else "raw"),
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    if item is not None and item["result"].get("residual_deg") is not None:
        entry["residual_deg"] = item["result"]["residual_deg"]
    if state.get("base_key") in config.CONFIGS:
        entry["derived_from"] = state["base_key"]
    try:
        is_new = config.save_user_config(key, entry)
    except ValueError as err:
        _log(window, f"保存失败：{err}")
        return
    _reload_config_combo(window, key)
    if is_new:
        _log(window, f"已保存新配置条目 {key} 并自动选中；重启后仍在，"
                     f"命令行脚本可用 --config {key} 取用")
    else:
        _log(window, f"已覆盖配置条目 {key} 并自动选中")
