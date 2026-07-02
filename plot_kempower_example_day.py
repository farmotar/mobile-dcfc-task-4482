"""
plot_kempower_example_day.py
----------------------------
Publication-ready Kempower DCFC figures for any Caltrans site and date.

Reads pre-computed MILP results from:
  scenario_outputs/{site}_analysis/per_day/{date}/kempower/

Figure 1: Operational schedule — Site power demand | Charger-lane Gantt
Figure 2: Vehicle service summary — energy bar chart, V1–VN by arrival

Usage:
    python plot_kempower_example_day.py --site northgate --date 2025-09-15
    python plot_kempower_example_day.py --site fresno    --date 2026-04-27
    python plot_kempower_example_day.py --site glendale  --date 2025-06-18
    python plot_kempower_example_day.py --site san_diego --date 2025-05-17
"""
from __future__ import annotations

import sys, argparse, datetime, math
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import pytz

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.dates  as mdates
from matplotlib.gridspec import GridSpec

# ── Paths ──────────────────────────────────────────────────────────────────────
REPO_DIR     = Path(__file__).resolve().parent
SCENARIO_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test\scenario_outputs")
OUT_DIR      = REPO_DIR / "xos_outputs"   # same output folder as XOS figures
OUT_DIR.mkdir(exist_ok=True)

TZ  = pytz.timezone("America/Los_Angeles")
UTC = pytz.utc

# ── Site metadata ──────────────────────────────────────────────────────────────
SITE_META = {
    "northgate":    {"label": "Northgate Maintenance Station", "utility": "SMUD"},
    "fresno":       {"label": "Fresno Maintenance Station",    "utility": "PG&E BEV-2"},
    "glendale":     {"label": "Glendale Maintenance Station",  "utility": "PG&E BEV-2 (proxy)"},
    "glendale_smud":{"label": "Glendale Maintenance Station",  "utility": "SMUD (proxy)"},
    "san_diego":    {"label": "San Diego Maintenance Station", "utility": "SDG&E EV-HP"},
}

# ── Kempower charger type colors / ordering ────────────────────────────────────
TYPE_COLOR = {
    "Kempower_50kW":  "#2166ac",   # blue
    "Kempower_150kW": "#1a9641",   # green
    "Kempower_250kW": "#d73027",   # red
}
TYPE_POWER = {
    "Kempower_50kW":  "50 kW",
    "Kempower_150kW": "150 kW",
    "Kempower_250kW": "250 kW",
}
TYPE_ORDER = ["Kempower_50kW", "Kempower_150kW", "Kempower_250kW"]

# ── 25 visually distinct vehicle colors ───────────────────────────────────────
VEH_PALETTE = [
    "#e6194b","#3cb44b","#4363d8","#f58231","#911eb4",
    "#42d4f4","#f032e6","#bfef45","#fabed4","#469990",
    "#dcbeff","#9A6324","#000075","#800000","#aaffc3",
    "#808000","#ffd8b1","#ff7f50","#40e0d0","#dc143c",
    "#4b0082","#a9a9a9","#ffe119","#808080","#566573",
]

MODEL_ABBR = {
    "Freightliner eCascadia 126":        "FL eCascadia",
    "Freightliner eCascadia":            "FL eCascadia",
    "Freightliner eM2":                  "FL eM2",
    "Chevrolet Silverado EV Work Truck": "Silverado EV",
    "Chevrolet Silverado EV WT":         "Silverado EV",
    "GMC Hummer EV":                     "Hummer EV",
    "Ford F-150 Lightning":              "F-150 Lt",
    "Tesla Model 3":                     "Tesla M3",
    "BYD 6F Cab-Forward Truck":          "BYD 6F",
    "Ram ProMaster EV (cargo)":          "ProMaster EV",
    "Volkswagen ID. Buzz":               "VW ID. Buzz",
    "Volkswagen ID.4":                   "VW ID.4",
    "Volvo VNR 4X2 Electric":            "Volvo VNR",
    "Blue Arc EV":                       "Blue Arc",
    "Global Electric Street Sweeper (M4E)": "Elec. Sweeper",
    "Rivian R1T":                        "Rivian R1T",
    "Rivian R1S":                        "Rivian R1S",
    "Kia EV6":                           "Kia EV6",
    "Chevrolet Bolt EV":                 "Bolt EV",
    "Ford E-Transit":                    "E-Transit",
}

