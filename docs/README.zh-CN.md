# XRD Toolkit

XRD 衍射图像处理工具箱：读取 `.tif` / `.edf` / `.cbf` 二维衍射数据，用 LaB₆ 标样做几何校准，把二维衍射图积分成标准 1D 粉末谱——四个命令行脚本，外加一个跑在同一个引擎上的 PySide6 桌面界面。本项目是香港城市大学 *PHY6528 Advanced Research in Applied Physics* 的课题工作。

English: [README.md](../README.md) · 详细说明（校准指标、扣背景、批量与产物、工程细节）：[NOTES.zh-CN.md](NOTES.zh-CN.md)

## 一览

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
      <img src="../showcase/gui/gui_heatmap.png" width="100%"><br>
      <sub>批量热图——三个数据集拼成一张 2θ×样品 强度图</sub>
    </td>
  </tr>
</table>

## 能做什么

- **2D → 1D 积分**——0°–360° 完整方位角积分，输出标准两列 txt（2θ(deg), intensity），供寻峰、拟合、PDF 分析使用。
- **LaB₆ 标样几何校准**——pyFAI `GeometryRefinement` 对 NIST SRM 660（a = 4.156 Å）标定，初值用 FFT 互相关自动定位直射束位置（精度 < 1 px）。实测：距离精修到 1595.80 mm（条目 `lmfp1_lab6`）。每次校准还报**三个拟合残差量不到的引擎指标**：环位偏差中位、16 条环里完整跑出来的条数、逐环反推的晶格常数离散度——实测过残差 0.0000° 而环位偏 10 px 的情况。
- **自动 2θ 区间**——`--range auto`（默认）：下界按材料专属标准取，上界按"数据失效点"自动判定，判据是**相对弧覆盖率**（环落在探测器内的方位比例跌破自身峰值的 50%，居中摆法约 7.9°）而不是一个写死的阈值，因此束心摆在哪儿都适用。
- **扇形积分 + 瀑布图**——默认 36 扇区各自积分，原强度堆叠瀑布图一眼看出择优取向 / 大晶粒，并把探测器切环的几何画出来。
- **偏置束心（部分环）**——束心打在探测器边缘或角落的数据全程支持；pyFAI 分箱不可靠时自动切到自研的 numpy 极坐标积分（合成环上验证过）。
- **背景扣除**——空扫相减（可填曝光/束流归一化系数）、自动基线（滑动窗）、手动锚点（在图上点只有背景的位置），三种都实时预览：原始曲线（虚线）、基线（点线）叠在扣除结果上。负值默认保留（噪声地板是真的，截到 0 会把均值抬高约 1σ）。SNIP 也实现并实测过，因过度扣除 43%~98% 而不放进界面。
- **批量与分阶段产物**——文件夹一键导入、整批积分（一次最多画 24 张，超出的照样后台算完落盘），一张图就能对比或看热图。1D 曲线与扣背景曲线按阶段缓存（`outputs/_stage/`，键 = 文件指纹 + 几何 + 设置，跨会话复用），并在文件栏里显示成**分组**：扣完背景的那一批，两下点击就进对比图，不用重积分。
- **桌面界面**——MDI 图面板（可弹出、可平铺）、实时参数面板、悬停读数、框选放大、单条曲线自定义配色、PNG/TIF 导出：

  ```bash
  python -m xrd_toolkit.gui
  ```

## 安装

```bash
conda env create -f environment.yml    # 只需一次
conda activate XRD_Toolkit_Environment
pip install -e .                       # numpy / scipy / matplotlib / fabio / pyFAI 一并装上
```

> 示例数据没有上传到仓库，请把 `--file` 换成你自己的衍射数据路径。

```bash
# 校准 + 积分一张图（跑完会打印一段可直接粘进 config.py 的配置条目）
python scripts/calibrate_integrate.py --file data/xxx.tif --wavelength 0.1223 --pixel 200 --dist0 1600
# 用已标定的几何做全角度积分；扇形积分 + 瀑布图
python scripts/integrate_pattern.py  --file data/xxx.tif
python scripts/sector_waterfall.py   --file data/xxx.tif --n-sectors 36
```

