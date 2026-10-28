# Week 9：Mask 集成 + LMFP 原位三相批量

本文对应 Week 9 任务（2026-10-27）：把工具从"单帧拟合"推进到"原位序列
批处理"。所有数字来自本机实测，复现命令在文末。

## 1. Mask（2D → 1D 之前）

### 1.1 为什么必须在积分前

方位角积分是对每个 2θ 环带内的像素求平均。被 beamstop、模块缝、坏点
污染的像素如果留在图里，会直接进入平均：坏点抬高整段背景/制造假峰，
beamstop 阴影压低低角强度。事后再在 1D 上扣背景已经晚了——1D 曲线
本身已经错了。

### 1.2 本批策略

约定：`mask[y, x] = True` 表示该像素被遮掉。多个来源取**并集**（任何
一方认定坏就不参与平均），不是交集。

| 来源 | 实现 | 本批结果 |
|---|---|---|
| 自动坏点（生产） | `services/hot_pixels.py`：15×15 局部中值、>8× 中值、连通域面积 ≤300 px、峰值 ≥20k | 24 团 / 691 px |
| 手动 beamstop | Mask 编辑器矩形（束心周围 90×90 px） | 8100 px |
| 探测器边缘 | 最外 2 px | ~16k px |
| 合计 | 并集 | 25159 px（0.60%） |

**径向 nσ 自动检测**（按径向箱中位数/MAD、>5σ）已经实现并放进 GUI 的
"实验性"按钮，但**没有用于生产**：在真实 LMFP 帧上它会沿真实衍射环
标出约 18k px（见 `services/mask.py` 的说明）。这正好回答思考题：
自动检测不能替代手动 mask——它分不清"坏像素"和"真实强环的方位起伏"。

### 1.3 界面与文件

- 工具栏 `[校准]` 旁边新增 `[Mask]`，打开**独立**编辑器（不塞进参数坞）：
  打开代表性图像、画笔/橡皮/矩形、清空/撤销、自动坏点、径向 nσ
  （实验性）、保存/加载、mask 前后 1D 预览。
- 文件格式 `.npy` / `.edf`（`fabio`）。默认路径
  `outputs/masks/<geometry>_mask.npy`，同一批原位数据共用。
- **Mask 后的 2D 产物**（2026-10-28 定稿）：[生成 Mask 2D] 在 stage cache
  的 `masked2d/` 下写一个 `.mask2d.npz` manifest = 原始图路径 + bool mask
  （packbits）+ 形状/遮罩像素数/标签/mask 哈希。**不复制 16 MB 像素**，
  原始图也不改写；masked 像素只是“不参与积分”，不是填 0/NaN。
- 文件栏新增「Mask 后的 2D」组：每张原图 × 每个 mask 一条。双击 = 2D
  面板（原图 + 红色 mask 叠加）；右键/[打开 1D 图] = 按这份 mask 积分；
  1D 产物挂在该条目下，`masked` 与未遮罩的 1D 可以并存、并排对比。
- `load_diffraction_image(manifest)` 返回 `np.ma.MaskedArray`；
  `integrate_1d` / `integrate_sectors` 自动取出它的 mask（与显式传入的
  mask 取并集），pyFAI 与自研偏置束心两条路径都排除被遮像素。
- manifest 是**真实文件**，所以 1D 缓存键、面板键、对比/热图/导出/批量
  处理都把它当“一个源”走：masked 与 unmasked 的结果天然是两条不同来源，
  不需要窗口级 mask/epoch 栅栏。
- [应用到勾选文件]：给文件栏里勾选的每个原始图各生成一条（同形状/同
  探测器）。[清空] 只清编辑器里的当前 mask，已生成的 Mask 2D 产物保留；
  右键可删单条/整组（原图与 mask 文件都不动）。
- 加载只接受 `.npy/.edf`（选错 `.tif` 会被拒绝）；自动坏点/径向 nσ 在
  后台线程跑；**标定的环检测仍用原图**（不套 mask）。
