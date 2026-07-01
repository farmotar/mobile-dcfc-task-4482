"""
xos_trip_simulation.py
=======================
Physically correct simulation for XOS Hub MC02 units deployed at Northgate.

Each XOS unit cycles between two states:
  "serving"    — actively discharging to vehicles via CCS1 ports.
                 NOT connected to grid. SOC drops. Up to 4 vehicles at once.
  "recharging" — connected to SMUD grid, recharging battery back to 100%.
                 Cannot serve vehicles during this time.

Rules:
  - All XOS units stay at Northgate (they do NOT travel to remote sites).
  - Vehicles come to Northgate and plug in to an XOS port.
  - All ports charge at the same fixed rate: 80 kW (CCS1) × 0.95 η = 76 kW to vehicle.
  - Once a vehicle is assigned to a port, charging is CONTINUOUS until either:
      (a) the vehicle's energy need is fully met, or
      (b) the unit's SOC hits 20% → unit switches to recharging mode.
  - When a unit goes to recharging, all its vehicles are released and must
    wait for another serving unit to have a free port.
  - SOC is physically clamped: never below 20% while serving, rises to 100% when recharging.
  - SCHEDULER RULE: fill existing active units (units already serving vehicles)
    before starting idle units. This keeps unit count minimal.
"""

from __future__ import annotations
import sys, importlib
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.cm as cm

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT_DIR  = BASE_DIR / "xos_trip_outputs"
OUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
xos    = importlib.import_module("xos_hub_soc_simulation")
mobile = importlib.import_module("xos_mobile_charging_analysis")

# XOS constants
B_KWH          = xos.B_KWH
SOC_MIN        = xos.SOC_MIN
SOC_MAX        = xos.SOC_MAX
P_GRID         = xos.P_GRID_KW
P_PORT         = xos.P_PORT_KW       # 80 kW per CCS1 port
ETA_C          = xos.ETA_C
ETA_D          = xos.ETA_D
DT_H           = xos.DT_H
N_PORTS        = xos.N_PORTS
ENERGY_TOL     = xos.ENERGY_TOL
MAX_UNITS      = xos.MAX_UNITS
USABLE_KWH     = (SOC_MAX - SOC_MIN) * B_KWH          # 224 kWh usable per charge cycle
RECHARGE_STEPS = int(np.ceil(USABLE_KWH / (P_GRID * ETA_C * DT_H)))  # ~12 steps = 3 h

SMUD_TZ   = mobile.SMUD_TZ
smud_rate = mobile.smud_rate

_apply_extended_dwell = mobile._apply_extended_dwell


# ── Simulation ────────────────────────────────────────────────────────────────

