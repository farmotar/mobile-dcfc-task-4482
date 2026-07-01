"""
xos_mobile_charging_analysis.py
=================================
XOS Hub MC02 — remote-deployment model.

The XOS is treated as a MOBILE battery unit:
  1. Starts at 100% SOC (full charge at depot)
  2. Deployed to remote Caltrans site — NO grid available there
  3. Serves vehicles from its battery until SOC reaches ~25% floor
  4. Returns to depot (Northgate, SMUD-connected) for ONE bulk recharge
  5. Best recharge window = cheapest SMUD rate available after return

Key difference from stationary model:
  - No micro-charges between vehicle visits
  - Battery runs DOWN continuously while at site
  - One recharge block per trip (at depot)
  - Recharge takes ~2.7 h (210 kWh usable / 83 kW grid / 0.95 efficiency)

SMUD C&I 21-299 kW rates (Northgate depot):
  Non-summer 9 AM-4 PM  : $0.0888/kWh  ← cheapest (OFF-SAVER)
  Non-summer off-peak   : $0.1264/kWh
  Non-summer 4-9 PM     : $0.1477/kWh
  Summer off-peak       : $0.1215/kWh
  Summer 4-9 PM         : $0.2341/kWh  ← most expensive
"""

from __future__ import annotations
import sys, importlib
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT_DIR  = BASE_DIR / "xos_mobile_outputs"
OUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
xos = importlib.import_module("xos_hub_soc_simulation")

# ── XOS specs ──────────────────────────────────────────────────────────────────
B_KWH    = xos.B_KWH      # 280 kWh nominal
SOC_MIN  = xos.SOC_MIN    # 0.20 → 20%  (floor — return to depot at this point)
SOC_MAX  = xos.SOC_MAX    # 1.00 → 100%
ETA_C    = xos.ETA_C      # 0.95 charge efficiency
ETA_D    = xos.ETA_D      # 0.95 discharge efficiency
P_GRID   = xos.P_GRID_KW  # 83 kW grid input at depot
DT_H     = xos.DT_H       # 0.25 h per step

USABLE_KWH     = (SOC_MAX - SOC_MIN) * B_KWH      # 224 kWh usable per trip
RECHARGE_H     = USABLE_KWH / (P_GRID * ETA_C)    # ≈ 2.85 h to recharge fully
RECHARGE_STEPS = int(np.ceil(RECHARGE_H / DT_H))  # steps needed for full recharge

# ── SMUD rates ─────────────────────────────────────────────────────────────────
SMUD_TZ              = "America/Los_Angeles"
C_SUMMER_PEAK        = 0.2341
C_SUMMER_OFFPEAK     = 0.1215
C_NONSUMMER_PEAK     = 0.1477
C_NONSUMMER_OFFSAVER = 0.0888
C_NONSUMMER_OFFPEAK  = 0.1264

RATE_COLORS = {
    C_SUMMER_PEAK:        "#d62728",
    C_SUMMER_OFFPEAK:     "#ffaaaa",
    C_NONSUMMER_PEAK:     "#ff7f0e",
    C_NONSUMMER_OFFSAVER: "#2ca02c",
    C_NONSUMMER_OFFPEAK:  "#98df8a",
}

RATE_LABELS = {
    C_SUMMER_PEAK:        "Summer peak 4-9 PM ($0.2341/kWh) — AVOID",
    C_SUMMER_OFFPEAK:     "Summer off-peak ($0.1215/kWh)",
    C_NONSUMMER_PEAK:     "Non-summer peak 4-9 PM ($0.1477/kWh) — AVOID",
    C_NONSUMMER_OFFSAVER: "Non-summer off-saver 9 AM-4 PM ($0.0888/kWh) — CHEAPEST",
    C_NONSUMMER_OFFPEAK:  "Non-summer off-peak ($0.1264/kWh)",
}


