"""
xos_combined_figure.py
======================
Single giant figure with all XOS units stacked vertically.
Each unit gets one compact row with 3 panels: SOC | Energy delivered | Gantt.
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
import matplotlib.cm as cm
import matplotlib.gridspec as gridspec

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT_DIR  = BASE_DIR / "xos_trip_outputs"
OUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
trip = importlib.import_module("xos_trip_simulation")

B_KWH      = trip.B_KWH
SOC_MIN    = trip.SOC_MIN
SOC_MAX    = trip.SOC_MAX
P_PORT     = trip.P_PORT
ETA_D      = trip.ETA_D
N_PORTS    = trip.N_PORTS
ENERGY_TOL = trip.ENERGY_TOL
SMUD_TZ    = trip.SMUD_TZ


def plot_combined(res: dict, date_str: str,
                  site_label: str = "Northgate") -> Path:

    n_units   = res["n_units"]
    time_grid = res["time_grid"]
    n_steps   = res["n_steps"]
    events    = res["events"]
    soc_hist  = res["soc_history"]
    remaining = res["remaining"]
    disp_log  = res["disp_log"]

    disp_df = pd.DataFrame(disp_log) if disp_log else pd.DataFrame(
        columns=["step_idx", "unit", "event_id", "energy_to_vehicle_kwh"])

    times_local = pd.DatetimeIndex(time_grid).tz_convert(SMUD_TZ)
    is_summer   = times_local[0].month in (6, 7, 8, 9)
    season      = "Summer" if is_summer else "Non-summer"
    x           = np.arange(n_steps)
    hour_ticks  = [i for i in range(n_steps) if times_local[i].minute == 0]
    hour_lbls   = [times_local[i].strftime("%H:%M") for i in hour_ticks]

    # Per-unit data
    unit_rows: list[dict] = []
    for k in range(n_units):
        ud = disp_df[disp_df["unit"] == k] if not disp_df.empty else pd.DataFrame()
        eids = ud["event_id"].unique().tolist() if not ud.empty else []
        soc_line = np.array([r[f"soc_unit_{k}"] for r in soc_hist]) * 100
        states   = [r[f"state_unit_{k}"] for r in soc_hist]
        unit_rows.append({"k": k, "ud": ud, "eids": eids,
                          "soc_line": soc_line, "states": states})

    # Global colour map: one colour per vehicle (across all units)
    all_eids = events["charging_event_id"].tolist()
    cmap     = cm.get_cmap("tab20", max(len(all_eids), 20))
    eid_col  = {eid: cmap(i) for i, eid in enumerate(all_eids)}

    def ts_to_step(ts):
        tg = time_grid
        if tg.tz is not None and ts.tz is None:
            ts = ts.tz_localize("UTC")
        elif tg.tz is None and ts.tz is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        diffs = np.abs((tg - ts).total_seconds())
        ci = int(np.argmin(diffs))
        if ci < n_steps - 1:
            frac = (ts - tg[ci]).total_seconds() / (tg[ci+1] - tg[ci]).total_seconds()
            return ci + max(0.0, min(1.0, frac))
        return float(ci)

    def shade_states(ax, states, alpha_srv=0.22, alpha_rch=0.32):
        for state, color, alpha in [
            ("serving",    "#aed6f1", alpha_srv),
            ("recharging", "#a9dfbf", alpha_rch),
        ]:
            in_b = False
            for ti in range(n_steps + 1):
                s = states[ti] if ti < n_steps else None
                if not in_b and s == state:
                    in_b = True; b0 = ti
                elif in_b and (s != state or ti == n_steps):
                    ax.axvspan(b0, ti, color=color, alpha=alpha, linewidth=0)
                    in_b = False

    # ── Layout: n_units rows × 3 columns ─────────────────────────────────────
    row_h   = 2.8          # inches per unit row
    fig_w   = 30
    fig_h   = row_h * n_units + 2.0   # +2 for title
    fig     = plt.figure(figsize=(fig_w, fig_h))

    outer = gridspec.GridSpec(
        n_units, 3,
        figure=fig,
        hspace=0.55,
        wspace=0.20,
        left=0.04, right=0.99,
        top=1.0 - 1.2/fig_h,
        bottom=0.03,
        width_ratios=[3, 2.5, 3],
    )

    for row_idx, ur in enumerate(unit_rows):
        k      = ur["k"]
        ud     = ur["ud"]
        eids   = ur["eids"]
        sl     = ur["soc_line"]
        states = ur["states"]
        last_row = (row_idx == n_units - 1)

        ax_soc = fig.add_subplot(outer[row_idx, 0])
        ax_ene = fig.add_subplot(outer[row_idx, 1], sharex=ax_soc)
        ax_gnt = fig.add_subplot(outer[row_idx, 2], sharex=ax_soc)

        # ── SOC panel ────────────────────────────────────────────────────────
        shade_states(ax_soc, states)
        ax_soc.plot(x, sl, color="navy", linewidth=1.6, zorder=4)
        ax_soc.axhline(20, color="red", linewidth=1.1, linestyle="--",
                       alpha=0.75, zorder=3)
        ax_soc.fill_between(x, sl, 20, where=sl > 20,
                            color="steelblue", alpha=0.10, zorder=1)

        # Colour bands per vehicle
        for eid in eids:
            col   = eid_col[eid]
            steps = sorted(ud[ud["event_id"] == eid]["step_idx"].tolist())
            if steps:
                ax_soc.axvspan(steps[0], steps[-1]+1,
                               color=col, alpha=0.18, linewidth=0, zorder=2)

        ax_soc.set_ylim(0, 110)
        ax_soc.set_yticks([20, 60, 100])
        ax_soc.set_yticklabels(["20%", "60%", "100%"], fontsize=6)
        ax_soc.set_ylabel(f"U{k+1}", fontsize=8, fontweight="bold",
                          rotation=0, labelpad=22, va="center")
        ax_soc.tick_params(axis="x", labelbottom=last_row, labelsize=6.5)
        ax_soc.tick_params(axis="y", labelsize=6)
        ax_soc.grid(axis="x", linestyle=":", alpha=0.25, color="gray")
        ax_soc.grid(axis="y", linestyle=":", alpha=0.20, color="gray")
        if row_idx == 0:
            ax_soc.set_title("XOS Battery SOC (%)", fontsize=8.5, pad=3)

        # ── Energy delivered panel ────────────────────────────────────────────
        shade_states(ax_ene, states, alpha_srv=0.18, alpha_rch=0.28)
        max_e = 1.0
        for eid in eids:
            rows_ev = events[events["charging_event_id"] == eid]
            if rows_ev.empty:
                continue
            e_need = float(rows_ev.iloc[0]["energy_needed_kwh_for_visit"])
            max_e  = max(max_e, e_need)
            col    = eid_col[eid]
            step_e = {int(r["step_idx"]): float(r["energy_to_vehicle_kwh"])
                      for _, r in ud[ud["event_id"] == eid].iterrows()}
            cum = np.zeros(n_steps)
            run = 0.0
            for ti in range(n_steps):
                run    += step_e.get(ti, 0.0)
                cum[ti] = run
            ax_ene.plot(x, cum, color=col, linewidth=1.5, zorder=3)
            ax_ene.axhline(e_need, color=col, linewidth=0.7,
                           linestyle="--", alpha=0.45, zorder=2)

        ax_ene.set_ylim(0, max_e * 1.15)
        ax_ene.tick_params(axis="x", labelbottom=last_row, labelsize=6.5)
        ax_ene.tick_params(axis="y", labelsize=6)
        ax_ene.grid(axis="x", linestyle=":", alpha=0.25)
        ax_ene.grid(axis="y", linestyle=":", alpha=0.20)
        if row_idx == 0:
            ax_ene.set_title("Energy delivered (kWh)", fontsize=8.5, pad=3)

        # ── Gantt panel ───────────────────────────────────────────────────────
        shade_states(ax_gnt, states, alpha_srv=0.15, alpha_rch=0.22)
        n_veh = len(eids)
        for vi, eid in enumerate(eids):
            col  = eid_col[eid]
            rows_ev = events[events["charging_event_id"] == eid]
            if rows_ev.empty:
                continue
            r_ev   = rows_ev.iloc[0]
            arr_s  = ts_to_step(r_ev["arrival_time"])
            dep_s  = ts_to_step(r_ev["departure_time"])
            e_need = float(r_ev["energy_needed_kwh_for_visit"])
            model  = str(r_ev.get("ev_equivalent_model", "") or "").split(" (")[0][:18]
            served = remaining.get(eid, 1.0) <= ENERGY_TOL

            # Dwell
            ax_gnt.barh(vi, max(dep_s - arr_s, 0.5), left=arr_s,
                        height=0.70, color=col, alpha=0.18,
                        edgecolor=col, linewidth=0.5, zorder=1)

            # Charging sessions
            steps = sorted(ud[ud["event_id"] == eid]["step_idx"].tolist())
            if steps:
                sessions: list[tuple[int, int]] = []
                s0, sp = steps[0], steps[0]
                for s in steps[1:] + [steps[-1] + 2]:
                    if s > sp + 1:
                        sessions.append((s0, sp)); s0 = s
                    sp = s
                for sa, sb in sessions:
                    ax_gnt.barh(vi, sb - sa + 1, left=sa, height=0.70,
                                color=col, alpha=0.90,
                                edgecolor="white", linewidth=0.2, zorder=3)

            tick = "✓" if served else "✗"
            ax_gnt.text(arr_s + 0.3, vi,
                        f"{tick} {model} {e_need:.0f}kWh",
                        va="center", ha="left", fontsize=5.5,
                        color="black", zorder=5,
                        clip_on=True)

        ax_gnt.set_ylim(-0.5, max(n_veh - 0.4, 0.6))
        ax_gnt.invert_yaxis()
        ax_gnt.set_yticks([])
        ax_gnt.tick_params(axis="x", labelbottom=last_row, labelsize=6.5)
        ax_gnt.grid(axis="x", linestyle=":", alpha=0.25)
        if row_idx == 0:
            ax_gnt.set_title("Vehicle dwell & charging sessions", fontsize=8.5, pad=3)

        # x-axis labels only on last row
        if last_row:
            for ax in (ax_soc, ax_ene, ax_gnt):
                ax.set_xticks(hour_ticks)
                ax.set_xticklabels(hour_lbls, rotation=45, fontsize=6.5)
                ax.set_xlabel("Time (Pacific)", fontsize=7)
        ax_soc.set_xlim(0, n_steps)

    # ── Legend strips ─────────────────────────────────────────────────────────
    srv_p  = mpatches.Patch(color="#aed6f1", alpha=0.7, label="SERVING vehicles")
    rch_p  = mpatches.Patch(color="#a9dfbf", alpha=0.8, label="RECHARGING from SMUD")
    rate_kw = P_PORT * ETA_D

    fig.suptitle(
        f"{site_label}  |  {date_str}  ({season})  |  XOS Hub MC02  —  All {n_units} units\n"
        f"BLUE = serving vehicles (battery draining, no grid)   "
        f"GREEN = recharging from SMUD grid (no vehicle service)\n"
        f"Energy slope = {rate_kw:.0f} kW (same for every vehicle: 80 kW port × 0.95 η)   "
        f"Dashed line = vehicle energy need   "
        f"Served: {res['n_served']}/{res['n_vehicles']} vehicles",
        fontsize=11, fontweight="bold",
        y=1.0 - 0.15/fig_h,
    )
    fig.legend(handles=[srv_p, rch_p], loc="upper right",
               bbox_to_anchor=(0.99, 1.0 - 0.45/fig_h),
               fontsize=9, framealpha=0.9)

    out = OUT_DIR / f"xos_all_units_{date_str.replace('-','_')}.png"
    plt.savefig(out, dpi=130, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out}")
    return out


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-04-29"
    date_tag = date_str.replace("-", "_")
    csv_path = BASE_DIR / f"z2z_milp_events_northgate_{date_tag}.csv"

    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}"); return

    print(f"\nRunning XOS simulation for {date_str} ...")
    res = trip.simulate_xos(csv_path, verbose=False)
    print(f"  Units: {res['n_units']}  Served: {res['n_served']}/{res['n_vehicles']}")

    print("Building combined figure ...")
    plot_combined(res, date_str)
    print("Done.")


if __name__ == "__main__":
    main()
