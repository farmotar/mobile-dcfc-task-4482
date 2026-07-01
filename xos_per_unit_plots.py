"""
xos_per_unit_plots.py
======================
One figure per XOS unit. Each figure has three panels:

  Panel 1 — XOS Battery SOC (%)
      Line goes DOWN from 100% to ~20% as the unit serves vehicles.
      Coloured bands show which vehicle is being charged at each moment.

  Panel 2 — Vehicle charge level (%)
      One line per vehicle served by this unit.
      Line goes UP from 0% (just plugged in) to 100% (fully charged).
      "100%" means the full energy need for this visit has been delivered.

  Panel 3 — Vehicle dwell & service windows (Gantt)
      Light bar  = vehicle is parked at site (dwell window)
      Solid bar  = XOS is actively charging that vehicle

Usage:
    python xos_per_unit_plots.py                  # April 29 2026 (default)
    python xos_per_unit_plots.py 2025-07-17
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

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT_DIR  = BASE_DIR / "xos_per_unit_outputs"
OUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
xos     = importlib.import_module("xos_hub_soc_simulation")
mobile  = importlib.import_module("xos_mobile_charging_analysis")

SMUD_TZ  = mobile.SMUD_TZ
SOC_MIN  = mobile.SOC_MIN
SOC_MAX  = mobile.SOC_MAX
B_KWH    = mobile.B_KWH
ETA_D    = mobile.ETA_D
P_GRID   = mobile.P_GRID
DT_H     = mobile.DT_H
RATE_COLORS = mobile.RATE_COLORS


def ts_to_step(ts: pd.Timestamp, time_grid: pd.DatetimeIndex) -> float:
    """Convert a UTC timestamp to a fractional step index."""
    tg = time_grid
    if tg.tz is not None and ts.tz is None:
        ts = ts.tz_localize("UTC")
    elif tg.tz is None and ts.tz is not None:
        ts = ts.tz_convert("UTC").tz_localize(None)
    diffs = np.abs((tg - ts).total_seconds())
    ci = int(np.argmin(diffs))
    if ci < len(tg) - 1:
        span = (tg[ci + 1] - tg[ci]).total_seconds()
        frac = (ts - tg[ci]).total_seconds() / span
        return ci + max(0.0, min(1.0, frac))
    return float(ci)


def make_unit_figure(k: int, res: dict, date_str: str,
                     site_label: str = "Northgate") -> Path | None:
    """
    Build and save the three-panel figure for XOS unit k.
    Returns the output path, or None if this unit served no vehicles.
    """
    n_units   = res["n_units"]
    time_grid = res["time_grid"]
    rates     = res["rates"]
    n_steps   = res["n_steps"]
    events    = res["events"]
    disp_log  = res["disp_log"]
    mob_soc   = res["mobile_soc"]     # (n_units, n_steps) — no intermediate grid charge
    remaining = res["remaining"]

    disp_df = (pd.DataFrame(disp_log) if disp_log
               else pd.DataFrame(columns=["step_idx","unit","event_id","energy_to_vehicle_kwh"]))

    # Vehicles served by this unit
    if disp_df.empty or k not in disp_df["unit"].values:
        print(f"  Unit {k+1}: no vehicles served — skipping.")
        return None

    unit_disp = disp_df[disp_df["unit"] == k].copy()
    vehicle_eids = unit_disp["event_id"].unique().tolist()
    n_veh = len(vehicle_eids)

    # Pull vehicle metadata
    veh_meta: dict[str, dict] = {}
    for eid in vehicle_eids:
        rows = events[events["charging_event_id"] == eid]
        if rows.empty:
            continue
        r = rows.iloc[0]
        veh_meta[eid] = {
            "model":  str(r.get("ev_equivalent_model","") or "").split(" (")[0][:28],
            "e_need": float(r["energy_needed_kwh_for_visit"]),
            "arr":    r["arrival_time"],
            "dep":    r["departure_time"],
            "served": remaining.get(eid, float(r["energy_needed_kwh_for_visit"])) <= 1e-3,
        }

    times_local = pd.DatetimeIndex(time_grid).tz_convert(SMUD_TZ)
    is_summer   = times_local[0].month in (6, 7, 8, 9)
    season      = "Summer" if is_summer else "Non-summer"

    x           = np.arange(n_steps)
    hour_ticks  = [i for i in range(n_steps) if times_local[i].minute == 0]
    hour_labels = [times_local[i].strftime("%H:%M") for i in hour_ticks]

    # One distinct colour per vehicle
    cmap   = cm.get_cmap("tab10", max(n_veh, 10))
    eid_col = {eid: cmap(i) for i, eid in enumerate(vehicle_eids)}

    # ── Figure layout ──────────────────────────────────────────────────────────
    gantt_h = max(2.5, n_veh * 0.65)
    fig, axes = plt.subplots(
        3, 1,
        figsize=(20, 4.5 + 4.0 + gantt_h + 0.5),
        sharex=True,
        gridspec_kw={"height_ratios": [4.5, 4.0, gantt_h]},
    )

    # ── PANEL 1: XOS battery SOC ───────────────────────────────────────────────
    ax1 = axes[0]

    # SMUD rate background
    for ti in range(n_steps - 1):
        ax1.axvspan(ti, ti + 1,
                    color=RATE_COLORS.get(rates[ti], "#eeeeee"),
                    alpha=0.09, linewidth=0)

    # Coloured service-window bands (which vehicle is being served)
    for eid in vehicle_eids:
        col  = eid_col[eid]
        meta = veh_meta.get(eid, {})
        steps = sorted(unit_disp[unit_disp["event_id"] == eid]["step_idx"].tolist())
        if not steps:
            continue
        # One continuous band: first charge step → last charge step
        s0, s1 = steps[0], steps[-1] + 1
        ax1.axvspan(s0, s1, color=col, alpha=0.22, linewidth=0, zorder=1)
        mid = (s0 + s1) / 2
        label_txt = meta.get("model", eid)[:18]
        ax1.text(mid, SOC_MIN * 100 + 3, label_txt,
                 rotation=90, fontsize=6.5, ha="center", va="bottom",
                 color=col, fontweight="bold", zorder=3)

    # XOS SOC line
    soc_line = mob_soc[k] * 100
    ax1.plot(x, soc_line, color="navy", linewidth=2.8,
             label="XOS Battery SOC (%)", zorder=4)
    ax1.fill_between(x, soc_line, SOC_MIN * 100,
                     where=soc_line > SOC_MIN * 100,
                     color="steelblue", alpha=0.15, zorder=2)

    # Floor line
    ax1.axhline(SOC_MIN * 100, color="red", linewidth=1.8, linestyle="--",
                alpha=0.9, zorder=3,
                label=f"Return-to-depot threshold  ({SOC_MIN*100:.0f}%  SOC)")

    # Annotate min SOC reached
    min_soc_val = soc_line.min()
    min_soc_ti  = int(np.argmin(soc_line))
    ax1.annotate(
        f"Min SOC: {min_soc_val:.0f}%",
        xy=(min_soc_ti, min_soc_val),
        xytext=(min_soc_ti + 3, min_soc_val + 8),
        fontsize=8, color="red",
        arrowprops=dict(arrowstyle="->", color="red", lw=1.0),
    )

    ax1.set_ylim(-5, 112)
    ax1.set_yticks([0, 20, 40, 60, 80, 100])
    ax1.set_yticklabels(["0%","20%","40%","60%","80%","100%"], fontsize=8)
    ax1.set_ylabel("XOS Battery SOC", fontsize=9)
    ax1.set_title(
        f"XOS Unit {k+1} — Battery draining from 100% as it charges vehicles  "
        f"(coloured bands = vehicle being served at that moment)",
        fontsize=10, pad=4, loc="left",
    )
    ax1.legend(loc="upper right", fontsize=8.5, framealpha=0.95)
    ax1.grid(axis="x", linestyle=":", alpha=0.25, color="gray")
    ax1.grid(axis="y", linestyle=":", alpha=0.20, color="gray")

    # ── PANEL 2: Vehicle charge level (%) ─────────────────────────────────────
    ax2 = axes[1]

    for ti in range(n_steps - 1):
        ax2.axvspan(ti, ti + 1,
                    color=RATE_COLORS.get(rates[ti], "#eeeeee"),
                    alpha=0.09, linewidth=0)

    ax2.axhline(100, color="black", linewidth=1.0, linestyle=":",
                alpha=0.45, label="Fully charged (100%)")

    for eid in vehicle_eids:
        col  = eid_col[eid]
        meta = veh_meta.get(eid, {})
        e_need = meta.get("e_need", 1.0)
        model  = meta.get("model", eid)
        served = meta.get("served", False)

        # Build cumulative energy received at each step
        step_e = {int(row["step_idx"]): float(row["energy_to_vehicle_kwh"])
                  for _, row in unit_disp[unit_disp["event_id"] == eid].iterrows()}

        cum = np.zeros(n_steps)
        running = 0.0
        for ti in range(n_steps):
            running += step_e.get(ti, 0.0)
            cum[ti] = running

        pct = np.clip(cum / max(e_need, 1e-6) * 100, 0, 100)

        status = "✓ fully charged" if served else "✗ partial"
        lbl    = f"{model}  [{e_need:.0f} kWh needed | {status}]"
        ax2.plot(x, pct, color=col, linewidth=2.0, label=lbl, zorder=3)

        # Shade the area under the curve
        ax2.fill_between(x, 0, pct, color=col, alpha=0.08, zorder=1)

        # Mark where charging stopped
        final_pct = pct[-1]
        last_active = int(np.max(np.where(pct > 0)[0])) if pct.max() > 0 else 0
        ax2.plot(last_active, pct[last_active], "o",
                 color=col, markersize=5, zorder=4)

    ax2.set_ylim(-5, 112)
    ax2.set_yticks([0, 25, 50, 75, 100])
    ax2.set_yticklabels(["0%","25%","50%","75%","100%"], fontsize=8)
    ax2.set_ylabel("Vehicle charge\nreceived (%)", fontsize=9)
    ax2.set_title(
        "Vehicle charge level — rising from 0% (just plugged in) to 100% (energy need fully delivered)",
        fontsize=10, pad=4, loc="left",
    )
    ax2.legend(loc="upper left", fontsize=7.8, framealpha=0.95, ncol=1)
    ax2.grid(axis="x", linestyle=":", alpha=0.25, color="gray")
    ax2.grid(axis="y", linestyle=":", alpha=0.20, color="gray")

    # ── PANEL 3: Dwell & service Gantt ────────────────────────────────────────
    ax3 = axes[2]

    for ti in range(n_steps - 1):
        ax3.axvspan(ti, ti + 1,
                    color=RATE_COLORS.get(rates[ti], "#eeeeee"),
                    alpha=0.09, linewidth=0)

    for vi, eid in enumerate(vehicle_eids):
        col  = eid_col[eid]
        meta = veh_meta.get(eid, {})
        arr_s = ts_to_step(meta["arr"], time_grid)
        dep_s = ts_to_step(meta["dep"], time_grid)
        e_need = meta.get("e_need", 0)
        model  = meta.get("model", eid)

        # Dwell window (light, transparent)
        ax3.barh(vi, max(dep_s - arr_s, 0.5), left=arr_s, height=0.68,
                 color=col, alpha=0.18, edgecolor=col, linewidth=0.8, zorder=1)

        # XOS charging periods — draw as ONE continuous block (first→last step).
        # Physical meaning: once the cable is connected, charging runs without
        # interruption until the energy need is fully met.
        steps = sorted(unit_disp[unit_disp["event_id"] == eid]["step_idx"].tolist())
        if steps:
            ax3.barh(vi, steps[-1] - steps[0] + 1, left=steps[0], height=0.68,
                     color=col, alpha=0.90, edgecolor="white",
                     linewidth=0.3, zorder=3)

        # Arrival and departure markers
        ax3.axvline(arr_s, color=col, linewidth=0.8, linestyle="--", alpha=0.55, ymin=vi/n_veh, ymax=(vi+1)/n_veh)

        # Label
        ax3.text(arr_s + 0.4, vi, f"{model}  [{e_need:.0f} kWh]",
                 va="center", ha="left", fontsize=7.0, color="black", zorder=5)

    ax3.set_yticks(range(n_veh))
    ax3.set_yticklabels([f"V{i+1}" for i in range(n_veh)], fontsize=7.5)
    ax3.set_ylim(-0.6, n_veh - 0.3)
    ax3.invert_yaxis()
    ax3.set_xticks(hour_ticks)
    ax3.set_xticklabels(hour_labels, fontsize=8.5, rotation=45)
    ax3.set_xlabel("Time (Pacific local time)", fontsize=9)
    ax3.set_xlim(0, n_steps)
    ax3.set_title(
        "Vehicle dwell & charging windows  "
        "(light bar = parked at site,  solid bar = XOS actively delivering charge)",
        fontsize=9.5, pad=4, loc="left",
    )
    ax3.grid(axis="x", linestyle=":", alpha=0.25, color="gray")

    # Colour legend (vehicles)
    patches = [mpatches.Patch(color=eid_col[eid], alpha=0.85,
                               label=f"V{i+1}: {veh_meta.get(eid,{}).get('model', eid)[:22]}")
               for i, eid in enumerate(vehicle_eids)]
    ax3.legend(handles=patches, loc="upper right", fontsize=7.0,
               framealpha=0.9, ncol=max(1, n_veh // 6))

    # ── Figure title ──────────────────────────────────────────────────────────
    min_soc_pct = mob_soc[k].min() * 100
    energy_served = sum(
        veh_meta.get(eid, {}).get("e_need", 0)
        for eid in vehicle_eids
        if veh_meta.get(eid, {}).get("served", False)
    )
    fig.suptitle(
        f"{site_label}  |  {date_str}  ({season})  |  XOS Hub MC02  —  Unit {k+1} of {n_units}\n"
        f"Vehicles served: {n_veh}   |   "
        f"Energy delivered: {energy_served:.0f} kWh   |   "
        f"Battery: 100% → {min_soc_pct:.0f}% min  "
        f"({'returned to depot for recharge' if min_soc_pct <= SOC_MIN * 100 + 5 else 'still had charge remaining'})",
        fontsize=11, fontweight="bold", y=1.005,
    )

    plt.tight_layout(rect=[0, 0, 1, 1])
    out = OUT_DIR / f"xos_unit{k+1}_{date_str.replace('-','_')}.png"
    plt.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {out.name}")
    return out


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "2026-04-29"
    date_tag = date_str.replace("-", "_")
    csv_path = BASE_DIR / f"z2z_milp_events_northgate_{date_tag}.csv"

    if not csv_path.exists():
        print(f"[ERROR] CSV not found: {csv_path}")
        sys.exit(1)

    print(f"\n{'='*72}")
    print(f"  XOS PER-UNIT PLOTS  —  {date_str}")
    print(f"{'='*72}\n")
    print(f"  Loading: {csv_path.name}")

    res = mobile.simulate_mobile(csv_path)
    if not res:
        print("[ERROR] Simulation failed.")
        sys.exit(1)

    print(f"  Units: {res['n_units']}  |  Vehicles: {res['n_vehicles']}")
    print(f"  Generating one figure per unit ...\n")

    saved = []
    for k in range(res["n_units"]):
        out = make_unit_figure(k, res, date_str, site_label="Northgate")
        if out:
            saved.append(out)

    print(f"\n  Done.  {len(saved)} figures saved to:")
    print(f"  {OUT_DIR}")


if __name__ == "__main__":
    main()