- 删除 Mask 2D（右键单条/整组）会同时清掉它名下的 1D 与处理产物缓存；
  原始图与 mask 文件不动。
- 导出 1D 的文件头写 mask 溯源：`# mask: sha <哈希前 8 位>, N px`。
- `scripts/check_gui.py` I 节：真工具栏打开编辑器 → 画矩形 → 生成 manifest
  → 文件栏条目 → 双击 2D（MaskedArray）→ 1D 积分 → 清空编辑器，真数据
  exit 0。

### 1.4 前后对比

`outputs/masks/mask_before_after_00029.png`：灰色 = 无 mask，红色 = 有
mask。最明显的是 2.8–3.0° 的固定坏点团（假峰，max 差 ~2100 counts）；
束心矩形/边缘在 1–8° 积分范围外，对曲线无影响，只保护全范围积分。
GUI 截图：`outputs/masks/mask_editor.png`。

## 2. 三相全精修批量

### 2.1 数据与物相

- 数据：`~/Desktop/lmfpdata`，81 帧 TIFF（`00029`–`00189`，步长 2），
  2048×2048，同一几何 `lmfp1_lab6`（λ = 0.1223 Å）。
- `LMFP_rich`：LiMn₀.₆Fe₀.₄PO₄，橄榄石，主相，a/b/c 自由。
- `LMFP_poor`：Mn₀.₆Fe₀.₄PO₄ heterosite（COD 9000260 去掉 Li、按
  富锂轴序置换），额外相，a/b/c 自由。
- `Al`：集流体参考相，立方 a=4.061 Å **固定**，峰强逐反射自由
  （单轴 March–Dollase 在 (331) 上高估，2026-10-27 已改）。

### 2.2 精修参数

| 项 | 设置 |
|---|---|
| 仪器 | LaB₆ Le Bail 定 zero/U/V/W/X/Y，之后**固定**（逐帧不精修峰形） |
| 晶胞 | rich a/b/c、poor a/b/c 自由；Al 固定 4.061 Å（用户 2026-10-27 拍板 Al 作参考） |
| 晶胞边界 | rich 10.20–10.50 / 5.90–6.15 / 4.65–4.76；poor 9.50–10.35 / 5.50–6.05 / 4.65–4.90 Å |
| 背景 | 自动基线 + 0.25° 分段修正 + 曲率罚项 0.1，逐帧 |
| 强度 | rich/poor 结构 scale；Al 逐反射（修 (331) 纹理误差，见 §1/2026-10-27）；rich 加 2 阶平滑强度修正 |
| 首帧 | max_nfev=120；充分收敛后作为种子 |
| 后续帧 | max_nfev=40；继承**上一可用帧**（Rwp 合格、晶胞不跳）的 zero 与两相晶胞 |
| 异常 | 高 Rwp>20% / rich 晶胞跳 >0.12 Å / poor 跳 >0.25 Å / 无相信号 → 标记，不中断；优化器退出标志失败但 Rwp/相分数合格 → `ok_soft` |

### 2.3 相分数口径

```
A_p = scale_p × Σ(table.inten_p) × table.norm_p / 100
f_rich = A_rich / (A_rich + A_poor)
```

这是**相对散射强度分数**，不是 Rietveld 重量分数（没有乘 ZMV/密度，
也没有绝对标度校正）。Al 只作参考强度（其逐反射强度是为修 Al (331) 纹理误差而放开的，
Al 总和列 CSV 的 `al_ref`），不参与 LMFP 相分数。看趋势可以，
别当 wt% 写。

**esd**：fit 在最终点给出线性块（scale/强度修正/背景）的协方差，
`f_rich_esd` 由 rich/poor 两个 scale 的 2×2 子块传播：
`∂f/∂scale_r = k_r·A_p/Σ²`、`∂f/∂scale_p = -k_p·A_r/Σ²`。
它**不含**非线性参数（晶胞/峰形）的不确定度，所以是下限；Al 逐反射
若顶到非负边界，其 esd 也只是近似（Al 不进 f_rich，不影响本列）。

