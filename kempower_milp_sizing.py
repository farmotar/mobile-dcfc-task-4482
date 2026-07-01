"""
kempower_milp_sizing.py
========================
Kempower-only DCFC sizing adapter.

Wraps the existing exact_northgate_charger_sizing_milp.py MILP optimizer
with Kempower DGS-contract charger specs (50 kW / 150 kW / 250 kW DC).
No code duplication — all MILP logic stays in the original file.

Kempower scenario assumptions (meeting Jun 16 2026):
  - No battery / no energy storage — chargers are grid-connected only.
  - 3 charger types: 50 kW, 150 kW, 250 kW (DC, from CA DGS Contract 1-23-61-15A).
  - No L2 chargers (all vehicles served by Kempower are DC-compatible).
  - No trenching — electrical upgrade only (240 V service, ~$10k–$20k per unit).
  - Permit: ~$2,000 per unit.
  - Maintenance: $1,573/charger-year (ChargerHelp! DGS rate, all groups).
  - The MILP objective and all constraints are unchanged — only the charger
    cost / power parameters change.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KEMPOWER MILP FORMULATION (for quarterly report)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The Kempower-only scenario reuses the exact MILP formulation from
exact_northgate_charger_sizing_milp.py with Kempower charger parameters
substituted.  A brief summary is given here; see the main MILP file for
the full formulation.

Sets
----
  V   : vehicle charging events
  T   : 15-minute (or 5-minute) discrete time steps
  C   : charger types  = { Kempower_50kW, Kempower_150kW, Kempower_250kW }

Decision variables
------------------
  N_c        ∈ ℤ₊  : number of Kempower chargers of type c installed
  u[v,t,c]   ∈ {0,1}: 1 if vehicle v is charging on a type-c charger at step t

Effective power
---------------
  P_eff[v,c] = min(P_c, P_dc_max_v)   [kW at vehicle]
  where P_c ∈ {50, 150, 250} kW and P_dc_max_v is the vehicle's DC max rate.
  (No AC chargers — vehicles with max_dc_charge_kw = 0 are excluded.)

Objective: minimise
  Σ_c  N_c × C_daily_c          (annualised charger CapEx)
  + P_max  × C_demand_global    (SMUD site-infrastructure demand charge proxy)
  + P_peak × C_demand_peak_win  (SMUD summer peak-window demand charge proxy)
  + λ_smooth × (1/|T|) × Σ_t [max(0, P_total[t] - P_mean)]²  (peak-shaving)
  + λ_err × Σ_v (E_delivered_v − E_required_v)                (overcharge penalty)

Subject to (same as existing MILP):
  (A) Energy lower bound:  Σ_{t,c} u[v,t,c] × P_eff[v,c] × Δt × η ≥ E_v
  (B) Energy upper bound:  ≤ E_max_v  (battery room from SOC data)
  (C) Single-plug:         Σ_c u[v,t,c] ≤ 1  ∀ v,t
  (C2) Charger exclusivity: each vehicle uses exactly one charger type
  (C3) Contiguous charging: plug-in blocks are uninterrupted
  (D) Charger capacity:    Σ_v u[v,t,c] ≤ N_c  ∀ t,c

Daily CapEx formula (same across all charger types):
  C_daily_c = [(purchase_c + install_c) / (life_years × 12)
               + annual_maint_c / 12] / 30.42

Kempower DGS unit costs (CA DGS Contract 1-23-61-15A):
  Kempower_50kW  : purchase $23,408  install $855  maint $1,573/yr  life 8yr
  Kempower_150kW : purchase $62,154  install $4,750  maint $1,573/yr  life 8yr
  Kempower_250kW : purchase $101,946  install $5,225  maint $1,573/yr  life 8yr

Note: the MILP does not model utility demand charges for the mobile scenario
(Kempower draws from grid at site; appropriate utility tariff should be applied
at the deployment depot, not necessarily SMUD).  The demand-charge terms serve
as a proxy for infrastructure sizing conservatism.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import importlib
import sys
from datetime import datetime
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  (edit before running)
# ──────────────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")

# Events CSV — same format as the fixed-charger MILP
INPUT_CSV  = BASE_DIR / "z2z_milp_events_northgate_2025_06_30.csv"

# Output directory for Kempower results
OUTPUT_DIR = BASE_DIR / "kempower_milp_outputs"

# Upper bounds on charger counts (keep search space manageable)
KEMPOWER_UPPER_BOUNDS = {
    "Kempower_50kW":  20,
    "Kempower_150kW": 15,
    "Kempower_250kW": 10,
}

# Optional: run over multiple days (list of CSV paths).
# If empty, only INPUT_CSV is processed.
MULTI_DAY_CSVS: list[Path] = []   # e.g. sorted(BASE_DIR.glob("z2z_milp_events_northgate_*.csv"))


# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _load_modules() -> tuple:
    """Import the MILP module and the Kempower cost module."""
    sys.path.insert(0, str(BASE_DIR))
    milp = importlib.import_module("exact_northgate_charger_sizing_milp")
    from charger_costs_kempower_dgs import build_charger_specs_kempower_dgs
    kempower_specs = build_charger_specs_kempower_dgs()
    return milp, kempower_specs


def _run_one(
    milp,
    kempower_specs: dict,
    csv_path: Path,
    out_dir: Path,
    label: str = "",
) -> dict | None:
    """
    Run the MILP for one events CSV with Kempower charger specs.

    Patches module-level globals on the MILP module (safe because
    we restore nothing — each call is a fresh override).
    """
    import pandas as pd

    # ── Patch MILP module globals ───────────────────────────────────────────
    milp.CHARGER_UPPER_BOUNDS = KEMPOWER_UPPER_BOUNDS
    milp.INPUT_PATH_PRIMARY   = csv_path
    milp.INPUT_PATH_FALLBACK  = csv_path          # avoid fallback to northgate default
    milp.OUTPUT_DIR           = out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Run MILP ───────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  KEMPOWER MILP SIZING — {label or csv_path.stem}")
    print(f"{'='*70}")

    try:
        milp.main(charger_specs_override=kempower_specs)
    except Exception as exc:
        print(f"[ERROR] MILP failed for {csv_path.name}: {exc}")
        import traceback; traceback.print_exc()
        return None

    # ── Read back the selected-mix CSV for summary ──────────────────────────
    mix_csv = out_dir / "exact_milp_selected_charger_mix.csv"
    if not mix_csv.exists():
        print(f"[WARNING] No output mix CSV found in {out_dir}")
        return None

    mix_df = pd.read_csv(mix_csv)

    # Compute fleet cost per the Kempower cost model
    # (lifecycle = purchase + install + maint × life + electrical + permit)
    ELECTRICAL_LOW  = 10_000   # 240V Kempower, no trenching
    ELECTRICAL_HIGH = 20_000
    PERMIT          = 2_000

    cost_rows = []
    for _, row in mix_df.iterrows():
        n    = int(row.get("count", 0))
        ctype = row["charger_type"]
        spec = kempower_specs.get(ctype, {})
        life = spec.get("life_years", 8)
        maint = spec.get("annual_maint", 0)
        purch = spec.get("purchase_cost", 0)
        inst  = spec.get("install_cost", 0)

        lifecycle_low  = (purch + inst + ELECTRICAL_LOW  + PERMIT + maint * life) * n
        lifecycle_high = (purch + inst + ELECTRICAL_HIGH + PERMIT + maint * life) * n

        cost_rows.append({
            "charger_type":          ctype,
            "count":                 n,
            "purchase_per_unit":     purch,
            "install_per_unit":      inst,
            "electrical_low":        ELECTRICAL_LOW,
            "electrical_high":       ELECTRICAL_HIGH,
            "permit_per_unit":       PERMIT,
            "annual_maint":          maint,
            "life_years":            life,
            "lifecycle_fleet_low":   lifecycle_low,
            "lifecycle_fleet_high":  lifecycle_high,
        })

    cost_df = pd.DataFrame(cost_rows)
    cost_csv = out_dir / "kempower_lifecycle_costs.csv"
    cost_df.to_csv(cost_csv, index=False)
    print(f"  Saved: {cost_csv.name}")

    total_low  = cost_df["lifecycle_fleet_low"].sum()
    total_high = cost_df["lifecycle_fleet_high"].sum()

    # ── Print mix summary ───────────────────────────────────────────────────
    print("\n  --- Kempower charger mix ---")
    for _, row in mix_df.iterrows():
        if int(row.get("count", 0)) > 0:
            print(f"    {row['charger_type']:<20}  {int(row['count'])} unit(s)  "
                  f"{row['power_kw']:.0f} kW  "
                  f"daily_capex=${row['daily_capex_per_unit']:.2f}/unit")

    print(f"\n  Fleet lifecycle cost (excl. EOL): "
          f"${total_low:,.0f} – ${total_high:,.0f}")
    print(f"  Fleet annual cost               : "
          f"${total_low / 8:,.0f} – ${total_high / 8:,.0f}   (8-yr life)")

    return {
        "label":           label or csv_path.stem,
        "mix_df":          mix_df,
        "lifecycle_low":   total_low,
        "lifecycle_high":  total_high,
    }


# ──────────────────────────────────────────────────────────────────────────────
# MULTI-DAY RUNNER
# ──────────────────────────────────────────────────────────────────────────────

def run_all_days(milp, kempower_specs: dict, csv_list: list[Path]) -> None:
    """Run the Kempower MILP for every CSV in csv_list."""
    import pandas as pd

    summary_rows = []
    for csv_path in sorted(csv_list):
        date_tag = (
            csv_path.stem
            .replace("z2z_milp_events_northgate_", "")
            .replace("_", "-")
        )
        out_dir = OUTPUT_DIR / f"kempower_milp_outputs_{date_tag.replace('-', '_')}"

        result = _run_one(milp, kempower_specs, csv_path, out_dir, label=date_tag)
        if result is None:
            summary_rows.append({"date": date_tag, "status": "error"})
            continue

        mix = result["mix_df"]
        row: dict = {"date": date_tag, "status": "ok"}
        for _, mr in mix.iterrows():
            row[f"N_{mr['charger_type']}"] = int(mr.get("count", 0))
        row["lifecycle_fleet_low"]  = result["lifecycle_low"]
        row["lifecycle_fleet_high"] = result["lifecycle_high"]
        summary_rows.append(row)

    summary_df = pd.DataFrame(summary_rows)
    out_csv = OUTPUT_DIR / "kempower_multi_day_summary.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(out_csv, index=False)
    print(f"\n[DONE] Multi-day summary -> {out_csv}")


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Entry point.
    Usage:
      python kempower_milp_sizing.py                     # single day (INPUT_CSV)
      python kempower_milp_sizing.py <events.csv>        # specified CSV
      python kempower_milp_sizing.py --all-days          # all northgate z2z CSVs
    """
    print("=" * 70)
    print("  KEMPOWER DGS — MILP CHARGER SIZING")
    print("=" * 70)
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    milp, kempower_specs = _load_modules()

    print("  Kempower DGS charger types:")
    DAYS = 30.42
    for ctype, spec in kempower_specs.items():
        mc = (spec["purchase_cost"] + spec["install_cost"]) / (spec["life_years"] * 12)
        mm = spec["annual_maint"] / 12
        daily = (mc + mm) / DAYS
        print(f"    {ctype:<20}  {spec['power_kw']:>4.0f} kW  "
              f"purchase=${spec['purchase_cost']:>7,}  "
              f"daily_capex=${daily:.2f}")
    print()

    # ── Mode: all-days ───────────────────────────────────────────────────────
    if "--all-days" in sys.argv:
        csv_list = sorted(BASE_DIR.glob("z2z_milp_events_northgate_*.csv"))
        if not csv_list:
            print(f"[ERROR] No northgate event CSVs found in {BASE_DIR}")
            sys.exit(1)
        print(f"  Running all-day sweep: {len(csv_list)} files")
        run_all_days(milp, kempower_specs, csv_list)
        return

    # ── Mode: single day ────────────────────────────────────────────────────
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        csv_path = Path(sys.argv[1])
    else:
        csv_path = INPUT_CSV

    if not csv_path.exists():
        print(f"[ERROR] Events CSV not found: {csv_path}")
        sys.exit(1)

    date_tag = (
        csv_path.stem
        .replace("z2z_milp_events_northgate_", "")
        .replace("_", "-")
    )
    out_dir = OUTPUT_DIR / date_tag.replace("-", "_") if date_tag else OUTPUT_DIR

    _run_one(milp, kempower_specs, csv_path, out_dir, label=date_tag)


if __name__ == "__main__":
    main()
