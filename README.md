<p align="center">
  <img src="Logo.png" alt="ScanFlow" width="220"/>
</p>

# ScanFlow

Semi-automated STM/AFM control and drift-corrected measurement portal for CreaTec instruments.

ScanFlow is a desktop companion to the CreaTec STMAFM software — it connects
over the manufacturer's COM interface and exposes everything you usually do by
hand (approach, bias/current control, scanning, spectroscopy) plus the things
the standard UI doesn't help with: drift correction, recipe-driven overnight
runs, and unattended parameter sweeps.

## What it does

| Capability | Where |
|---|---|
| Manual scan controls in physical units (V, A, nm, nm/s) | **Scan Control** tab |
| Coarse approach, Z-limit, XYZ slider stepper motion | **Coarse / Approach** tab |
| Lock-in configuration + I/V point spectroscopy | **Spectroscopy** tab |
| Recipe-driven automation (overnight, bias ramp, current ramp) | **Automation** tab |
| Drift detection via phase cross-correlation + tip recentring | runs during automation; live chart in **Drift Monitor** |
| Live cryo temperature readout | status bar |
| Timestamped session log | **Log** tab |

## Architecture

```
scanflow/
├── core/                 # CreaTec COM facade with namespaced sub-controllers
│   ├── stm_client.py     #   STMClient — setp/getp wrapper, holds sub-clients
│   ├── scan.py           #   ScanController — params, channels, start/stop/save
│   ├── feedback.py       #   FeedbackController — bias, setpoint, preamp, ramps
│   ├── coarse.py         #   CoarseController — approach, Z-limit, slider
│   ├── lockin.py         #   LockInController — internal lock-in
│   ├── spectroscopy.py   #   SpectroscopyController — VERTMAN I/V, dI/dV
│   ├── afm.py            #   AFMController — PLL/qPlus tuning, FM-AFM mode
│   ├── tipform.py        #   TipFormController — voltage-pulse conditioning
│   └── temperature.py    #   TemperatureMonitor — cryo readouts
├── drift/
│   └── detector.py       # Phase cross-correlation + continuous-drift model
├── automation/
│   ├── recipe.py         # YAML-serialisable MeasurementRecipe + ScanStep
│   └── runner.py         # QThread runner; emits scan/drift/error signals
├── gui/
│   ├── main_window.py    # Tabbed PySide6 window
│   ├── panels/           # ControlPanel, CoarsePanel, SpectroscopyPanel, …
│   └── widgets/          # TemperatureWidget (status bar)
└── io/
    └── session.py        # Per-user session persistence (~/.scanflow_session.json)
```

The `STMClient` exposes the new manufacturer `setp`/`getp` API (STMAFM 2020+)
with SI units throughout. Sub-controllers map onto the natural namespaces in
the CreaTec key tree (`SCAN.*`, `LOCK-IN.*`, `AFM.*`, `HVAMPCOARSE.*`, etc.).

## Requirements

- Python 3.10+
- Windows + CreaTec STMAFM software running (for live STM control)
- The `pywin32` package (Windows only — guarded behind the `[createc]` extra)

Without those, the GUI still launches and every panel works in offline mode
— STM calls raise `STMNotConnectedError` cleanly.

## Installation

```bash
cd ScanFlow
pip install -e .                # offline / development
pip install -e ".[createc]"     # Windows with the CreaTec COM bridge
pip install -e ".[dev]"         # to run the test suite
```

## Launch

```bash
scanflow
```

## Python API example

```python
from scanflow.core import STMClient, ScanParams

stm = STMClient()
stm.connect()

# Apply a complete scan setup
stm.scan.apply(ScanParams(
    bias_V=0.1, setpoint_A=100e-12,
    size_nm=(50.0, 50.0), speed_nm_s=50.0,
    pixels=(256, 256),
    channels=("TOPOGRAPHY", "CURRENT", "Lock-in X"),
))

# Configure the lock-in and run an I/V at pixel (128, 128)
stm.lockin.configure(freq_Hz=652.7, amplitude_mVpp=20.0)
stm.spec.configure(IVTable(bias_start_V=-0.7, bias_end_V=0.7, points=1024))
stm.spec.single_at_pixel(128, 128)
stm.spec.save_vert("spectrum.VERT")
```

## Recipe example

```python
from scanflow.automation import MeasurementRecipe

# Repeat a single 256×256 scan 100 times overnight with drift correction
r = MeasurementRecipe.overnight(
    bias_V=0.1, setpoint_A=50e-12, repetitions=100,
    size_nm=(50.0, 50.0), speed_nm_s=50.0, pixels=(256, 256),
    drift_correction=True,
)
r.save("overnight.yaml")
```

## Roadmap

See [`ROADMAP.md`](ROADMAP.md) for the next phases (live scan viewer, AFM/PLL
panel, grid spectroscopy, sample-map tracking, mock STM for offline development).