def simulate_xos(csv_path: Path, n_units: int | None = None,
                 verbose: bool = False) -> dict:
    """
    Run the XOS simulation at Northgate.

    Scheduler priority (reduces unit count):
      1. Fill ports on units already serving at least one vehicle.
      2. Only activate an idle unit when all active units are full.
    """
    raw    = xos.load_events(csv_path)
    events = _apply_extended_dwell(raw)
    p_eff  = xos.compute_p_eff(events)   # used only for DC-compatibility check
    ev_ids = events["charging_event_id"].tolist()

    def _run(K: int) -> dict:
        remaining: Dict[str, float] = {}
        delivered: Dict[str, float] = {}
        ev_info:   Dict[str, dict]  = {}
        for _, row in events.iterrows():
            v = row["charging_event_id"]
            remaining[v] = float(row["energy_needed_kwh_for_visit"])
            delivered[v] = 0.0
            ev_info[v]   = {"arr": row["arrival_time"],
                            "dep": row["departure_time"]}

        soc           = [SOC_MAX] * K
        unit_state    = ["serving"] * K      # "serving" or "recharging"
        rech_remain   = [0] * K              # steps left in recharge cycle
        port_assign: List[List[Optional[str]]] = [
            [None] * N_PORTS for _ in range(K)
        ]

        t_start    = events["arrival_time"].min().floor("15min")
        t_end      = events["departure_time"].max().ceil("15min") + pd.Timedelta(hours=8)
        time_steps = pd.date_range(t_start, t_end, freq="15min", tz="UTC")
        n_steps    = len(time_steps)

        soc_history:  List[dict] = []
        dispatch_log: List[dict] = []

        for ti, t in enumerate(time_steps):
            t_next = t + pd.Timedelta(hours=DT_H)

            # ── A. Recharging units: add grid power, return to serving when full ──
            for k in range(K):
                if unit_state[k] == "recharging":
                    room   = (SOC_MAX - soc[k]) * B_KWH
                    add_b  = min(P_GRID * ETA_C * DT_H, room)
                    soc[k] += add_b / B_KWH
                    soc[k]  = min(soc[k], SOC_MAX)
                    rech_remain[k] -= 1
                    if rech_remain[k] <= 0 or soc[k] >= SOC_MAX - 1e-4:
                        soc[k]          = SOC_MAX
                        unit_state[k]   = "serving"
                        rech_remain[k]  = 0
                        if verbose:
                            tl = t.tz_convert(SMUD_TZ)
                            print(f"  {tl.strftime('%H:%M')} Unit {k+1} back to serving (SOC=100%)")

            # ── B. Release ports: vehicle done or dwell expired ────────────────
            for k in range(K):
                if unit_state[k] == "serving":
                    for p in range(N_PORTS):
                        v = port_assign[k][p]
                        if v and (remaining[v] <= ENERGY_TOL or ev_info[v]["dep"] <= t):
                            port_assign[k][p] = None

            # ── C. Serving units at 20% SOC → switch to recharging ────────────
            for k in range(K):
                if unit_state[k] == "serving" and soc[k] <= SOC_MIN + 1e-6:
                    for p in range(N_PORTS):
                        port_assign[k][p] = None   # release all vehicles
                    unit_state[k]  = "recharging"
                    rech_remain[k] = RECHARGE_STEPS
                    if verbose:
                        tl = t.tz_convert(SMUD_TZ)
                        print(f"  {tl.strftime('%H:%M')} Unit {k+1} → recharging (SOC hit 20%)")

            # ── D. Assign waiting vehicles (smart scheduler) ──────────────────
            already = {v for k in range(K)
                       for v in port_assign[k] if v is not None}
            waiting: List[tuple] = []
            for v in ev_ids:
                if (v not in already
                        and remaining[v] > ENERGY_TOL
                        and p_eff.get(v, 0) > 0
                        and ev_info[v]["arr"] < t_next
                        and ev_info[v]["dep"] > t):
                    tl_h = max((ev_info[v]["dep"] - t).total_seconds() / 3600, DT_H)
                    waiting.append((remaining[v] / tl_h, v))
            waiting.sort(reverse=True)   # most urgent first

            for _, v in waiting:
                placed = False

                # Prefer units with FEWER vehicles already (spread load evenly).
                # This ensures each vehicle gets ~76 kW dedicated rate rather than
                # draining a shared unit 4x faster and hitting 20% mid-session.
                def _sort_key(i):
                    n_active = sum(1 for p in range(N_PORTS) if port_assign[i][p])
                    return (n_active, -soc[i])    # fewer active → first; then higher SOC

                for k in sorted(range(K), key=_sort_key):
                    if unit_state[k] != "serving" or soc[k] <= SOC_MIN + 1e-6:
                        continue
                    for p in range(N_PORTS):
                        if port_assign[k][p] is None:
                            port_assign[k][p] = v
                            placed = True
                            break
                    if placed:
                        break

            # ── E. Serve vehicles on active ports (constant 80 kW per port) ──
            for k in range(K):
                if unit_state[k] != "serving":
                    continue
                for p in range(N_PORTS):
                    v = port_assign[k][p]
                    if v is None:
                        continue

                    usable = (soc[k] - SOC_MIN) * B_KWH * ETA_D
                    if usable < ENERGY_TOL:
                        port_assign[k][p] = None
                        continue

                    eff_h = (min(t_next, ev_info[v]["dep"])
                             - max(t, ev_info[v]["arr"])).total_seconds() / 3600.0
                    e_del = min(P_PORT * eff_h * ETA_D, remaining[v], usable)
                    if e_del < ENERGY_TOL:
                        continue

                    soc_b    = soc[k]
                    soc[k]  -= e_del / (ETA_D * B_KWH)
                    soc[k]   = max(soc[k], SOC_MIN)

                    delivered[v] += e_del
                    remaining[v]  = max(remaining[v] - e_del, 0.0)

                    dispatch_log.append({
                        "step_idx":              ti,
                        "time_utc":              t.isoformat(),
                        "unit":                  k,
                        "port":                  p,
                        "event_id":              v,
                        "soc_before":            round(soc_b, 4),
                        "soc_after":             round(soc[k], 4),
                        "energy_to_vehicle_kwh": round(e_del, 4),
                    })

            # ── F. Record state ───────────────────────────────────────────────
            row_s: dict = {"step_idx": ti, "time_utc": t.isoformat()}
            for k in range(K):
                row_s[f"soc_unit_{k}"]   = round(soc[k], 4)
                row_s[f"state_unit_{k}"] = unit_state[k]
            soc_history.append(row_s)

        n_served = sum(1 for v in ev_ids if remaining[v] <= ENERGY_TOL)
        return {
            "n_units":     K,
            "n_vehicles":  len(ev_ids),
            "n_served":    n_served,
            "events":      events,
            "disp_log":    dispatch_log,
            "delivered":   delivered,
            "remaining":   remaining,
            "soc_history": soc_history,
            "time_grid":   pd.DatetimeIndex(
                [pd.Timestamp(r["time_utc"]) for r in soc_history]
            ),
            "n_steps":     len(soc_history),
        }

    # Find minimum K: start from 1 and increase until all vehicles served
    start_K = n_units if n_units is not None else 1
    for K in range(start_K, MAX_UNITS + 1):
        res = _run(K)
        print(f"    K={K:2d}  served={res['n_served']}/{res['n_vehicles']}")
        if res["n_served"] >= res["n_vehicles"]:
            print(f"  → Minimum XOS units needed: {K}")
            break
    else:
        print(f"  [WARNING] Even {MAX_UNITS} units cannot serve all vehicles.")

    res["rates"] = np.array([smud_rate(t) for t in res["time_grid"]])
    return res