def smud_rate(t_utc: pd.Timestamp) -> float:
    t_loc  = t_utc.tz_convert(SMUD_TZ)
    hour   = t_loc.hour + t_loc.minute / 60.0
    summer = t_loc.month in (6, 7, 8, 9)
    wday   = t_loc.weekday() < 5
    peak   = wday and 16 <= hour < 21
    saver  = (not summer) and 9 <= hour < 16
    if summer:
        return C_SUMMER_PEAK if peak else C_SUMMER_OFFPEAK
    return C_NONSUMMER_PEAK if peak else (C_NONSUMMER_OFFSAVER if saver else C_NONSUMMER_OFFPEAK)


def _apply_extended_dwell(events: pd.DataFrame) -> pd.DataFrame:
    """Extend departure times so every vehicle has enough window to fully charge."""
    events = events.copy()
    for idx, row in events.iterrows():
        e_need  = float(row["energy_needed_kwh_for_visit"])
        arr     = row["arrival_time"]
        dep     = row["departure_time"]
        dwell_h = (dep - arr).total_seconds() / 3600.0
        mdc     = float(row.get("max_dc_charge_kw", 0) or 0)
        p_eff_v = min(xos.P_PORT_KW, mdc) if mdc > 0 else xos.P_PORT_KW
        req_h   = e_need / (p_eff_v * ETA_D)
        extra_h = max(0.0, req_h - dwell_h)
        if extra_h > 1e-6:
            events.at[idx, "departure_time"] = dep + pd.Timedelta(hours=extra_h)
    return events


def simulate_mobile(csv_path: Path) -> dict:
    """
    Run the XOS simulation WITHOUT intermediate grid charging.
    Battery runs down continuously while serving vehicles.
    Returns per-unit SOC trajectories and vehicle service info.
    """
    raw    = xos.load_events(csv_path)
    events = _apply_extended_dwell(raw)
    p_eff  = xos.compute_p_eff(events)

    # Determine minimum units needed (still need to know how many units to deploy)
    n_units, result = xos.find_min_xos_units(events, p_eff, verbose=False)

    soc_hist = result["soc_history"]
    disp_log = result["dispatch_log"]
    if not soc_hist:
        return {}

    time_grid = pd.DatetimeIndex([pd.Timestamp(r["time_utc"]) for r in soc_hist])
    n_steps   = len(time_grid)
    rates     = np.array([smud_rate(t) for t in time_grid])

    # Build discharge array per unit per step (energy drawn from battery)
    discharge = np.zeros((n_units, n_steps))
    for entry in disp_log:
        k  = entry["unit"]
        ti = entry["step_idx"]
        discharge[k, ti] += entry["energy_to_vehicle_kwh"] / ETA_D

    # ── Mobile SOC trajectory (NO intermediate charging) ──────────────────────
    # Battery runs down from 100%. No recharging until a full recharge event.
    # A "recharge event" is scheduled when SOC hits SOC_MIN.
    # Naive recharge: immediately at the moment SOC hits floor.
    # Optimal recharge: wait for cheapest SMUD window.

    mobile_soc     = np.ones((n_units, n_steps)) * SOC_MAX
    recharge_events = []   # list of dicts per unit

    for k in range(n_units):
        soc       = SOC_MAX
        recharging = False
        rch_steps_left = 0
        rch_start  = None
        rch_rate   = None

        for ti in range(n_steps):
            # If recharging: add grid energy
            if recharging:
                room    = (SOC_MAX - soc) * B_KWH
                add_b   = min(P_GRID * ETA_C * DT_H, room)
                soc    += add_b / B_KWH
                soc     = min(soc, SOC_MAX)
                rch_steps_left -= 1
                if rch_steps_left <= 0 or soc >= SOC_MAX - 1e-4:
                    recharging = False
            else:
                # Discharge from vehicle service
                soc -= discharge[k, ti] / B_KWH
                soc  = max(soc, 0.0)

                # Hit floor → needs recharge
                if soc <= SOC_MIN + 1e-4 and any(discharge[k, ti2] > 1e-4
                                                   for ti2 in range(ti + 1, n_steps)):
                    # Start recharging immediately (naive)
                    recharge_events.append({
                        "unit":       k,
                        "start_step": ti + 1,
                        "rate":       rates[ti + 1] if ti + 1 < n_steps else rates[ti],
                        "kwh":        (SOC_MAX - soc) * B_KWH / ETA_C,
                    })
                    recharging     = True
                    rch_steps_left = RECHARGE_STEPS
                    rch_start      = ti + 1

            mobile_soc[k, ti] = soc

    # ── Optimal recharge: wait for cheapest SMUD window ───────────────────────
    # For each unit, identify windows when it NEEDS recharge (SOC would drop to floor),
    # then shift the recharge to start at the cheapest rate window.

    optimal_soc    = np.ones((n_units, n_steps)) * SOC_MAX
    opt_recharge   = []

    RATE_ORDER = sorted(set(rates), key=lambda r: r)   # cheapest first

    for k in range(n_units):
        soc        = SOC_MAX
        recharging = False
        rch_left   = 0
        needs_rch  = False   # flag: unit needs recharge ASAP
        rch_soc_at_floor = SOC_MAX

        for ti in range(n_steps):
            if recharging:
                room   = (SOC_MAX - soc) * B_KWH
                add_b  = min(P_GRID * ETA_C * DT_H, room)
                soc   += add_b / B_KWH
                soc    = min(soc, SOC_MAX)
                rch_left -= 1
                if rch_left <= 0 or soc >= SOC_MAX - 1e-4:
                    recharging = False
                    needs_rch  = False
            elif needs_rch:
                # Wait for a cheap rate window before starting recharge
                # Start if: this is a cheap window AND no vehicles coming in next few steps
                vehicles_soon = any(discharge[k, ti2] > 1e-4
                                    for ti2 in range(ti, min(ti + RECHARGE_STEPS, n_steps)))
                cheap = rates[ti] <= C_NONSUMMER_OFFSAVER * 1.05 or (
                    not (16 <= pd.Timestamp(time_grid[ti]).tz_convert(SMUD_TZ).hour < 21))
                if cheap or not vehicles_soon:
                    opt_recharge.append({
                        "unit":       k,
                        "start_step": ti,
                        "rate":       rates[ti],
                        "kwh":        (SOC_MAX - soc) * B_KWH / ETA_C,
                    })
                    recharging = True
                    rch_left   = RECHARGE_STEPS
            else:
                soc -= discharge[k, ti] / B_KWH
                soc  = max(soc, 0.0)
                if soc <= SOC_MIN + 1e-4:
                    needs_rch        = True
                    rch_soc_at_floor = soc

            optimal_soc[k, ti] = soc

    return {
        "events":          events,
        "disp_log":        disp_log,
        "delivered":       result["delivered"],
        "remaining":       result["remaining"],
        "n_units":         n_units,
        "n_vehicles":      result["n_total"],
        "time_grid":       time_grid,
        "rates":           rates,
        "n_steps":         n_steps,
        "discharge":       discharge,
        "mobile_soc":      mobile_soc,
        "optimal_soc":     optimal_soc,
        "recharge_events": recharge_events,
        "opt_recharge":    opt_recharge,
    }