### 2.4 结果

- **81/81 帧跑完**。严格 `least_squares` 成功 32 帧；优化器退出标志
  失败但 Rwp/相分数合格的 `ok_soft` 45 帧；`cell_jump` 4 帧
  （严格收敛率 39.5%）。但 **81/81 帧都可用**：Rwp ≤ 20%、
  晶胞/相分数有限；Rwp 范围 **2.10–2.62%**，最大在相变中点
  （第 34 帧，2.62%）。`ok_soft` 集中在相变/放电段，原因是
  `max_nfev=40` 撞上限，不是拟合发散；种子策略已让后续帧继续跟踪。
- **相分数 V 形**：f_rich = **0.917**（第 1 帧，00029）→
  **0.042**（第 45 帧，00117，充电末端）→ **0.707**（第 81 帧，
  00189，放电回升）。f_poor 互补。
- **相分数 esd / 充电末端区间**：`f_rich_esd` 由线性协方差传播
  （§2.3）：第 1 帧 **0.917 ± 0.008**、第 45 帧 **0.042 ± 0.036**、
  第 81 帧 **0.707 ± 0.015**（81/81 帧有限）。相变/放电段（第
  25–77 帧）esd 普遍 **0.035–0.043**，不是只有第 45 帧；第 45 帧
  因为 f 最小，相对 esd 达 85%。再把 rich a 固定在
  10.20/10.22/10.25/10.30 Å 重拟合，f_rich = 4.2 / 5.4 / 8.8 / 13.2%
  （Rwp 2.206–2.238）——rich a 已贴下限，**这帧只能报 ~4–13% 的区间**，
  不要当 4.2% 单点。
- **Al 参考强度** 32.4–32.8 M（波动 ~1%），稳定。
- **晶胞**（首 → 充电末端 → 末）：
  - rich a 10.349 → 10.200 → 10.320 Å；b 6.019 → 5.969 → 6.011；
    c 4.700 → 4.696 → 4.706。
  - poor a 10.168 → 9.847 → 10.118 Å；b 5.820 → 5.807 → 5.948；
    c 4.780 → 4.792 → 4.769。
  - 注意：f_rich < ~5% 时 rich 相峰很弱，rich a 会贴到下限
    10.20 Å；这段的 rich 晶胞只能作趋势参考，不要当精确值。
- CSV 额外给出 `usable`（Rwp≤20%、晶胞/分数有限、非错误；跳变仍标在
  status）、两相晶胞 esd（`rich_a_esd` … `poor_c_esd`）和 `f_rich_esd`
  （线性参数传播，见 §2.3），可直接画误差棒/筛坏帧；4-panel 的相分数
  面板画成 esd 阴影带。
- 图：`outputs/insitu/insitu_fit_frame_001.png`（首）、
  `_041.png`（中）、`_081.png`（末）、`insitu_trends.png`（趋势）。
- 4-panel 展示图：`outputs/insitu/insitu_summary.png`（相分数+状态、
  两相晶胞+esd、Rwp/Rp+异常、充放电分区）。

### 2.5 效率评估

- 总耗时 **685.5 s（11.4 min）**，平均 **8.5 s/帧**。
- 分布（平均）：**fit 8.34 s（98.6%）**；integrate（含 mask）0.09 s；
  load 0.03 s；背景 0.002 s。**瓶颈非常明确：逐帧非线性精修**，
  不是 IO/积分/背景。
- 可扩展性：线性估计 2× 帧数约 **22.9 min**，1000 帧约 **2.4 h**。
  积分可以并行，但只占 1%；真正要提速必须动 fit。
