# XRD Toolkit

XRD 衍射图像处理工具箱：读取 .tif / .edf / .cbf 衍射数据，支持二维衍射图与强度剖面查看（环心默认用校准值）、LaB₆ 几何校准（pyFAI，自动定位环心作初值）、2D→1D 全角度积分（标准粉末衍射谱）与扇形积分（瀑布图 / 方位均匀性分析）。除校准外，所有脚本统一使用 `src/xrd_toolkit/config.py` 里的校准几何（按批次登记成多个条目，用 `--config` 选择，默认 lmfp1_lab6）。

## 安装

```bash
# 1. 用 environment.yml 一键创建 conda 环境（只需一次）
conda env create -f environment.yml
# 2. 激活环境
conda activate XRD_Toolkit_Environment
# 3. 安装本项目（numpy / scipy / matplotlib / fabio / pyFAI 依赖会自动装上）
pip install -e .
```

> 环境自检：`python scripts/check_env.py` 一条命令回答"现在能不能跑"（解释器、依赖、安装指向、GUI 导入、测试与数据）。**搬动或重命名过项目文件夹之后**要重跑上面的 `pip install -e .`——可编辑安装记的是绝对路径，搬家会让 `import xrd_toolkit` 失败，但源码一个字都没坏。

## 使用

典型流程：`view_diffraction`（看图）→ `calibrate_integrate`（几何校准）→ `integrate_pattern`（1D 标准谱）→ `sector_waterfall`（方位均匀性检查）。所有输出都在 `outputs/{数据文件名}/` 下。

四个脚本都支持两种方式选文件：`--file` 指定单个文件；不带 `--file` 则弹出交互菜单，列出 `data/` 里所有数据文件按编号选择（`1,2` 多选、`all` 全选）。菜单逻辑统一在 `xrd_toolkit/cli.py`，四个脚本共用一份。

三个消费脚本（view_diffraction / integrate_pattern / sector_waterfall）还支持 `--config` 点名选择几何配置条目（默认 lmfp1_lab6）。交互模式下（不带 `--file`）选完数据文件后，会再弹一个配置菜单让你选一次（回车 = 默认条目）。新增批次：先用 calibrate_integrate.py 标定，脚本会在精修结果后打印一段可直接粘贴进 config.py `CONFIGS` 的条目模板，改好 key（如 lmfp2_lab6）后即可用 `--config` 取用。

三个积分脚本（calibrate_integrate / integrate_pattern / sector_waterfall）都支持 `--range`：`full`（完整数据，txt 母版永远存这一版）、`auto`（默认，自动选区）、`lo,hi`（手动指定，如 1.3,7.3）。`auto` 的下界按材料专属标准选取（粉末样品：第一个已知峰 − 0.3°；LaB₆ 标样：检测到的光环结束点 − 0.6°，≈1.0°），上界自动检测"数据失效点"——环被方形探测器切掉的位置。失效判据是相对弧覆盖率：环上落在探测器内的方位角比例跌破自身峰值的 50%（居中摆法即"覆盖率 <50%"，约 7.9°；束心偏置摆法峰值只有 50%/25%，按各摆法自身峰值相对判定，失效点自动后移），实测值与几何精确计算互相印证。

```bash
python scripts/view_diffraction.py --file data/xxx.tif --angle 0 --outdir outputs
```

> 注意：示例数据没有上传到仓库，请把 `data/xxx.tif` 换成你自己的衍射数据文件路径。

参数说明：
- `--file`：衍射图像路径（.tif / .edf / .cbf）
- `--center`：环圆心坐标 cx,cy，不填则用选中配置的束心（默认 lmfp1_lab6 的校准值）
- `--config`：几何配置条目名（config.py 的 `CONFIGS` key，默认 lmfp1_lab6）
- `--angle`：剖面线与水平方向的夹角（度），默认 0
- `--outdir`：PNG 输出目录，默认 outputs/

### 几何校准 + 1D 图谱（LaB₆ 标样）

```bash
python scripts/calibrate_integrate.py --file data/xxx.tif --wavelength 0.1223 --pixel 200 --dist0 1600
```

