"""
run_xos_pipeline.py
====================
Full XOS Hub MC02 sizing pipeline for all 4 Caltrans sites — all operating days.

Methodology
-----------
Phase 1: For EVERY operating day at each site, run the XOS SoC simulation
         (xos_hub_soc_simulation.py) using the add-one-until-covered rule to
         find the minimum number of XOS units that serve all vehicles.

Phase 2: Compute total daily cost for each day:
           daily_capex   = n_units × daily_capex(infra_mid)    (purchase + infra + O&M + warranty)
           energy_cost   = Σ_t [grid_kw[t] × TOU_rate[t] × DT_H]  (site utility TOU rate)
           demand_cost   = peak_grid_kw × c_demand_global / 30.42   (monthly → daily)
           peak_win_cost = peak_grid_win_kw × c_demand_peak_win / 30.42  (SMUD only)

Phase 3: Sort ALL days by total daily cost (worst = highest).

Phase 4: Select 10 worst-cost days per site.

Phase 5: Generate:
   - xos_outputs/{site}_all_days_xos.csv
   - xos_outputs/{site}_worst10_schedule.csv
   - xos_outputs/XOS_Sizing_Results.xlsx

Infrastructure cost: electrical_infra_cost(n_units, estimate='mid') from
  charger_costs_xos_hub.py — building-side only (after meter).
  Includes panel upgrade, breakers, conduit, wiring. Excludes utility-side work.

Utility rates: utility_rates.py (SMUD / PG&E BEV-2 / SDG&E EV-HP per site)
"""
from __future__ import annotations

import sys
import importlib
from pathlib import Path
from collections import defaultdict

import pandas as pd
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
sys.path.insert(0, str(BASE_DIR))

import utility_rates as ur
from charger_costs_xos_hub import XOS_HUB_SPECS, electrical_infra_cost, daily_capex

xos = importlib.import_module("xos_hub_soc_simulation")

OUT_DIR = BASE_DIR / "xos_outputs"
OUT_DIR.mkdir(parents=True, exist_ok=True)

TZ_PAC       = "America/Los_Angeles"
DT_H         = 0.25          # 15-min timesteps
DAYS_PER_MON = 30.42
INFRA_TIER   = "mid"         # building-side electrical estimate scenario

SITES = [
    ("northgate", "Northgate",  "SMUD"),
    ("fresno",    "Fresno",     "PG&E BEV-2"),
    ("glendale",  "Glendale",   "PG&E BEV-2 (proxy)"),
    ("san_diego", "San Diego",  "SDG&E EV-HP"),
]

WORST_N   = 10
MAX_UNITS = 10    # maximum XOS units to try per day


# ── Sizing: add-one-until-covered with plateau detection ───────────────────────

def find_efficient_units(
    ev_df: pd.DataFrame,
    p_eff: dict,
    max_k: int = MAX_UNITS,
) -> tuple[int, dict, bool]:
    """
    Find the minimum number of XOS units that achieves the best possible service.

    Returns (recommended_k, result_at_k, full_service_possible).

    If 100% service is achievable, stops as soon as all vehicles are served.
    If not, stops when service rate plateaus for 2 consecutive unit additions
    (adding another unit no longer helps) and returns the minimum K at that rate.
    """
    best_served  = -1
    best_k       = 1
    best_result  = None
    plateau_run  = 0

    for k in range(1, max_k + 1):
        result   = xos.simulate_one_day(ev_df, k, p_eff, verbose=False)
        n_served = result["n_served"]

        if result["all_served"]:
            return k, result, True

        if n_served > best_served:
            best_served = n_served
            best_k      = k
            best_result = result
            plateau_run = 0
        else:
            plateau_run += 1
            if plateau_run >= 2:
                # Two consecutive K values with no improvement — plateau confirmed.
                # Recommend the K that first reached this service rate.
                break

    if best_result is None:
        best_result = result
    return best_k, best_result, False


# ── Cost helpers ───────────────────────────────────────────────────────────────