C = dict(
    full    = "#27ae60",
    partial = "#e67e22",
    unserved= "#c0392b",
    grid    = "#2166ac",
    peak_bg = "#fff3cd",
    annot   = "#555555",
)

plt.rcParams.update({
    "font.family":      "DejaVu Sans",
    "axes.spines.top":  False,
    "axes.spines.right":False,
    "axes.grid":        True,
    "grid.color":       "#e0e0e0",
    "grid.linewidth":   0.5,
    "grid.alpha":       1.0,
    "xtick.direction":  "out",
    "ytick.direction":  "out",
    "xtick.major.size": 4,
    "ytick.major.size": 4,
})


def _to_pac(ts) -> datetime.datetime:
    t = pd.Timestamp(ts)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert("America/Los_Angeles").replace(tzinfo=None).to_pydatetime()


def _short_model(m: str) -> str:
    for k, v in MODEL_ABBR.items():
        m = m.replace(k, v)
    return m[:18]


def _assign_lanes(sched_df: pd.DataFrame, mix_df: pd.DataFrame) -> pd.DataFrame:
    """Assign each charging block to a physical charger lane (greedy earliest-free)."""
    sched = sched_df.copy()
    sched["t_start"] = pd.to_datetime(sched["time_step_start"], utc=True)
    sched["t_end"]   = pd.to_datetime(sched["time_step_end"],   utc=True)

    # Collapse to one row per (vehicle, charger_type): earliest start, latest end
    veh = (sched.groupby(["charging_event_id", "charger_type"])
                .agg(charge_start=("t_start", "min"),
                     charge_end=  ("t_end",   "max"),
                     energy_del=  ("energy_delivered_kwh", "sum"))
                .reset_index()
                .sort_values("charge_start"))

    # Per-type lane pool
    lane_ends: dict[str, list] = {}
    for _, r in mix_df.iterrows():
        ct = r["charger_type"]
        n  = int(r["count"])
        lane_ends[ct] = [pd.Timestamp.min.tz_localize("UTC")] * n

    # Lane offsets: 50kW lanes first, then 150kW, then 250kW
    base_offset: dict[str, int] = {}
    off = 0
    for ct in TYPE_ORDER:
        base_offset[ct] = off
        if ct in mix_df["charger_type"].values:
            off += int(mix_df.loc[mix_df["charger_type"] == ct, "count"].values[0])

    assigned_lanes = []
    for _, row in veh.iterrows():
        ct   = row["charger_type"]
        ends = lane_ends.get(ct, [pd.Timestamp.min.tz_localize("UTC")])
        best = min(range(len(ends)), key=lambda i: ends[i])
        ends[best] = max(ends[best], row["charge_end"])
        assigned_lanes.append(base_offset.get(ct, 0) + best)
    veh["lane"] = assigned_lanes
    return veh


