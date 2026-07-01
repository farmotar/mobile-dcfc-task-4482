"""
xos_grid_charging_analysis.py
==============================
Shows WHEN the XOS Hub MC02 charges its battery from the grid, and solves
for the cost-optimal grid-charging schedule using SMUD C&I 21-299 kW TOD rates.

The vehicle dispatch schedule is held FIXED (from the existing XOS simulation).
Only the grid-charging decisions (which idle 15-min steps to pull from grid)
are optimised.

SMUD rates (from exact_northgate_charger_sizing_milp.py):
  Summer (Jun-Sep) weekdays 4-9 PM    : $0.2341/kWh   ← most expensive
  Summer all other hours              : $0.1215/kWh
  Non-summer weekdays 4-9 PM         : $0.1477/kWh
  Non-summer 9 AM-4 PM (off-saver)   : $0.0888/kWh   ← cheapest
  Non-summer all other hours         : $0.1264/kWh

Demand charges (planning proxy):
  Global site demand charge           : $6.454/kW
  Summer peak-window demand charge    : $9.960/kW

Usage:
    python xos_grid_charging_analysis.py
    python xos_grid_charging_analysis.py 2026-04-29
    python xos_grid_charging_analysis.py --all-northgate
"""

from __future__ import annotations

import sys
import importlib
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT_DIR  = BASE_DIR / "xos_grid_charging_outputs"
OUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
xos = importlib.import_module("xos_hub_soc_simulation")

# ── SMUD TOD parameters (mirrored from exact_northgate_charger_sizing_milp.py) ─
SMUD_TZ               = "America/Los_Angeles"
PEAK_WIN_START_H      = 16.0   # 4 PM local
PEAK_WIN_END_H        = 21.0   # 9 PM local
OFFPEAK_SAVER_START_H =  9.0   # 9 AM local
OFFPEAK_SAVER_END_H   = 16.0   # 4 PM local

C_SUMMER_PEAK         = 0.2341  # $/kWh
C_SUMMER_OFFPEAK      = 0.1215
C_NONSUMMER_PEAK      = 0.1477
C_NONSUMMER_OFFSAVER  = 0.0888
C_NONSUMMER_OFFPEAK   = 0.1264

C_DEMAND_GLOBAL       = 6.454   # $/kW
C_DEMAND_PEAK_WIN     = 9.960   # $/kW

DT_H      = xos.DT_H       # 0.25 h (15 min)
P_GRID    = xos.P_GRID_KW  # 83 kW
ETA_C     = xos.ETA_C      # 0.95
B_KWH     = xos.B_KWH      # 280 kWh
SOC_MIN   = xos.SOC_MIN    # 0.20
SOC_MAX   = xos.SOC_MAX    # 1.00


# ── SMUD rate function ─────────────────────────────────────────────────────────

def smud_rate(t_utc: pd.Timestamp) -> float:
    """Return SMUD C&I 21-299 kW TOD energy rate ($/kWh) for a UTC timestamp."""
    t_loc      = t_utc.tz_convert(SMUD_TZ)
    hour       = t_loc.hour + t_loc.minute / 60.0
    is_summer  = t_loc.month in (6, 7, 8, 9)
    is_weekday = t_loc.weekday() < 5
    is_peak    = is_weekday and PEAK_WIN_START_H <= hour < PEAK_WIN_END_H
    is_saver   = (not is_summer) and (OFFPEAK_SAVER_START_H <= hour < OFFPEAK_SAVER_END_H)

    if is_summer:
        return C_SUMMER_PEAK if is_peak else C_SUMMER_OFFPEAK
    else:
        if is_peak:   return C_NONSUMMER_PEAK
        if is_saver:  return C_NONSUMMER_OFFSAVER
        return C_NONSUMMER_OFFPEAK


# ── Core analysis for one day ──────────────────────────────────────────────────

def _apply_extended_dwell(events: pd.DataFrame) -> pd.DataFrame:
    """
    Extend vehicle departure times so every vehicle has enough window to fully charge.
    Mirrors the logic in run_site_full_year.py compute_extensions().
    Uses P_PORT_KW × ETA_D as the conservative charge rate for extension.
    """
    events = events.copy()
    for idx, row in events.iterrows():
        e_need   = float(row["energy_needed_kwh_for_visit"])
        arr      = row["arrival_time"]
        dep      = row["departure_time"]
        dwell_h  = (dep - arr).total_seconds() / 3600.0
        # Use vehicle-specific effective power (capped at P_PORT_KW)
        mdc      = float(row.get("max_dc_charge_kw", 0) or 0)
        p_eff_v  = min(xos.P_PORT_KW, mdc) if mdc > 0 else xos.P_PORT_KW
        req_h    = e_need / (p_eff_v * xos.ETA_D)
        extra_h  = max(0.0, req_h - dwell_h)
        if extra_h > 1e-6:
            events.at[idx, "departure_time"] = dep + pd.Timedelta(hours=extra_h)
    return events