def plot_mobile(res: dict, out_path: Path, date_str: str,
                site_label: str = "Northgate") -> None:
    """
    Clean three-panel plot for mobile XOS deployment:

    Panel A — Vehicle schedule (Gantt, one row per vehicle)
    Panel B — Battery SOC per unit (smooth line, no oscillations)
              Shows: battery running DOWN while serving, then ONE recharge block
    Panel C — SMUD rate at each 15-min step (green = charge here, red = avoid)
    """
    events    = res["events"]
    disp_log  = res["disp_log"]
    delivered = res["delivered"]
    remaining = res["remaining"]
    n_units   = res["n_units"]
    time_grid = res["time_grid"]
    rates     = res["rates"]
    n_steps   = res["n_steps"]
    discharge = res["discharge"]
    mob_soc   = res["mobile_soc"]
    opt_soc   = res["optimal_soc"]
    rch_ev    = res["recharge_events"]
    opt_rch   = res["opt_recharge"]

    tz_loc      = SMUD_TZ
    times_local = pd.DatetimeIndex(time_grid).tz_convert(tz_loc)
    is_summer   = times_local[0].month in (6, 7, 8, 9)
    season      = "Summer" if is_summer else "Non-summer"

    veh   = events.copy().sort_values("arrival_time").reset_index(drop=True)
    n_veh = len(veh)
    disp_df = (pd.DataFrame(disp_log) if disp_log
               else pd.DataFrame(columns=["step_idx","unit","event_id","energy_to_vehicle_kwh"]))

    UNIT_COLORS = [
        "#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
        "#8c564b","#e377c2","#17becf","#bcbd22","#7f7f7f",
    ]

    x = np.arange(n_steps)
    hour_ticks  = [i for i in range(n_steps) if times_local[i].minute == 0]
    hour_labels = [times_local[i].strftime("%H:%M") for i in hour_ticks]

    # ── Compute costs ──────────────────────────────────────────────────────────
    # Naive: recharge immediately when battery hits floor
    naive_cost = sum(ev["kwh"] * ev["rate"] for ev in rch_ev)
    # Optimal: recharge at cheapest window
    opt_cost   = sum(ev["kwh"] * ev["rate"] for ev in opt_rch)
    saving     = naive_cost - opt_cost

    # ── Layout: vehicle panel + SOC panel + rate panel ─────────────────────────
    veh_h   = max(4.0, n_veh * 0.55)
    soc_h   = max(3.5, n_units * 2.2)
    heights = [veh_h, soc_h, 1.8]
    fig, axes = plt.subplots(
        3, 1,
        figsize=(20, sum(heights) + 1.5),
        sharex=True,
        gridspec_kw={"height_ratios": heights},
    )

    # ── Panel A: Vehicle schedule ──────────────────────────────────────────────
    ax_v = axes[0]

    def ts_to_step(ts):
        if time_grid.tz is not None and ts.tz is None:
            ts = ts.tz_localize("UTC")
        elif time_grid.tz is None and ts.tz is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        diffs = np.abs((time_grid - ts).total_seconds())
        ci = int(np.argmin(diffs))
        if ci < n_steps - 1:
            span = (time_grid[ci + 1] - time_grid[ci]).total_seconds()
            frac = (ts - time_grid[ci]).total_seconds() / span
            return ci + max(0.0, min(1.0, frac))
        return float(ci)

    for vi, row in veh.iterrows():
        eid   = row["charging_event_id"]
        arr_s = ts_to_step(row["arrival_time"])
        dep_s = ts_to_step(row["departure_time"])
        e_req = float(row["energy_needed_kwh_for_visit"])
        model = str(row.get("ev_equivalent_model", "") or "").split(" (")[0][:26]
        is_served = remaining.get(eid, e_req) <= 1e-3

        # Grey parking window
        ax_v.barh(vi, max(dep_s - arr_s, 0.5), left=arr_s, height=0.72,
                  color="#dddddd", alpha=0.6, edgecolor="#aaaaaa", linewidth=0.5, zorder=1)

        if not disp_df.empty and eid in disp_df["event_id"].values:
            ev_d = disp_df[disp_df["event_id"] == eid].sort_values("step_idx")
            u    = int(ev_d["unit"].mode().iloc[0])
            uc   = UNIT_COLORS[u % len(UNIT_COLORS)]
            steps = sorted(ev_d["step_idx"].tolist())
            if steps:
                s0, sp = steps[0], steps[0]
                for s in steps[1:] + [steps[-1] + 2]:
                    if s > sp + 1:
                        ax_v.barh(vi, sp - s0 + 1, left=s0, height=0.72,
                                  color=uc, alpha=0.90, edgecolor="white",
                                  linewidth=0.3, zorder=3)
                        s0 = s
                    sp = s
            unit_lbl = f"Unit {u+1}"
        else:
            unit_lbl = "unserved"
            ax_v.barh(vi, max(dep_s - arr_s, 0.5), left=arr_s, height=0.72,
                      color="#d62728", alpha=0.28, edgecolor="#d62728",
                      linewidth=0.6, zorder=2)

        lbl = f"{model}  [{e_req:.0f} kWh | {unit_lbl}]"
        ax_v.text(arr_s + 0.4, vi, lbl, va="center", ha="left",
                  fontsize=6.8, color="black", zorder=5)

    for ti in range(n_steps - 1):
        ax_v.axvspan(ti, ti + 1, color=RATE_COLORS.get(rates[ti], "#eeeeee"),
                     alpha=0.07, linewidth=0)

    ax_v.set_yticks(range(n_veh))
    ax_v.set_yticklabels([f"V{i+1}" for i in range(n_veh)], fontsize=7)
    ax_v.set_ylim(-0.6, n_veh - 0.3)
    ax_v.invert_yaxis()
    ax_v.set_title(
        f"Vehicle Schedule  —  {n_veh} vehicles  "
        f"(grey = parked at site, coloured = XOS actively charging vehicle, colour = unit ID)",
        fontsize=9.5, pad=4, loc="left"
    )
    unit_patches = [mpatches.Patch(color=UNIT_COLORS[k % len(UNIT_COLORS)],
                                    label=f"Unit {k+1}") for k in range(n_units)]
    unit_patches.append(mpatches.Patch(color="#d62728", alpha=0.4, label="Unserved"))
    ax_v.legend(handles=unit_patches, loc="upper right",
                fontsize=7.5, framealpha=0.9, ncol=min(n_units + 1, 7))

    # ── Panel B: Battery SOC (smooth lines, one per unit) ─────────────────────
    ax_s = axes[1]

    for ti in range(n_steps - 1):
        ax_s.axvspan(ti, ti + 1, color=RATE_COLORS.get(rates[ti], "#eeeeee"),
                     alpha=0.12, linewidth=0)

    # Floor line
    ax_s.axhline(SOC_MIN * 100, color="red", linewidth=1.5, linestyle="--",
                 alpha=0.8, label=f"Return-to-depot threshold ({SOC_MIN*100:.0f}% SOC)")
    ax_s.axhline(100, color="#aaaaaa", linewidth=0.8, linestyle=":", alpha=0.5,
                 label="Full charge (100%)")

    for k in range(n_units):
        uc = UNIT_COLORS[k % len(UNIT_COLORS)]

        # Naive SOC: runs down, then jumps up immediately at floor
        ax_s.plot(x, mob_soc[k] * 100, color=uc, linewidth=1.5,
                  linestyle="--", alpha=0.55,
                  label=f"Unit {k+1} SOC — NAIVE (charge immediately at floor)")

        # Optimal SOC: runs down, waits for cheap window, then charges
        ax_s.plot(x, opt_soc[k] * 100, color=uc, linewidth=2.2,
                  alpha=0.95,
                  label=f"Unit {k+1} SOC — OPTIMAL (charge at cheapest SMUD window)")

        # Mark naive recharge events for this unit
        for ev in rch_ev:
            if ev["unit"] == k:
                si = ev["start_step"]
                ei = min(si + RECHARGE_STEPS, n_steps - 1)
                ax_s.axvspan(si, ei, color="#bcbd22", alpha=0.25, linewidth=0, zorder=1)

        # Mark optimal recharge events for this unit
        for ev in opt_rch:
            if ev["unit"] == k:
                si = ev["start_step"]
                ei = min(si + RECHARGE_STEPS, n_steps - 1)
                ax_s.axvspan(si, ei, color="#2ca02c", alpha=0.30, linewidth=0, zorder=1)

    ax_s.set_ylabel("Battery SOC (%)", fontsize=9)
    ax_s.set_ylim(-5, 112)
    ax_s.set_yticks([0, 20, 40, 60, 80, 100])
    ax_s.set_xlim(0, n_steps)
    ax_s.set_xticks(hour_ticks)
    ax_s.set_xticklabels(hour_labels, fontsize=8, rotation=45)
    ax_s.tick_params(axis="y", labelsize=8.5)
    ax_s.grid(axis="x", linestyle=":", alpha=0.25, color="gray")
    ax_s.grid(axis="y", linestyle=":", alpha=0.20, color="gray")

    rch_naive_patch  = mpatches.Patch(color="#bcbd22", alpha=0.5,
                                       label="NAIVE recharge window (immediate)")
    rch_opt_patch    = mpatches.Patch(color="#2ca02c", alpha=0.5,
                                       label="OPTIMAL recharge window (cheapest SMUD rate)")
    floor_line       = mpatches.Patch(color="red", alpha=0.6,
                                       label=f"Floor: return to depot at {SOC_MIN*100:.0f}% SOC")

    handles, labels = ax_s.get_legend_handles_labels()
    ax_s.legend(handles + [rch_naive_patch, rch_opt_patch, floor_line],
                labels + ["NAIVE recharge window", "OPTIMAL recharge window",
                           f"Floor: return to depot at {SOC_MIN*100:.0f}% SOC"],
                loc="lower right", fontsize=7.5, framealpha=0.95, ncol=2)

    ax_s.set_title(
        "XOS Battery State of Charge (%)  —  No grid at remote site; "
        "battery runs down continuously while serving vehicles\n"
        "Yellow band = NAIVE recharge (depot, immediately)    "
        "Green band = OPTIMAL recharge (depot, cheapest SMUD window)",
        fontsize=9.5, pad=4, loc="left",
    )

    # Cost annotation box
    cost_txt = (
        f"RECHARGE ELECTRICITY COST\n"
        f"Naive (charge right away): ${naive_cost:.2f}\n"
        f"Optimal (wait for cheap window): ${opt_cost:.2f}\n"
        f"Daily saving: ${saving:.2f}"
    )
    ax_s.text(0.01, 0.97, cost_txt, transform=ax_s.transAxes,
              fontsize=8.5, va="top", ha="left",
              bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                        edgecolor="#2ca02c", linewidth=1.2, alpha=0.92))

    # ── Panel C: SMUD rate ─────────────────────────────────────────────────────
    ax_r = axes[2]
    for ti in range(n_steps):
        col = RATE_COLORS.get(rates[ti], "#dddddd")
        ax_r.bar(ti, rates[ti], width=1.0, color=col, alpha=0.90, edgecolor="none")

    ax_r.axhline(C_NONSUMMER_OFFSAVER, color="#2ca02c", linewidth=1.3, linestyle="--",
                 alpha=0.85, label=f"Cheapest: $0.0888/kWh  (9 AM-4 PM, non-summer)")
    ax_r.axhline(C_SUMMER_PEAK, color="#d62728", linewidth=1.3, linestyle="--",
                 alpha=0.85, label=f"Most expensive: $0.2341/kWh  (4-9 PM, Jun-Sep)")

    ax_r.set_ylabel("$/kWh", fontsize=8)
    ax_r.set_title(
        "SMUD C&I Depot Rate  (green background = recharge the XOS here, "
        "red/orange = avoid — most expensive)",
        fontsize=9, pad=3, loc="left"
    )
    ax_r.set_ylim(0, 0.30)
    ax_r.set_xticks(hour_ticks)
    ax_r.set_xticklabels(hour_labels, fontsize=8.5, rotation=45)
    ax_r.set_xlabel("Time (Pacific local time)", fontsize=9)
    ax_r.set_xlim(0, n_steps)
    ax_r.legend(fontsize=8, loc="upper right", framealpha=0.9)
    ax_r.grid(axis="x", linestyle=":", alpha=0.25)

    # Rate key at bottom
    patches = [
        mpatches.Patch(facecolor=RATE_COLORS[r], alpha=0.80,
                       label=RATE_LABELS[r])
        for r in RATE_COLORS
    ]
    fig.legend(handles=patches, loc="lower center", ncol=2,
               fontsize=7.5, framealpha=0.9, bbox_to_anchor=(0.5, -0.02),
               title="SMUD rate colour key", title_fontsize=7.5)

    saving_pct = 100 * saving / max(naive_cost, 1e-9)
    fig.suptitle(
        f"{site_label}  |  {date_str}  ({season})  |  "
        f"XOS Hub MC02  —  Mobile Deployment, Remote Site  (no grid at site)\n"
        f"Battery drains from 100% → 20% serving vehicles, then ONE bulk recharge at SMUD depot\n"
        f"NAIVE recharge cost: ${naive_cost:.2f}   →   "
        f"OPTIMAL recharge cost: ${opt_cost:.2f}   "
        f"|   Daily saving: ${saving:.2f}  ({saving_pct:.0f}%)",
        fontsize=11, fontweight="bold", y=1.005,
    )

    plt.tight_layout(rect=[0, 0.05, 1, 1])
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out_path.name}")