- `--wavelength`：X 光波长（Å）
- `--pixel`：探测器像素尺寸（µm）
- `--dist0`：探测器距离初值（mm），脚本用 pyFAI 自动精修出精确值
- `--center`：环心初值 cx,cy（像素，不传则自动定位，精度 <1 px）。偏置束心（部分环）数据必须显式传入：自动定位需要完整环，且该摆法下精修不可靠——距离/倾斜角请用居中标样图像标定
- `--max-rings`：参与校准的环数（默认 16）
- `--range`：2θ 区间——`full` / `auto`（默认，材料专属标准）/ `lo,hi` 度（如 1.3,7.3）；完整版 txt 永远保存

脚本流程：LaB₆ 峰位校准（pyFAI GeometryRefinement）→ 方位角积分（2D→1D）→ 输出 1D 图谱（`outputs/{数据名}/calibrated_2th.txt` + `calibrated.png`，图中红虚线为理论峰位）。校准与积分函数在 `xrd_toolkit/services/integrator.py`。精修完成后会打印一段可直接粘贴进 config.py `CONFIGS` 的条目模板（含新几何参数与建议束心），改好 key 即完成新批次登记。

### 全角度积分（2D → 1D 标准谱）

```bash
python scripts/integrate_pattern.py --file data/xxx.tif
```

用标定好的几何做 0°–360° 完整方位角积分，输出标准两列 txt（2θ(deg), intensity）+ PNG 图谱，供后续寻峰、拟合、PDF 分析使用。几何参数默认使用选中配置的标定值（默认 lmfp1_lab6：1595.80 mm 等），同一批实验通用；可用 `--dist`、`--poni`、`--wavelength` 覆盖。

### 扇形积分 + 瀑布图

```bash
python scripts/sector_waterfall.py --file data/xxx.tif --n-sectors 36
```

把 0°–360° 方位角分成 N 个扇区分别积分（默认 36 个，每 10° 一个；几何同样来自选中的 `--config` 条目，默认 lmfp1_lab6），输出每个扇区的两列 txt（`outputs/{数据名}/sectors/`）+ 一张原强度堆叠瀑布图（36 条曲线沿 Y 轴错开、行间距自适应，每条画到自己强度变 0 的位置——右端阶梯即各扇区衍射环被探测器边缘切掉的位置），用于检查衍射环的方位均匀性（大晶粒、择优取向会表现为强度集中在少数扇区）。

## 图形界面（GUI）

在同样的分析引擎之上套了一个 PySide6 桌面界面——看图、积分、叠加对比、打磨图样，全程不用碰终端：

```bash
python -m xrd_toolkit.gui
```

<table>
  <tr>
    <td align="center" width="50%">
      <img src="../showcase/gui/gui_main.png" width="100%"><br>
      <sub>主窗口——LaB₆ 标样积分出的 1D 图谱，右侧是参数面板</sub>
    </td>
    <td align="center" width="50%">
      <img src="../showcase/gui/gui_compare.png" width="100%"><br>
      <sub>对比视图——两张 LMFP 样品谱线叠加，带图例</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/gui/gui_customize.png" width="32%"><br>
      <sub>Customize 对话框——标题、轴标签、纵轴线性/对数、图边距</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/gui/gui_views.png" width="50%"><br>
      <sub>2D / 剖面 / 瀑布视图——四个一键出图按钮全部接线（LaB₆ 标样真数据）</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/gui/gui_batch.png" width="50%"><br>
      <sub>批量管线——文件夹一键导入、三个真数据集批量积分（日志实时进度 k/n）、导出 txt + CSV 总表</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/gui/gui_heatmap.png" width="50%"><br>
      <sub>批量热图——三个真数据集的 1D 曲线拼成 2θ×样品强度图（每行各自最强峰归一化），旁列单条 1D 对照</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="../showcase/gui/gui_background.png" width="50%"><br>
      <sub>背景扣除——真 LMFP 图谱上手动锚点：原始曲线（虚线）、过四个点选锚点的基线（点线）、扣除后结果（实线），调参实时预览</sub>
    </td>
  </tr>
</table>