- 收敛率：严格 32/81 = 39.5%；另有 `ok_soft` 45 帧（退出标志失败、
  Rwp 2.1–2.6%、晶胞/分数有限），81/81 帧可用。`ok_soft` 是
  **系统性**的（相变/放电段的 max_nfev），不是随机坏帧。
- 与手工流程对比：手工逐帧建三相模型、检查晶胞/相分数，约
  2–5 min/帧；81 帧约 **3–7 h 的操作时间**。批量 **11.4 min
  无人值守**，省的是操作时间，不是单帧计算时间（单帧计算仍 ~9 s）。
- 最值得做的 1–2 件事：
  1. **降 fit 成本**：稳定段继承种子后减少 `nfev`/固定更多参数；
     或按充/放电段分段，段间并行（现在顺序继承，fit 98% 时间）。
  2. **区分"严格收敛"与"可用"**：✅ 2026-10-28 已落地——Rwp 合格的
     max_nfev 帧标成 `ok_soft`（本批 45 帧），status 与 usable 分开报，
     同时不掩盖真正发散的帧。

### 2.6 与市面标准对比

典型能力（版本不同会有差异）：

| 能力 | 本项目 | GSAS-II | FullProf | TOPAS | HighScore | pyFAI/DAWN |
|---|---|---|---|---|---|---|
| 2D→1D + mask | ✅ 独立 Mask 窗口 | 图像处理有 mask/limits | 不含 2D 积分 | 不含 | 主要吃 1D | ✅ pyFAI-drawmask/DAWN |
| 顺序批量精修 | ✅ 自写 81 帧 | ✅ Sequential Refine | ✅ sequential | ✅ | ✅ 批处理 | ❌ |
| 相分数 | **相对散射强度** | **重量分数** | **重量分数** | **重量分数** | **重量分数** | ❌ |
| 不确定度 | 晶胞/分数有 esd（分数为线性参数下限） | ✅ | ✅ | ✅ | ✅ | ❌ |
| 标准导出 | CSV/PNG | `.gpx`/CSV | `.pcr/.sum` | Excel/CSV | 报告 | 1D/2D |
| 脚本/开源 | Python 开源 | Python 开源 | 学术免费 | 商业 | 商业 | 开源 |
| 本批速度 | 81 帧 11.4 min | 视模型 | 视模型 | 视模型 | 视模型 | 积分很快 |

**定位**：本项目的强项是一体化（2D mask → 1D → 三相批量 →
CSV/图）、脚本化、开源、快，适合快速筛趋势；不是 GSAS-II/FullProf/
TOPAS/HighScore 那种定量 Rietveld 的替代品——我们的 f_rich 是相对
散射强度，不是 wt%；分数 esd 只含 scale/背景线性块，不含晶胞/峰形
非线性项，应当下限；且固定了 U/V/W/size/strain。
报告里应写“relative scattering fraction”，不要写“wt%”。

数值对照已经做了（2026-10-28），见 §2.8：用 GSAS-II 跑第 1/45/81 帧，
比较晶胞和相分数趋势。`scripts/gsas2_crosscheck.py` 仍只做模拟。

### 2.7 模型无关校验（(200) 双峰质心）

三相 Rietveld 的 f_rich 依赖模型；用低角 (200) 双峰做一把独立的尺子：

- rich (200) ≈ 1.35°，poor (200) ≈ 1.43°（λ=0.1223 Å），只差 ~0.08°，
  单峰窗口切不开；对**整个双峰**做强度加权质心。
- 每帧：同一 mask/几何 → 1D → 自动基线 → 1.18–1.55° 窗口，
  两侧平带（1.18–1.28、1.50–1.56）做局部线性背景 → 面积/质心。
- 结果：质心 **1.352°（第 1 帧）→ 1.419°（第 45 帧）→ 1.359°（第 81 帧）**，
  与批量 f_poor 的散点 **R² = 0.967**。这条曲线完全没用到三相
  Rietveld 的结构因子/峰形/背景模型，说明相分数趋势不是模型
  “编”出来的。
