<p align="center">
  <img src="Logo.png" alt="ScanFlow" width="220"/>
</p>

# ScanFlow

Automated bias / current sweep companion for the CreaTec STM.

ScanFlow runs **alongside** the CreaTec STMAFM software (it does not replace
it). The native software handles live monitoring, image display, and
manual control; ScanFlow handles the things STMAFM doesn't help with —
unattended bias or current sweeps, drift-corrected overnight runs, and a
hard tip-crash safety abort.

## What it does

- **Bias ramp** at constant tunneling current — sweep `V_start → V_end` in steps of e.g. 10 mV.
- **Current ramp** at constant bias — sweep `I_start → I_end` in pA steps.
- **Drift correction** between scans via phase cross-correlation.
- **Tip-crash safety**: aborts the run and retracts the tip the moment `|I|` exceeds a configurable threshold (default 1 nA).
- **Time estimate** shown before every run.
- **Mock STM** for offline testing.

Two ways to use it:

| Mode | When |
|---|---|
| **GUI** — two-tab window (Sweep / Log) | Interactive setup, click Start |
| **CLI** — `python -m scanflow ...` | Overnight scripts, reproducible runs |

## Installation

```bash
pip install -e ".[createc]"     # Windows with CreaTec COM
pip install -e .                # offline / development
```

## Usage

### GUI

```bash
python -m scanflow
```

Two tabs:
- **Sweep** — scan frame, sweep range, drift + safety toggles, Start/Pause/Stop.
- **Log** — running events and errors.

### CLI

```bash
# Bias ramp from -1.0 V to +1.0 V in 10 mV steps, at 50 pA
python -m scanflow bias --start -1.0 --end 1.0 --step 0.01 --setpoint 50

# Current ramp from 10 pA to 100 pA in 5 pA steps, at 0.1 V
python -m scanflow current --start 10 --end 100 --step 5 --bias 0.1

# Estimate time without running
python -m scanflow estimate bias --start -1.0 --end 1.0 --step 0.01

# Run a saved recipe
python -m scanflow run overnight.yaml
```

Common flags:

| Flag | Meaning | Default |
|---|---|---|
| `--size` | Scan side length (nm) | 50 |
| `--speed` | Scan speed (nm/s) | 50 |
| `--pixels` | Resolution per side | 256 |
| `--safety-nA` | Tip-crash threshold | 1.0 |
| `--no-drift` | Disable drift correction | drift on |
| `--no-safety` | Disable safety abort (not recommended) | safety on |
| `--save-folder` | Output directory | STMAFM default |
| `--mock` | Use mock STM (offline) | live |
| `--yes` / `-y` | Skip confirmation | prompt |

Every command prints the plan and estimated total time before starting:

```
=== ScanFlow plan: Bias ramp -1.00–1.00 V ===
  Mode      : Bias ramp -1.000 → 1.000 V  step 10.0 mV  @ 50.00 pA
  Scans     : 201
  Frame     : 50.0 × 50.0 nm, 256 × 256 px @ 50.0 nm/s
  Per scan  : ≈ 8 min 36 s
  Drift     : on   (adds ~50% alignment time)
  Safety    : on, threshold 1.000 nA
  Estimated total time: 43 h 12 min

Proceed? [y/N]
```

## Architecture

```
scanflow/
├── __main__.py            # entry point: GUI if no args, else CLI
├── cli.py                 # argparse-driven sweeps
├── core/                  # CreaTec COM facade (setp/getp API)
│   ├── stm_client.py      #   STMClient — connect, sub-controllers
│   ├── scan.py            #   ScanController — params, start/stop/save
│   ├── feedback.py        #   FeedbackController — bias, setpoint, ramps
│   ├── safety.py          #   SafetyMonitor — current-threshold abort
│   ├── mock_dispatch.py   #   Mock STM for offline development
│   └── (others used by Python API: coarse, lockin, spectroscopy, afm, …)
├── drift/
│   └── detector.py        # Phase cross-correlation
├── automation/
│   ├── recipe.py          # MeasurementRecipe (bias_ramp, current_ramp, …)
│   └── runner.py          # QThread runner with safety hooks
├── gui/
│   ├── main_window.py     # Two tabs: Sweep + Log
│   └── panels/
│       ├── sweep_panel.py
│       └── log_panel.py
└── io/
    └── session.py
```

## Tests

```bash
pip install -e ".[dev]"
pytest -v
```

25 tests covering recipes, drift detection, mock STM, and the safety monitor.

## License

MIT — see [LICENSE](LICENSE).