- **独立图面板**——每张图是一个 MDI 子窗口，互不牵连：可自由缩放（每次拖动记住这张图自己的画布比例）、弹出成独立系统窗口、按各自比例横排/竖排平铺；绘图区支持 Ctrl+滚轮缩放（Excel 式）。
- **一键出图**——[2D] [剖面] [1D] [瀑布] 四个按钮把勾选的文件一次全部出图；[对比] 把多条 1D 曲线叠进同一面板，四种归一化模式（各曲线最强峰 / 全体最强峰 / 指定文件 / 不归一化，默认不归一化）。四个视图全部接线：2D 显示原始衍射图（对数强度 magma 色图、带颜色条、自动或手动对比度、束心十字），剖面沿过束心的直线按设定角度采样，1D 是全体方位角积分，瀑布把 36 个扇形各自积分后按 χ 错开堆叠（行标签 = 扇区 χ，每条曲线画到探测器截断处）。
- **批量热图**——[热图] 把勾选文件的多条 1D 曲线拼成一张 2θ×样品 强度热图（横轴 2θ、纵轴样品序号 = 文件名、颜色 = 强度，带颜色条）：原位实验里一眼看峰位/强度/峰形随样品（时间/充电状态）的变化。已算过 1D 的曲线直接复用，没算过的后台自动补积分（进度 k/n）；各文件 2θ 网格不一致时自动重插值到第一个文件的网格（日志提示）。显示参数可调：颜色映射（magma/viridis/plasma/inferno/gray）、强度归一化（每行各自最强峰 / 全图最强峰 / 不归一化）、对数强度、强度显示范围（自动 1%/99.9% 分位或手填）。
- **实时参数面板**——每个面板的数据与显示参数集中一栏（提示气泡全覆盖，分组重置/应用，面板快照）；数据组的 2θ 范围直接约束积分本身（npt 个采样点摊在区间内），缩放 / 平移则实时写回视图范围。
- **面板手势**——左键拖动平移、滚轮以光标为中心缩放（可开放大镜）、悬停时状态栏实时显示数据点读数；面板每条边都有抓握缩放手柄。
- **每面板工具栏**——Home / 放大镜 / Customize（标题、轴标签、纵轴线性/对数、图边距）/ 保存 PNG。
- **校准模式**——[校准] 开关切到校准工作台：一键自动精修（自动定位环心 → pyFAI 迭代）或手动在图上点环（≥3 点、覆盖 ≥2 环、±0.5° 自动吸附），自动/手动结果与 Δ 偏差三列并列。出口从不藏着：进入后工具栏开关文字变为「退出校准」，校准页底部还有 [返回分析模式] 按钮一键翻回常规参数面板。校准结果可 [保存为配置] 存成命名条目（本地 config_user.json，不进 git），保存后立即出现在"几何配置"下拉框并自动选中，重启仍在，命令行脚本同样可用 `--config` 选取。图上那圈青线是**当前几何预测的真实环**：每个点都由 pyFAI 的 2θ 场反解而来，倾斜探测器上就以它本来的椭圆样子画出来——用"圆心 + 半径"的正圆近似会整体偏 8~23 px（环的公共圆心是直射束落点，不是 PONI）。点图判环用同一套几何，因此点你看到的那条线不会判成隔壁环号；若当前几何把 16 条环全推出探测器（距离/像素尺寸/波长填错或精修跑出离谱解），面板会在日志里明说并把视野放大到看得见，而不是默默画一圈看不见的线。
- **批量管线**——[打开文件夹] 一键遍历目录（.tif/.tiff/.edf/.cbf 全收，重复自动跳过；把文件夹直接拖进窗口同样生效），任意视图按钮对全部勾选文件一次出图，日志实时报进度（完成行末尾的（k/n））；[导出数据] 把每个文件的 1D 结果存成两列 `integrated_2th.txt` / `.chi`（`outputs/{文件名}/` 下，与命令行逐字同格式），可顺带生成 `1d_summary.csv` 总表（每文件一列强度；2θ 网格不一致时弹窗三选：取公共交集重插值 / 跳过范围不同的文件 / 取消）。单个文件失败不中断整批。
- **保存/加载几何参数（.poni）**——"几何配置"下拉框旁的 [保存参数] 把当前选中配置写成 pyFAI 通用交换格式 .poni 文件（保存内容：探测器距离、中心点 poni1/poni2 米制坐标、像素尺寸、波长、倾斜角 rot1/rot2；作业规格中的掩膜文件为可选项——引擎尚未支持掩膜且 .poni 格式本身无掩膜字段，故不写）；[加载参数] 读 .poni 存成命名配置条目（与 [保存为配置] 同源，自动选中、重启仍在）。标定一次到处用，别的工具标定的几何直接拿来用，避免每次重新校准。导入或保存的用户条目可用 [删除] 按钮移除（内置条目置灰不可删）：删除前弹确认框，删后自动回退默认条目。

