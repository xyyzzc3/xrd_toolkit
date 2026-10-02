"""导出文件的格式与写法：唯一出处（2026-10-02），GUI 与 CLI 共用。

为什么单独一个模块：导出文件的表头要做到"任何软件打开都不乱码"（表头
纯 ASCII + CSV 带 BOM），还要写清这条曲线**是什么、做过哪些处理**。这份
格式以前散在 gui/plot_export.py 与三个 CLI 脚本里各写各的：GUI 学 CLI 的
头、CLI 又只有半个——改一处漏一处，还出过乱码。现在只有一个定义，谁导出
都走这里（连测试都读这里的常量，不再各写字面量）。

目录约定（详见 docs/UI_COPY.zh-CN.md「导出文件规范」）：

    单个数据集    {目录}/{曲线名}.txt            光一个文件，不包文件夹
    多个数据集    {目录}/导出_{时间}/txt/…       每条一个
                  {目录}/导出_{时间}/全部数据.csv  大集合

曲线名 = 文件栏条目的显示名（原始数据 = 文件 stem；产物 = stem_1D /
stem_处理产物），由 GUI 的 _checked_1d_results 给出，本模块只管写。
"""
from datetime import datetime
from pathlib import Path

import numpy as np

from xrd_toolkit.services.process import chain_ascii

# 数据类别（与 docs/UI_COPY.zh-CN.md §1.2 的英文对照一致）
CATEGORY_RAW = "Raw"
CATEGORY_ONED = "1D product"
CATEGORY_PROCESSED = "Processed"

BATCH_PREFIX = "导出_"        # 批量文件夹前缀（测试按它 + 下一条常量找目录）
TXT_DIR_NAME = "txt"          # 批量文件夹里单条 txt 的子目录
CSV_NAME = "全部数据.csv"     # 大集合文件名

# 曲线文件第一行：外部约定（测试、探针、别的工具都认它），逐字不动
FIRST_LINE = "2theta(deg)  intensity"


def stamp(now: datetime = None) -> str:
    """导出时间戳 "2026-10-02 22:55:03"（当地时间；now 可注入 → 可测）。"""
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def batch_dir(outdir: Path, now: datetime = None) -> Path:
    """批量导出的文件夹：{outdir}/导出_YYYY-MM-DD_HHMMSS，**建好并返回**。

    秒级时间戳；同一秒里再导一次（脚本连跑、手快）就依次 _2、_3…——
    不覆盖上一批，也不为撞名去等一秒。
    """
    base = f"{BATCH_PREFIX}{(now or datetime.now()):%Y-%m-%d_%H%M%S}"
    n = 1
    while True:
        target = Path(outdir) / (base if n == 1 else f"{base}_{n}")
        try:
            target.mkdir(parents=True, exist_ok=False)
            return target
        except FileExistsError:
            n += 1


def normalize_row(row) -> tuple:
    """一条导出结果统一成 6 元组 (名, tth, 强度, 链, 类别, 配置)。

    3/4/5 元的旧形状（老调用方与测试）缺啥补啥：链缺省 none、类别按链
    推断（none → Raw，否则 Processed）、配置缺省空。补出来的默认值就是
    "没做处理"的诚实表示，不会把老调用方的意图猜歪。
    """
    row = tuple(row)
    chain = row[3] if len(row) > 3 and row[3] else "none"
    category = (row[4] if len(row) > 4 and row[4]
                else (CATEGORY_RAW if str(chain).strip() in ("", "none")
                      else CATEGORY_PROCESSED))
    config = row[5] if len(row) > 5 and row[5] else None
    return (row[0], row[1], row[2], chain, category, config)


