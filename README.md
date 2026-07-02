# Mobile DCFC Sizing — XOS Hub + Kempower

Analysis and sizing tools for two **mobile DC Fast Charger** technologies evaluated for Caltrans EV fleet maintenance stations. Unlike fixed chargers (see the companion repo), both systems avoid utility-side infrastructure — no trenching, no service-entrance upgrade from the utility.

| Technology | Type | Grid required at site? | Sizing method |
|---|---|---|---|
| XOS Hub MC02 | Mobile battery trailer | **Yes — building-side only** | Time-series SoC simulation |
| Kempower DGS | Portable grid-connected DCFC | **Yes — building-side only** | Exact MILP |

**Infrastructure scope for both technologies:** Costs include everything **after the utility meter** — panel/switchboard upgrades, breakers, conduit, wiring, and permits inside the maintenance building. Costs **do not** include anything before the meter (utility transformer upgrades, service-entrance work by the utility, or trenching from the utility connection point).

---

## Sites Covered

Four Caltrans maintenance stations, plus one Glendale sensitivity run under SMUD proxy pricing:

| Site key | Location | Utility |
|----------|----------|---------|
| `northgate` | Sacramento, CA | SMUD |
| `fresno` | Fresno, CA | PG&E BEV-2 |
| `glendale` | Glendale, CA | PG&E BEV-2 *(proxy)* |
| `glendale_smud` | Glendale, CA | SMUD *(sensitivity proxy)* |
| `san_diego` | San Diego, CA | SDG&E EV-HP |

> **Glendale proxy:** Glendale Water & Power's actual tariff (Schedule LD-2/PC-1) was not available. PG&E BEV-2 is the primary proxy. `glendale_smud` is a separate sensitivity run using SMUD rates on the same vehicle events — it lets you bound the range of possible costs for Glendale.

---

## Technology Overview

### XOS Hub MC02

The XOS Hub MC02 is a **trailer-mounted battery system** with an onboard 282 kWh LFP battery pack and four CCS1 charging ports.

Key specs (from XOS User Manual, Section 5):
- Battery capacity: 282 kWh nominal; 225.6 kWh usable (SoC ≥ 20%)
- Charging ports: 4 × CCS1, up to 80 kW per port simultaneously
- Max battery-to-vehicle output: 150 kW continuous
- Grid input: 480 V 3-phase, 100 A (~83 kW at unity PF)
- Battery chemistry: LFP (3,000 cycles at 70% DoD, 10-year life)

**Deployment model (grid-connected at site):** Each XOS unit is permanently stationed at the maintenance site and recharges its battery from the **local site grid** between vehicle visits. The site provides a 480 V 3-phase, 100 A dedicated circuit per unit — all building-side electrical work (panel upgrade, breakers, conduit, wiring) is included in the cost model. Utility-side work (before the meter) is not included.

**Sizing rule:** Add XOS units one at a time until all vehicles are served on the target day (greedy add-one-until-covered). No MILP. The minimum unit count that covers the worst day of the year determines the fleet recommendation; 90th-percentile cost across the full year is used for planning cost estimates.

### Kempower DGS

Kempower chargers are **traditional grid-connected DCFC units** sourced from California DGS Contract 1-23-61-15A (National Car Charging LLC). Three power levels are modeled:

| Type | Power | DGS Group | Hardware price | Install | O&M/yr |
|---|---|---|---|---|---|
| `Kempower_50kW` | 50 kW | Group 5 (portable) | $23,408 | $855 | $1,573 |
| `Kempower_150kW` | 150 kW | Group 6 (cabinet) | $62,154 | $4,750 | $1,573 |
| `Kempower_250kW` | 250 kW | Group 7 (cabinet) | $101,946 | $5,225 | $1,573 |

No L2 chargers — all vehicles served by Kempower in this analysis are DC-compatible.

**Sizing method:** Exact MILP (same formulation as the fixed-charger repo), with Kempower charger parameters substituted via `kempower_milp_sizing.py`.

---

## Prerequisites

### Python
Python 3.11 or later:
```bash
pip install -r requirements.txt
```

### Gurobi (required for Kempower MILP only)
XOS simulation does not need Gurobi. Kempower sizing does.