def analyse_day(csv_path: Path, date_str: str) -> dict:
    """
    Run XOS simulation for one day (with extended dwell applied),
    then solve cost-optimal grid-charging.

    Returns a result dict with:
      naive_cost_usd      : electricity cost of naive schedule (charge every idle step)
      optimal_cost_usd    : electricity cost of optimised schedule
      savings_usd         : naive - optimal
      naive_grid_kwh      : total kWh drawn from grid in naive schedule
      optimal_grid_kwh    : total kWh drawn from grid in optimal schedule
      per_unit_steps      : list[dict] — per-unit per-step data for plotting
      time_grid           : pd.DatetimeIndex of all steps
      rates               : SMUD rate at each step
      n_units             : minimum XOS units used
      n_vehicles          : number of vehicles
      demand_naive_kw     : peak grid demand (naive)
      demand_optimal_kw   : peak grid demand (optimal)
    """
    events = xos.load_events(csv_path)
    events = _apply_extended_dwell(events)   # match full-year script behaviour
    p_eff  = xos.compute_p_eff(events)
    n_units, result = xos.find_min_xos_units(events, p_eff, verbose=False)
    print(f"    Min XOS units: {n_units}  |  Served: {result['n_served']}/{result['n_total']}")

    soc_hist = result["soc_history"]
    disp_log = result["dispatch_log"]

    if not soc_hist:
        return {}

    # Build time grid
    time_grid = pd.DatetimeIndex(
        [pd.Timestamp(row["time_utc"]) for row in soc_hist]
    )
    n_steps = len(time_grid)
    rates   = np.array([smud_rate(t) for t in time_grid])  # $/kWh per step

    # Build per-unit discharge array from dispatch log
    # discharge_kwh[k, t] = energy drawn from battery of unit k at step t (kWh)
    discharge_kwh = np.zeros((n_units, n_steps))
    for entry in disp_log:
        k  = entry["unit"]
        ti = entry["step_idx"]
        e_delivered = entry["energy_to_vehicle_kwh"]        # kWh at vehicle
        # Battery energy withdrawn = e_delivered / ETA_D
        discharge_kwh[k, ti] += e_delivered / xos.ETA_D    # kWh from battery

    # Build naive SOC trajectory and grid-charging array from soc_history
    # (the simulation already did this; just decode it)
    naive_soc   = np.zeros((n_units, n_steps))
    for row in soc_hist:
        ti = row["step_idx"]
        for k in range(n_units):
            naive_soc[k, ti] = row[f"soc_unit_{k}"]

    # Naive grid charging: SOC increased when not dispatching → compute from SOC diff
    # Grid kWh at step t for unit k = (SOC[t+1] - SOC[t] + discharge[t]/B) × B  (if positive)
    naive_grid_kwh = np.zeros((n_units, n_steps))
    for k in range(n_units):
        for ti in range(n_steps - 1):
            delta_soc = naive_soc[k, ti + 1] - naive_soc[k, ti]
            net_battery_change = delta_soc * B_KWH
            # If net_battery_change + discharge > 0 → grid charged this step
            charged_kwh_battery = net_battery_change + discharge_kwh[k, ti]
            if charged_kwh_battery > 1e-4:
                naive_grid_kwh[k, ti] = charged_kwh_battery / ETA_C   # kWh from grid

    # Naive cost
    naive_cost = 0.0
    for k in range(n_units):
        for ti in range(n_steps):
            if naive_grid_kwh[k, ti] > 1e-4:
                kwh_from_grid = naive_grid_kwh[k, ti]
                naive_cost += kwh_from_grid * rates[ti]

    naive_demand_kw = naive_grid_kwh.sum(axis=0).max() / (ETA_C * DT_H) if n_steps > 0 else 0

    # ── Cost-optimal grid charging ─────────────────────────────────────────────
    # Strategy: for each unit independently, decide which idle steps to charge,
    # subject to SOC ∈ [SOC_MIN, SOC_MAX] at all times.
    # Sort idle steps by SMUD rate (ascending). Greedily add cheapest steps until
    # SOC is always ≥ SOC_MIN after all discharges are applied.
    #
    # A step is "idle" for unit k if discharge_kwh[k, ti] ≈ 0.

    optimal_grid_kwh = np.zeros((n_units, n_steps))
    optimal_soc      = np.zeros((n_units, n_steps))

    for k in range(n_units):
        # Identify idle steps (unit not serving any vehicle this step)
        idle_steps = [ti for ti in range(n_steps) if discharge_kwh[k, ti] < 1e-4]

        # Sort idle steps by SMUD rate (ascending = cheapest first)
        idle_by_rate = sorted(idle_steps, key=lambda ti: rates[ti])

        # Track which idle steps are chosen for charging
        charge_flags = np.zeros(n_steps, dtype=bool)

        # Iteratively add the cheapest idle step that improves feasibility
        # Simple approach: compute SOC trajectory without any charging,
        # then add cheapest steps until SOC never drops below SOC_MIN.
        def compute_soc(flags):
            s = [SOC_MAX]
            for ti in range(n_steps - 1):
                soc_t = s[-1]
                # Discharge from battery
                soc_t -= discharge_kwh[k, ti] / B_KWH
                # Grid charge if flag set
                if flags[ti]:
                    room   = SOC_MAX - soc_t
                    add    = min(P_GRID * ETA_C * DT_H / B_KWH, room)
                    soc_t += add
                soc_t = min(max(soc_t, 0.0), SOC_MAX)
                s.append(soc_t)
            return np.array(s)

        soc_traj = compute_soc(charge_flags)
        min_soc  = soc_traj.min()

        for ti in idle_by_rate:
            if min_soc >= SOC_MIN:
                break
            if not charge_flags[ti]:
                charge_flags[ti] = True
                soc_traj = compute_soc(charge_flags)
                min_soc  = soc_traj.min()

        optimal_soc[k] = soc_traj

        # Compute actual kWh charged from grid at each flagged step
        _soc = SOC_MAX
        for ti in range(n_steps):
            _soc -= discharge_kwh[k, ti] / B_KWH
            if charge_flags[ti]:
                room   = (SOC_MAX - _soc) * B_KWH
                add_b  = min(P_GRID * ETA_C * DT_H, room)
                kw_from_grid = add_b / (ETA_C * DT_H)
                optimal_grid_kwh[k, ti] = kw_from_grid * DT_H   # kWh from grid
                _soc += add_b / B_KWH
            _soc = min(max(_soc, SOC_MIN), SOC_MAX)
            if ti < n_steps - 1:
                optimal_soc[k, ti + 1] = _soc   # overwrite with precise trajectory

    # Optimal cost
    optimal_cost = 0.0
    for k in range(n_units):
        for ti in range(n_steps):
            if optimal_grid_kwh[k, ti] > 1e-4:
                optimal_cost += optimal_grid_kwh[k, ti] * rates[ti]

    optimal_demand_kw = optimal_grid_kwh.sum(axis=0).max() / (ETA_C * DT_H) if n_steps > 0 else 0

    naive_total_kwh   = naive_grid_kwh.sum()
    optimal_total_kwh = optimal_grid_kwh.sum()

    per_unit_steps = []
    for k in range(n_units):
        for ti in range(n_steps):
            per_unit_steps.append({
                "unit": k,
                "step_idx": ti,
                "time_utc": time_grid[ti],
                "smud_rate": rates[ti],
                "discharge_kwh": discharge_kwh[k, ti],
                "naive_grid_kwh": naive_grid_kwh[k, ti],
                "optimal_grid_kwh": optimal_grid_kwh[k, ti],
                "naive_soc": naive_soc[k, ti],
                "optimal_soc": optimal_soc[k, ti],
            })

    return {
        "date_str":            date_str,
        "n_units":             n_units,
        "n_vehicles":          result["n_total"],
        "naive_cost_usd":      round(naive_cost, 4),
        "optimal_cost_usd":    round(optimal_cost, 4),
        "savings_usd":         round(naive_cost - optimal_cost, 4),
        "naive_grid_kwh":      round(naive_total_kwh, 2),
        "optimal_grid_kwh":    round(optimal_total_kwh, 2),
        "demand_naive_kw":     round(naive_demand_kw, 1),
        "demand_optimal_kw":   round(optimal_demand_kw, 1),
        "per_unit_steps":      per_unit_steps,
        "time_grid":           time_grid,
        "rates":               rates,
        "n_steps":             n_steps,
        # Vehicle-level data for the vehicle panel in the plot
        "events_df":           events,          # with extended departure times applied
        "dispatch_log":        disp_log,
        "delivered":           result["delivered"],
        "remaining":           result["remaining"],
        "p_eff":               p_eff,
    }


