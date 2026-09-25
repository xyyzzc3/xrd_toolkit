# XRD Toolkit

![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-active_development-orange)

A modular Python toolkit for X-ray diffraction (XRD) data analysis, developed as part of *PHY6528 Advanced Research in Applied Physics* at City University of Hong Kong. It reads 2D diffraction images (`.tif` / `.edf` / `.cbf`), calibrates the detector geometry against a LaB₆ standard, and integrates the 2D pattern into standard 1D powder spectra — as four CLI scripts and as a PySide6 desktop app over the same engine.

中文说明：[README.zh-CN.md](docs/README.zh-CN.md) · Long version (calibration metrics, background subtraction, batching, engineering notes): [docs/NOTES.md](docs/NOTES.md)

## At a glance

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
      <sub>The GUI on the same data — file tree with stage folders on the left, parameter dock on the right</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="showcase/gui/gui_calib.png" width="100%"><br>
      <sub>Calibration workbench — current geometry beside comparison slots A / B, engine metrics and a verdict line</sub>
    </td>
    <td align="center" width="50%">
      <img src="showcase/gui/gui_heatmap.png" width="100%"><br>
      <sub>Batch heatmap — three datasets as one 2θ × sample intensity map</sub>
    </td>
  </tr>
</table>

## What it does

- **2D → 1D integration** — full 0°–360° azimuthal integration into a standard two-column powder pattern (2θ(deg), intensity), ready for peak finding, profile fitting and PDF analysis.
- **Geometric calibration on a LaB₆ standard** — pyFAI `GeometryRefinement` against NIST SRM 660 (a = 4.156 Å), starting from an FFT cross-correlation estimate of the direct-beam position (accuracy < 1 px). Example result: distance refined to 1595.80 mm (`lmfp1_lab6`). Every run reports **engine metrics the fit residual cannot**: median ring-position deviation, how many of the 16 rings came out complete, and the dispersion of the lattice constant back-solved ring by ring — a run can report a 0.0000° residual while the rings sit 10 px off the real ones.
- **Automatic 2θ range** — `--range auto` (default) takes the lower bound from the material's standard and finds the upper bound where the data starts failing, judged by *relative arc coverage* (the ring's fraction of azimuth inside the detector falling below 50 % of its own maximum ≈ 7.9° for a centred beam) rather than a fixed threshold, so one criterion adapts to any beam placement.
- **Sector-wise waterfall** — N azimuthal sectors (default 36) integrated separately; the stacked raw-intensity plot reveals preferred orientation and large-grain spotiness, and visualises the detector-clipping geometry.
- **Off-centre (partial-ring) beams** — datasets recorded with the beam at the detector edge or corner are handled end to end, including a built-in numpy integration that takes over where pyFAI's radial binning becomes unreliable (verified on synthetic rings).
- **Background subtraction** — empty-scan subtraction (with an exposure/beam-current factor), a rolling-window auto baseline, and manual anchors, all previewed live with the raw curve (dashed) and baseline (dotted) drawn over the result. Negative values are kept by default — clipping the noise floor at 0 biases the mean up by ≈ 1σ. SNIP was implemented and measured, then deliberately left out of the GUI: it over-subtracts by 43–98 % on these patterns.
- **Batches and staged products** — import a folder, integrate the batch (24 panels at a time, the rest integrated and stored anyway), and compare or heat-map it in one figure. Integrated curves and background-subtracted curves are cached per stage under `outputs/_stage/` — keyed by file fingerprint + geometry + settings, reusable across sessions — and show up as **groups in the file bar**, so a background-subtracted batch goes to a comparison plot in two clicks instead of re-integrating.
- **Desktop app** — MDI plot panels with pop-out and tiling, a live parameter dock, hover data readout, box zoom, per-curve styling, and PNG/TIF export:

  ```bash
  python -m xrd_toolkit.gui
  ```

## Install

```bash
conda env create -f environment.yml    # once
conda activate XRD_Toolkit_Environment
pip install -e .                       # numpy / scipy / matplotlib / fabio / pyFAI come along
```

> Sample data are not in the repository — point `--file` at your own diffraction images.

```bash
# calibrate + integrate one image (prints a config entry to paste into config.py)
python scripts/calibrate_integrate.py --file data/xxx.tif --wavelength 0.1223 --pixel 200 --dist0 1600
# integrate with an existing geometry, or run the sector/waterfall analysis
python scripts/integrate_pattern.py  --file data/xxx.tif
python scripts/sector_waterfall.py   --file data/xxx.tif --n-sectors 36
```