1. Download from [gurobi.com/downloads](https://www.gurobi.com/downloads/)
2. Get a free academic license at [gurobi.com/academia](https://www.gurobi.com/academia/academic-program-and-licenses/)
3. Activate:
   ```bash
   grbgetkey <your-license-key>
   ```

### Shared dependency — MILP solver module
`kempower_milp_sizing.py` imports the core MILP solver from the companion fixed-charger repo. Both repos must be cloned side-by-side:
```
D:\Geotab_EV_Parameters\
    charger_sizing_test\               ← fixed-charger repo (clone as this name)
    mobile-dcfc-task-4482\             ← this repo
```
If you clone to a different path, update the `sys.path.insert` at the top of `kempower_milp_sizing.py` and `run_kempower_pipeline.py`.

### Input data
Per-day charging event CSV files named:
```
z2z_milp_events_{site}_{YYYY_MM_DD}.csv
```
These live in the `charger_sizing_test/` directory (see the fixed-charger repo README for how to generate them from Geotab Zone-to-Zone exports).

---

## Repository Structure

```
mobile-dcfc-task-4482/
│
├── ── XOS HUB MC02 ──────────────────────────────────────────────────────────
│
├── run_xos_only.py                  ← START HERE for XOS. Runs the SoC
│                                       simulation for the top-5 worst days
│                                       at all sites.
│
├── xos_hub_soc_simulation.py        Core SoC simulation module. Tracks
│                                    battery state each 15-min step; adds
│                                    units one at a time until all vehicles
│                                    are served. Grid-connected mode.
│
├── xos_trip_simulation.py           Trip-level SoC simulation (individual
│                                    vehicle dispatch within one XOS unit).
│
├── xos_grid_charging_analysis.py    Grid charging profile analysis for XOS
│                                    units — power draw curves and cost.
│
├── xos_combined_figure.py           Combined multi-panel figure (XOS results
│                                    across all sites).
│
├── xos_per_unit_plots.py            Per-unit cost and performance charts.
│
├── charger_costs_xos_hub.py         XOS Hub cost model: unit price $245,437.50,
│                                    tiered infrastructure costs, 10-yr life.
│
├── ── KEMPOWER DGS ──────────────────────────────────────────────────────────
│
├── run_kempower_pipeline.py         ← START HERE for Kempower. Runs the MILP
│                                       sizing for all days at all sites and
│                                       generates Gantt / power-profile figures.
│
├── kempower_milp_sizing.py          Kempower adapter for the MILP solver.
│                                    Wraps exact_northgate_charger_sizing_milp.py
│                                    with Kempower specs (50/150/250 kW, DGS costs).
│                                    No code duplication — all MILP logic stays
│                                    in the companion repo.
│
├── charger_costs_kempower_dgs.py    Kempower cost model: DGS Contract prices,
│                                    Group 5/6/7 install costs, 8-yr life.
│
├── ── FIGURE GENERATION ─────────────────────────────────────────────────────
│
├── build_presentation_figures.py    Multi-site comparison figures (P01–P11):
│                                    coverage curves, grid demand profiles,
│                                    monthly heat maps, worst-day dispatch.
│                                    Output → appendix_a_figures/presentation_style/
│
├── build_glendale_proxy_figures.py  Glendale PG&E vs SMUD proxy comparison
│                                    figures (P11–P15): cost distributions,
│                                    charger mix, and cross-technology summary.
│
├── plot_xos_example_day.py          Single-day schedule and vehicle summary
│                                    figures for XOS and Kempower. Accepts
│                                    --site and --date arguments.
│
├── ── ONE-OFF / REPRICING SCRIPTS ───────────────────────────────────────────
│
├── _reprice_glendale_xos_pge.py     Reprice Glendale XOS results under
│                                    PG&E BEV-2 rate.
│
├── _rerun_kempower_fresno_glendale.py   Re-run Kempower MILP for Fresno and
│                                        Glendale with corrected utility rates.
│
├── _rerun_kempower_glendale_pge.py   Glendale-only Kempower rerun (PG&E proxy).
│
├── _run_glendale_smud.py            Glendale SMUD sensitivity run.
│
├── _test_kempower_one_day.py        Single-day Kempower MILP test script.
│
├── ── OUTPUTS ───────────────────────────────────────────────────────────────
│
├── xos_outputs/
│   ├── {site}_all_days_xos.csv      One row per operating day — units required,
│   │                                service rate, energy delivered, daily cost
│   └── {site}_worst10_schedule.csv  Per-vehicle schedule for the 10 worst days
│
├── appendix_a_figures/
│   ├── xos_{site}_breakdown.png     XOS stacked cost breakdown per site
│   ├── xos_{site}_daily.png         XOS daily cost over the analysis year
│   ├── xos_xsite_summary.png        Cross-site XOS comparison
│   ├── kmp_{site}_breakdown.png     Kempower cost breakdown per site
│   ├── kmp_{site}_daily.png         Kempower daily cost per site
│   └── presentation_style/
│       ├── P01_xos_coverage_curves.png        XOS service coverage vs. units deployed
│       ├── P02_xos_grid_profile_4panel.png    Grid power demand (4-site panel)
│       ├── P03_xos_monthly_k_heatmap.png      Monthly unit-count heat map
│       ├── P04_xos_worst_days_4panel.png      Worst-day dispatch (4-site panel)
│       ├── P05_xos_coverage_summary.png       Coverage fraction at each K level
│       ├── P06_kmp_charger_mix_{site}.png     Kempower charger mix by site
│       ├── P07_kmp_service_energy_{site}.png  Service rate and energy delivery
│       ├── P08_kmp_cost_power_{site}.png      Cost vs. peak power
│       ├── P09_kmp_vs_xos_scatter_{site}.png  Head-to-head cost scatter
│       ├── P10_kmp_worst_days_{site}.png      Kempower worst-day dispatch
│       ├── P11_kmp_glendale_proxy_comparison.png  Glendale Kempower PG&E vs SMUD
│       ├── P12_xos_glendale_proxy_comparison.png  Glendale XOS PG&E vs SMUD
│       ├── P13_fixed_glendale_proxy_comparison.png  Glendale fixed charger PG&E vs SMUD
│       ├── P14_glendale_cross_technology_summary.png  All-technology cost summary
│       ├── P15_glendale_summary_table.png     Color-coded cost summary table
│       └── figure_captions.txt                Figure captions reference file
│
└── ── CONFIGURATION ─────────────────────────────────────────────────────────
    └── LICENSE
```

---

## Running the XOS Hub Simulation

```bash
python run_xos_only.py
```

This runs the SoC time-series simulation for the **top-5 worst-demand days** at each site and prints a service summary (vehicles served, energy delivered, units required). Results are written to `xos_outputs/{site}_all_days_xos.csv` for the full year once the full-year run is complete.

To run a single custom day, use `xos_hub_soc_simulation.py` directly:

```python
import xos_hub_soc_simulation as xos
import pandas as pd
events_df = pd.read_csv("z2z_milp_events_northgate_2025_06_09.csv")
result = xos.simulate_day(events_df, site="northgate", max_units=10)
print(result["units_required"], result["service_rate_pct"])
```

To generate a single-day schedule figure for any site:

```bash
python plot_xos_example_day.py --site northgate --date 2025_06_09
python plot_xos_example_day.py --site glendale_smud --date 2026_03_09
```

Output figures go to `xos_outputs/` (XOS schedule and vehicle-summary PNGs) alongside Kempower counterpart figures for the same day.

### How the XOS simulation works

1. Load the day's charging event CSV (arrival time, departure time, energy needed).
2. Set `K = 1` XOS unit and simulate all 15-minute timesteps:
   - If any port on the unit is dispensing to a vehicle → grid charging is paused.
   - If all ports are idle → grid charges the battery at up to 83 kW.
   - Vehicle is assigned to a port on a first-come-first-served basis.
   - Battery SoC is tracked; unit will not discharge below 20%.
3. If any vehicle goes unserved, increment `K += 1` and repeat.
4. Report the minimum `K` that achieves 100% service, plus costs.

---

## Running the Kempower MILP

```bash
python run_kempower_pipeline.py               # all sites
python run_kempower_pipeline.py northgate     # single site
```

This runs the exact MILP optimizer for every operating day at the specified site(s), then generates per-day Gantt charts and power-demand figures.

### Outputs per day (in `per_day/{date}/kempower/`)

| File | Contents |
|------|----------|
| `exact_milp_selected_charger_mix.csv` | Optimal count of each Kempower charger type |
| `exact_milp_event_results.csv` | Per-vehicle service status (full/partial/unserved) |
| `exact_milp_charging_schedule.csv` | Vehicle-by-timestep charging schedule |
| `exact_milp_site_power_profile.csv` | Aggregate site power draw each timestep |
| `exact_milp_cost_breakdown.csv` | Itemized daily cost (CapEx, energy, demand) |

### Site summary outputs

| File | Contents |
|------|----------|
| `{site}_analysis/kempower_summary.csv` | One row per day — config, cost, service rate |
| `{site}_analysis/kempower_report.txt` | Human-readable summary |

### How the Kempower MILP works

`kempower_milp_sizing.py` is a thin adapter: it substitutes Kempower DGS charger specs (50/150/250 kW, DGS contract costs) into the exact same MILP formulation used by the fixed-charger repo. The math is identical — only the charger cost and power parameters change.

**Decision variables**

| Variable | Type | Meaning |
|---|---|---|
| `N_c` | Integer ≥ 0 | Number of Kempower chargers of type `c` to install |
| `u[v,t,c]` | Binary | 1 if vehicle `v` uses charger type `c` at timestep `t` |
| `P_total[t]` | Continuous | Total site power draw at timestep `t` |
| `P_max`, `P_peak_win` | Continuous | Global and peak-window peak demand (kW) |

**Objective** — minimize daily cost:
```
min:  Σ_c [N_c × C_daily_c]          ← Kempower CapEx (DGS prices, 8-yr life)
    + P_max × c_demand_global         ← site demand charge ($/kW)
    + P_peak_win × c_demand_peak_win  ← peak-window demand charge (SMUD sites)
    + Σ_t [P_total[t] × rate(t) × dt] ← energy at site TOU rate
```

No L2 chargers — vehicles whose DC max charge rate is 0 are excluded from the Kempower analysis.

---

## Running the Glendale Proxy Comparison

The `glendale_smud` sensitivity run uses the **same vehicle events** as the primary Glendale run but prices them under SMUD commercial rates instead of PG&E BEV-2. This bounds the cost uncertainty for Glendale, where the actual utility rate is unknown.

**Step 1 — Run all three charger technologies for both Glendale proxies:**
```bash
# Kempower — primary Glendale (PG&E proxy)
python run_kempower_pipeline.py glendale

# Kempower — SMUD sensitivity
python _run_glendale_smud.py

# XOS — both proxies are included in run_xos_only.py
python run_xos_only.py
```

The fixed-charger pipeline (in the companion repo) must also have been run for both `glendale` and `glendale_smud` sites.

**Step 2 — Generate comparison figures:**
```bash
python build_glendale_proxy_figures.py
```

Output: 5 figures (P11–P15) written to `appendix_a_figures/presentation_style/`, comparing PG&E vs SMUD proxy costs across all three charger technologies.

---

## Generating Multi-Site Summary Figures

```bash
python build_presentation_figures.py
```

Outputs P01–P11 to `appendix_a_figures/presentation_style/`. Figures include:
- **P01** — XOS coverage curves (% of days fully served vs. number of units deployed)
- **P02** — Grid power demand profiles (4-site panel, one month sample)
- **P03** — Monthly unit-count heat map (how many XOS units each calendar month needs)
- **P04** — Top-10 worst days per site — vehicle count and coverage breakdown
- **P05** — Coverage fraction at fixed K (fraction of all days covered at each unit count)
- **P06–P08** — Kempower charger mix, service rate, and cost vs. peak power per site
- **P09** — XOS vs. Kempower daily cost scatter (head-to-head)
- **P10** — Kempower worst-day dispatch timelines per site

---

## Updating Cost Assumptions

### XOS Hub costs
Edit `charger_costs_xos_hub.py`. Key parameters:

| Parameter | Current value | Source |
|---|---|---|
| Unit price | $245,437.50 | Caltrans informal quote |
| O&M | $6,000/yr | Assumed (no published data) |
| Life | 10 years | XOS User Manual, Section 5 |
| Shared infra (one-time) | tiered — see `electrical_infra_cost()` | Panel/switchboard upgrade, engineering, permits — **after meter only** |
| Per-unit circuit cost | tiered — see `electrical_infra_cost()` | 480V/100A breaker + ~50 ft conduit + #2 AWG wire + 480V outlet + labor — **after meter only** |

> **Infrastructure scope:** `electrical_infra_cost()` covers building-side electrical only. It does **not** include utility transformer upgrades, service-entrance work, or any work the utility performs before the meter.

> **Note:** The O&M figure ($6,000/yr) is an estimate. Update it once XOS provides actual service contract pricing.

### Kempower DGS costs
Edit `charger_costs_kempower_dgs.py`. Hardware prices come from DGS Contract 1-23-61-15A Attachment A. The 1% discount (orders > $100K) and 2% discount (orders > $500K) are not applied — prices are pre-discount list.

---

## Adding a New Site

1. Make sure per-day event CSVs exist in the `charger_sizing_test/` folder with the naming pattern `z2z_milp_events_{newsite}_{YYYY_MM_DD}.csv`.
2. **For XOS:** Add the site's CSV paths to the `SITE_TOP5` dict in `run_xos_only.py`. Add an entry to `SITE_META` in `plot_xos_example_day.py`.
3. **For Kempower:** Add the site tuple to the `SITES` list in `run_kempower_pipeline.py` and add the utility mapping in `kempower_milp_sizing.py` (it imports `utility_rates.py` from the companion repo).

---

## Relationship to the Fixed-Charger Repo

This repo and `fixed-charger-sizing-optimization` share some code:

| Shared resource | Location | Notes |
|---|---|---|
| MILP solver | `charger_sizing_test/exact_northgate_charger_sizing_milp.py` | Kempower imports it directly |
| Utility rates | `charger_sizing_test/utility_rates.py` | Same TOU rate functions |
| Input event CSVs | `charger_sizing_test/z2z_milp_events_*.csv` | Both repos read from same files |

The XOS simulation and Kempower MILP are independent — you can run either without the other. But the Kempower MILP cannot run without the companion repo cloned at the expected path.

---

## Troubleshooting

**`ModuleNotFoundError: exact_northgate_charger_sizing_milp`**
The Kempower pipeline imports the MILP module from the companion repo. Check that `charger_sizing_test/` is cloned at `D:\Geotab_EV_Parameters\charger_sizing_test\` (or update the path in `kempower_milp_sizing.py`).

**`GurobiError: No Gurobi license found`**
Run `grbgetkey <your-key>`. Only needed for Kempower; XOS runs without Gurobi.

**XOS: all vehicles unserved on a day**
Check that the event CSV has non-zero `energy_needed_kwh` and valid `arrival_time` / `departure_time` values. If dwell windows are very short (< 30 min), the SoC simulation may not be able to deliver enough energy even with unlimited units.

**Kempower: `no_solution` for a day**
Solver hit the 60-second time limit. Increase `GUROBI_TIME_LIMIT` in `run_kempower_pipeline.py`, or check if the day has an unusually large number of vehicles with overlapping dwell windows.

**Glendale results use PG&E BEV-2 proxy**
Glendale Water & Power's actual tariff was not available. PG&E BEV-2 is the primary proxy. Contact GWP Customer Service at 855-550-4497 for the actual Schedule LD-2/PC-1 tariff. The `glendale_smud` run provides a SMUD-based sensitivity bound.

---

## File Naming Conventions

| Pattern | Meaning |
|---|---|
| `xos_*.py` | XOS Hub simulation and analysis scripts |
| `kempower_*.py` / `charger_costs_kempower_*.py` | Kempower analysis and cost model |
| `charger_costs_xos_hub.py` | XOS cost model |
| `run_*.py` | Top-level pipeline runners (start here) |
| `build_*.py` / `plot_*.py` | Figure generation scripts |
| `_*.py` | One-off or test scripts (underscore prefix = not main workflow) |
| `appendix_a_figures/` | Generated figures (tracked in git) |
| `xos_outputs/` | XOS simulation outputs — daily summaries, worst-10 schedules |

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `pandas` | ≥ 1.5 | Data wrangling and CSV/Excel I/O |
| `numpy` | ≥ 1.23 | Numerical operations |
| `matplotlib` | ≥ 3.6 | Figures and Gantt charts |
| `openpyxl` | ≥ 3.0 | Excel output |
| `gurobipy` | ≥ 10.0 | Kempower MILP solver (requires Gurobi license) |
| `pytz` | latest | Timezone conversion (UTC → America/Los_Angeles) |

---

## License

MIT License — see [LICENSE](LICENSE) for details.