# ── Plot ───────────────────────────────────────────────────────────────────────

RATE_LABELS = {
    C_SUMMER_PEAK:        "Summer Peak\n(4–9 PM)",
    C_SUMMER_OFFPEAK:     "Summer Off-Peak",
    C_NONSUMMER_PEAK:     "Non-Summer Peak\n(4–9 PM)",
    C_NONSUMMER_OFFSAVER: "Off-Saver\n(9 AM–4 PM)",
    C_NONSUMMER_OFFPEAK:  "Non-Summer Off-Peak",
}

RATE_COLORS = {
    C_SUMMER_PEAK:        "#d62728",    # red — expensive
    C_SUMMER_OFFPEAK:     "#ff9896",    # light red
    C_NONSUMMER_PEAK:     "#ff7f0e",    # orange
    C_NONSUMMER_OFFSAVER: "#2ca02c",    # green — cheapest
    C_NONSUMMER_OFFPEAK:  "#98df8a",    # light green
}


def _ts_to_step(ts: pd.Timestamp, time_grid: pd.DatetimeIndex) -> float:
    """Convert a UTC timestamp to a fractional step index in the time grid."""
    if time_grid.tz is None:
        if ts.tz is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
    else:
        if ts.tz is None:
            ts = ts.tz_localize("UTC")
    diffs = np.abs((time_grid - ts).total_seconds())
    closest = int(np.argmin(diffs))
    if closest < len(time_grid) - 1:
        span = (time_grid[closest + 1] - time_grid[closest]).total_seconds()
        frac = (ts - time_grid[closest]).total_seconds() / span
        return closest + max(0.0, min(1.0, frac))
    return float(closest)


