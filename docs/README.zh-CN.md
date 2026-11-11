# XRD Toolkit

XRD 衍射图像处理工具箱：读取 `.tif` / `.edf` / `.cbf` 二维衍射数据，用标样做几何校准（默认 LaB₆，内置 CeO₂、Si 等常用标样），把二维衍射图积分成标准 1D（一维）粉末谱，再做寻峰、单峰/全谱峰形拟合、Le Bail/结构精修与 CIF 理论谱模拟——一组命令行脚本，外加一个跑在同一个引擎上的 PySide6 桌面界面。本项目是香港城市大学 *PHY6528 Advanced Research in Applied Physics* 的课题工作。

English: [README.md](../README.md) · 试用者上手图文：[使用说明.html](使用说明.html)（程序内：帮助 → 使用说明，快捷键 F1）· 详细说明：[NOTES.zh-CN.md](NOTES.zh-CN.md) · 更新记录：[CHANGELOG.md](../CHANGELOG.md) · 引用：[CITATION.cff](../CITATION.cff)

## 下载即用（不用装 Python）

**最新版本**——GitHub Actions 从本仓库构建的三平台独立应用（链接自动指向最新 Release）：

| 你的电脑 | 下载 |
|---|---|
| Windows 10/11 | [XRD-Toolkit-windows.zip](https://github.com/xyyzzc3/xrd_toolkit/releases/latest/download/XRD-Toolkit-windows.zip) |
| Mac（苹果芯片 M1…M4） | [XRD-Toolkit-macos-apple-silicon.zip](https://github.com/xyyzzc3/xrd_toolkit/releases/latest/download/XRD-Toolkit-macos-apple-silicon.zip) |
| Mac（Intel 处理器） | [XRD-Toolkit-macos-intel.zip](https://github.com/xyyzzc3/xrd_toolkit/releases/latest/download/XRD-Toolkit-macos-intel.zip) |

解压 → 双击。应用没做系统签名，第一次打开会被系统拦一下（Windows：更多信息
→ 仍要运行；Mac：右键 → 打开），详细步骤见 [RELEASE_NOTES.md](RELEASE_NOTES.md)。
macOS 13 起 / Windows 10 起，下载约 150 MB（解压约 340 MB）。zip 里还带《使用说明.html》（程序内 帮助 → 使用说明，快捷键 F1）和第三方许可声明 `THIRD_PARTY_NOTICES.txt`（程序内 帮助 → 关于）。

## 三步上手

1. **打开**——`[打开…]` 选仪器导出的 `.tif` / `.edf` / `.cbf` 图像（整个文件夹也行）
2. **出图**——勾上文件 → `[1D]` → `[出图（勾选文件）]`
3. **导出**——`[导出数据]` → 两列 txt / `.chi` / 全部数据总表

几何配置：内置第一条 LMFP 批次的标定；自己批次在 `[校准]` 页用标样（默认 LaB₆，也可选 CeO₂/Si 等常用标样）标一套，
或把别人给的 pyFAI `.poni` 文件导进来。

## 一览

<details>
<summary>展开截图与拟合图</summary>

<table>
  <tr>
    <td align="center" width="50%">
      <img src="../showcase/lab6/diffraction_image.png" width="100%"><br>
      <sub>二维衍射原图（对数色标）——小十字是校准后的束心</sub>
    </td>
    <td align="center" width="50%">
      <img src="../showcase/lab6/calibrated_pattern.png" width="100%"><br>
      <sub>校准后的 1D 图谱——红虚线是 LaB₆ 理论峰位</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="../showcase/lab6/sector_waterfall.png" width="100%"><br>
      <sub>36 扇区瀑布图——方位均匀性；右端阶梯即各扇区衍射环被探测器切掉的位置</sub>
    </td>
    <td align="center" width="50%">
      <img src="../showcase/gui/gui_main.png" width="100%"><br>
      <sub>界面（同一份真数据）——左边是带阶段文件夹的文件栏，右边是参数面板</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="../showcase/gui/gui_calib.png" width="100%"><br>
      <sub>校准工作台——当前几何与对比位 A / B 三列并排，带引擎指标与结论行</sub>
    </td>
    <td align="center" width="50%">
      <img src="../showcase/gui/gui_background.png" width="100%"><br>
      <sub>真 LMFP 图谱上的背景扣除——自动基线与扣完的曲线；原始、基线、结果同框</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/gui/gui_compare_heat.png" width="100%"><br>
      <sub>一批图一眼扫完——同一批 8 个文件（自动扣完背景）：左边堆叠对比，右边 2θ×样品 热图</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/simulation/sim_xrd_fit_LaB6.png" width="100%"><br>
      <sub>LaB₆ 标样的全谱拟合——观测（点）/计算（线）/背景、差值曲线与布拉格峰位刻度（Le Bail 强度，Rwp 4.24%）</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/simulation/sim_xrd_gsas_keys.png" width="100%"><br>
      <sub>峰形模型的两条硬指标——面积守恒（峰高 ∝ 1/FWHM），以及 W / X / Size / Mustrain 四个通道的 FWHM 与 GSAS-II 偏差都在 1.2% 以内</sub>
    </td>
  </tr>
</table>

</details>

<details>
<summary>展开分析工作台（峰形拟合 / 物相 / 精修 / 原位）</summary>

<table>
  <tr>
    <td align="center" width="50%">
      <img src="../showcase/gui/gui_wh.png" width="100%"><br>
      <sub>1D 页的峰形拟合与 Williamson–Hall——逐峰拟合后自动给出 D / ε（这组 LaB₆ 标样：20 根峰）</sub>
    </td>
    <td align="center" width="50%">
      <img src="../showcase/gui/gui_phase.png" width="100%"><br>
      <sub>物相筛选——13 个候选按峰位打分；LaB₆ 与 LiFePO₄ 双选，两组理论峰位叠在观测曲线上</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/gui/gui_refine.png" width="100%"><br>
      <sub>全谱精修——LaB₆ + Al 额外相、仪器基线固定；观测/计算/背景三联图、obs−calc 差值与两相布拉格刻度</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/gui/gui_insitu.png" width="100%"><br>
      <sub>原位三相批量——3 帧真 LMFP 数据逐帧精修；相分数（带 esd）与 Rwp 趋势 + 结果表</sub>
    </td>
  </tr>
</table>

</details>

## 亮点

- **2D → 1D 积分**——0°–360° 完整方位角积分，输出标准两列 txt（2θ(deg), intensity），供寻峰、拟合、PDF 分析使用。
- **校准指标抓到残差抓不到的东西**——每次校准除拟合残差外还报环位偏差中位、16 条环里完整跑出来的条数、逐环反推的晶格常数离散度：实测过残差 0.0000° 而环位偏 10 px 的情况。
- **背景扣除，实时预览**——空扫相减 / 自动基线 / 手动锚点（锚点定电平、自动基线定形状），加平滑（滑动平均 / Savitzky–Golay）与区间裁剪，全程叠着原始曲线调；负值默认保留（截到 0 会把均值抬高约 1σ）。
- **批量与分阶段产物**——文件夹一键导入、整批积分；曲线与扣背景曲线按阶段缓存（文件指纹 + 几何 + 设置的键，跨会话复用）并显示成文件栏**分组**：81 个文件的那一批，两下点击进对比图或热图，不用重积分。
- **偏置束心与扇形分析**——束心打在探测器边缘/角落的数据全程支持（pyFAI 分箱不可靠时自动切自研 numpy 积分）；36 扇区瀑布图一眼看出择优取向与被切环的几何。
- **桌面界面（同一引擎）**——五个阶段入口、实时参数面板、1D 图上直接寻峰与峰表、悬停读数、框选放大、对比曲线单色渐变、`.poni` 进出、PNG/TIF/PDF/SVG 导出（PDF/SVG 为矢量，适合论文排版与再编辑）。
- **四个分析工作台**——物相筛选（内置精选相库或自选 CIF 目录 / 周期表点选元素 / 多相叠加 / CSV）、全谱精修（额外相 / 仪器基线 / 采纳为图面板）、原位三相批量（逐帧结果与趋势）、掩膜编辑器；全部与参数页、图面板共享同一套引擎。**进入工作台时收起参数坞和日志坞，文件栏保留**：双击文件栏条目 = 对这条操作，勾选 = 批量范围；工作台内的下拉作镜像/兜底，退出恢复原样。
- **一键分析报告**——把当前会话的 1D 曲线、峰表、单峰拟合、W-H、物相筛选、全谱精修与原位三相批量结果汇成一份自包含 HTML（图内嵌，浏览器可打印 PDF）；缺的节标注“本次未做”，并写清定量口径（wt% 与 f_rich、背景系统偏差、少数相晶胞）。
- **命名项目预设层**——一份预设打包几何配置、第二相 CIF、原位帧目录/掩膜、相库目录，以及可选的**处理设置**（背景/平滑/裁剪快照）和**掩膜文件**（`.mask.npz` 路径）；参数坞可切换/保存/管理。处理设置在切换后点 `[套用当前预设]` 才生效，调好后用 `[存进预设…]` 写回；掩膜文件在掩膜工作台按图兜底。旧版 `outputs/recipes.json` 的命名配方在启动时一次性迁成用户预设。内置 LMFP 预设只读，用户预设存 `presets_user.json`，下次启动自动接上上次用的那份。重新校准等操作让几何偏离预设时，下拉会显示「（当前设置）」并在管理里支持一键写回用户预设。
- **从 CIF 算理论谱（XRD 模拟）**——布拉格定律 + 结构因子全手写，出谱带 (hkl) 峰位标注与六方卡片式三位指数；与 pymatgen 参考版逐峰对过答案（`scripts/simulate_xrd.py data/structures/Si_9008565.cif outputs/sim_Si.png`；pymatgen 自 0.2 起随主依赖安装（GUI 分析工作台需要它）；`[simulation]` extra 保留为别名）。
- **自动寻峰与单峰峰形拟合**——稳健噪声估计 + 显著度/峰高门槛 + 最小峰距 + 可选 FWHM 过滤的自动寻峰（`services/peak_finding.py`）；Gaussian / Lorentzian / pseudo-Voigt / split-Pearson VII 单峰拟合（局部背景），以及逐峰 Scherrer 尺寸和带仪器校正（quadrature / linear 扣标样，或 GUI 里已保存的 LaB₆ 基线）的 Williamson–Hall 分析；命令行入口 `scripts/fit_peaks.py`。
- **定量相分数与晶胞精修**——按 Rietveld 公式给拟合模型里的晶态相算 wt%（Z·M·V 来自 CIF 与精修晶胞；非晶/未知相不参与分母，报告里写明），以及七个晶系的峰位级晶胞精修（含 2θ 零位；`scripts/refine_cell.py`，或物相工作台的 `[精修晶胞]`）。
- **实测标样谱全谱拟合**——独立实现的 Thompson–Cox–Hastings 伪 Voigt 峰形与 Caglioti/Lorentzian 峰宽项，参数口径与单位沿用 GSAS-II 的 PXC 模型（Caglioti, Paoletti & Ricci, 1958；Thompson, Cox & Hastings, 1987；Toby & Von Dreele, 2013），峰形与 GSAS-II 模拟做过逐点对拍。支持 Le Bail/结构拟合、额外相、按空间群约束的原子坐标与位点占位精修，以及固定坏点掩膜。LaB₆ 标样定仪器峰形并用 Le Bail 强度拟合（Rwp 4.24%）；图见 `showcase/simulation/sim_xrd_fit_LaB6.png`。

## 安装（从源码跑）

```bash
conda env create -f environment.yml    # 只需一次
conda activate XRD_Toolkit_Environment
pip install -e .                       # numpy / scipy / matplotlib / fabio / pyFAI / PySide6 一并装上
python -m xrd_toolkit.gui              # 桌面界面
```

> 示例数据没有上传到仓库，请把 `--file` 换成你自己的衍射数据路径。样品派生的图/报告写到 `outputs/`（不进 git）；仓库只放代码、CIF 和 LaB₆ 标样示例。

```bash
# 校准 + 积分一张图（跑完会打印一段可直接粘进 config.py 的配置条目）
python scripts/calibrate_integrate.py --file data/xxx.tif --wavelength 0.1223 --pixel 200 --dist0 1600
# 用已标定的几何做全角度积分；扇形积分 + 瀑布图
python scripts/integrate_pattern.py  --file data/xxx.tif
python scripts/sector_waterfall.py   --file data/xxx.tif --n-sectors 36
# 拟合 LaB6 标样（Le Bail 峰形 + 布拉格刻度）
python scripts/simulate_study.py fit
# 从 CIF 模拟理论粉末谱（参考结构随仓库附在 data/structures/）
python scripts/simulate_xrd.py data/structures/Si_9008565.cif outputs/sim_Si.png
```

典型流程：`view_diffraction`（看图）→ `calibrate_integrate`（几何校准）→ `integrate_pattern`（1D 标准谱）→ `sector_waterfall`（方位均匀性）。输出都在 `outputs/{数据名}/` 下。不带 `--file` 时每个脚本弹出 `data/` 里的文件菜单；三个积分脚本用 `--config 条目名` 选几何（默认 `lmfp1_lab6`）。**脚本一览与完整参数说明见 [NOTES.zh-CN.md](NOTES.zh-CN.md#脚本一览)。**

## 测试

```bash
python scripts/run_tests.py            # 全量，带看门狗
python scripts/run_tests.py --fast     # 只跑单测 + 静态护栏（约 3 分钟）
python scripts/run_tests.py --jobs 8   # GUI 集成套件按文件并行（8 个进程）
```

合成图像单元测试（不需要真实数据，纯 unittest）覆盖校准与积分引擎、各背景估计器、分阶段产物缓存与界面接线。`scripts/check_gui.py` 用真数据 + 真画布事件把界面走一遍；`scripts/stress_panels.py` 是两处 offscreen 死锁的回归探针。

## 掩膜 + 原位三相批量

工具栏 `[校准]` 旁新增 `[掩膜]` 中央工作台（进入时收起参数坞/日志坞，文件栏保留；双击原始图换当前图像）：自动载入第一张原始图，顶部「图像」下拉列出全部原始图；画笔/橡皮/矩形画掩膜，`[孤立坏点（局部）]` 抓坏点簇、`[整环离群（实验性）]` 只作参考；坏点参数按几何配置记忆，高级面板可 `[恢复默认]`；`[放大镜]` 与主图面板同语义（点亮 = 滚轮缩放 + 左键框选）；`[导出掩膜文件…]` 保存 `.mask.npz`（含来源分层），`[导出通用掩膜…]` 另存 `.npy/.edf`；`[预览效果]` 只画掩膜前后 1D，`[生成掩膜 2D]` 按范围（当前图像 / 所有原始图 / 选择文件…）建产物（原始图 + 掩膜记录；形状不一致的会跳过并记日志），双击看 2D、也可按它做 1D；掩膜/未掩膜的 1D 是两个来源，可并排对比。`scripts/fit_insitu.py` 对原位 LMFP 序列做三相（富锂 LMFP / 贫锂 heterosite / Al 参考）全精修，自动双起点稳健拟合（首解收敛用种子，不收敛再补 CIF 端元）、种子重锁、异常帧标记、CSV 与分段计时；GUI 原位工作台提供趋势 / 汇总 / 单帧拟合 / 模型无关四视图，少数相晶胞淡化显示。`scripts/gsas2_insitu_crosscheck.py` 用 GSAS-II 对 3 帧做独立对照（晶胞与相分数趋势）。掩膜的操作步骤见《使用说明》第 5 节；掩膜策略、原位精修参数、对照结果与效率评估见 [docs/NOTES.zh-CN.md](NOTES.zh-CN.md) 的「掩膜」与「LMFP 原位三相批量」两节。

## 路线图

寻峰、峰形拟合、W-H、全谱（Le Bail/结构）拟合、四个分析工作台、一键报告 v1.1（1D / 峰 / 拟合 / W-H / 物相 / 精修 / 原位）与命名项目预设层 B1（含处理设置/掩膜文件）已落地。剩下的：

- 原位提速（81 帧 23 min → 目标 <15 min）与多仪器基线管理；
- 机器学习：物相识别聚类 + 峰形 CNN 分类（放在最后，不排期）

实验验证已由 v0.2.0 的同步辐射 LaB₆ + LMFP 工作覆盖；实验室光源数据与
Si 标样不再单列为待办。

## 项目结构

```
.
├── data/                       # XRD 原始数据（.tif，不进 git）+ data/structures/ 参考 CIF（进 git）
├── outputs/                    # 脚本输出 + 产物缓存（本地，不进 git）
├── showcase/                   # 精选示例图（README / 说明书 / 模拟模块用，进 git）
├── packaging/                  # PyInstaller 打包（图标、spec、版本钉）
├── scripts/                    # 分析脚本 + 自检/探针工具
├── src/xrd_toolkit/
│   ├── config.py               # 几何配置注册表（每批实验一个条目，--config 选择）
│   ├── cli.py                  # 命令行共用交互选文件菜单
│   ├── core/processor.py       # 图像计算（线剖面、自动定位环圆心）
│   ├── services/               # data_loader · integrator · background · range_selector · ring_metrics · peak_finding · stage_cache
│   ├── simulation/             # CIF → 理论粉末谱、寻峰/峰形拟合与微结构分析（手写版；pymatgen 核心依赖）
│   └── gui/                    # PySide6 桌面界面（python -m xrd_toolkit.gui）
├── tests/                      # 合成图像单元测试 + 界面集成测试
├── docs/                       # 英文 README、详细说明
├── environment.yml             # conda 环境定义
└── pyproject.toml              # 项目元信息与依赖
```

## 数据、隐私与合规

所有计算都在**本机**完成：程序不联网、不上传数据、没有使用统计。科研工具，
发表/交付前请复核关键数值。本程序 MIT 许可；随附第三方组件（Qt/PySide6、
pyFAI、NumPy、SciPy、Matplotlib 等）的许可全文随包分发
（`THIRD_PARTY_NOTICES.txt`，程序内 帮助 → 关于 可打开），仓库里也留了一份
[packaging/THIRD_PARTY_NOTICES.txt](../packaging/THIRD_PARTY_NOTICES.txt)。

## 作者

Chenze Bian —— 香港城市大学 物理学与数据建模及量子技术硕士 · [GitHub](https://github.com/xyyzzc3)

## 许可

[MIT](../LICENSE)