- **背景扣除**——参数面板的"背景扣除"小节减掉**不含结构信息**的那部分信号：空气散射、样品非晶漫散射、荧光、探测器暗电流、直射束光晕。这一步在本数据上是必须的：两个样品的背景都朝低角抬升 3.8~5.2 倍（LMFP 1~2° 处 1429 counts，9~10° 处只有 275），所以"减一个常数"根本不行。三种模式，**全部实时预览**（参数只改画法，缓存里的原始曲线永不被改动，因此不触发重新积分）：**空扫相减**（先拍一张没有样品的图逐点减掉，曝光/束流不一致时填归一化系数）、**自动基线**（滑动窗估计背景水平，窗口宽度应取最宽峰宽的 3~10 倍）、**手动锚点**（在 1D 图上点选"只有背景"的位置，基线连过这些点、两端按首末锚点线性外推）。扣背景时 1D 面板会把原始曲线（虚线）和基线（点线）一起画出来，边调边看扣前扣后。锚点按文件各记各的；对比 / 瀑布 / 热图跟随同一套设置（瀑布减的是**扇区均值的共同基线**，这样扇区之间的真实强度差才保留得住），[导出数据] 也可导出扣完背景的曲线。扣完的负值默认保留——噪声地板是真的，强行截断到 0 会把均值抬高约 1σ——想截断有单独的复选项。SNIP 也实现了并实测过，但它在这些图谱上会过度扣除 43%~98%（把 2.2° 附近的宽非晶鼓包当成峰削掉了），所以界面里不提供。

## 项目结构

```
.
├── data/                       # XRD 原始数据（.tif，不进 git）
├── outputs/                    # 脚本输出（按数据文件名分文件夹，本地保留，不进 git）
├── showcase/                   # README 展示用的精选示例图（进 git）
├── scripts/
│   ├── view_diffraction.py     # 衍射图查看器（画图 + 线剖面）
│   ├── calibrate_integrate.py  # LaB₆ 几何校准 + 方位角积分（2D→1D）
│   ├── integrate_pattern.py    # 全角度积分：2D 图 → 标准 1D 谱（两列 txt）
│   └── sector_waterfall.py     # 扇形积分（36 扇区）+ 瀑布图 + 方位均匀性统计
├── src/xrd_toolkit/
│   ├── config.py               # 几何配置注册表（每批实验一个条目，--config 选择）
│   ├── cli.py                  # 命令行共用交互选文件菜单（四个脚本共用）
│   ├── core/processor.py       # 图像计算（线剖面、自动定位环圆心）
│   ├── services/data_loader.py # 数据读取（fabio）
│   ├── services/integrator.py  # 几何校准 + 1D 积分（pyFAI）
│   ├── services/background.py  # 背景扣除：空扫相减 / 自动基线 / 手动锚点（无 Qt）
│   ├── services/range_selector.py # 自动 2θ 区间选择（材料专属标准）
│   └── gui/                    # PySide6 桌面界面（python -m xrd_toolkit.gui）
├── tests/                      # 合成图像单元测试（unittest）：部分环几何 + 自研积分回归护栏
├── environment.yml             # conda 环境定义（一键创建环境）
├── pyproject.toml              # 项目元信息与依赖
└── README.md                   # 项目说明
```

## 测试

合成图像单元测试（不需要真实数据；纯 unittest，无需 pytest）：

```bash
conda activate XRD_Toolkit_Environment
python -m unittest discover -s tests -v
```

覆盖弧覆盖率失效判据（居中 / 边缘 / 角落 / 束心在图像外四种摆法）、几何失效点数值，以及偏置摆法下的自研积分兜底（pyFAI 分箱缺陷的回归护栏）。

## 输出示例

以下图片来自一份 LaB₆ 标样校准数据集（NIST SRM 660）。

二维衍射图（对数色标，黑边白芯小十字标记校准环圆心，白色虚线为剖面线取样方向，标题注明圆心坐标）：

<img src="../showcase/lab6/diffraction_image.png" width="60%">

过圆心的强度剖面（横轴为到圆心的距离，单位像素）：

<img src="../showcase/lab6/radial_profile.png" width="60%">

校准后的 1D 图谱（红虚线为 LaB₆ 理论峰位）：

<img src="../showcase/lab6/calibrated_pattern.png" width="60%">

36 扇区瀑布图（原强度堆叠，每条曲线按方位角沿 Y 轴错开、画到自己强度变 0 处，右端阶梯即探测器切环位置）：

<img src="../showcase/lab6/sector_waterfall.png" width="60%">