Typical workflow: `view_diffraction` (inspect) → `calibrate_integrate` (geometry) → `integrate_pattern` (1D pattern) → `sector_waterfall` (uniformity). Outputs land in `outputs/{dataset}/`. Omit `--file` and each script shows an interactive menu of everything in `data/`; the three consuming scripts take `--config NAME` (default `lmfp1_lab6`) to pick a calibrated geometry. **Full flag list: [docs/NOTES.md](docs/NOTES.md#cli-flags).**

| Script | What it does |
|---|---|
| `scripts/view_diffraction.py` | 2D image viewer (log scale, line profile, calibrated beam-centre mark) |
| `scripts/calibrate_integrate.py` | LaB₆ geometric calibration (pyFAI) + azimuthal integration (2D → 1D) |
| `scripts/integrate_pattern.py` | Full-angle integration: 2D image → standard 1D powder pattern |
| `scripts/sector_waterfall.py` | Sector integration (36 sectors) + waterfall plot + uniformity statistics |
| `scripts/check_env.py` | Environment self-check — run this first when something "just won't run" |
| `scripts/check_gui.py` | GUI link check — drives the interface with real data and real canvas events |
| `scripts/show_gui.py` | Real-window peek — opens the app, walks three steps, leaves the window up |
| `scripts/run_tests.py` | The full unit suite behind a watchdog |
| `scripts/stress_panels.py` | Panel-stress probe — a regression check for two offscreen deadlocks |

## GUI

- **Five toolbar entrances** — `[校准] [1D] [扣背景] [对比] │ [绘图]`: each one turns the parameter dock to that stage's page, which carries only that stage's controls and its own produce button. **The window opens with nothing selected and the parameter dock hidden** — an entrance shows its page when you pick it.
- **One-click views** — [2D] [Profile] [1D] [Waterfall] plot every checked file at once, straight from the engine. [Compare] overlays several curves with four normalization modes; [Heatmap] assembles the batch into one 2θ × sample map, and clicking a heatmap row toggles that sample's curve in an open Compare panel.
- **File bar** — a tree: the raw files at the top, then one group per stage product (1D 产物, and one group per batch background run, named with its time and recipe). Checking a group checks everything in it, so a batch of background-subtracted curves goes to a comparison plot in two clicks. Right-clicking a product (or a group) deletes it — every product can be removed from the file bar. Imports start unchecked — select with [全选] / [全不选] / [按条件选…] (range, stride, name filter, with a live count).
- **Live parameter dock** — data and display parameters per panel, tooltips throughout, per-panel snapshots; zoom and pan write the view range back in real time.
- **Panel chrome and gestures** — one self-drawn row per panel (Home / magnifier / Customize / save, plus pop-out and close), left-drag to pan, box zoom when the magnifier is lit, hover readout in the status bar, resize grips on every edge.
- **Calibration workbench** — a three-column table (current geometry + slots A / B) over auto and manual calibration; every result accumulates under its own name and the engine metrics decide which one wins. Details, including what the metrics can *not* catch, are in [docs/NOTES.md](docs/NOTES.md#calibration).
- **Save / load .poni** — export the current geometry as a standard pyFAI `.poni` file, or import one as a named config entry that survives restarts and is usable from the CLI via `--config`.

## Tests

```bash
python scripts/run_tests.py     # 528 tests, behind a watchdog
```

Synthetic-image unit tests (no sample data needed, plain `unittest`): arc-coverage failure criterion across beam placements, geometric failure-point values, the off-centre integration fallback, background estimators characterised against known synthetic backgrounds, and the GUI wiring — including live preview, per-file anchors and the raw/baseline overlay lines.

`scripts/stress_panels.py` is the regression probe for two offscreen deadlocks (both fixed: the panel toolbar is ours, and background tasks run on long-lived worker threads); `scripts/run_tests.py` dumps every thread's Python stack and exits non-zero if a run ever stalls instead of hanging silently.

## Roadmap

- Peak finding & profile fitting on the 1D patterns (background subtraction landed first — net peak heights are only meaningful once the pedestal is gone)
- Line-profile analysis: Scherrer / Williamson–Hall size–strain
- Structure-factor simulation
- A basic Rietveld refinement engine
- Machine learning: clustering for phase identification and CNN peak-shape classification

## Project structure

```
.
├── data/                          # raw XRD images (.tif), not tracked by git
├── outputs/                       # script outputs + product cache (local, not tracked)
├── showcase/                      # curated example figures used in this README (tracked)
├── scripts/                       # the four analysis scripts + five self-check tools
├── src/xrd_toolkit/
│   ├── config.py                  # named geometry configs (one per batch; --config selects)
│   ├── cli.py                     # shared interactive file-selection menu
│   ├── core/processor.py          # image processing (line profiles, ring-centre localization)
│   ├── services/                  # data_loader · integrator · background · range_selector · ring_metrics · stage_cache
│   └── gui/                       # PySide6 desktop app (python -m xrd_toolkit.gui)
├── tests/                         # synthetic-image unit tests + GUI integration tests
├── docs/                          # Chinese README, long-form notes
├── environment.yml                # conda environment (one-command setup)
└── pyproject.toml                 # package metadata & dependencies
```

## Author

Chenze Bian — MSc in Physics with Data Modelling and Quantum Technologies, City University of Hong Kong · [GitHub](https://github.com/xyyzzc3)

## License

[MIT](LICENSE)