def plot_day(res: dict, out_path: Path, site_label: str = "Northgate") -> None:
    """
    Three-panel plot:

    Panel A (top, tall) — Vehicle schedule
        One row per vehicle. Grey bar = full parking window (arrival → extended
        departure). Coloured bar = period the XOS was actively charging that
        vehicle (coloured by unit). Label = EV model + energy demand (kWh).

    Panel B (middle) — XOS battery power per unit
        ▼ downward bars (coloured)  = battery discharging TO vehicle
        ▲ upward bars yellow-green  = NAIVE grid recharge (charge every idle slot)
        ▲ upward bars dark green    = OPTIMAL grid recharge (cheapest SMUD windows)
        Line on right axis          = battery SOC %

    Panel C (bottom) — SMUD energy rate ($/kWh) at each 15-min step
        Green background  = cheapest window (charge here if possible)
        Red background    = most expensive (avoid charging here)
    """
    step_df   = pd.DataFrame(res["per_unit_steps"])
    n_units   = res["n_units"]
    time_grid = res["time_grid"]
    rates     = res["rates"]
    date_str  = res["date_str"]
    n_steps   = res["n_steps"]
    events_df = res["events_df"]
    disp_log  = res["dispatch_log"]
    delivered = res["delivered"]
    remaining = res["remaining"]

    tz_loc      = SMUD_TZ
    times_local = pd.DatetimeIndex(time_grid).tz_convert(tz_loc)
    is_summer   = times_local[0].month in (6, 7, 8, 9)
    season      = "Summer" if is_summer else "Non-summer"

    veh   = events_df.copy().sort_values("arrival_time").reset_index(drop=True)
    n_veh = len(veh)

    disp_df = (pd.DataFrame(disp_log) if disp_log
               else pd.DataFrame(columns=["step_idx", "unit", "event_id",
                                           "energy_to_vehicle_kwh"]))

    # ── Layout ─────────────────────────────────────────────────────────────────
    veh_h   = max(4.5, n_veh * 0.55)
    heights = [veh_h] + [3.0] * n_units + [1.8]
    fig, axes = plt.subplots(
        len(heights), 1,
        figsize=(20, sum(heights) + 1.5),
        sharex=True,
        gridspec_kw={"height_ratios": heights},
    )

    x = np.arange(n_steps)
    hour_ticks  = [i for i in range(n_steps) if times_local[i].minute == 0]
    hour_labels = [times_local[i].strftime("%H:%M") for i in hour_ticks]

    UNIT_COLORS = [
        "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
        "#8c564b", "#e377c2", "#17becf", "#bcbd22", "#7f7f7f",
    ]

    # ── Panel A: Vehicle schedule ──────────────────────────────────────────────
    ax_v = axes[0]

    for vi, row in veh.iterrows():
        eid   = row["charging_event_id"]
        arr_s = _ts_to_step(row["arrival_time"],  time_grid)
        dep_s = _ts_to_step(row["departure_time"], time_grid)
        e_req = float(row["energy_needed_kwh_for_visit"])
        model = str(row.get("ev_equivalent_model", "") or "").split(" (")[0][:24]
        is_served = remaining.get(eid, e_req) <= 1e-3

        # Grey parking window bar
        ax_v.barh(vi, max(dep_s - arr_s, 0.5), left=arr_s, height=0.70,
                  color="#cccccc", alpha=0.55, edgecolor="#999999",
                  linewidth=0.5, zorder=1)

        if not disp_df.empty and eid in disp_df["event_id"].values:
            ev_disp   = disp_df[disp_df["event_id"] == eid].sort_values("step_idx")
            unit_used = int(ev_disp["unit"].mode().iloc[0])
            uc        = UNIT_COLORS[unit_used % len(UNIT_COLORS)]
            steps_srv = sorted(ev_disp["step_idx"].tolist())

            # Draw contiguous blocks of XOS charging on this vehicle
            if steps_srv:
                s0 = steps_srv[0]
                s_prev = s0
                for s in steps_srv[1:] + [steps_srv[-1] + 2]:
                    if s > s_prev + 1:
                        ax_v.barh(vi, s_prev - s0 + 1, left=s0, height=0.70,
                                  color=uc, alpha=0.88, edgecolor="white",
                                  linewidth=0.3, zorder=3)
                        s0 = s
                    s_prev = s
            unit_lbl = f"Unit {unit_used + 1}"
        else:
            unit_lbl = "unserved"
            ax_v.barh(vi, max(dep_s - arr_s, 0.5), left=arr_s, height=0.70,
                      color="#d62728", alpha=0.30, edgecolor="#d62728",
                      linewidth=0.8, zorder=2)

        label_str = f"{model}  [{e_req:.0f} kWh | {unit_lbl}]"
        ax_v.text(arr_s + 0.4, vi, label_str, va="center", ha="left",
                  fontsize=6.8, color="black", zorder=5)

    # Rate background on vehicle panel
    for ti in range(n_steps - 1):
        col = RATE_COLORS.get(rates[ti], "#dddddd")
        ax_v.axvspan(ti, ti + 1, color=col, alpha=0.07, linewidth=0)

    ax_v.set_yticks(range(n_veh))
    ax_v.set_yticklabels([f"V{i+1}" for i in range(n_veh)], fontsize=7)
    ax_v.set_ylim(-0.6, n_veh - 0.3)
    ax_v.invert_yaxis()
    ax_v.set_title(
        f"Vehicle Schedule  —  {n_veh} vehicles  "
        f"(grey bar = parking window, coloured bar = XOS actively charging vehicle)",
        fontsize=9.5, pad=4, loc="left",
    )

    unit_patches  = [
        mpatches.Patch(color=UNIT_COLORS[k % len(UNIT_COLORS)], label=f"Unit {k+1}")
        for k in range(n_units)
    ]
    unit_patches += [mpatches.Patch(color="#d62728", alpha=0.4, label="Unserved")]
    ax_v.legend(handles=unit_patches, loc="upper right",
                fontsize=7.5, framealpha=0.9, ncol=min(n_units + 1, 6))

    # ── Panels B: per-unit power + SOC ────────────────────────────────────────
    for k in range(n_units):
        ax = axes[1 + k]
        uk = step_df[step_df["unit"] == k].sort_values("step_idx")

        discharge   = uk["discharge_kwh"].values
        naive_g     = uk["naive_grid_kwh"].values
        optimal_g   = uk["optimal_grid_kwh"].values
        naive_soc   = uk["naive_soc"].values
        optimal_soc = uk["optimal_soc"].values

        for ti in range(n_steps - 1):
            col = RATE_COLORS.get(rates[ti], "#dddddd")
            ax.axvspan(ti, ti + 1, color=col, alpha=0.10, linewidth=0)

        # Discharge to vehicle (downward)
        dkw = np.where(discharge > 1e-4, discharge / (xos.ETA_D * DT_H), 0)
        ax.bar(x, -dkw, width=1.0,
               color=UNIT_COLORS[k % len(UNIT_COLORS)], alpha=0.85,
               label=f"Unit {k+1}: Battery → Vehicle (kW)  [downward]", zorder=3)

        # Naive grid charge (upward, pale yellow-green)
        naive_kw = np.where(naive_g > 1e-4, naive_g / (ETA_C * DT_H), 0)
        ax.bar(x, naive_kw, width=1.0, color="#bcbd22", alpha=0.55,
               label="NAIVE — Grid → Battery  (every idle slot)", zorder=2)

        # Optimal grid charge (upward, dark green)
        opt_kw = np.where(optimal_g > 1e-4, optimal_g / (ETA_C * DT_H), 0)
        ax.bar(x, opt_kw, width=1.0, color="#2ca02c", alpha=0.90,
               label="OPTIMAL — Grid → Battery  (cheapest SMUD windows only)", zorder=4)

        # SOC on right axis
        ax2 = ax.twinx()
        ax2.plot(x, naive_soc * 100,   color="#bcbd22", linewidth=1.3,
                 linestyle="--", label="SOC  NAIVE (%)", alpha=0.75)
        ax2.plot(x, optimal_soc * 100, color="#2ca02c", linewidth=1.8,
                 label="SOC  OPTIMAL (%)", alpha=0.95)
        ax2.axhline(SOC_MIN * 100, color="red", linewidth=0.9, linestyle=":",
                    alpha=0.7, label=f"Minimum SOC ({SOC_MIN*100:.0f}%)")
        ax2.set_ylim(-5, 115)
        ax2.set_ylabel("Battery SOC (%)", fontsize=7.5)
        ax2.tick_params(axis="y", labelsize=7.5)

        ax.set_ylabel("Power (kW)", fontsize=8)
        ax.set_title(
            f"XOS Unit {k+1}  |  ▼ Battery discharging to vehicle   "
            f"▲ Grid recharging battery   —   (right axis) Battery SOC %",
            fontsize=8.5, pad=3, loc="left",
        )
        ax.set_ylim(-100, 95)
        ax.axhline(0, color="black", linewidth=0.5, alpha=0.4)
        ax.set_xticks(hour_ticks)
        ax.set_xticklabels(hour_labels, fontsize=8, rotation=45)
        ax.tick_params(axis="y", labelsize=8)
        ax.set_xlim(0, n_steps)
        ax.grid(axis="x", linestyle=":", alpha=0.25, color="gray")

        if k == 0:
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, loc="upper right",
                      fontsize=7.0, framealpha=0.95, ncol=2)

    # ── Panel C: SMUD rate ─────────────────────────────────────────────────────
    ax_rate = axes[1 + n_units]
    for ti in range(n_steps):
        col = RATE_COLORS.get(rates[ti], "#dddddd")
        ax_rate.bar(ti, rates[ti], width=1.0, color=col, alpha=0.90, edgecolor="none")

    ax_rate.axhline(C_NONSUMMER_OFFSAVER, color="#2ca02c", linewidth=1.2,
                    linestyle="--", alpha=0.85,
                    label=f"Cheapest — Off-saver ${C_NONSUMMER_OFFSAVER}/kWh "
                           f"(9 AM–4 PM, non-summer)")
    ax_rate.axhline(C_SUMMER_PEAK, color="#d62728", linewidth=1.2,
                    linestyle="--", alpha=0.85,
                    label=f"Most expensive — Summer peak ${C_SUMMER_PEAK}/kWh "
                           f"(4–9 PM, Jun–Sep)")
    ax_rate.set_ylabel("$/kWh", fontsize=8)
    ax_rate.set_title(
        "SMUD C&I 21–299 kW Energy Rate per 15-min step  "
        "(green = charge here, red = avoid charging here)",
        fontsize=9, pad=3, loc="left",
    )
    ax_rate.set_ylim(0, 0.30)
    ax_rate.set_xticks(hour_ticks)
    ax_rate.set_xticklabels(hour_labels, fontsize=8.5, rotation=45)
    ax_rate.set_xlabel("Time (Pacific local time)", fontsize=9)
    ax_rate.set_xlim(0, n_steps)
    ax_rate.legend(fontsize=7.5, loc="upper left", framealpha=0.9)
    ax_rate.grid(axis="x", linestyle=":", alpha=0.25)

    # Rate-colour key at bottom of figure
    rate_patches = [
        mpatches.Patch(facecolor=RATE_COLORS[r], alpha=0.75,
                       label=f"${r:.4f}/kWh  {lbl.replace(chr(10),' ')}")
        for r, lbl in RATE_LABELS.items()
    ]
    fig.legend(handles=rate_patches, loc="lower center", ncol=3,
               fontsize=7.5, framealpha=0.9, bbox_to_anchor=(0.5, -0.01),
               title="SMUD background colour key", title_fontsize=7.5)

    saving_pct = 100 * res["savings_usd"] / max(res["naive_cost_usd"], 1e-9)
    fig.suptitle(
        f"{site_label}  |  {date_str}  ({season})  |  "
        f"XOS Hub MC02  —  Grid-Recharging Cost Optimisation\n"
        f"NAIVE (charge every idle 15-min slot): ${res['naive_cost_usd']:.2f}/day   "
        f"→   OPTIMAL (defer to cheapest SMUD windows): ${res['optimal_cost_usd']:.2f}/day   "
        f"|   Saving: ${res['savings_usd']:.2f}  ({saving_pct:.0f}%)",
        fontsize=11, fontweight="bold", y=1.005,
    )

    plt.tight_layout(rect=[0, 0.04, 1, 1])
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out_path.name}")


