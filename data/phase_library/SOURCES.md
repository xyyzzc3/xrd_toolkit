# 内置精选相库来源（2026-11-11）

这套相库随软件包发布，供物相工作台的 `[内置相库]` 使用：常见无机物、
电池材料、集流体/基片与少量标样，共 41 张 CIF。它**不是**完整晶体学
数据库——用户自己的相库仍可用 `[选目录…]` 指向任意 CIF 目录。

## 来自 Crystallography Open Database（COD）

数据来源：<https://www.crystallography.net/cod/>（开放获取；使用时请
按 COD 的要求注明来源）。文件名尾号即 COD ID：

| 文件 | COD ID |
|---|---|
| `Si_diamond.cif` | 9008565 |
| `Al2O3_corundum.cif` | 1000032 |
| `CoO_1533087.cif` | 1533087 |
| `Co3O4_9005887.cif` | 9005887 |
| `Li2MnO3_1513973.cif` | 1513973 |
| `MgO_1000053.cif` | 1000053 |
| `CaO_1011095.cif` | 1011095 |

## 仓库参考 CIF（随包参考结构，部分文件名带 COD ID）

`Al_fcc.cif`、`LaB6.cif`（NIST SRM 660c 参考结构）、
`LiFePO4_olivine.cif`、`LiMnPO4_olivine.cif`、
`LiMn0.6Fe0.4PO4_olivine.cif`、`Mn0.6Fe0.4PO4_heterosite.cif`、
`FePO4.cif`、`Li2CO3.cif`、`Li3PO4.cif`。
这些文件同时保留在 `data/structures/`（原位/精修工作台的参考结构），
这里放一份方便筛相时直接选到。

## 用标准原型生成的结构

以下文件不是实验 CIF，而是用 pymatgen 按标准空间群、Wyckoff 位置和
常用晶胞参数生成的原型结构（元素金属、岩盐/萤石/金红石/锐钛矿/纤锌矿/
尖晶石/层状氧化物等）：

`Cu_fcc`、`Ni_fcc`、`Ag_fcc`、`Au_fcc`、`Fe_bcc`、`Ti_hcp`、
`Zn_hcp`、`Mg_hcp`、`Graphite`、`NiO_rocksalt`、`MnO_rocksalt`、
`NaCl`、`KCl`、`LiF`、`CaF2_fluorite`、`CeO2_fluorite`、
`Cu2O_cuprite`、`TiO2_rutile`、`TiO2_anatase`、`ZnO_wurtzite`、
`SnO2_cassiterite`、`FeS2_pyrite`、`MoS2`、`Fe2O3_hematite`、
`LiCoO2_layered`、`LiNiO2_layered`、`LiMn2O4_spinel`、
`Fe3O4_magnetite`。

用途是**峰位筛选**：晶胞参数取常用实验值，原子坐标取标准原型；它们
足以判断"这张谱里有没有这个相"，但不能当作精修用的实验结构（要定量/
精修请换真实的实验 CIF）。

## 维护

- 新增条目：优先放真实 CIF，并在上表登记来源/ID。
- 删除条目：确认没有预设或测试直接引用文件名。
- 生成脚本没有随包保留；需要重建时按上面的原型/空间群与晶胞参数
  用 pymatgen `Structure.from_spacegroup` + `CifWriter` 重新生成。