def make_figures(site: str, date_str: str) -> None:
    meta       = SITE_META[site]
    site_label = meta["label"]
    utility    = meta["utility"]
    date_tag   = date_str.replace("-", "_")

    kmp_dir = SCENARIO_DIR / f"{site}_analysis" / "per_day" / date_str / "kempower"
    if not kmp_dir.exists():
        print(f"  [SKIP] No Kempower results at {kmp_dir}")
        return

    # Load all result files
    mix_df   = pd.read_csv(kmp_dir / "exact_milp_selected_charger_mix.csv")
    event_df = pd.read_csv(kmp_dir / "exact_milp_event_results.csv")
    sched_df = pd.read_csv(kmp_dir / "exact_milp_charging_schedule.csv")
    power_df = pd.read_csv(kmp_dir / "exact_milp_site_power_profile.csv")
    cost_df  = pd.read_csv(kmp_dir / "exact_milp_cost_breakdown.csv")

    # Cost lookup
    def _cv(comp):
        r = cost_df[cost_df["component"] == comp]
        col = "value" if "value" in cost_df.columns else "value_usd"
        return float(r[col].iloc[0]) if not r.empty else 0.0

    capex_daily  = _cv("daily_capex_cost")
    energy_cost  = _cv("energy_cost")
    demand_cost  = _cv("global_demand_cost")
    total_cost   = capex_daily + energy_cost

    # Parse event timestamps
    event_df = event_df.copy()
    event_df["arrival_time"]   = pd.to_datetime(event_df["arrival_time"],   utc=True)
    event_df["departure_time"] = pd.to_datetime(event_df["departure_time"], utc=True)

    # Sort vehicles by arrival time → V1=earliest
    event_df = event_df.sort_values("arrival_time").reset_index(drop=True)
    event_ids  = list(event_df["charging_event_id"])
    veh_num    = {ev: i+1 for i, ev in enumerate(event_ids)}
    veh_color  = {ev: VEH_PALETTE[i % len(VEH_PALETTE)] for i, ev in enumerate(event_ids)}

    def vname(ev):
        return f"V{veh_num[ev]}"

    def model_of(ev):
        row = event_df[event_df["charging_event_id"] == ev]
        if row.empty: return ""
        return _short_model(str(row.iloc[0].get("ev_equivalent_model", "")))

    def vid4(ev):
        row = event_df[event_df["charging_event_id"] == ev]
        if row.empty: return "????"
        return str(row.iloc[0].get("vehicle_id", ev))[-4:]

    def arr_pac(ev):
        row = event_df[event_df["charging_event_id"] == ev]
        if row.empty: return None
        return _to_pac(row.iloc[0]["arrival_time"])

    def dep_pac(ev):
        row = event_df[event_df["charging_event_id"] == ev]
        if row.empty: return None
        return _to_pac(row.iloc[0]["departure_time"])

    # Stats
    n_full = int((event_df["unmet_energy_kwh"] <= 0.1).sum())
    n_part = int(((event_df["delivered_energy_kwh"] > 0.1) &
                  (event_df["unmet_energy_kwh"] > 0.1)).sum())
    n_unsv = len(event_df) - n_full - n_part
    n_chars = int(mix_df["count"].sum())
    mix_str = " + ".join(
        f"{int(r['count'])}×{r['charger_type'].replace('Kempower_','')}"
        for _, r in mix_df.iterrows() if int(r["count"]) > 0)
    # Cap delivered at required (MILP time-step discretization can over-shoot by a few kWh)
    event_df["delivered_capped_kwh"] = event_df[["delivered_energy_kwh","required_energy_kwh"]].min(axis=1)
    e_del   = float(event_df["delivered_capped_kwh"].sum())
    e_unmet = float(event_df["unmet_energy_kwh"].sum())

    print(f"  {site} {date_str}: {len(event_ids)} vehicles, "
          f"{n_full} full, {n_part} partial, {n_unsv} unserved, "
          f"{n_chars} chargers ({mix_str}), ${total_cost:.0f}/day")

    # Power profile (Pacific time)
    power_df = power_df.copy()
    power_df["t_pac"] = pd.to_datetime(power_df["time_step_start"], utc=True).apply(_to_pac)
    peak_kw  = float(power_df["P_total_kw"].max()) if "P_total_kw" in power_df.columns else 0.0

    # Lane assignment
    lane_df = _assign_lanes(sched_df, mix_df)
    # Merge vehicle metadata onto lane_df
    ev_meta = event_df[["charging_event_id","arrival_time","departure_time"]].copy()
    lane_df = lane_df.merge(ev_meta, on="charging_event_id", how="left")
    n_lanes = int(mix_df["count"].sum())

    # X-axis range (Pacific)
    all_t = [arr_pac(ev) for ev in event_ids if arr_pac(ev)] + \
            [dep_pac(ev) for ev in event_ids if dep_pac(ev)]
    X0 = (min(all_t) - datetime.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    X1 = (max(all_t) + datetime.timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)

    DT15 = datetime.timedelta(minutes=5)   # Kempower uses 5-min steps

    # Lane labels
    lane_labels = []
    for ct in TYPE_ORDER:
        sub = mix_df[mix_df["charger_type"] == ct]
        if sub.empty: continue
        n = int(sub["count"].values[0])
        kw = TYPE_POWER[ct]
        for i in range(n):
            lane_labels.append(f"{kw}  #{ i+1}")

    # ── Figure 1: Operational Schedule ────────────────────────────────────────
    gantt_h = max(4.0, n_lanes * 0.75 + 2.0)
    fig1 = plt.figure(figsize=(22, 5.5 + gantt_h))
    fig1.patch.set_facecolor("white")

    gs = GridSpec(2, 1, figure=fig1,
                  height_ratios=[2.2, gantt_h],
                  hspace=0.08,
                  left=0.09, right=0.80, top=0.93, bottom=0.06)
    ax_pow  = fig1.add_subplot(gs[0])
    ax_gnt  = fig1.add_subplot(gs[1], sharex=ax_pow)

    # ── Power panel ────────────────────────────────────────────────────────────
    pt = power_df["t_pac"].tolist()
    py = power_df["P_total_kw"].tolist() if "P_total_kw" in power_df.columns else [0]*len(pt)

    # Step-fill power curve
    pt_step = pt + ([pt[-1] + datetime.timedelta(minutes=5)] if pt else [])
    py_step = py + ([py[-1]] if py else [])
    ax_pow.fill_between(pt_step, py_step, step="post", color=C["grid"], alpha=0.35)
    ax_pow.step(pt_step, py_step, where="post", color=C["grid"], lw=1.8,
                label=f"Site grid draw (peak {peak_kw:,.0f} kW)")

    # SMUD peak window 16-21h shading
    day_start = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    pk0 = day_start.replace(hour=16)
    pk1 = day_start.replace(hour=21)
    if pk0 < X1:
        ax_pow.axvspan(max(pk0, X0), min(pk1, X1), color="#fee08b", alpha=0.28,
                       label="SMUD peak window 16–21 h", zorder=0)

    ax_pow.set_ylabel("Site Grid Draw (kW)", fontsize=11)
    ax_pow.set_ylim(0, max(peak_kw, 50) * 1.22)
    ax_pow.legend(loc="upper right", fontsize=9, framealpha=0.95, edgecolor="#ccc")
    plt.setp(ax_pow.get_xticklabels(), visible=False)
    ax_pow.spines["bottom"].set_visible(False)

    # Stats box
    svc_pct = 100 * n_full / max(len(event_ids), 1)
    stats_txt = (
        "Daily Performance Summary\n"
        "==========================\n"
        f"  Charging events  {len(event_ids):>5}\n"
        f"  Fully served     {n_full:>5}  ({svc_pct:.0f}%)\n"
        f"  Partially served {n_part:>5}  ({100*n_part/max(len(event_ids),1):.0f}%)\n"
        f"  Unserved         {n_unsv:>5}\n"
        f"  Charger units    {n_chars:>5}  ({mix_str})\n"
        f"  Peak grid draw  {peak_kw:>5.0f} kW\n"
        f"  Energy delivered{e_del:>5.0f} kWh\n"
        f"  Daily cost      ${total_cost:>7.2f}"
    )
    ax_pow.text(0.012, 0.97, stats_txt,
        transform=ax_pow.transAxes, va="top", ha="left",
        fontsize=8.0, fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.55", fc="white", ec="#aaaaaa", lw=1.0, alpha=0.95),
        zorder=10)

    # ── Gantt panel ────────────────────────────────────────────────────────────
    ax_gnt.set_facecolor("#f9f9f9")
    BAR_H = 0.68
    ROW_H = 1.00
    ax_gnt.set_ylim(-0.5, n_lanes - 0.5)

    # Lane separator lines between charger types
    type_boundaries = []
    off = 0
    for ct in TYPE_ORDER:
        sub = mix_df[mix_df["charger_type"] == ct]
        if sub.empty: continue
        n = int(sub["count"].values[0])
        if off > 0:
            type_boundaries.append(off - 0.5)
        off += n

    for yb in type_boundaries:
        ax_gnt.axhline(yb, color="#888", lw=1.2, alpha=0.5, ls="--", zorder=2)

    # Idle lane backgrounds
    x0n = mdates.date2num(X0)
    x1n = mdates.date2num(X1)
    for ln in range(n_lanes):
        ax_gnt.add_patch(mpatches.FancyBboxPatch(
            (x0n, ln - BAR_H/2), x1n - x0n, BAR_H,
            boxstyle="square,pad=0",
            fc="white", ec="#e8e8e8", lw=0.3, zorder=1))

    # Draw dwell windows (translucent) then charge bars (solid, vehicle colored)
    for _, row in lane_df.iterrows():
        ev   = row["charging_event_id"]
        ln   = int(row["lane"])
        ct   = row["charger_type"]
        tc   = TYPE_COLOR.get(ct, "#888888")
        vc   = veh_color.get(ev, "#888888")
        cs   = _to_pac(row["charge_start"])
        ce   = _to_pac(row["charge_end"]) + datetime.timedelta(minutes=5)

        # Dwell window (light type color)
        arr = _to_pac(row["arrival_time"])
        dep = _to_pac(row["departure_time"]) + datetime.timedelta(minutes=5)
        ax_gnt.add_patch(mpatches.FancyBboxPatch(
            (mdates.date2num(arr), ln - BAR_H/2),
            mdates.date2num(dep) - mdates.date2num(arr), BAR_H,
            boxstyle="square,pad=0",
            fc=vc, ec="none", alpha=0.18, zorder=2))

        # Charge bar (vehicle color)
        width = mdates.date2num(ce) - mdates.date2num(cs)
        is_part  = ev in event_df["charging_event_id"].values and \
                   float(event_df.loc[event_df["charging_event_id"]==ev, "unmet_energy_kwh"].values[0]) > 0.1
        ax_gnt.add_patch(mpatches.FancyBboxPatch(
            (mdates.date2num(cs), ln - BAR_H/2), max(width, 1e-5), BAR_H,
            boxstyle="square,pad=0",
            fc=vc, ec=tc, lw=1.0 if not is_part else 0.4,
            alpha=0.88, zorder=3))

        if is_part:
            ax_gnt.add_patch(mpatches.FancyBboxPatch(
                (mdates.date2num(cs), ln - BAR_H/2), max(width, 1e-5), BAR_H,
                boxstyle="square,pad=0",
                fc="none", ec="#333", lw=0, hatch="////", alpha=0.3, zorder=4))
            ax_gnt.add_patch(mpatches.FancyBboxPatch(
                (mdates.date2num(cs), ln - BAR_H/2), max(width, 1e-5), BAR_H,
                boxstyle="square,pad=0",
                fc="none", ec=C["partial"], lw=1.6, alpha=0.9, zorder=5))

        # V-label if bar wide enough
        dur_h = (ce - cs).total_seconds() / 3600
        if dur_h >= 0.25:
            midx = mdates.date2num(cs + (ce - cs)/2)
            ax_gnt.text(midx, ln, vname(ev),
                ha="center", va="center", fontsize=7,
                fontweight="bold", color="white", clip_on=True, zorder=6)

    ax_gnt.set_yticks(range(n_lanes))
    ax_gnt.set_yticklabels(lane_labels, fontsize=8.5)
    ax_gnt.set_ylabel("Kempower Charger Lane", fontsize=10)
    ax_gnt.spines["left"].set_visible(False)
    ax_gnt.invert_yaxis()
    ax_gnt.yaxis.grid(False)

    # Charger type legend patches
    type_patches = [
        mpatches.Patch(fc=TYPE_COLOR[ct], label=TYPE_POWER[ct])
        for ct in TYPE_ORDER
        if ct in mix_df["charger_type"].values and
           int(mix_df.loc[mix_df["charger_type"]==ct, "count"].values[0]) > 0
    ]
    dwell_p = mpatches.Patch(fc="#aaaaaa", alpha=0.25, label="Dwell window (vehicle color)")
    ax_gnt.legend(handles=type_patches + [dwell_p],
                  loc="lower right", fontsize=8.5, ncol=4,
                  framealpha=0.92, edgecolor="#ccc")

    # X-axis
    ax_gnt.set_xlim(mdates.date2num(X0), mdates.date2num(X1))
    for ax in [ax_pow, ax_gnt]:
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_gnt.tick_params(axis="x", labelsize=9)
    ax_gnt.set_xlabel(f"Time — Pacific  ({date_str})", fontsize=10)

    # ── Vehicle key legend (right panel) ──────────────────────────────────────
    legend_ax = fig1.add_axes([0.815, 0.07, 0.175, 0.86])
    legend_ax.set_axis_off()
    legend_ax.text(0.0, 1.0, "Vehicle Key",
        fontsize=10, fontweight="bold", va="top", transform=legend_ax.transAxes)
    legend_ax.text(0.0, 0.964, "Hatched border = partial",
        fontsize=7.5, va="top", color=C["partial"], transform=legend_ax.transAxes)

    n_evs = len(event_ids)
    row_h = min(0.062, 0.88 / max(n_evs, 1))
    for i, ev in enumerate(event_ids):
        ev_row  = event_df[event_df["charging_event_id"] == ev]
        is_part = not ev_row.empty and float(ev_row.iloc[0]["unmet_energy_kwh"]) > 0.1
        ypos    = 0.92 - i * row_h

        rect = mpatches.FancyBboxPatch((0.0, ypos - 0.014), 0.13, 0.032,
            boxstyle="square,pad=0",
            fc=veh_color[ev],
            ec=C["partial"] if is_part else "white",
            lw=1.5 if is_part else 0.3,
            transform=legend_ax.transAxes, clip_on=False)
        legend_ax.add_patch(rect)
        if is_part:
            legend_ax.add_patch(mpatches.FancyBboxPatch((0.0, ypos - 0.014), 0.13, 0.032,
                boxstyle="square,pad=0",
                fc="none", ec="#333", lw=0, hatch="////", alpha=0.3,
                transform=legend_ax.transAxes, clip_on=False))

        fs  = max(6.0, 8.5 - n_evs * 0.12)
        tag = " *" if is_part else ""
        clr = C["partial"] if is_part else "#222"
        legend_ax.text(0.17, ypos,
            f"{vname(ev)}{tag}  #{vid4(ev)}",
            fontsize=fs, va="center", fontweight="bold" if is_part else "normal",
            color=clr, transform=legend_ax.transAxes)
        legend_ax.text(0.17, ypos - row_h*0.38,
            model_of(ev),
            fontsize=max(5.0, fs-1.5), va="center", color="#666",
            transform=legend_ax.transAxes)

    legend_ax.text(0.0, 0.92 - n_evs*row_h - 0.02,
        "* = partial service",
        fontsize=7, color=C["partial"], transform=legend_ax.transAxes)

    # ── Title ─────────────────────────────────────────────────────────────────
    fig1.text(0.44, 0.966,
        "Kempower DCFC  —  Operational Charging Schedule",
        ha="center", fontsize=15, fontweight="bold", color="#111")
    fig1.text(0.44, 0.951,
        f"{site_label}  |  {date_str}  |  {n_chars} Chargers ({mix_str})  "
        f"|  MILP Optimal Dispatch  |  Utility: {utility}",
        ha="center", fontsize=9.5, color=C["annot"])

    out1 = OUT_DIR / f"kempower_{site}_{date_tag}_schedule.png"
    fig1.savefig(out1, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"    Figure 1 saved: {out1.name}")
    plt.close(fig1)

    # ── Figure 2: Vehicle Service Summary ────────────────────────────────────
    rows = []
    for ev in event_ids:
        ev_row = event_df[event_df["charging_event_id"] == ev]
        if ev_row.empty: continue
        r     = ev_row.iloc[0]
        need  = float(r.get("required_energy_kwh", 0) or 0)
        # Cap at required: MILP 15-min time steps can over-deliver by up to one step's worth
        delv  = min(float(r.get("delivered_energy_kwh", 0) or 0), need)
        unmet = float(r.get("unmet_energy_kwh", 0) or 0)
        arr   = _to_pac(r["arrival_time"])
        dep   = _to_pac(r["departure_time"])
        dwell = (dep - arr).total_seconds() / 3600
        is_part = unmet > 0.1 and delv > 0.1
        is_unsv = delv <= 0.1 and unmet > 0.1
        # Which charger type was used?
        ct_used = None
        sl = sched_df[sched_df["charging_event_id"] == ev]
        if not sl.empty:
            ct_used = sl.iloc[0]["charger_type"]

        rows.append(dict(
            ev=ev, vn=vname(ev), vnum=veh_num[ev], vid=vid4(ev),
            model=model_of(ev), vc=veh_color[ev],
            need=need, delv=delv, unmet=unmet,
            is_part=is_part, is_unsv=is_unsv,
            dwell=dwell,
            arr=arr.strftime("%H:%M") if arr else "",
            ct_used=ct_used))
    rows.sort(key=lambda r: r["vnum"])

    fig2_h = max(8.0, len(rows) * 0.62 + 2.5)
    fig2, ax = plt.subplots(figsize=(15, fig2_h))
    fig2.patch.set_facecolor("white")
    ax.set_facecolor("white")

    max_need = max(r["need"] for r in rows) or 1.0
    n = len(rows)

    for i, r in enumerate(rows):
        y        = n - i - 1
        is_part  = r["is_part"]
        is_unsv  = r["is_unsv"]

        # Background bar (energy needed)
        ax.barh(y, r["need"], height=0.65, left=0,
                color="#e8e8e8", edgecolor="white", lw=0.4, zorder=2)

        if r["delv"] > 0:
            ax.barh(y, r["delv"], height=0.65, left=0,
                    color=r["vc"], edgecolor="white", lw=0.4, alpha=0.90, zorder=3)
            if is_part:
                ax.barh(y, r["delv"], height=0.65, left=0,
                        color="none", edgecolor="#333", lw=0, hatch="////",
                        alpha=0.25, zorder=4)

        gap = max(r["need"] - r["delv"], 0)
        if gap > 0.5:
            ax.barh(y, gap, height=0.65, left=r["delv"],
                    color="#f5c6a0" if is_part else "#dddddd",
                    edgecolor="#bbb", lw=0.3, alpha=0.6, hatch="///", zorder=3)

        if r["delv"] > 5:
            ax.text(2, y, r["vn"], ha="left", va="center",
                    fontsize=8.5, fontweight="bold", color="white", zorder=6)

        clr = C["partial"] if is_part else (C["unserved"] if is_unsv else "#222")
        ax.text(-2, y,
                f"{r['vn']}  #{r['vid']}  {r['model']}",
                ha="right", va="center", fontsize=8.5, fontweight="bold", color=clr)
        ct_lbl = r["ct_used"].replace("Kempower_", "") if r["ct_used"] else "—"
        ax.text(-2, y - 0.28,
                f"Arrive {r['arr']}  dwell {r['dwell']:.1f}h  {ct_lbl}",
                ha="right", va="center", fontsize=7, color="#666")

        if is_unsv:
            tag, tc = "UNSERVED", C["unserved"]
        elif is_part:
            tag, tc = "PARTIAL *", C["partial"]
        else:
            tag, tc = "FULL ✓", C["full"]
        ax.text(r["need"] + max_need*0.01, y,
                f"{r['delv']:.0f} / {r['need']:.0f} kWh  [{tag}]",
                ha="left", va="center", fontsize=8, color=tc, fontweight="bold")

    ax.set_xlim(-max_need*0.58, max_need*1.55)
    ax.set_ylim(-0.8, n)
    ax.set_xlabel("Energy (kWh)", fontsize=11)
    ax.set_yticks([])
    for sp in ["left","top","right"]:
        ax.spines[sp].set_visible(False)

    ax.legend(handles=[
        mpatches.Patch(fc="#aaaaaa",
                       label="Energy delivered (bar color = vehicle, border color = charger type)"),
        mpatches.Patch(fc="#f5c6a0", hatch="///", ec="#bbb",
                       label="Unmet energy gap (partial service)"),
    ], loc="lower right", fontsize=9, framealpha=0.95, edgecolor="#ccc")
    ax.text(0.68, 0.97,
        "* = partial: delivered < required  |  bars capped at required (100% SoC limit)",
        transform=ax.transAxes, fontsize=8, color=C["partial"], va="top")

    cost_line = (f"Cost:  CapEx ${capex_daily:.0f}/day  +  "
                 f"Energy ({utility}) ${energy_cost:.0f}/day  "
                 f"[demand charge ${demand_cost:.0f}/mo handled separately]  "
                 f"=  Total ${total_cost:.0f}/day")
    fig2.text(0.5, 0.01, cost_line, ha="center", va="bottom", fontsize=8,
             bbox=dict(boxstyle="round,pad=0.4", fc="#eaf4ea", ec="#27ae60", alpha=0.9))

    fig2.text(0.5, 0.97, "Vehicle Service Summary — Kempower DCFC",
        ha="center", fontsize=14, fontweight="bold", color="#111")
    fig2.text(0.5, 0.954,
        f"{site_label}  |  {date_str}  |  {n} Charging Events  "
        f"|  {n_chars} Chargers ({mix_str})  |  MILP optimal dispatch",
        ha="center", fontsize=9.5, color=C["annot"])

    plt.tight_layout(rect=[0, 0.04, 1, 0.95])

    out2 = OUT_DIR / f"kempower_{site}_{date_tag}_vehicle_summary.png"
    fig2.savefig(out2, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"    Figure 2 saved: {out2.name}")
    plt.close(fig2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True, choices=list(SITE_META))
    parser.add_argument("--date", required=True)
    args = parser.parse_args()
    make_figures(args.site, args.date)


if __name__ == "__main__":
    main()