- (200) 双峰总面积随相变在 ~0.8–1.5× 变化——poor (200) 的结构因子
  本来比 rich 强，**不是守恒量**；图上只作数据质量参考，不当相分数。
- 产物：`outputs/insitu/model_independent.csv` / `.png`。

### 2.8 GSAS-II 独立对照（3 帧）

用 GSAS-II 对第 1/45/81 帧做独立精修，核对本批的晶胞与相分数趋势。
脚本 `scripts/gsas2_insitu_crosscheck.py` 分三步：`prepare`（本项目环境）、
`refine`（单独的 `gsas2` conda 环境）、`compare`（本项目环境）。

**共同输入**：同一 1D 数据（同一 mask/几何、3000 点、1–8°）、同一仪器
参数（LaB6 Le Bail 定出的 zero/U/V/W/X/Y，全部固定）、同一结构、Al 晶胞
固定 4.061 Å 且只作参考相。GSAS-II 背景是 Chebyshev-1 六项逐帧精修，
本项目是滑动基线 + 0.25° 分段 + 曲率罚项——**两边 Rwp 不可直接比较**
（GSAS-II wR 10.1–10.2%，本批 Rwp 2.10–2.62%），只看晶胞和趋势。

**必须记录的坑**：仓库里的模型 CIF 是 P1 展开（给手写结构因子用）。
GSAS-II 对这批 P1 展开 CIF 做晶胞精修（6 个参数）时，profile 的 Fortran
代码会零除（`divide by zero encountered in scalar divide`）；Le Bail、
参数边界、缩小 shift factor 都试过，均失败。Pm-3m 的 LaB6 和 Pnma 的
橄榄石正常。所以 `prepare` 用 pymatgen 的 `SpacegroupAnalyzer` 把
rich/poor 还原成 Pnma，并核对还原前后的峰位与强度逐峰一致
（相对差 < 1e-4，模式完全一样），保证 GSAS-II 吃的是同一个结构。

**精修顺序**：以多数相帧为锚，45 → 1 → 81（45 帧 poor 占 96%、
1 帧 rich 占 92%）。每帧两段：先固定晶胞修背景 + 三相 scale（4 轮），
再放开 rich/poor 晶胞（8 轮）；直方图总标度固定，相分数由三相 scale
承担。Biso 按本批设置（rich 0、poor/Al 0.5 Å²）。

**相分数（rich/(rich+poor)）**

| 帧 | 本项目（相对散射强度） | GSAS-II（wt%） | rich wt | poor wt | Al wt |
|---|---|---|---|---|---|
| 1 | 0.917 | 0.900 | 0.469 | 0.052 | 0.479 |
| 45 | 0.042 | 0.178 | 0.092 | 0.428 | 0.479 |
| 81 | 0.707 | 0.818 | 0.392 | 0.087 | 0.521 |

V 形趋势一致（45 帧最低、81 帧回升）。45 帧差得最多（4.2% vs 17.8%）：
这一帧 rich 是少数相、rich a 已贴 10.20 Å 下限，线性 esd 就有 ±3.6%
（相对 85%）；把 rich a 固定在 10.25/10.30 Å 时本项目 f_rich 升到
8.8%/13.2%——所以这帧只能报 **~4–13% 的区间**，GSAS-II 的 17.8%
落在区间上端外侧一点，不构成趋势矛盾。Al 的 wt% 稳定在 48–52%
（Al 基片很强），两边都不把 Al 计入 LMFP 分数。

