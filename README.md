# XRD Toolkit

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-active_development-orange)

A modular Python toolkit for X-ray diffraction (XRD) data analysis, developed as part of *PHY6528 Advanced Research in Applied Physics* at City University of Hong Kong. It reads 2D diffraction images (`.tif` / `.edf` / `.cbf`), calibrates the detector geometry against a LaB₆ standard (pyFAI, with automatic ring-center localization as the starting point), and integrates the 2D pattern into standard 1D powder spectra — including sector-wise azimuthal uniformity analysis. All scripts except calibration read a named geometry config from `src/xrd_toolkit/config.py` (selectable via `--config`, default `lmfp1_lab6`).

中文说明：[README.zh-CN.md](docs/README.zh-CN.md)

## At a glance

<details open>
<summary>Show / hide figures</summary>

<table>
  <tr>
    <td align="center" width="50%">
      <img src="showcase/lab6/diffraction_image.png" width="100%"><br>
      <sub>2D diffraction image (log scale) — cross marks the calibrated beam center</sub>
    </td>
    <td align="center" width="50%">
      <img src="showcase/lab6/radial_profile.png" width="100%"><br>
      <sub>Radial intensity profile through the center</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="showcase/lab6/calibrated_pattern.png" width="100%"><br>
      <sub>Calibrated 1D powder pattern (red dashed = theoretical LaB₆ positions)</sub>
    </td>
    <td align="center" width="50%">
      <img src="showcase/lab6/sector_waterfall.png" width="100%"><br>
      <sub>36-sector waterfall plot — azimuthal uniformity check; the staircase right edge marks where each sector's ring is clipped by the detector</sub>
    </td>
  </tr>
</table>

Figures generated from a LaB₆ standard calibration dataset (NIST SRM 660).

</details>

## GUI

A PySide6 desktop front end on top of the same analysis engine — inspect raw images, integrate, overlay and polish plots without touching a terminal:

```bash
python -m xrd_toolkit.gui
```

<table>
  <tr>
    <td align="center" width="50%">
      <img src="showcase/gui/gui_main.png" width="100%"><br>
      <sub>Main window — LaB₆ standard integrated to 1D, parameter dock on the right</sub>
    </td>
    <td align="center" width="50%">
      <img src="showcase/gui/gui_compare.png" width="100%"><br>
      <sub>Compare view — two LMFP scans overlaid with legend</sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src="showcase/gui/gui_customize.png" width="32%"><br>
      <sub>Customize dialog — title, axis labels, linear–log y scale, figure margins</sub>
    </td>
  </tr>
</table>