典型流程：`view_diffraction`（看图）→ `calibrate_integrate`（几何校准）→ `integrate_pattern`（1D 标准谱）→ `sector_waterfall`（方位均匀性）。输出都在 `outputs/{数据名}/` 下。不带 `--file` 时每个脚本会弹出 `data/` 里的文件菜单（`1,2` 多选、`all` 全选）；三个积分脚本用 `--config 条目名` 选几何（默认 `lmfp1_lab6`）。**完整参数说明见 [NOTES.zh-CN.md](NOTES.zh-CN.md#命令行参数)。**

| 脚本 | 作用 |
|---|---|
| `scripts/view_diffraction.py` | 衍射图查看器（对数色标、线剖面、标定过的束心标记） |
| `scripts/calibrate_integrate.py` | LaB₆ 几何校准（pyFAI）+ 方位角积分（2D→1D） |
| `scripts/integrate_pattern.py` | 全角度积分：2D 图 → 标准 1D 谱 |
| `scripts/sector_waterfall.py` | 扇形积分（36 扇区）+ 瀑布图 + 方位均匀性统计 |
| `scripts/check_env.py` | 环境自检——"跑不起来"时先跑它 |
| `scripts/check_gui.py` | 界面链路探针——真数据 + 真画布事件把界面走一遍 |
| `scripts/show_gui.py` | 真窗口看一眼——真数据走三步，窗口留给你自己点 |
| `scripts/run_tests.py` | 带看门狗的全量测试 |
| `scripts/stress_panels.py` | 面板压力探针——两处 offscreen 死锁的回归检查 |

## 界面

- **工具栏五个入口**——`[校准] [1D] [扣背景] [对比] │ [绘图]`：点一个 = 右侧参数面板翻到那一页，只放本阶段的东西（含本页的产出按钮）。**开界面时上面一个都不选、右侧参数栏是收起的**，选到谁才露出谁。
- **处理页**——三项可选项，都是"改参数立刻看到"：[背景扣除]（空扫 / 自动基线 / 手动锚点）、
  [平滑]（滑动平均，窗口按 2θ 给）、[裁剪]（把某段 2θ 挖掉，让被巨峰压扁的其余部分看得清）。
  一张图调好后点 [批量处理]，整批各生成一份处理产物（文件栏里成为「处理后 …」分组），
  对比 / 热图 / 导出都读它。
- **一键出图**——「绘图」页里的 [2D] [剖面] [1D] [瀑布] 把勾选的文件一次全部出图；[对比] 把多条曲线叠进同一面板（四种归一化）；[热图] 把整批拼成一张 2θ×样品 图，点热图某一行 = 在对比图里隐藏/显示该条曲线。
- **文件栏**——一棵树：顶上是原始数据，往下是各阶段产物分组（「1D 产物」= 当前设置算好的；「处理后 …」= 每次 [批量处理] 一组，组名带时间与参数）。**勾组 = 组里整组勾上**，整组勾上去 [对比]/[热图] 就是两下点击的事。导入默认一个都不勾：[全选] / [全不选] / [按条件选…]（区间·间隔·名字筛选，带实时预览）。
- **右键一个入口管全部**——**删除**：原始数据只从列表移除（硬盘上的文件永远不动），产物真删（单条或整组，连带台账）；**导出**：产物条目 / 分组可直接导出 txt / chi / CSV，不必先去勾选；菜单最后一项是**删除所有缓存**（二次确认）。
- **实时参数面板**——每个面板的数据与显示参数集中一栏（提示气泡全覆盖，分组重置/应用，面板快照）；数据组的 2θ 范围直接约束积分本身，缩放/平移实时写回视图范围。
- **面板壳与手势**——每面板一行自绘标题栏（Home / 放大镜 / Customize / 保存，外加弹出与关闭）；左键拖动平移、放大镜点亮后滚轮以光标为中心缩放 + 左键拖框放大、悬停时状态栏显示数据点读数、每条边都能拖。
- **校准工作台**——三列表（当前配置 + 对比位 A / B）压着自动与手动两套动作；每个结果按自己的名字累积，"用的到底是哪一份"由引擎指标决定。指标**抓不到什么**也写在 [详细说明](NOTES.zh-CN.md#校准) 里。
- **几何参数进出**——[保存参数] 把当前配置写成 pyFAI 通用 `.poni` 文件，[加载参数] 读回来存成命名条目（重启仍在，命令行 `--config` 同样可用）。

## 测试

```bash
python scripts/run_tests.py     # 528 条，带看门狗
```

合成图像单元测试（不需要真实数据，纯 unittest）：弧覆盖率失效判据（居中/边缘/角落/束心在图像外四种摆法）、几何失效点数值、偏置摆法的自研积分兜底、各背景估计器对已知合成背景的表征，以及界面的接线（实时预览、按文件记锚点、原始/基线辅助线）。

`scripts/stress_panels.py` 是两处 offscreen 死锁的回归探针（都已修：面板工具栏改自绘、后台任务改长驻工作线程）；`scripts/run_tests.py` 给套件带看门狗——万一卡住会打印所有线程的 Python 栈并以非 0 退出，不会静默挂到天荒地老。

## 路线图

- 1D 图谱上的寻峰与峰形拟合（背景扣除先落地——扣掉基线之后峰高与峰面积才有意义）
- 线形分析：Scherrer / Williamson–Hall 尺寸-应变
- 结构因子模拟
- 基本的 Rietveld 精修引擎
- 机器学习：物相识别聚类 + 峰形 CNN 分类

## 项目结构

```
.
├── data/                       # XRD 原始数据（.tif，不进 git）
├── outputs/                    # 脚本输出 + 产物缓存（本地，不进 git）
├── showcase/                   # README 展示用的精选示例图（进 git）
├── scripts/                    # 四个分析脚本 + 五个自检/探针工具
├── src/xrd_toolkit/
│   ├── config.py               # 几何配置注册表（每批实验一个条目，--config 选择）
│   ├── cli.py                  # 命令行共用交互选文件菜单
│   ├── core/processor.py       # 图像计算（线剖面、自动定位环圆心）
│   ├── services/               # data_loader · integrator · background · range_selector · ring_metrics · stage_cache
│   └── gui/                    # PySide6 桌面界面（python -m xrd_toolkit.gui）
├── tests/                      # 合成图像单元测试 + 界面集成测试
├── docs/                       # 英文 README、详细说明
├── environment.yml             # conda 环境定义
└── pyproject.toml              # 项目元信息与依赖
```

## 作者

Chenze Bian —— 香港城市大学 物理学与数据建模及量子技术硕士 · [GitHub](https://github.com/xyyzzc3)

## 许可

[MIT](../LICENSE)
