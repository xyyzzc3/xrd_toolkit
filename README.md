# XRD Toolkit

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
[![Version](https://img.shields.io/github/v/release/xyyzzc3/xrd_toolkit?color=blue)](https://github.com/xyyzzc3/xrd_toolkit/releases/latest)
![Status](https://img.shields.io/badge/Status-active_development-orange)

A modular Python toolkit for X-ray diffraction (XRD) data analysis, developed as part of *PHY6528 Advanced Research in Applied Physics* at City University of Hong Kong. It reads 2D diffraction images (`.tif` / `.edf` / `.cbf`), calibrates the detector geometry against a standard sample (LaB₆ by default; CeO₂, Si and other common standards are built in), integrates the 2D pattern into standard 1D powder spectra, and analyses them — peak finding, single-peak and full-pattern profile fitting, Le Bail/structural refinement and CIF-based pattern simulation — as a set of CLI scripts and a PySide6 desktop app over the same engine.

中文说明：[README.zh-CN.md](docs/README.zh-CN.md) · Quick start for trial users: [docs/使用说明.html](docs/使用说明.html) (in-app: Help → 使用说明, F1) · Long version: [docs/NOTES.md](docs/NOTES.md) · Changes: [CHANGELOG.md](CHANGELOG.md) · Citation: [CITATION.cff](CITATION.cff)

## Download & run (no Python needed)

**Latest trial builds** — self-contained apps built from this repo by GitHub
Actions (the links below always point to the newest release):

| Your machine | Download |
|---|---|
| Windows 10/11 | [XRD-Toolkit-windows.zip](https://github.com/xyyzzc3/xrd_toolkit/releases/latest/download/XRD-Toolkit-windows.zip) |
| Mac, Apple Silicon (M1…M4) | [XRD-Toolkit-macos-apple-silicon.zip](https://github.com/xyyzzc3/xrd_toolkit/releases/latest/download/XRD-Toolkit-macos-apple-silicon.zip) |
| Mac, Intel | [XRD-Toolkit-macos-intel.zip](https://github.com/xyyzzc3/xrd_toolkit/releases/latest/download/XRD-Toolkit-macos-intel.zip) |

Unzip → double-click. The builds are unsigned, so the system blocks the first
launch once: Windows *More info → Run anyway*; macOS *right-click → Open*.
Step-by-step instructions (in Chinese, for lab use): [docs/RELEASE_NOTES.md](docs/RELEASE_NOTES.md) — the zip also carries a Chinese quick-start (`使用说明.html`, also under Help → 使用说明, F1) and third-party license notices (`THIRD_PARTY_NOTICES.txt`, also under Help → 关于).
Requires macOS 13+ / Windows 10+; ~110 MB download.

## Three steps

1. **Open** — `[打开…]` → pick your `.tif` / `.edf` / `.cbf` images (a whole folder works too)
2. **Plot** — check the files → `[1D]` → `[出图（勾选文件）]`
3. **Export** — `[导出数据]` → two-column txt / `.chi` / a combined CSV

Geometry comes from a built-in config for the first LMFP batch; calibrate your
own on the `[校准]` page against the selected standard (LaB₆ by default; common alternatives are built in), or import a pyFAI `.poni`.

## At a glance

<details>
<summary>Show the screenshots and fit figures</summary>

<table>
  <tr>
    <td align="center" width="50%">
      <img src="showcase/lab6/diffraction_image.png" width="100%"><br>
      <sub>Raw 2D image (log scale) — the cross marks the calibrated beam centre</sub>
    </td>
    <td align="center" width="50%">
      <img src="showcase/lab6/calibrated_pattern.png" width="100%"><br>
      <sub>Calibrated 1D powder pattern — red dashed lines are the theoretical LaB₆ positions</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="showcase/lab6/sector_waterfall.png" width="100%"><br>
      <sub>36-sector waterfall — azimuthal uniformity; the staircase edge marks where the detector clips each ring</sub>
    </td>
    <td align="center" width="50%">
      <img src="showcase/gui/gui_main.png" width="100%"><br>
      <sub>The GUI on the same data — file tree with stage folders on the left, parameter panel on the right</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="showcase/gui/gui_calib.png" width="100%"><br>
      <sub>Calibration workbench — current geometry beside comparison slots A / B, engine metrics and a verdict line</sub>
    </td>
    <td align="center" width="50%">
      <img src="showcase/gui/gui_background.png" width="100%"><br>
      <sub>Background subtraction on a real LMFP pattern — the auto baseline and the resulting curve; raw, baseline and result in one frame</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="showcase/gui/gui_compare_heat.png" width="100%"><br>
      <sub>A whole batch at a glance — eight background-subtracted frames stacked beside their 2θ × sample heatmap</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="showcase/simulation/sim_xrd_fit_LaB6.png" width="100%"><br>
      <sub>Full-pattern fit of the LaB₆ standard — observed (points), calculated (line), background, the observed−calculated difference and the Bragg-position ticks (Le Bail intensities, Rwp 4.24%)</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="showcase/simulation/sim_xrd_gsas_keys.png" width="100%"><br>
      <sub>Two hard checks behind the profile model — area is conserved (peak height ∝ 1/FWHM), and the calculated FWHM agrees with GSAS-II to within 1.2% across the W / X / Size / Mustrain channels</sub>
    </td>
  </tr>
</table>

</details>

## Highlights

- **Calibrated 2D → 1D integration** — geometric refinement against the selected standard (LaB₆ by default, using pyFAI's LaB₆ values; certificate not recorded; CeO₂, Si, α-Al₂O₃ and other common standards are built in), followed by full 0°–360° azimuthal integration into a standard two-column powder pattern `(2θ(deg), intensity)`, ready for peak finding, profile fitting and PDF analysis.
- **Calibration metrics that catch what the fit residual cannot** — every run reports the median ring-position deviation, how many of the 16 rings came out complete, and the per-ring lattice-constant dispersion: a run can print a 0.0000° residual while its rings sit 10 px off the real ones.
- **Background subtraction, live** — empty-scan subtraction (with an exposure/beam-current factor), a rolling-window auto baseline, and manual anchors that set the level while the auto baseline supplies the shape; smoothing (boxcar / Savitzky–Golay) and interval cuts on top. Negatives are kept by default — clipping the noise floor at 0 biases the mean up by ≈ 1σ.
- **Batch workflow with staged products** — integrate a folder; curves and background-subtracted curves are cached per stage (keyed by file fingerprint + geometry + settings, reusable across sessions) and listed as **groups in the file bar**, so an 80-file batch goes to a comparison plot or heatmap in two clicks instead of re-integrating.
- **Off-centre beams and sector analysis** — partial-ring datasets (beam at the detector edge or corner) run end to end, including a built-in numpy integration where pyFAI's radial binning gets unreliable; the 36-sector waterfall exposes preferred orientation and detector clipping.
- **Desktop app over the same engine** — five stage entrances, a live parameter panel, on-curve peak finding with a peak table, hover readout, box zoom, single-hue gradient curves, `.poni` in/out, PNG/TIF export.
- **Theoretical patterns from a CIF** — an XRD simulation module (Bragg + structure factor, written out by hand) plots a structure's powder pattern with (hkl) annotations and card-style hexagonal indices, verified peak-for-peak against pymatgen's reference calculator (`scripts/simulate_xrd.py data/structures/Si_9008565.cif outputs/sim_Si.png`; pymatgen is an optional extra: `pip install 'xrd-toolkit[simulation]'`).
- **Peak finding and single-peak profile fitting** — automatic peak detection with a robust noise estimate, prominence/height thresholds, minimum spacing and optional FWHM filtering (`services/peak_finding.py`); Gaussian, Lorentzian, pseudo-Voigt and split-Pearson VII single-peak fits with local backgrounds (`scripts/fit_peaks.py`), plus Scherrer size and instrument-corrected Williamson–Hall analysis (quadrature or linear subtraction of a LaB₆ standard's width).
- **Full-pattern fitting of measured standard patterns** — an independent implementation of the Thompson–Cox–Hastings pseudo-Voigt profile with Caglioti/Lorentzian width terms, following the GSAS-II PXC parameter conventions and units (Caglioti, Paoletti & Ricci, 1958; Thompson, Cox & Hastings, 1987; Toby & Von Dreele, 2013), with the profile cross-checked against GSAS-II simulations. Supports Le Bail or structural fits, extra phases, symmetry-constrained atomic-coordinate and site-occupancy refinement, and fixed-pixel hot-spot masking. The LaB₆ standard fixes the instrument profile and its own pattern is fitted with Le Bail intensities (Rwp 4.24%); example: `showcase/simulation/sim_xrd_fit_LaB6.png`.

## Install (from source)

```bash
conda env create -f environment.yml    # once
conda activate XRD_Toolkit_Environment
pip install -e .                       # numpy / scipy / matplotlib / fabio / pyFAI / PySide6 come along
python -m xrd_toolkit.gui              # the desktop app
```

> Sample data are not in the repository — point `--file` at your own diffraction images. Sample-derived figures and reports go under `outputs/` (gitignored); the repository ships code, CIFs and standard-sample (LaB₆) examples only.

```bash
# calibrate + integrate one image (prints a config entry to paste into config.py)
python scripts/calibrate_integrate.py --file data/xxx.tif --wavelength 0.1223 --pixel 200 --dist0 1600
# integrate with an existing geometry, or run the sector/waterfall analysis
python scripts/integrate_pattern.py  --file data/xxx.tif
python scripts/sector_waterfall.py   --file data/xxx.tif --n-sectors 36
# simulate a theoretical powder pattern from a CIF (structures ship in data/structures/)
python scripts/simulate_xrd.py data/structures/Si_9008565.cif outputs/sim_Si.png
# fit the LaB6 standard (Le Bail profile; Bragg-position ticks)
python scripts/simulate_study.py fit
```

Typical workflow: `view_diffraction` (inspect) → `calibrate_integrate` (geometry) → `integrate_pattern` (1D pattern) → `sector_waterfall` (uniformity). Outputs land in `outputs/{dataset}/`. Omit `--file` and each script shows an interactive menu of everything in `data/`; the three consuming scripts take `--config NAME` (default `lmfp1_lab6`). **Script table and full flag list: [docs/NOTES.md](docs/NOTES.md#cli-scripts).**

## Tests

```bash
python scripts/run_tests.py     # 990+ tests, behind a watchdog
```

Synthetic-image unit tests (no sample data needed, plain `unittest`) cover the calibration and integration engine, the background estimators, the staged-product cache, and the GUI wiring. `scripts/check_gui.py` drives the real interface with real data and real canvas events; `scripts/stress_panels.py` is the regression probe for two offscreen deadlocks.

## Week 9: mask + in-situ three-phase batch

The toolbar now has an independent **Mask** editor next to **Calibrate**:
draw/erase beamstop, module gaps and hot pixels, auto-detect isolated hot
pixels, save/load `.npy`/`.edf`, and preview the 1D pattern before/after
masking. Applying a mask writes a small **Mask 2D** product (raw image + mask
manifest) into the file dock; double-click it for a 2D view, or integrate it
to 1D. Masked and unmasked 1D results are distinct sources that can be
compared side by side.

`scripts/fit_insitu.py` refines an in-situ LMFP sequence with three phases
(Li-rich LMFP, Li-poor heterosite, Al reference), sequential seeding, anomaly
flags, a CSV summary and split timings. A three-frame GSAS-II cross-check
(`scripts/gsas2_insitu_crosscheck.py`) compares cells and phase-fraction
trends against our fit. **Mask quick start (Chinese):**
[docs/MASK.zh-CN.md](docs/MASK.zh-CN.md). Mask strategy, fit parameters,
the cross-check and the efficiency measurements:
[docs/WEEK9_MASK_AND_INSITU.md](docs/WEEK9_MASK_AND_INSITU.md).

## Roadmap

Peak finding, profile fitting and full-pattern (Le Bail/structural) fitting
landed in v0.1.5. The remaining items are:

- Machine learning: clustering for phase identification and CNN peak-shape classification
- Experimental validation on laboratory-source data and an Si standard

## Project structure

```
.
├── data/                          # raw XRD images (.tif, untracked) + data/structures/ (reference CIFs, tracked)
├── outputs/                       # script outputs + product cache (local, not tracked)
├── showcase/                      # curated example figures (this README, the manual, the simulation module)
├── packaging/                     # PyInstaller build (icons, spec, constraints)
├── scripts/                       # the analysis scripts + self-check tools
├── src/xrd_toolkit/
│   ├── config.py                  # named geometry configs (one per batch; --config selects)
│   ├── cli.py                     # shared interactive file-selection menu
│   ├── core/processor.py          # image processing (line profiles, ring-centre localization)
│   ├── services/                  # data_loader · integrator · background · range_selector · ring_metrics · peak_finding · stage_cache
│   ├── simulation/                # CIF → powder pattern, peak/profile fitting and microstructure (hand-written; pymatgen optional)
│   └── gui/                       # PySide6 desktop app (python -m xrd_toolkit.gui)
├── tests/                         # synthetic-image unit tests + GUI integration tests
├── docs/                          # Chinese README, long-form notes
├── environment.yml                # conda environment (one-command setup)
└── pyproject.toml                 # package metadata & dependencies
```

## Data, privacy & compliance

All computation happens **on your machine**: the app never connects to the
network, uploads nothing, and has no telemetry. It is a scientific tool —
verify critical numbers before publication. Licensed MIT; the full license
texts of the bundled third-party components (Qt/PySide6, pyFAI, NumPy,
SciPy, Matplotlib, …) ship as `THIRD_PARTY_NOTICES.txt` inside the app
(Help → About) and in [`packaging/THIRD_PARTY_NOTICES.txt`](packaging/THIRD_PARTY_NOTICES.txt).

## Author

Chenze Bian — MSc in Physics with Data Modelling and Quantum Technologies, City University of Hong Kong · [GitHub](https://github.com/xyyzzc3)

## License

[MIT](LICENSE)