- **Independent plot panels** — every plot is an MDI subwindow with free resizing (each drag remembers the panel's own aspect), pop-out into a separate OS window, cascade / tile arrangements, and a zoomable drawing area (Ctrl + wheel, Excel-style).
- **One-click views** — [2D] [Profile] [1D] [Waterfall] buttons plot all checked files at once; [Compare] overlays several 1D curves in one panel with four normalization modes (per-curve strongest peak / strongest of all / a chosen file / off). 1D and Compare are fully wired; the remaining views open placeholder panels pending wiring.
- **Live parameter dock** — data and display parameters per panel (tooltips everywhere, Reset / Apply per group, per-panel snapshots); the 2θ range of the data group bounds the integration itself (npt samples within it), while zoom / pan writes the view range back in real time.
- **Panel gestures** — left-drag pans, wheel zooms around the cursor (magnifier toggle), hover shows a data-point readout in the status bar; every panel edge has a resize grip.
- **Per-panel toolbar** — Home / magnifier / Customize (title, axis labels, linear–log y scale, figure margins) / Save PNG.
- **Calibration mode** — a [校准] toggle switches to the calibration workbench: one-click automatic refinement (ring-center localization → pyFAI) or manual point-picking on the rings (≥ 3 points, ≥ 2 rings, ±0.5° snapping), with side-by-side result columns, a Δ-deviation column, and a copy-paste CONFIGS entry template (geometry configs shared with the CLI).

## Highlights

- **Automatic ring-center localization (calibration starting point)** — the direct-beam position is found by FFT cross-correlation, exploiting the fact that a diffraction pattern is centrosymmetric about the ring center; accuracy < 1 px. It is used only as the initial value for geometric calibration — every other step uses the geometry of the config selected via `--config` from `src/xrd_toolkit/config.py`.
- **Geometric calibration with a LaB₆ standard** — pyFAI `GeometryRefinement` refines detector distance and PONI from known LaB₆ peak positions (NIST SRM 660, a = 4.156 Å). Example result: detector distance refined to 1595.80 mm (stored as config `lmfp1_lab6` in `src/xrd_toolkit/config.py`).
- **2D → 1D integration** — full 0°–360° azimuthal integration into a standard two-column powder pattern (2θ, intensity), ready for peak finding, profile fitting, and PDF analysis.
- **Automatic 2θ range selection** — `--range auto` (default) picks the interval with one standard per material: lower bound from the material's standard (first known peak − 0.3° for powder samples; detected halo end − 0.6° ≈ 1.0° for the LaB₆ standard), upper bound auto-detected where the data starts failing. The failure criterion is relative arc coverage — the ring's fraction of azimuth inside the detector falling below 50% of its own maximum — so one criterion adapts to any beam placement: ≈ 7.9° for a centered beam (the old 80% absolute criterion stopped at ≈ 7.44°), and automatically later for off-center beams. Single-curve scripts use the exact geometric value; the waterfall's sector-based measured value lands ≈ 0.5° later (10° sector quantization) — the two are cross-checked and a warning fires only on a real mismatch. The full-range txt master copy is always saved.
- **Off-center (partial-ring) beam support** — datasets recorded with the beam deliberately placed at the detector edge or corner (each ring only partially captured, revealing higher-2θ rings) are handled end to end: a built-in numpy polar integration takes over automatically when pyFAI's radial binning becomes unreliable (pyFAI bins relative to the detector center, verified on synthetic rings), sector χ labels report the actually covered azimuth span instead of a fake 360°, and the waterfall statistics average only over sectors with real signal. Calibration on such datasets should be done on a centered standard image (distance/tilt are instrument properties); `--center` documents this.
- **Named geometry configs** — per-batch calibrated geometries are registered in `src/xrd_toolkit/config.py` and selected with `--config` (in interactive mode, a second menu picks the config after the data files). Calibration prints a copy-paste-ready entry to register a new batch.
- **Sector-wise integration** — the pattern is split into N azimuthal sectors (default 36) and integrated separately; the stacked raw-intensity waterfall (one curve per sector, each drawn until its own intensity drops to zero) reveals preferred orientation or large-grain spotiness and visualizes the detector-clipping geometry.
- **Shared interactive CLI** — one file-selection menu (`xrd_toolkit/cli.py`) reused by all four scripts: number selection, `1,2` multi-select, or `all`.

## Quick start

```bash
# 1. Create the conda environment (once)
conda env create -f environment.yml
# 2. Activate it
conda activate XRD_Toolkit_Environment
# 3. Install this package in editable mode (numpy / matplotlib / fabio / pyFAI come along)
pip install -e .
```

> Note: sample data are not included in this repository — point `--file` at your own diffraction data.

## Scripts

Typical workflow: `view_diffraction` (inspect) → `calibrate_integrate` (geometry) → `integrate_pattern` (1D pattern) → `sector_waterfall` (uniformity). All outputs are written under `outputs/{dataset_name}/`.

Each script accepts either `--file` to pick one dataset, or — without `--file` — an interactive menu listing all files in `data/`. The three consuming scripts also accept `--config NAME` to select a geometry config (default `lmfp1_lab6`); in interactive mode, after picking the data files a second menu asks for the config. `calibrate_integrate.py` prints a copy-paste-ready entry to register a newly calibrated batch in `src/xrd_toolkit/config.py`.

| Script | What it does |
|---|---|
| `scripts/view_diffraction.py` | 2D image viewer (log scale, line profile, calibrated beam-center mark) |
| `scripts/calibrate_integrate.py` | LaB₆ geometric calibration (pyFAI) + azimuthal integration (2D → 1D) |
| `scripts/integrate_pattern.py` | Full-angle integration: 2D image → standard 1D powder pattern (two-column txt) |
| `scripts/sector_waterfall.py` | Sector integration (36 sectors) + waterfall plots + azimuthal uniformity statistics |

## Usage details

<details>
<summary><b>View a diffraction image</b> — <code>scripts/view_diffraction.py</code></summary>

```bash
python scripts/view_diffraction.py --file data/xxx.tif --angle 0 --outdir outputs
```

- `--file`: diffraction image path (.tif / .edf / .cbf)
- `--center`: ring center `cx,cy` — defaults to the beam center of the selected config
- `--config`: geometry config name from `config.py` (default `lmfp1_lab6`)
- `--angle`: profile angle in degrees (default 0)
- `--outdir`: PNG output directory (default `outputs/`)

</details>

<details>
<summary><b>Geometric calibration + 1D pattern (LaB₆ standard)</b> — <code>scripts/calibrate_integrate.py</code></summary>

```bash
python scripts/calibrate_integrate.py --file data/xxx.tif --wavelength 0.1223 --pixel 200 --dist0 1600
```

- `--wavelength`: X-ray wavelength (Å)
- `--pixel`: detector pixel size (µm)
- `--dist0`: initial detector distance (mm) — refined automatically by pyFAI
- `--center`: initial ring center `cx,cy` in pixels (default: auto-localized, < 1 px accuracy). Pass it explicitly for off-center-beam (partial-ring) datasets: the auto-localizer needs full rings, and refinement is unreliable there — calibrate distance/tilt on a centered standard image instead
- `--max-rings`: number of LaB₆ rings used for calibration (default 16)
- `--range`: 2θ range — `full`, `auto` (default, per-material standard), or `lo,hi` degrees (e.g. 1.3,7.3); the full txt is always saved
- `--outdir`: output directory (default `outputs/`)

Pipeline: LaB₆ peak-position calibration (pyFAI `GeometryRefinement`) → azimuthal integration (2D → 1D) → writes `calibrated_2th.txt` + `calibrated.png` (red dashed lines = theoretical peak positions). Code in `xrd_toolkit/services/integrator.py`. After refinement, a copy-paste-ready `CONFIGS` entry is printed so the new batch can be registered in `src/xrd_toolkit/config.py` (the script never writes that file itself).

</details>

<details>
<summary><b>Full-angle integration (2D → 1D standard pattern)</b> — <code>scripts/integrate_pattern.py</code></summary>

```bash
python scripts/integrate_pattern.py --file data/xxx.tif
```

Integrates 0°–360° with the geometry of the selected config (default `lmfp1_lab6`, e.g. 1595.80 mm) and writes a two-column txt (2θ(deg), intensity) + PNG. Override with `--dist`, `--poni`, `--wavelength`; `--range full/auto/lo,hi` controls the 2θ interval of the plot and the `_auto` trimmed txt (full txt always saved).

</details>

<details>
<summary><b>Sector integration + waterfall plots</b> — <code>scripts/sector_waterfall.py</code></summary>

```bash
python scripts/sector_waterfall.py --file data/xxx.tif --n-sectors 36
```

Splits the 0°–360° azimuth into N sectors (default 36, one per 10°), integrates each sector separately (geometry from the selected `--config`, default `lmfp1_lab6`), and writes per-sector two-column txt files under `outputs/{dataset_name}/sectors/` plus a raw-intensity stacked waterfall plot (36 curves offset along the Y axis with adaptive row spacing; each curve is drawn until its own intensity drops to zero, so the staircase right edge marks where each sector's ring is clipped by the detector). Large grains or preferred orientation show up as intensity concentrated in a few sectors. Supports `--range full/auto/lo,hi`.

</details>

## Tests

Synthetic-image unit tests (no sample data needed; plain `unittest`, no pytest):

```bash
conda activate XRD_Toolkit_Environment
python -m unittest discover -s tests -v
```

They cover the arc-coverage failure criterion for centered / edge / corner / outside-image beam placements, the geometric failure-point values, and the off-center integration fallback (regression guard for a pyFAI binning bug).

## Roadmap

- Peak finding & profile fitting on the 1D patterns
- Line-profile analysis: Scherrer / Williamson–Hall size–strain
- Structure-factor simulation
- A basic Rietveld refinement engine
- Machine learning: clustering for phase identification and CNN peak-shape classification

## Project structure

```
.
├── data/                          # raw XRD images (.tif), not tracked by git
├── outputs/                       # script outputs, one folder per dataset (local only, not tracked)
├── showcase/                      # curated example figures shown in the README (tracked)
├── scripts/
│   ├── view_diffraction.py        # 2D viewer (image + line profile + auto center)
│   ├── calibrate_integrate.py     # LaB₆ geometric calibration + azimuthal integration
│   ├── integrate_pattern.py       # full 2D → 1D integration
│   └── sector_waterfall.py        # sector integration + waterfall plots
├── src/xrd_toolkit/
│   ├── config.py                  # registry of named geometry configs (one per batch; --config selects)
│   ├── cli.py                     # shared interactive file-selection menu (used by all scripts)
│   ├── core/processor.py          # image processing (line profiles, auto ring-center localization)
│   ├── services/
│       ├── data_loader.py         # image I/O (fabio)
│       ├── integrator.py          # geometry refinement + 1D integration (pyFAI)
│       └── range_selector.py      # automatic 2θ range selection (per-material standards)
│   └── gui/                       # PySide6 desktop GUI (python -m xrd_toolkit.gui)
├── tests/                         # synthetic-image unit tests (unittest): partial-ring geometry & DIY integration
├── docs/                          # Chinese README (original)
├── environment.yml                # conda environment (one-command setup)
├── pyproject.toml                 # package metadata & dependencies
└── README.md
```

## Author

Chenze Bian — MSc in Physics with Data Modelling and Quantum Technologies, City University of Hong Kong · [GitHub](https://github.com/xyyzzc3)

## License

[MIT](LICENSE)