# ── Full-year summary for all Northgate days ──────────────────────────────────

def run_all_northgate(sample_only: bool = False) -> pd.DataFrame:
    """Run cost analysis for every Northgate day and return annual summary."""
    all_csvs = sorted(BASE_DIR.glob("z2z_milp_events_northgate_*.csv"))
    if sample_only:
        # One per month for speed
        by_month: dict = {}
        for p in all_csvs:
            mon = p.stem.split("northgate_")[1][:7]   # YYYY_MM
            if mon not in by_month:
                by_month[mon] = p
        all_csvs = sorted(by_month.values())

    rows = []
    for csv_path in all_csvs:
        date_tag = csv_path.stem.split("northgate_")[1].replace("_", "-")
        print(f"\n  Analysing {date_tag} ...")
        try:
            res = analyse_day(csv_path, date_tag)
            if not res:
                continue
            rows.append({
                "date":              date_tag,
                "n_units":           res["n_units"],
                "n_vehicles":        res["n_vehicles"],
                "naive_cost_usd":    res["naive_cost_usd"],
                "optimal_cost_usd":  res["optimal_cost_usd"],
                "savings_usd":       res["savings_usd"],
                "naive_grid_kwh":    res["naive_grid_kwh"],
                "optimal_grid_kwh":  res["optimal_grid_kwh"],
                "demand_naive_kw":   res["demand_naive_kw"],
                "demand_optimal_kw": res["demand_optimal_kw"],
            })
        except Exception as exc:
            print(f"    [SKIP] {exc}")

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    out_csv = OUT_DIR / "xos_grid_charging_annual_northgate.csv"
    df.to_csv(out_csv, index=False)

    print(f"\n{'='*72}")
    print("  XOS GRID-CHARGING COST OPTIMISATION — NORTHGATE ANNUAL SUMMARY")
    print(f"{'='*72}")
    print(f"  Days analysed                  : {len(df)}")
    print(f"  Naive electricity cost (total) : ${df['naive_cost_usd'].sum():,.2f}")
    print(f"  Optimal electricity cost       : ${df['optimal_cost_usd'].sum():,.2f}")
    print(f"  Total savings                  : ${df['savings_usd'].sum():,.2f}")
    print(f"  Savings %                      : "
          f"{100*df['savings_usd'].sum()/max(df['naive_cost_usd'].sum(),1):.1f}%")
    print(f"  Avg naive cost/day             : ${df['naive_cost_usd'].mean():.3f}")
    print(f"  Avg optimal cost/day           : ${df['optimal_cost_usd'].mean():.3f}")
    print(f"  Avg daily saving               : ${df['savings_usd'].mean():.3f}")
    print(f"  Total naive grid energy        : {df['naive_grid_kwh'].sum():,.0f} kWh")
    print(f"  Total optimal grid energy      : {df['optimal_grid_kwh'].sum():,.0f} kWh")
    print()
    print(f"  Best charging windows (by season):")
    print(f"    Non-summer (Oct-May) : 9 AM–4 PM daily  @ ${C_NONSUMMER_OFFSAVER}/kWh  ← CHEAPEST")
    print(f"    Summer (Jun-Sep)     : Overnight/morning @ ${C_SUMMER_OFFPEAK}/kWh")
    print(f"    AVOID: Weekdays 4–9 PM (${C_NONSUMMER_PEAK}–${C_SUMMER_PEAK}/kWh)")
    print(f"  Full summary CSV: {out_csv}")
    print(f"{'='*72}\n")

    return df


