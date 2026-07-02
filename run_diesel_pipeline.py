"""
run_diesel_pipeline.py
-----------------------
Evaluate the hypothetical diesel mobile DCFC for all operating days at all sites,
using the same event CSVs consumed by the Kempower and XOS pipelines.

Architecture: Tier 4 Final towable genset + DCFC power cabinet (see charger_costs_diesel_genset.py)
Size-matching: Diesel_50kW / Diesel_150kW / Diesel_250kW match Kempower 50/150/250 kW DC output.

For each operating day and each diesel config, compute:
  - Total DC energy demanded (kWh) — same as Kempower input
  - Daily CapEx (straight-line, same formula as Kempower)
  - Fuel cost, DEF cost, variable O&M
  - Runtime hours and feasibility check
  - LCOD ($/kWh) and total $/day

Outputs: diesel_outputs/{site}_all_days_diesel.csv (one file per site)

Aggregation follows existing model convention:
  - All operating days evaluated
  - p90 and mean reported in summary
  - 10 worst-cost days identified (cost_rank, is_worst10)

NOTE: No utility demand charges for diesel — the genset is the AC supply,
not the utility grid. The comparison script (build_technology_comparison.py)
adds demand charges to Kempower and XOS but not diesel.
"""
from __future__ import annotations

import sys
import math
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

# ── Path setup (same as Kempower pipeline) ─────────────────────────────────────
REPO_DIR  = Path(__file__).parent
BASE_DIR  = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT_DIR   = REPO_DIR / "diesel_outputs"

sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(BASE_DIR))

from charger_costs_diesel_genset import (
    build_diesel_configs,
    total_daily_cost,
    lcod_per_kwh,
    runtime_hours_day,
    is_feasible,
    engine_replacements,
    LOAD_FACTOR,
    ETA_GENSET,
    ETA_DCFC,
    LHV_KWH_PER_GAL,
    DIESEL_PRICE_PER_GAL,
)

# ── Site configuration ─────────────────────────────────────────────────────────
SITES = [
    ("northgate",  "northgate"),
    ("fresno",     "fresno"),
    ("glendale",   "glendale"),
    ("san_diego",  "san_diego"),
    ("glendale_smud", "glendale"),  # same event CSVs as glendale, SMUD rate sensitivity
]

# Operating window (hours) for feasibility check
# Derived from typical site operating hours; all charging must fit within window
# Conservative: 10-hour depot operating window (6 AM – 4 PM typical maintenance yard)
OPERATING_WINDOW_HRS = 10.0

# Annual runtime assumption for engine replacement calculation (days/yr × runtime/day)
ASSUMED_ANNUAL_DAYS = 250   # approximate operating days per year


def load_event_csvs(site_csv_key: str) -> list[tuple[str, Path]]:
    """
    Find all z2z_milp_events_{site_csv_key}_{YYYY_MM_DD}.csv files.
    Returns list of (date_str, path) tuples sorted by date.
    """
    pattern = f"z2z_milp_events_{site_csv_key}_*.csv"
    files   = sorted(BASE_DIR.glob(pattern))
    result  = []
    for f in files:
        # extract YYYY-MM-DD from stem e.g. z2z_milp_events_northgate_2025_06_09
        parts = f.stem.split("_events_")[-1]   # "northgate_2025_06_09"
        date_part = "_".join(parts.split("_")[-3:])   # "2025_06_09"
        date_str  = date_part.replace("_", "-")        # "2025-06-09"
        result.append((date_str, f))
    return result


