#!/usr/bin/env python3
"""生成 THIRD_PARTY_NOTICES.txt —— 随包分发的第三方许可声明（2026-10-03）。

为什么必须有：LGPL / MIT / BSD 一类的许可都要求**分发二进制时随附许可
文本**——0.1 的 zip 会被同事之间转发，声明得跟着文件走（只挂在 GitHub
页面上不算"随附"）。其中 PySide6/Qt 是最要紧的一类（LGPL-3.0）：它的
wheel 里**一个许可文件都没有**，全文只能从权威源取（packaging/licenses/
下存了 LGPL-3.0 与 GPL-3.0 官方文本，从 gnu.org 取的）。

做法：把构建环境里的发行包（去掉 pip/setuptools/PyInstaller 这些**构建
工具**——它们不进包）逐个读元数据：版本 / 许可标识 / 许可文件；能收到
正文的收进来，按名字排序。输出提交进仓库，并由 xrd_toolkit.spec 打进
app 包（Contents/Resources/），「关于」对话框里也能打开它。

依赖变动（改 constraints.txt）后重新跑一遍：
    python packaging/make_licenses.py
"""
import sys
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "THIRD_PARTY_NOTICES.txt"

# 只进构建机器、不进包的工具（不列进声明）：pip/setuptools 一族、
# PyInstaller 及其依赖（altgraph/macholib 是它打包 macOS 应用用的）、
# 项目自己
BUILD_ONLY = {"pip", "setuptools", "wheel", "pyinstaller",
              "pyinstaller-hooks-contrib", "altgraph", "macholib",
              "modulegraph", "pyinstaller-hooks", "git-filter-repo",
              "filelock", "xrd-toolkit"}

# 许可正文兜底：PySide6/Qt 的 wheel 不带任何许可文件 → 用官方文本
QT_LICENSE_FILES = ("LGPL-3.0.txt", "GPL-3.0.txt")
QT_PKGS = {"pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6"}


def _license_files(dist) -> list:
    """该发行包自带的许可/版权文件（正文）。

    名字要**不区分大小写**地认：pyFAI/silx 的 wheel 里叫小写 `copyright`
    （Debian 风格），matplotlib 叫 `LICENSE`，scipy 叫 `LICENSE.txt`——
    各家用各家的拼法（第一版脚本只认大写，漏了 pyFAI 的）。
    """
    import fnmatch
    patterns = ("license*", "licence*", "copying*", "copyright*",
                "notice*", "authors*", "licenses/*")
    out = []
    base = Path(dist._path)                     # dist-info 目录
    for f in sorted(base.rglob("*")):
        if not f.is_file() or f.stat().st_size >= 200_000:
            continue
        rel = f.relative_to(base).as_posix().lower()
        if any(fnmatch.fnmatch(rel, p) for p in patterns):
            out.append(f)
    return out


def _license_label(meta) -> str:
    expr = meta.get("License-Expression")
    if expr:
        return expr
    lic = (meta.get("License") or "").strip()
    if lic and len(lic) < 60 and "\n" not in lic:
        return lic
    for cl in meta.get_all("Classifier") or []:
        if cl.startswith("License ::"):
            return cl.split("::")[-1].strip()
    return "见下方正文 / see text below"


def collect() -> list:
    rows = []
    for dist in metadata.distributions():
        name = (dist.metadata["Name"] or "").strip()
        if not name or name.lower() in BUILD_ONLY:
            continue
        rows.append((name, dist.version, _license_label(dist.metadata),
                     _license_files(dist)))
    rows.sort(key=lambda r: r[0].lower())
    return rows


def main() -> int:
    rows = collect()
    lines = [
        "=" * 72,
        "THIRD-PARTY NOTICES — XRD Toolkit 0.1.0",
        "=" * 72,
        "",
        "本软件随附以下第三方组件。各组件版权归其各自作者所有，按各自的",
        "许可条款分发（全文见本文件下半部分）。",
        "",
        "This distribution bundles the third-party components listed below,",
        "each under its own license. Full license texts follow.",
        "",
        "组件清单 / Components",
        "-" * 72,
    ]
    for name, version, label, files in rows:
        lines.append(f"  {name} {version} — {label}")
    lines += [
        "",
        "关于 PySide6 / Qt（LGPL-3.0-only）",
        "-" * 72,
        "XRD Toolkit 以**动态链接**方式使用 PySide6（Qt for Python）：Qt 以",
        "独立的共享库（.dylib / .dll）随应用分发，用户可以自行替换为修改",
        "过的 Qt 版本（LGPL-3.0 的要求之一）。Qt 源代码可从 https://qt.io/",
        "或 Qt 的官方仓库获取。",
        "",
        "PySide6 / Qt for Python is used under the LGPL-3.0-only option,",
        "dynamically linked: the Qt shared libraries ship as separate files",
        "inside this bundle and may be replaced by the user.",
        "",
        "— 许可全文 / Full license texts —",
        "=" * 72,
    ]
    seen = set()
    for fname in QT_LICENSE_FILES:              # Qt 的正文放最前（最要紧）
        f = HERE / "licenses" / fname
        if f.exists():
            lines += ["", f"########## {fname} ##########", "",
                      f.read_text(encoding="utf-8").rstrip()]
            seen.add(fname)
    for name, version, label, files in rows:
        for f in files:
            if f.name in seen:
                continue
            seen.add(f.name)
            text = f.read_text(encoding="utf-8", errors="replace").rstrip()
            lines += ["", f"########## {name} {version} — {f.name} "
                          "##########", "", text]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"✓ {OUT}（{len(rows)} 个组件，{len(seen)} 份许可正文）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