def _capacity_rates(site: str) -> tuple[float, float]:
    """Return (c_demand_global $/kW/month, c_demand_peak_win $/kW/month) for site."""
    util = ur.SITE_UTILITY[site]
    if util == "smud":
        return ur._SMUD_DEMAND_GLOBAL, ur._SMUD_DEMAND_PEAK_WIN
    if util == "pge_bev2":
        return ur._PGE_SUBSCRIPTION, 0.0
    if util == "sdge_evhp":
        return ur._SDGE_SUBSCRIPTION, 0.0
    return 0.0, 0.0


def compute_daily_cost(
    n_units: int,
    result:  dict,
    site:    str,
) -> dict:
    """
    Compute all daily cost components for one XOS simulation result.

    Infrastructure cost is amortized as a per-unit daily CapEx using the
    mid-tier building-side electrical estimate (after-meter scope only).
    """
    # ── CapEx ─────────────────────────────────────────────────────────────────
    infra     = electrical_infra_cost(n_units, INFRA_TIER)
    per_unit_install = infra["per_unit_avg"]
    d_capex   = n_units * daily_capex(install_cost_override=per_unit_install)

    # ── Energy cost ───────────────────────────────────────────────────────────
    rate_fn   = ur.energy_rate_fn(site)
    peak_fn   = ur.peak_win_fn(site)
    e_cost    = 0.0
    peak_grid_kw     = 0.0
    peak_grid_win_kw = 0.0

    for row in result["soc_history"]:
        grid_kw = row.get("grid_kw", 0.0)
        if grid_kw <= 0:
            continue
        t_utc = pd.Timestamp(row["time_utc"])
        e_cost        += grid_kw * DT_H * rate_fn(t_utc)
        peak_grid_kw   = max(peak_grid_kw, grid_kw)
        if peak_fn(t_utc):
            peak_grid_win_kw = max(peak_grid_win_kw, grid_kw)

    # ── Demand charges (monthly → daily) ──────────────────────────────────────
    c_global, c_peak_win = _capacity_rates(site)
    demand_cost   = peak_grid_kw     * c_global   / DAYS_PER_MON
    peak_win_cost = peak_grid_win_kw * c_peak_win / DAYS_PER_MON

    total = d_capex + e_cost + demand_cost + peak_win_cost

    return {
        "capex_cost":          round(d_capex,       2),
        "energy_cost":         round(e_cost,         2),
        "demand_cost":         round(demand_cost,    2),
        "peak_win_cost":       round(peak_win_cost,  2),
        "total_daily_cost":    round(total,          2),
        "peak_grid_kw":        round(peak_grid_kw,   1),
        "peak_grid_win_kw":    round(peak_grid_win_kw, 1),
        "infra_total_mid":     infra["total"],
        "infra_per_unit_mid":  round(per_unit_install, 0),
    }


# ── Per-vehicle schedule ───────────────────────────────────────────────────────

def _local(ts: pd.Timestamp | None) -> str:
    if ts is None:
        return ""
    try:
        return ts.tz_convert(TZ_PAC).strftime("%H:%M")
    except Exception:
        return ts.strftime("%H:%M")