def process_site(site_key: str, site_csv_key: str,
                 configs: dict) -> pd.DataFrame:
    """
    Run diesel cost calculation for all available operating days at one site.
    Returns a DataFrame with one row per (day, config) combination.
    """
    day_files = load_event_csvs(site_csv_key)
    if not day_files:
        print(f"  [WARN] No event CSVs found for {site_key} (csv key: {site_csv_key})")
        return pd.DataFrame()

    rows = []
    for date_str, csv_path in day_files:
        try:
            events = pd.read_csv(csv_path)
        except Exception as exc:
            print(f"  [ERROR] {csv_path.name}: {exc}")
            continue

        # Total DC energy demanded on this day across all vehicles
        e_day = float(events["energy_needed_kwh_for_visit"].sum())
        n_veh = len(events)

        # Compute operating window from event data (last departure - first arrival)
        try:
            arr  = pd.to_datetime(events["arrival_time"],   utc=True)
            dep  = pd.to_datetime(events["departure_time"], utc=True)
            window_hr = (dep.max() - arr.min()).total_seconds() / 3600
            window_hr = min(window_hr, OPERATING_WINDOW_HRS)  # cap at configured max
        except Exception:
            window_hr = OPERATING_WINDOW_HRS

        for cfg_name, cfg in configs.items():
            costs  = total_daily_cost(cfg, e_day)
            rt_hrs = runtime_hours_day(e_day, cfg["dc_power_kw"])
            lcd    = lcod_per_kwh(cfg, e_day)
            feas   = is_feasible(cfg, e_day, window_hr)

            rows.append({
                "date":             date_str,
                "site":             site_key,
                "config":           cfg_name,
                "matched_to":       cfg["matched_to"],
                "dc_power_kw":      cfg["dc_power_kw"],
                "genset_prime_kw":  cfg["genset_prime_kw"],
                "n_vehicles":       n_veh,
                "e_demanded_kwh":   round(e_day, 2),
                "runtime_hours":    costs["runtime_hours"],
                "window_hours":     round(window_hr, 2),
                "feasible":         feas,
                "gallons_day":      costs["gallons_day"],
                "capex_daily":      costs["capex_daily"],
                "fuel_cost":        costs["fuel_cost"],
                "def_cost":         costs["def_cost"],
                "var_om_cost":      costs["var_om_cost"],
                "demand_charge":    0.0,   # no utility demand charge for diesel
                "total_daily_cost": costs["total_daily_cost"],
                "lcod_per_kwh":     round(lcd, 5) if not math.isnan(lcd) else None,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Add ranking columns (consistent with existing model convention)
    # Rank by total_daily_cost descending; separate rank per config
    for cfg_name, grp in df.groupby("config"):
        idx = grp["total_daily_cost"].rank(ascending=False, method="first").astype(int)
        df.loc[grp.index, "cost_rank"] = idx
        df.loc[grp.index, "is_worst10"] = idx <= 10

    df["cost_rank"] = df["cost_rank"].astype(int)
    df["is_worst10"] = df["is_worst10"].astype(bool)

    return df


def print_summary(df: pd.DataFrame, site_key: str) -> None:
    """Print per-config summary statistics for a site."""
    print(f"\n  {'Config':<18} {'N days':>7} {'p90 $/day':>11} {'Mean $/day':>11} "
          f"{'Mean LCOD':>11} {'Infeas':>7}")
    print("  " + "-" * 67)
    for cfg_name, grp in df.groupby("config"):
        n_days  = len(grp)
        p90     = np.percentile(grp["total_daily_cost"], 90)
        mean_c  = grp["total_daily_cost"].mean()
        mean_l  = grp["lcod_per_kwh"].mean()
        infeas  = (~grp["feasible"]).sum()
        print(f"  {cfg_name:<18} {n_days:>7} ${p90:>10,.2f} ${mean_c:>10,.2f} "
              f"  ${mean_l:>8.4f}/kWh {infeas:>6}")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    configs = build_diesel_configs()

    print(f"\n{'='*65}")
    print("  DIESEL MOBILE DCFC PIPELINE")
    print(f"  Diesel price: ${DIESEL_PRICE_PER_GAL}/gal [PLACEHOLDER-D9 — update before submission]")
    print(f"  Load factor: {LOAD_FACTOR} | eta_genset={ETA_GENSET} | eta_DCFC={ETA_DCFC}")
    print(f"  LHV: {LHV_KWH_PER_GAL} kWh/gal | Engine life: 15,000 hr")
    print(f"  [PLACEHOLDER-D3] 50/175 kW genset $/kW from Lazard/NREL est. — pending quotes")
    print(f"{'='*65}\n")

    all_results = []
    for site_key, site_csv_key in SITES:
        print(f"  [{datetime.now().strftime('%H:%M:%S')}] {site_key.upper()}")
        df_site = process_site(site_key, site_csv_key, configs)
        if df_site.empty:
            print(f"  [SKIP] No data for {site_key}")
            continue

        out_path = OUT_DIR / f"{site_key}_all_days_diesel.csv"
        df_site.to_csv(out_path, index=False)
        print(f"  -> Written: {out_path.name}")
        print_summary(df_site, site_key)

        all_results.append(df_site)

    if not all_results:
        print("No results generated — check event CSV paths.")
        return

    # Combined summary across all sites
    df_all = pd.concat(all_results, ignore_index=True)
    combined_path = OUT_DIR / "all_sites_all_configs_diesel.csv"
    df_all.to_csv(combined_path, index=False)

    # p90 summary table
    print(f"\n{'='*65}")
    print("  DIESEL COST SUMMARY — p90 $/day by site × config")
    print(f"  [PLACEHOLDER-D3] Values for 50/150 kW class use estimated genset $/kW")
    print(f"{'='*65}")
    print(f"  {'Site':<16} {'Config':<18} {'N days':>7} {'p90 $/day':>11} "
          f"{'Mean $/day':>11} {'Mean LCOD':>11}")
    print("  " + "-" * 76)
    for (site, cfg), grp in df_all.groupby(["site", "config"]):
        p90  = np.percentile(grp["total_daily_cost"], 90)
        mean = grp["total_daily_cost"].mean()
        lcd  = grp["lcod_per_kwh"].mean()
        print(f"  {site:<16} {cfg:<18} {len(grp):>7} ${p90:>10,.2f} "
              f"${mean:>10,.2f}  ${lcd:>8.4f}/kWh")

    print(f"\nDone: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Outputs: {OUT_DIR}")


if __name__ == "__main__":
    main()