def _curve_header(*, category: str, chain: str, n_points: int, tth_min: float,
                  tth_max: float, cut_points: int, config: str,
                  exported: str, extra_lines=None) -> str:
    """曲线文件的表头（不含 "# " 前缀——np.savetxt 会给每行加）。

    全 ASCII：Excel 打开不认 BOM 之外的编码，° – → 、 这些字符就是乱码
    的来源（2026-10-02 用户报的问题）。配置名也可能不是 ASCII（用户自己
    起的名字），不是就省掉这一行——保证"表头永远纯 ASCII"这条规矩不破。
    extra_lines 给 CLI 用（扇区文件要写 chi 信息），插在第一行之后。
    """
    lines = [FIRST_LINE]
    lines.extend(str(ln) for ln in (extra_lines or []))
    lines += [f"data category: {category}",
              f"chain: {chain_ascii(chain)}",
              f"cut: {cut_points} points removed",
              f"points: {n_points}  range: {tth_min:.3f}-{tth_max:.3f} deg"]
    if config and str(config).isascii():
        lines.append(f"config: {config}")
    lines.append(f"exported: {exported}")
    return "\n".join(lines)


def write_curve(target: Path, tth, intensity, *, category: str,
                chain: str = "none", config: str = None,
                now: datetime = None, extra_lines=None) -> int:
    """写一条两列曲线（txt / chi），返回写入的数据行数。

    裁剪过的点（非有限值）**不写行**：写出去就是字面 "nan"，别的软件读
    不了；头里用 cut 行写明删了多少点——文件自己说清它是怎么来的。
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tth = np.asarray(tth, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    keep = np.isfinite(intensity)
    kept = tth[keep]
    header = _curve_header(
        category=category, chain=chain, n_points=int(keep.sum()),
        tth_min=float(kept.min()) if kept.size else 0.0,
        tth_max=float(kept.max()) if kept.size else 0.0,
        cut_points=int((~keep).sum()), config=config, exported=stamp(now),
        extra_lines=extra_lines)
    np.savetxt(str(target), np.c_[kept, intensity[keep]], fmt="%.6g",
               header=header, encoding="utf-8")
    return int(keep.sum())


def write_csv(target: Path, results, *, now: datetime = None) -> None:
    """一批曲线拼成一张大集合 CSV（第一列 2θ，其后每列一条强度）。

    网格必须已经对齐（GUI 在网格不一致时会先弹窗重插值/跳过，那是交互
    决策，不进这里）。

    - 第 1 行 =  `2theta(deg),列名…`（可被 np.loadtxt(skiprows=1,
      delimiter=",") 读——外层测试与外部工具都这么读）；
    - 其后每列一行明细（类别 / 链 / 裁剪点数 / 点数与范围 / 配置），
      再有裁剪列时加一行 blank 说明；
    - 写盘用 utf-8-sig（BOM）：列名可能含中文（_处理产物），Excel 打开
      无 BOM 的 UTF-8 会把第 1/2 行认成乱码（用户 2026-10-02 报的原问题）；
    - 强度里的非有限值在表里是**空单元格**（trim 掉，不写字面 nan——那会
      被后面的计算当成真实数据读进去）。
    """
    results = [normalize_row(r) for r in results]
    grid = np.asarray(results[0][1], dtype=float)
    columns = [np.asarray(r[2], dtype=float) for r in results]

    def fmt(v):
        return "" if not np.isfinite(v) else f"{v:.6g}"

    lines = ["2theta(deg)," + ",".join(r[0] for r in results),
             f"# exported: {stamp(now)}"]
    for i, (name, tth, intensity, chain, category, config) in enumerate(
            results, start=2):
        tth = np.asarray(tth, dtype=float)
        cut_points = int((~np.isfinite(np.asarray(intensity, dtype=float)))
                         .sum())
        detail = (f"# column {i}: {name} | category={category}"
                  f" | chain={chain_ascii(chain)}"
                  f" | cut={cut_points} points removed"
                  f" | {tth.size} points"
                  f" | 2theta={tth.min():.3f}-{tth.max():.3f} deg")
        if config and str(config).isascii():
            detail += f" | config={config}"
        lines.append(detail)
    if any(not np.isfinite(c).all() for c in columns):
        lines.append("# blank cells = 2theta points inside a cut range"
                     " (no data)")
    rows = [",".join([fmt(x)] + [fmt(c[i]) for c in columns])
            for i, x in enumerate(grid)]
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines + rows) + "\n", encoding="utf-8-sig")