def build_schedule(events_df: pd.DataFrame, result: dict, date: str,
                   rank: int, costs: dict, n_units: int) -> list[dict]:
    """Build per-vehicle schedule rows from simulation dispatch_log."""
    dispatch = result.get("dispatch_log", [])
    delivered = result.get("delivered", {})
    remaining = result.get("remaining", {})

    # Aggregate per vehicle
    first_t: dict[str, pd.Timestamp] = {}
    last_t:  dict[str, pd.Timestamp] = {}
    for log in dispatch:
        v  = log["event_id"]
        t  = pd.Timestamp(log["time_utc"])
        if v not in first_t or t < first_t[v]:
            first_t[v] = t
        if v not in last_t or t > last_t[v]:
            last_t[v]  = t

    rows = []
    ev_lk = events_df.set_index("charging_event_id")
    for v in events_df["charging_event_id"]:
        info     = ev_lk.loc[v] if v in ev_lk.index else {}
        needed   = float(info.get("energy_needed_kwh_for_visit", 0) or 0)
        deliv    = delivered.get(v, 0.0)
        gap      = max(needed - deliv, 0.0)
        tol      = getattr(xos, "ENERGY_TOL", 0.1)
        status   = ("full"    if gap <= tol else
                    "partial" if deliv > tol else
                    "unserved")

        arr_utc = (pd.Timestamp(info["arrival_time"])   if "arrival_time"   in info else None)
        dep_utc = (pd.Timestamp(info["departure_time"]) if "departure_time" in info else None)
        dwell   = ((dep_utc - arr_utc).total_seconds() / 3600
                   if arr_utc and dep_utc else 0.0)

        cs  = first_t.get(v)
        ce  = last_t.get(v)
        dur = ((ce - cs).total_seconds() / 3600 + DT_H) if cs and ce else 0.0

        bat_cap   = float(info.get("battery_capacity_kwh", 0) or 0)
        soc_start = float(info.get("assumed_initial_soc_percent", 0) or 0)
        soc_end   = (min(100.0, soc_start + 100.0 * deliv / bat_cap)
                     if bat_cap > 0 else None)

        rows.append({
            "date":                  date,
            "worst_day_rank":        rank,
            "total_daily_cost":      costs["total_daily_cost"],
            "n_xos_units":           n_units,
            "charging_event_id":     v,
            "vehicle_id":            str(info.get("vehicle_id", "")),
            "ev_model":              str(info.get("ev_equivalent_model", "")),
            "arrival_local":         _local(arr_utc),
            "departure_local":       _local(dep_utc),
            "dwell_h":               round(dwell, 2),
            "energy_needed_kwh":     round(needed, 1),
            "energy_delivered_kwh":  round(deliv,  1),
            "energy_gap_kwh":        round(gap,    1),
            "status":                status,
            "charge_start":          _local(cs),
            "charge_end":            _local(ce) if ce else "",
            "charge_duration_h":     round(dur, 2),
            "soc_start_pct":         round(soc_start, 1),
            "soc_end_pct":           round(soc_end, 1) if soc_end is not None else "",
        })
    return rows


# ── Main per-site run ──────────────────────────────────────────────────────────

def run_site(site: str, label: str) -> pd.DataFrame:
    csvs = sorted(BASE_DIR.glob(f"z2z_milp_events_{site}_*.csv"))
    print(f"\n{'='*62}")
    print(f"  {label}  ({site})  —  {len(csvs)} event files")
    print(f"{'='*62}")

    rows = []
    for i, csv_path in enumerate(csvs, 1):
        date_stem = "_".join(csv_path.stem.split("_")[-3:])   # YYYY_MM_DD
        date      = date_stem.replace("_", "-")

        try:
            raw_df  = pd.read_csv(csv_path)
            ev_df   = xos.load_events(csv_path)
        except Exception as exc:
            print(f"  [{i:>4}/{len(csvs)}]  {date}  LOAD ERROR: {exc}")
            continue

        if ev_df is None or len(ev_df) == 0:
            print(f"  [{i:>4}/{len(csvs)}]  {date}  no valid events — skip")
            continue

        try:
            p_eff                    = xos.compute_p_eff(ev_df)
            n_units, result, full_ok = find_efficient_units(ev_df, p_eff)
            costs                    = compute_daily_cost(n_units, result, site)
        except Exception as exc:
            print(f"  [{i:>4}/{len(csvs)}]  {date}  SIMULATION ERROR: {exc}")
            continue

        svc_pct = 100 * result["n_served"] / max(result["n_total"], 1)

        if i == 1 or i % 20 == 0 or i == len(csvs):
            print(f"  [{i:>4}/{len(csvs)}]  {date}  "
                  f"units={n_units}  svc={svc_pct:.0f}%  "
                  f"cost=${costs['total_daily_cost']:,.0f}")

        rows.append({
            "date":                 date,
            "n_vehicles":           result["n_total"],
            "n_xos_units":          n_units,
            "full_service_possible": full_ok,
            "n_full":               result["n_served"],
            "n_unserved":           result["n_total"] - result["n_served"],
            "service_rate_pct":     round(svc_pct, 1),
            "capex_cost":        costs["capex_cost"],
            "energy_cost":       costs["energy_cost"],
            "demand_cost":       costs["demand_cost"],
            "peak_win_cost":     costs["peak_win_cost"],
            "total_daily_cost":  costs["total_daily_cost"],
            "peak_grid_kw":      costs["peak_grid_kw"],
            "infra_total_mid":   costs["infra_total_mid"],
            "_ev_df":   ev_df,
            "_result":  result,
            "_costs":   costs,
        })

    if not rows:
        print(f"  No results for {label}")
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("total_daily_cost", ascending=False).reset_index(drop=True)
    df["cost_rank"] = df.index + 1
    df["is_worst10"] = df["cost_rank"] <= WORST_N

    return df