**晶胞**：多数相的 a/c 在 ~2 esd 内；rich 的 b 系统性偏 +0.006–0.009 Å
（3–4 esd），poor 在 45 帧的 b 只差 0.0002 Å。少数相更差：第 1/81 帧的
poor，GSAS-II 卡在种子上（poor a = 9.815–9.845 Å，本项目
10.118–10.168 Å）；第 45 帧的 rich b 也偏了 0.097 Å（11.8 esd）。
GSAS-II 的 poor (200) 会落在 ~1.43°，但第 81 帧的 1D 曲线在 1.43° 处是
平背景（poor 峰只可能在 1.36–1.40°，与 §2.7 质心 1.359° 一致）——
所以少数相的偏差是“弱相没收敛”，不是两套方法互相否定。表里的
sigma = Δ / GSAS-II 的 esd。

**结论**：趋势层面两套独立实现一致；绝对值层面，多数相晶胞在几 esd 内，
少数相的晶胞与分数是模型相关的，报告里应给区间/趋势而不是单点。
rich b 的 3–4 esd 系统偏差很可能来自峰形/背景模型差异（GSAS-II wR
10.1–10.2% vs 本批 Rwp 2.1–2.6%），不影响趋势结论。本批的 f_rich
继续按 §2.3 的相对散射强度口径使用，不改成 wt%。

产物：`outputs/insitu/gsas2/`（`comparison.txt`、`comparison.png`、
`gsas2_insitu.csv`、`summary.txt`、`meta.json`、`ours.json`、对称化 CIF）。

## 3. 复现

```bash
# Mask（GUI）：工具栏 [Mask] → 打开图像 → 画/自动 → 保存 → 应用并预览
# 本批 mask 已生成：
ls outputs/masks/lmfp1_lab6_mask.npy

# 3 帧 pilot
python scripts/fit_insitu.py --data ~/Desktop/lmfpdata \
    --mask outputs/masks/lmfp1_lab6_mask.npy \
    --outdir outputs/insitu --limit 3

# 全量
python scripts/fit_insitu.py --data ~/Desktop/lmfpdata \
    --mask outputs/masks/lmfp1_lab6_mask.npy \
    --outdir outputs/insitu

# 4-panel 展示图（读 CSV，不重拟合）
python scripts/plot_insitu_summary.py \
    --csv outputs/insitu/insitu_fit.csv \
    --out outputs/insitu/insitu_summary.png

# 模型无关 (200) 双峰质心校验
python scripts/check_insitu_peaks.py \
    --data ~/Desktop/lmfpdata \
    --mask outputs/masks/lmfp1_lab6_mask.npy \
    --batch-csv outputs/insitu/insitu_fit.csv \
    --outdir outputs/insitu

# GSAS-II 独立对照（3 帧；refine 必须用装了 gsas2pkg 的环境）
python scripts/gsas2_insitu_crosscheck.py prepare \
    --data ~/Desktop/lmfpdata \
    --mask outputs/masks/lmfp1_lab6_mask.npy \
    --batch-csv outputs/insitu/insitu_fit.csv \
    --outdir outputs/insitu/gsas2
conda run -n gsas2 python scripts/gsas2_insitu_crosscheck.py refine \
    --workdir outputs/insitu/gsas2
python scripts/gsas2_insitu_crosscheck.py compare \
    --workdir outputs/insitu/gsas2
```

## 4. 交付物

- `outputs/masks/lmfp1_lab6_mask.npy` / `.edf`
- Mask 后的 2D manifest：`<stage cache>/masked2d/*.mask2d.npz`（本地缓存；
  原始图 + mask，不复制像素）
- `outputs/masks/mask_before_after_00029.png`
- `outputs/masks/mask_editor.png`
- `outputs/insitu/insitu_fit.csv`
- `outputs/insitu/insitu_fit_frame_*.png`（首/中/末）
- `outputs/insitu/insitu_trends.png`
- `outputs/insitu/insitu_summary.png`（4-panel 展示图）
- `outputs/insitu/model_independent.csv` / `.png`（(200) 双峰质心校验）
- `outputs/insitu/gsas2/`（`comparison.txt` / `.png`、`gsas2_insitu.csv`、
  `summary.txt`、对称化 CIF 与输入 xye）
- `outputs/insitu/fit_summary.txt`