# ── Per-unit figure ────────────────────────────────────────────────────────────

def plot_unit(k: int, res: dict, date_str: str,
              site_label: str = "Northgate") -> Path | None:
    """
    Three-panel figure for XOS unit k:
      Panel 1 — XOS Battery SOC (%)
                Blue  bg = SERVING vehicles (discharging, no grid).
                Green bg = RECHARGING from SMUD grid (no vehicle service).
                SOC never drops below 20%.
      Panel 2 — Energy delivered to each vehicle (kWh), slope = 76 kW for every vehicle.
      Panel 3 — Vehicle dwell & charging sessions (Gantt).
    """
    n_units   = res["n_units"]
    time_grid = res["time_grid"]
    n_steps   = res["n_steps"]
    events    = res["events"]
    disp_log  = res["disp_log"]
    remaining = res["remaining"]
    soc_hist  = res["soc_history"]

    disp_df = (pd.DataFrame(disp_log) if disp_log
               else pd.DataFrame(columns=["step_idx", "unit", "event_id",
                                           "energy_to_vehicle_kwh"]))

    if disp_df.empty or k not in disp_df["unit"].values:
        print(f"  Unit {k+1}: no vehicles served — skipping.")
        return None

    unit_disp    = disp_df[disp_df["unit"] == k].copy()
    vehicle_eids = unit_disp["event_id"].unique().tolist()
    n_veh        = len(vehicle_eids)

    veh_meta: dict[str, dict] = {}
    for eid in vehicle_eids:
        rows = events[events["charging_event_id"] == eid]
        if rows.empty:
            continue
        r = rows.iloc[0]
        veh_meta[eid] = {
            "model":  str(r.get("ev_equivalent_model", "") or "").split(" (")[0][:28],
            "e_need": float(r["energy_needed_kwh_for_visit"]),
            "arr":    r["arrival_time"],
            "dep":    r["departure_time"],
            "served": remaining.get(eid, 1.0) <= ENERGY_TOL,
        }

    times_local = pd.DatetimeIndex(time_grid).tz_convert(SMUD_TZ)
    is_summer   = times_local[0].month in (6, 7, 8, 9)
    season      = "Summer" if is_summer else "Non-summer"

    x          = np.arange(n_steps)
    hour_ticks = [i for i in range(n_steps) if times_local[i].minute == 0]
    hour_lbls  = [times_local[i].strftime("%H:%M") for i in hour_ticks]

    cmap    = cm.get_cmap("tab10", max(n_veh, 10))
    eid_col = {eid: cmap(i) for i, eid in enumerate(vehicle_eids)}

    soc_line = np.array([r[f"soc_unit_{k}"] for r in soc_hist]) * 100
    states   = [r[f"state_unit_{k}"] for r in soc_hist]

    def ts_to_step(ts):
        tg = time_grid
        if tg.tz is not None and ts.tz is None:
            ts = ts.tz_localize("UTC")
        elif tg.tz is None and ts.tz is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        diffs = np.abs((tg - ts).total_seconds())
        ci = int(np.argmin(diffs))
        if ci < n_steps - 1:
            span = (tg[ci + 1] - tg[ci]).total_seconds()
            frac = (ts - tg[ci]).total_seconds() / span
            return ci + max(0.0, min(1.0, frac))
        return float(ci)

    def _shade_states(ax, alpha_srv=0.30, alpha_rch=0.40):
        """Draw blue (serving) and green (recharging) background bands."""
        for state, color, alpha in [
            ("serving",    "#aed6f1", alpha_srv),
            ("recharging", "#a9dfbf", alpha_rch),
        ]:
            in_block = False
            for ti in range(n_steps + 1):
                s = states[ti] if ti < n_steps else None
                if not in_block and s == state:
                    in_block = True; b0 = ti
                elif in_block and (s != state or ti == n_steps):
                    ax.axvspan(b0, ti, color=color, alpha=alpha, linewidth=0)
                    in_block = False

    # ── Layout ────────────────────────────────────────────────────────────────
    gantt_h = max(2.5, n_veh * 0.72)
    fig, axes = plt.subplots(
        3, 1,
        figsize=(20, 4.5 + 4.2 + gantt_h + 0.5),
        sharex=True,
        gridspec_kw={"height_ratios": [4.5, 4.2, gantt_h]},
    )

    # ── Panel 1: XOS SOC ──────────────────────────────────────────────────────
    ax1 = axes[0]
    _shade_states(ax1, alpha_srv=0.30, alpha_rch=0.40)

    # Coloured band per vehicle (time window when this unit served it)
    for eid in vehicle_eids:
        col   = eid_col[eid]
        steps = sorted(unit_disp[unit_disp["event_id"] == eid]["step_idx"].tolist())
        if not steps:
            continue
        ax1.axvspan(steps[0], steps[-1] + 1, color=col, alpha=0.18, linewidth=0, zorder=2)
        mid = (steps[0] + steps[-1] + 1) / 2
        ax1.text(mid, SOC_MIN * 100 + 2,
                 veh_meta.get(eid, {}).get("model", eid)[:16],
                 rotation=90, fontsize=6.5, ha="center", va="bottom",
                 color=col, fontweight="bold", zorder=3)

    ax1.plot(x, soc_line, color="navy", linewidth=2.8, zorder=4,
             label="XOS Battery SOC (%)")
    ax1.fill_between(x, soc_line, SOC_MIN * 100,
                     where=soc_line > SOC_MIN * 100,
                     color="steelblue", alpha=0.10, zorder=1)
    ax1.axhline(SOC_MIN * 100, color="red", linewidth=1.8, linestyle="--",
                alpha=0.85, zorder=3,
                label="20% floor — switches to RECHARGING when reached")

    min_val = soc_line.min()
    min_ti  = int(np.argmin(soc_line))
    ax1.annotate(f"Min: {min_val:.0f}%",
                 xy=(min_ti, min_val),
                 xytext=(min_ti + 3, min_val + 7),
                 fontsize=8, color="red",
                 arrowprops=dict(arrowstyle="->", color="red", lw=1.0))

    srv_patch  = mpatches.Patch(color="#aed6f1", alpha=0.65,
                                 label="SERVING vehicles (discharging battery, no grid)")
    rch_patch  = mpatches.Patch(color="#a9dfbf", alpha=0.75,
                                 label="RECHARGING from SMUD grid (no vehicle service)")
    h1, l1 = ax1.get_legend_handles_labels()
    ax1.legend(h1 + [srv_patch, rch_patch],
               l1 + [srv_patch.get_label(), rch_patch.get_label()],
               loc="upper right", fontsize=8, framealpha=0.95, ncol=2)

    ax1.set_ylim(-5, 112)
    ax1.set_yticks([0, 20, 40, 60, 80, 100])
    ax1.set_yticklabels(["0%", "20%", "40%", "60%", "80%", "100%"], fontsize=8)
    ax1.set_ylabel("XOS Battery SOC", fontsize=9)
    ax1.set_title(
        f"XOS Unit {k+1}  —  BLUE = serving vehicles (battery draining)   "
        f"GREEN = recharging from SMUD grid   |   SOC never drops below 20%",
        fontsize=9.5, pad=4, loc="left",
    )
    ax1.grid(axis="x", linestyle=":", alpha=0.25, color="gray")
    ax1.grid(axis="y", linestyle=":", alpha=0.20, color="gray")

    # ── Panel 2: Energy delivered (kWh) ──────────────────────────────────────
    ax2 = axes[1]
    _shade_states(ax2, alpha_srv=0.20, alpha_rch=0.30)

    rate_kw   = P_PORT * ETA_D          # 76 kW delivered to vehicle
    max_e     = max((veh_meta[e]["e_need"] for e in vehicle_eids), default=1.0)

    for eid in vehicle_eids:
        col    = eid_col[eid]
        meta   = veh_meta.get(eid, {})
        e_need = meta.get("e_need", 1.0)
        model  = meta.get("model", eid)
        served = meta.get("served", False)

        step_e = {int(r["step_idx"]): float(r["energy_to_vehicle_kwh"])
                  for _, r in unit_disp[unit_disp["event_id"] == eid].iterrows()}
        cum    = np.zeros(n_steps)
        run    = 0.0
        for ti in range(n_steps):
            run   += step_e.get(ti, 0.0)
            cum[ti] = run

        status = "fully charged" if served else "partial"
        ax2.plot(x, cum, color=col, linewidth=2.2, zorder=3,
                 label=f"{model}  [need {e_need:.0f} kWh | {status}]")
        ax2.fill_between(x, 0, cum, color=col, alpha=0.07, zorder=1)
        ax2.axhline(e_need, color=col, linewidth=0.8, linestyle="--", alpha=0.50, zorder=2)

        last = int(np.max(np.where(cum > 0)[0])) if cum.max() > 0 else 0
        ax2.plot(last, cum[last], "o", color=col, markersize=5, zorder=4)
        ax2.text(last + 0.5, cum[last], f"{cum[last]:.0f} kWh",
                 fontsize=7, va="center", color=col, zorder=5)

    ax2.set_ylim(-2, max_e * 1.14)
    ax2.set_ylabel("Energy delivered\nto vehicle (kWh)", fontsize=9)
    ax2.set_title(
        f"Energy delivered to each vehicle (kWh)  —  slope = {rate_kw:.0f} kW  "
        f"(same rate for every vehicle: 80 kW port × 0.95 η)\n"
        "Dashed line = vehicle's energy need.  "
        "Line PAUSES during RECHARGING periods; resumes when unit finishes recharging.",
        fontsize=9.5, pad=4, loc="left",
    )
    ax2.legend(loc="upper left", fontsize=7.5, framealpha=0.95, ncol=1)
    ax2.grid(axis="x", linestyle=":", alpha=0.25)
    ax2.grid(axis="y", linestyle=":", alpha=0.20)

    # ── Panel 3: Dwell & charging Gantt ──────────────────────────────────────
    ax3 = axes[2]
    _shade_states(ax3, alpha_srv=0.15, alpha_rch=0.25)

    for vi, eid in enumerate(vehicle_eids):
        col   = eid_col[eid]
        meta  = veh_meta.get(eid, {})
        arr_s = ts_to_step(meta["arr"])
        dep_s = ts_to_step(meta["dep"])
        e_need = meta.get("e_need", 0)
        model  = meta.get("model", eid)

        # Dwell window (light)
        ax3.barh(vi, max(dep_s - arr_s, 0.5), left=arr_s, height=0.72,
                 color=col, alpha=0.18, edgecolor=col, linewidth=0.7, zorder=1)

        # Charging sessions (solid), split by recharge gaps
        steps = sorted(unit_disp[unit_disp["event_id"] == eid]["step_idx"].tolist())
        if steps:
            sessions: list[tuple[int, int]] = []
            s0, sp = steps[0], steps[0]
            for s in steps[1:] + [steps[-1] + 2]:
                if s > sp + 1:
                    sessions.append((s0, sp))
                    s0 = s
                sp = s
            for idx, (sa, sb) in enumerate(sessions):
                ax3.barh(vi, sb - sa + 1, left=sa, height=0.72,
                         color=col, alpha=0.90, edgecolor="white",
                         linewidth=0.3, zorder=3)
                if idx == 0:
                    ax3.text(sa + 0.4, vi,
                             f"{model}  [{e_need:.0f} kWh]",
                             va="center", ha="left", fontsize=7,
                             color="black", zorder=5)

    ax3.set_yticks(range(n_veh))
    ax3.set_yticklabels([f"V{i+1}" for i in range(n_veh)], fontsize=7.5)
    ax3.set_ylim(-0.6, n_veh - 0.3)
    ax3.invert_yaxis()
    ax3.set_xticks(hour_ticks)
    ax3.set_xticklabels(hour_lbls, fontsize=8.5, rotation=45)
    ax3.set_xlabel("Time (Pacific local time)", fontsize=9)
    ax3.set_xlim(0, n_steps)
    ax3.set_title(
        "Vehicle dwell & charging sessions at Northgate  "
        "(light bar = parked,  solid bar = actively charging from XOS,  "
        "gap in solid = XOS is recharging — vehicle waits for it to return)",
        fontsize=9.5, pad=4, loc="left",
    )
    ax3.grid(axis="x", linestyle=":", alpha=0.25)

    patches = [mpatches.Patch(color=eid_col[eid], alpha=0.85,
                               label=f"V{i+1}: {veh_meta.get(eid,{}).get('model',eid)[:22]}")
               for i, eid in enumerate(vehicle_eids)]
    ax3.legend(handles=patches, loc="upper right", fontsize=7.0,
               framealpha=0.9, ncol=max(1, n_veh // 6))

    # ── Suptitle ──────────────────────────────────────────────────────────────
    n_rech = sum(1 for ti in range(1, n_steps)
                 if states[ti] == "recharging" and states[ti - 1] == "serving")
    e_srv  = sum(veh_meta[e]["e_need"] for e in vehicle_eids
                 if veh_meta[e]["served"])

    fig.suptitle(
        f"{site_label}  |  {date_str}  ({season})  |  XOS Hub MC02  —  "
        f"Unit {k+1} of {n_units}\n"
        f"Each unit stays at Northgate and cycles:  "
        f"SERVING vehicles → SOC hits 20% → RECHARGING from SMUD grid → back to SERVING\n"
        f"Recharge cycles today: {n_rech}  |  "
        f"Vehicles served by this unit: {n_veh}  |  "
        f"Energy delivered: {e_srv:.0f} kWh",
        fontsize=10.5, fontweight="bold", y=1.005,
    )

    plt.tight_layout(rect=[0, 0, 1, 1])
    out = OUT_DIR / f"xos_unit{k+1}_{date_str.replace('-','_')}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out.name}")
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-04-29"
    date_tag = date_str.replace("-", "_")
    csv_path = BASE_DIR / f"z2z_milp_events_northgate_{date_tag}.csv"

    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}"); return

    print(f"\n{'='*72}")
    print(f"  XOS SIMULATION  —  {date_str}  (Northgate, physically correct)")
    print(f"{'='*72}\n")

    res = simulate_xos(csv_path, verbose=False)

    print(f"\n  Units deployed  : {res['n_units']}")
    print(f"  Vehicles served : {res['n_served']} / {res['n_vehicles']}")
    print(f"\n  Generating per-unit figures ...\n")

    saved = []
    for k in range(res["n_units"]):
        out = plot_unit(k, res, date_str)
        if out:
            saved.append(out)

    print(f"\n  Done. {len(saved)} figures → {OUT_DIR}")


if __name__ == "__main__":
    main()