# ── Plot annual cost comparison bar chart ──────────────────────────────────────

def plot_annual_summary(df: pd.DataFrame) -> None:
    if df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(15, 5))

    # Left: daily cost comparison
    ax = axes[0]
    x = np.arange(len(df))
    ax.bar(x, df["naive_cost_usd"], color="#bcbd22", alpha=0.7, label="Naive (charge every idle step)")
    ax.bar(x, df["optimal_cost_usd"], color="#2ca02c", alpha=0.85, label="Optimised (cheapest windows)")
    ax.set_xlabel("Day index")
    ax.set_ylabel("SMUD electricity cost ($/day)")
    ax.set_title("XOS Grid-Charging Cost per Day\n(Northgate, May 2025 – Apr 2026)")
    ax.legend(fontsize=9)
    ax.set_xlim(0, len(df))
    ax.grid(axis="y", linestyle=":", alpha=0.4)

    # Right: cumulative savings
    ax2 = axes[1]
    cum_naive   = df["naive_cost_usd"].cumsum()
    cum_optimal = df["optimal_cost_usd"].cumsum()
    ax2.fill_between(x, cum_naive, cum_optimal,
                     alpha=0.35, color="#d62728", label="Cumulative saving")
    ax2.plot(x, cum_naive,   color="#bcbd22", linewidth=2, label="Naive cumulative")
    ax2.plot(x, cum_optimal, color="#2ca02c", linewidth=2, label="Optimised cumulative")
    ax2.set_xlabel("Day index")
    ax2.set_ylabel("Cumulative electricity cost ($)")
    ax2.set_title(f"Cumulative SMUD Cost\nTotal saving: ${df['savings_usd'].sum():,.2f}")
    ax2.legend(fontsize=9)
    ax2.grid(axis="y", linestyle=":", alpha=0.4)

    plt.tight_layout()
    out = OUT_DIR / "xos_grid_charging_annual_summary.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Annual summary plot saved: {out.name}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("  XOS HUB MC02 — GRID-CHARGING COST OPTIMISATION (SMUD TOD)")
    print("=" * 72)

    all_northgate = "--all-northgate" in sys.argv

    if all_northgate:
        print("\n  Mode: full-year Northgate sweep\n")
        df = run_all_northgate(sample_only=False)
        plot_annual_summary(df)
        return

    # Single day — pick from command line or use a representative Northgate day
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        date_str = sys.argv[1]          # e.g. "2026-04-29"
        date_tag = date_str.replace("-", "_")
        csv_path = BASE_DIR / f"z2z_milp_events_northgate_{date_tag}.csv"
    else:
        # Default: April 29, 2026 — a typical busy non-summer weekday
        csv_path = BASE_DIR / "z2z_milp_events_northgate_2026_04_29.csv"
        date_str = "2026-04-29"

    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}")
        sys.exit(1)

    print(f"\n  Analysing: {csv_path.name}")
    res = analyse_day(csv_path, date_str)

    if not res:
        print("[ERROR] Analysis returned no results.")
        sys.exit(1)

    is_summer = pd.Timestamp(res["time_grid"][0]).tz_convert(SMUD_TZ).month in (6, 7, 8, 9)
    season    = "Summer" if is_summer else "Non-summer"

    print(f"\n  {'='*65}")
    print(f"  RESULTS  {date_str}  ({season})")
    print(f"  {'='*65}")
    print(f"  XOS units deployed          : {res['n_units']}")
    print(f"  Vehicles served             : {res['n_vehicles']}")
    print(f"  Time steps analysed         : {res['n_steps']} (15-min intervals)")
    print()
    print(f"  NAIVE schedule (charge every idle step):")
    print(f"    Total grid energy         : {res['naive_grid_kwh']:.1f} kWh")
    print(f"    Peak grid demand          : {res['demand_naive_kw']:.1f} kW")
    print(f"    Electricity cost          : ${res['naive_cost_usd']:.4f}")
    print()
    print(f"  OPTIMISED schedule (cheapest SMUD windows first):")
    print(f"    Total grid energy         : {res['optimal_grid_kwh']:.1f} kWh")
    print(f"    Peak grid demand          : {res['demand_optimal_kw']:.1f} kW")
    print(f"    Electricity cost          : ${res['optimal_cost_usd']:.4f}")
    print()
    print(f"  Daily saving                : ${res['savings_usd']:.4f}")
    pct = 100 * res['savings_usd'] / max(res['naive_cost_usd'], 1e-9)
    print(f"  Saving %                    : {pct:.1f}%")
    ann = res['savings_usd'] * 365
    print(f"  Annualised saving (×365)    : ${ann:.2f}")
    print()
    print(f"  Best windows for this day ({season}):")
    if is_summer:
        print(f"    ✓ Before 4 PM / after 9 PM  @ ${C_SUMMER_OFFPEAK}/kWh")
        print(f"    ✗ Avoid 4–9 PM              @ ${C_SUMMER_PEAK}/kWh")
    else:
        print(f"    ✓ 9 AM–4 PM (off-saver)     @ ${C_NONSUMMER_OFFSAVER}/kWh  ← cheapest")
        print(f"    ✓ Overnight / early morning @ ${C_NONSUMMER_OFFPEAK}/kWh")
        print(f"    ✗ Avoid weekday 4–9 PM      @ ${C_NONSUMMER_PEAK}/kWh")

    out_png = OUT_DIR / f"xos_grid_charging_{date_str.replace('-','_')}.png"
    plot_day(res, out_png, site_label="Northgate")
    print(f"\n  Plot saved: {out_png}")


if __name__ == "__main__":
    main()