# ── Build worst-10 schedule ────────────────────────────────────────────────────

def worst10_schedule(df: pd.DataFrame) -> pd.DataFrame:
    sched_rows = []
    for _, row in df[df["is_worst10"]].iterrows():
        sched_rows.extend(
            build_schedule(
                events_df = row["_ev_df"],
                result    = row["_result"],
                date      = row["date"],
                rank      = int(row["cost_rank"]),
                costs     = row["_costs"],
                n_units   = int(row["n_xos_units"]),
            )
        )
    out = pd.DataFrame(sched_rows)
    if len(out):
        out = out.sort_values(["worst_day_rank", "arrival_local"]).reset_index(drop=True)
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

OUTPUT_COLS = [
    "date", "n_vehicles", "n_xos_units", "full_service_possible",
    "n_full", "n_unserved", "service_rate_pct",
    "capex_cost", "energy_cost", "demand_cost",
    "peak_win_cost", "total_daily_cost", "peak_grid_kw",
    "infra_total_mid", "cost_rank", "is_worst10",
]

site_all_dfs:   dict[str, pd.DataFrame] = {}
site_sched_dfs: dict[str, pd.DataFrame] = {}
summary_rows: list[dict] = []

for site, label, utility in SITES:
    df = run_site(site, label)
    if df.empty:
        continue

    # Strip private columns before saving
    df_out = df[OUTPUT_COLS].copy()
    df_out.to_csv(OUT_DIR / f"{site}_all_days_xos.csv", index=False)

    sched = worst10_schedule(df)
    sched.to_csv(OUT_DIR / f"{site}_worst10_schedule.csv", index=False)

    site_all_dfs[site]   = df_out
    site_sched_dfs[site] = sched

    worst10 = df[df["is_worst10"]]
    max_units = int(worst10["n_xos_units"].max())
    avg_svc   = worst10["service_rate_pct"].mean()
    avg_cost  = worst10["total_daily_cost"].mean()
    p90_cost  = df_out["total_daily_cost"].quantile(0.90)

    summary_rows.append({
        "site":                label,
        "utility":             utility,
        "n_days_analyzed":     len(df_out),
        "max_units_worst10":   max_units,
        "recommendation":      f"{max_units}× XOS Hub MC02",
        "infra_scope":         "Building-side only (after meter), mid estimate",
        "avg_svc_worst10_pct": round(avg_svc, 1),
        "avg_total_cost_worst10": round(avg_cost, 2),
        "p90_total_cost":      round(p90_cost, 2),
    })

    print(f"\n  {label}: recommend {max_units}× XOS  |  "
          f"avg svc={avg_svc:.0f}%  avg cost=${avg_cost:,.0f}")

# ── Write Excel ───────────────────────────────────────────────────────────────
print(f"\nWriting Excel: {OUT_DIR / 'XOS_Sizing_Results.xlsx'} ...")

LABEL = {
    "northgate": "Northgate",
    "fresno":    "Fresno",
    "glendale":  "Glendale",
    "san_diego": "SanDiego",
}

with pd.ExcelWriter(OUT_DIR / "XOS_Sizing_Results.xlsx", engine="openpyxl") as xl:
    pd.DataFrame(summary_rows).to_excel(xl, sheet_name="Summary", index=False)
    for site in [s for s, _, _ in SITES if s in site_all_dfs]:
        site_all_dfs[site].to_excel(xl, sheet_name=f"{LABEL[site]}_AllDays", index=False)
    for site in [s for s, _, _ in SITES if s in site_sched_dfs]:
        site_sched_dfs[site].to_excel(xl, sheet_name=f"{LABEL[site]}_Worst10Sched", index=False)

print("Done.")