def main():
    print("=" * 72)
    print("  XOS HUB MC02 — MOBILE DEPLOYMENT COST ANALYSIS")
    print("=" * 72)

    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        date_str = sys.argv[1]
    else:
        date_str = "2026-04-29"

    date_tag = date_str.replace("-", "_")
    csv_path = BASE_DIR / f"z2z_milp_events_northgate_{date_tag}.csv"

    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}"); return

    print(f"\n  Analysing: {csv_path.name}")
    res = simulate_mobile(csv_path)
    if not res:
        print("[ERROR] Simulation returned no results."); return

    # Cost summary
    naive_cost = sum(ev["kwh"] * ev["rate"] for ev in res["recharge_events"])
    opt_cost   = sum(ev["kwh"] * ev["rate"] for ev in res["opt_recharge"])
    saving     = naive_cost - opt_cost

    times_local = pd.DatetimeIndex(res["time_grid"]).tz_convert(SMUD_TZ)
    is_summer   = times_local[0].month in (6, 7, 8, 9)
    season      = "Summer" if is_summer else "Non-summer"

    print(f"\n  {'='*65}")
    print(f"  {date_str}  ({season})")
    print(f"  {'='*65}")
    print(f"  XOS units deployed  : {res['n_units']}")
    print(f"  Vehicles served     : {res['n_vehicles']}")
    print(f"  Usable battery/unit : {USABLE_KWH:.0f} kWh  (20%→100% SOC)")
    print(f"  Recharge time/unit  : {RECHARGE_H:.1f} h  at {P_GRID} kW grid input")
    print()
    print(f"  NAIVE  — recharge at depot the moment battery hits 20%:")
    for ev in res["recharge_events"]:
        t_loc = pd.Timestamp(res["time_grid"][ev["start_step"]]).tz_convert(SMUD_TZ)
        print(f"    Unit {ev['unit']+1}: {t_loc.strftime('%H:%M')} local  "
              f"  {ev['kwh']:.0f} kWh  @  ${ev['rate']:.4f}/kWh  "
              f"= ${ev['kwh']*ev['rate']:.2f}")
    print(f"  Total naive recharge cost : ${naive_cost:.2f}")
    print()
    print(f"  OPTIMAL — wait for cheapest SMUD window:")
    for ev in res["opt_recharge"]:
        t_loc = pd.Timestamp(res["time_grid"][ev["start_step"]]).tz_convert(SMUD_TZ)
        print(f"    Unit {ev['unit']+1}: {t_loc.strftime('%H:%M')} local  "
              f"  {ev['kwh']:.0f} kWh  @  ${ev['rate']:.4f}/kWh  "
              f"= ${ev['kwh']*ev['rate']:.2f}")
    print(f"  Total optimal recharge cost: ${opt_cost:.2f}")
    print()
    print(f"  Daily saving        : ${saving:.2f}  ({100*saving/max(naive_cost,1e-9):.0f}%)")
    print(f"  Annualised (×365)   : ${saving*365:,.0f}")
    print()
    print(f"  Best recharge windows ({season}):")
    if is_summer:
        print(f"    ✓ Before 4 PM or after 9 PM  @ ${C_SUMMER_OFFPEAK}/kWh")
        print(f"    ✗ AVOID 4-9 PM               @ ${C_SUMMER_PEAK}/kWh")
    else:
        print(f"    ✓ 9 AM–4 PM (off-saver)      @ ${C_NONSUMMER_OFFSAVER}/kWh  ← CHEAPEST")
        print(f"    ✓ Overnight                  @ ${C_NONSUMMER_OFFPEAK}/kWh")
        print(f"    ✗ AVOID weekday 4-9 PM       @ ${C_NONSUMMER_PEAK}/kWh")

    out_png = OUT_DIR / f"xos_mobile_{date_str.replace('-','_')}.png"
    plot_mobile(res, out_png, date_str, site_label="Northgate")
    print(f"\n  Plot: {out_png}")


if __name__ == "__main__":
    main()
