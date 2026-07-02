"""
plot_xos_example_day.py
-----------------------
Publication-ready XOS Hub MC02 figures for any Caltrans site and date.

Figure 1: Operational schedule — Battery SoC | Grid Power | Port-lane Gantt
Figure 2: Vehicle service summary — energy bar chart, V1–VN sorted by arrival

Usage:
    python plot_xos_example_day.py --site northgate --date 2025-12-31
    python plot_xos_example_day.py --site fresno    --date 2026-03-12
    python plot_xos_example_day.py --site glendale  --date 2026-03-23
    python plot_xos_example_day.py --site san_diego --date 2025-05-03
"""
from __future__ import annotations

import sys, argparse, importlib, datetime
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
REPO_DIR   = Path(__file__).resolve().parent
EVENTS_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT_DIR    = REPO_DIR / "xos_outputs"
OUT_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(EVENTS_DIR))
xos = importlib.import_module("xos_hub_soc_simulation")

# ── Site metadata ──────────────────────────────────────────────────────────────
SITE_META = {
    "northgate":     {"label": "Northgate Maintenance Station", "utility": "SMUD"},
    "fresno":        {"label": "Fresno Maintenance Station",    "utility": "PG&E BEV-2"},
    "glendale":      {"label": "Glendale Maintenance Station",  "utility": "PG&E BEV-2 (proxy)"},
    "glendale_smud": {"label": "Glendale Maintenance Station",  "utility": "SMUD (proxy)"},
    "san_diego":     {"label": "San Diego Maintenance Station", "utility": "SDG&E EV-HP"},
}

# Sites that share vehicle event CSVs with another site key
CSV_SITE = {"glendale_smud": "glendale"}

# ── Colors ─────────────────────────────────────────────────────────────────────
C = dict(
    full    = "#27ae60",
    partial = "#e67e22",
    grid    = "#2980b9",
    limit   = "#e74c3c",
    bg_chg  = "#ddeef8",
    sep     = "#4a4a4a",
    annot   = "#555555",
)

# 25 visually distinct vehicle colors
VEH_PALETTE = [
    "#e6194b","#3cb44b","#4363d8","#f58231","#911eb4",
    "#42d4f4","#f032e6","#bfef45","#fabed4","#469990",
    "#dcbeff","#9A6324","#000075","#800000","#aaffc3",
    "#808000","#ffd8b1","#ff7f50","#40e0d0","#dc143c",
    "#4b0082","#a9a9a9","#ffe119","#808080","#000000",
]

# Unit line colors (up to 20 units, shades of blue/teal)
UNIT_COLORS_ALL = [
    "#1a3a5c","#2980b9","#85c1e9","#17a589","#1e8449",
    "#a93226","#884ea0","#d4ac0d","#566573","#b7950b",
    "#1abc9c","#e74c3c","#9b59b6","#f39c12","#2ecc71",
    "#3498db","#e67e22","#1abc9c","#c0392b","#2c3e50",
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
}

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

PAC = pytz.timezone("America/Los_Angeles")

def t_pac(ts):
    return pd.Timestamp(ts).tz_convert(PAC).replace(tzinfo=None).to_pydatetime()


# ── Figure helpers ─────────────────────────────────────────────────────────────
def make_figures(site: str, date_str: str):
    meta       = SITE_META[site]
    site_label = meta["label"]
    utility    = meta["utility"]
    date_tag   = date_str.replace("-", "_")

    # Load events (some sites share CSVs with another site key)
    csv_site = CSV_SITE.get(site, site)
    csv_path = EVENTS_DIR / f"z2z_milp_events_{csv_site}_{date_tag}.csv"
    if not csv_path.exists():
        print(f"  [SKIP] CSV not found: {csv_path.name}")
        return

    ev_df = xos.load_events(csv_path)
    if len(ev_df) == 0:
        print(f"  [SKIP] No events for {site} {date_str}")
        return

    # Look up n_units and cost from all-days CSV
    all_days_csv = OUT_DIR / f"{site}_all_days_xos.csv"
    n_units = 3
    cost_row = {}
    if all_days_csv.exists():
        adf = pd.read_csv(str(all_days_csv))
        row = adf[adf["date"] == date_str]
        if not row.empty:
            n_units  = int(row.iloc[0]["n_xos_units"])
            cost_row = row.iloc[0].to_dict()

    # Run simulation
    p_eff  = xos.compute_p_eff(ev_df)
    result = xos.simulate_one_day(ev_df, n_units, p_eff, verbose=False)

    # Unit display properties
    unit_colors = UNIT_COLORS_ALL[:n_units]
    unit_names  = [f"Unit {k}" for k in range(n_units)]

    # XOS constants
    PGRID   = float(xos.P_GRID_KW)
    PPORT   = float(xos.P_PORT_KW)
    N_PORTS = int(xos.N_PORTS)
    ETOL    = getattr(xos, "ENERGY_TOL", 0.1)

    # Cost breakdown
    daily_cost    = cost_row.get("total_daily_cost", 0.0)
    capex_cost    = cost_row.get("capex_cost",  0.0)
    energy_cost   = cost_row.get("energy_cost", 0.0)
    demand_cost   = cost_row.get("demand_cost", 0.0)
    peak_win_cost = cost_row.get("peak_win_cost", 0.0)

    # Time series
    soc_h = result["soc_history"]
    times = [t_pac(r["time_utc"]) for r in soc_h]
    socs  = {k: np.array([r.get(f"soc_unit_{k}", 0.0)*100 for r in soc_h])
             for k in range(n_units)}
    grid  = np.array([r.get("grid_kw", 0.0) for r in soc_h])

    DT15    = datetime.timedelta(minutes=15)
    times_s = times + [times[-1] + DT15]
    grid_s  = np.append(grid, grid[-1])
    socs_s  = {k: np.append(socs[k], socs[k][-1]) for k in range(n_units)}

    # Vehicle metadata
    ev_lk     = ev_df.set_index("charging_event_id")
    delivered  = result["delivered"]
    event_ids  = list(ev_df["charging_event_id"])

    def inf(ev):
        return ev_lk.loc[ev] if ev in ev_lk.index else {}

    def model_of(ev):
        m = str(inf(ev).get("ev_equivalent_model", ""))
        for k, v in MODEL_ABBR.items():
            m = m.replace(k, v)
        return m[:18]

    def vid4(ev):
        return str(inf(ev).get("vehicle_id", ev))[-4:]

    def arr_time(ev):
        i = inf(ev)
        if "arrival_time" in i:
            return t_pac(pd.Timestamp(i["arrival_time"]))
        return datetime.datetime.max

    def status_of(ev):
        need  = float(inf(ev).get("energy_needed_kwh_for_visit", 0) or 0)
        deliv = delivered.get(ev, 0.0)
        if max(need - deliv, 0) <= ETOL: return "FULL",    C["full"]
        if deliv > ETOL:                  return "partial", C["partial"]
        return "unserved", C["limit"]

    # V-numbering
    sorted_evs = sorted(event_ids, key=arr_time)
    veh_num    = {ev: i+1 for i, ev in enumerate(sorted_evs)}
    veh_color  = {ev: VEH_PALETTE[i % len(VEH_PALETTE)]
                  for i, ev in enumerate(sorted_evs)}

    def vname(ev):
        return f"V{veh_num[ev]}"

    # Stats
    n_full = sum(1 for ev in event_ids if status_of(ev)[0] == "FULL")
    n_part = sum(1 for ev in event_ids if status_of(ev)[0] == "partial")
    n_unsv = len(event_ids) - n_full - n_part
    total_del  = sum(delivered.values())
    peak_grid  = grid.max()

    print(f"  {site} {date_str}: {len(event_ids)} events, "
          f"{n_full} full, {n_part} partial, {n_unsv} unserved, "
          f"{n_units} units, ${daily_cost:.0f}/day")

    # X-axis range
    all_arrs = [t_pac(pd.Timestamp(inf(ev)["arrival_time"])) for ev in event_ids
                if "arrival_time" in inf(ev)]
    all_deps = [t_pac(pd.Timestamp(inf(ev)["departure_time"])) for ev in event_ids
                if "departure_time" in inf(ev)]
    if all_arrs and all_deps:
        X0 = (min(all_arrs) - datetime.timedelta(hours=1)).replace(minute=0, second=0)
        X1 = (max(all_deps) + datetime.timedelta(hours=2)).replace(minute=0, second=0)
    else:
        day = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        X0, X1 = day, day + datetime.timedelta(hours=24)

    # Dispatch blocks
    raw = defaultdict(list)
    for log in result["dispatch_log"]:
        raw[log["unit"]].append((t_pac(log["time_utc"]), log["event_id"]))

    def merge_raw(slots):
        if not slots: return []
        slots = sorted(slots)
        out  = []
        t0, ev0, tp = slots[0][0], slots[0][1], slots[0][0]
        for t, ev in slots[1:]:
            if ev == ev0 and (t - tp).total_seconds() <= 901:
                tp = t
            else:
                out.append((t0, tp, ev0))
                t0, ev0, tp = t, ev, t
        return out + [(t0, tp, ev0)]

    def assign_ports(bars):
        bars_s    = sorted(bars, key=lambda x: x[0])
        port_free = [datetime.datetime.min] * N_PORTS
        ev_port   = {}
        out       = []
        for (t_s, t_e, ev) in bars_s:
            t_end = t_e + DT15
            if ev in ev_port:
                p = ev_port[ev]
                port_free[p] = max(port_free[p], t_end)
            else:
                p = min(range(N_PORTS), key=lambda x: port_free[x])
                if port_free[p] > t_s:
                    p = 0
                ev_port[ev] = p
                port_free[p] = max(port_free[p], t_end)
            out.append((t_s, t_e, ev, p))
        return out

    unit_bars = {u: assign_ports(merge_raw(raw[u])) for u in range(n_units)}

    def grid_wins(u):
        wins, on, t0 = [], False, None
        for i in range(1, len(times)):
            rising = socs[u][i] > socs[u][i-1] + 0.05
            if rising and not on:
                t0, on = times[i-1], True
            elif not rising and on:
                wins.append((t0, times[i]))
                on = False
        if on:
            wins.append((t0, times[-1]))
        return wins

    # Gantt layout
    PORT_H = 0.72
    ROW_H  = 1.00
    UNIT_G = 0.55

    unit_y0 = {}
    y = 0.0
    for u in reversed(range(n_units)):
        unit_y0[u] = y
        y += N_PORTS * ROW_H + UNIT_G
    GANTT_YMAX = y - UNIT_G + 0.2

    def bar_y(u, p):
        return unit_y0[u] + p * ROW_H + (ROW_H - PORT_H) / 2

    def row_center(u, p):
        return unit_y0[u] + p * ROW_H + ROW_H / 2

    def unit_center(u):
        return unit_y0[u] + (N_PORTS * ROW_H) / 2

    # ── Figure 1: Operational Schedule ─────────────────────────────────────────
    gantt_h = max(5.0, n_units * N_PORTS * 0.55 + 2.0)
    fig_h   = 5.0 + 2.0 + gantt_h
    fig1    = plt.figure(figsize=(22, fig_h))
    fig1.patch.set_facecolor("white")

    gs = GridSpec(3, 1, figure=fig1,
                  height_ratios=[2.0, 0.80, gantt_h],
                  hspace=0.06,
                  left=0.09, right=0.80, top=0.93, bottom=0.06)
    ax_soc   = fig1.add_subplot(gs[0])
    ax_grid  = fig1.add_subplot(gs[1], sharex=ax_soc)
    ax_gantt = fig1.add_subplot(gs[2], sharex=ax_soc)

    # SoC panel
    for k in range(n_units):
        ax_soc.fill_between(times_s, socs_s[k], alpha=0.08,
                            color=unit_colors[k], step="post")
        ax_soc.step(times_s, socs_s[k], where="post",
                    color=unit_colors[k], lw=2.0, label=unit_names[k], zorder=3)
    ax_soc.axhline(float(xos.SOC_MIN)*100, color=C["limit"], ls="--", lw=1.4,
                   alpha=0.85, label=f"Min SoC {xos.SOC_MIN*100:.0f}%", zorder=4)
    ax_soc.set_ylabel("Battery SoC (%)", fontsize=11)
    ax_soc.set_ylim(0, 115)
    ax_soc.set_yticks([0, 20, 40, 60, 80, 100])
    ncol_leg = min(n_units + 1, 5)
    ax_soc.legend(loc="lower right", fontsize=8.5, ncol=ncol_leg,
                  framealpha=0.95, edgecolor="#ccc")
    plt.setp(ax_soc.get_xticklabels(), visible=False)
    ax_soc.spines["bottom"].set_visible(False)

    svc_pct = 100*n_full/len(event_ids) if event_ids else 0
    stats_txt = (
        "Daily Performance Summary\n"
        "==========================\n"
        f"  Charging events  {len(event_ids):>5}\n"
        f"  Fully served     {n_full:>5}  ({svc_pct:.0f}%)\n"
        f"  Partially served {n_part:>5}  ({100*n_part/max(len(event_ids),1):.0f}%)\n"
        f"  Unserved         {n_unsv:>5}\n"
        f"  XOS units        {n_units:>5}\n"
        f"  Peak grid draw  {peak_grid:>5.0f} kW\n"
        f"  Energy delivered{total_del:>5.0f} kWh\n"
        f"  Daily cost      ${daily_cost:>7.2f}"
    )
    ax_soc.text(0.012, 0.97, stats_txt,
        transform=ax_soc.transAxes,
        va="top", ha="left", fontsize=8.2, fontfamily="monospace",
        bbox=dict(boxstyle="round,pad=0.55", fc="white", ec="#aaaaaa", lw=1.0, alpha=0.95),
        zorder=10)

    # Grid power panel
    ax_grid.fill_between(times_s, grid_s, step="post", color=C["grid"], alpha=0.55)
    ax_grid.step(times_s, grid_s, where="post", color=C["grid"], lw=1.0)
    grid_cap = PGRID * n_units
    ax_grid.axhline(grid_cap, color=C["limit"], ls="--", lw=1.3, alpha=0.85,
                    label=f"Grid cap  {PGRID:.0f}×{n_units} = {grid_cap:.0f} kW", zorder=4)
    ax_grid.set_ylabel("Grid\nPower (kW)", fontsize=10)
    ax_grid.set_ylim(0, grid_cap * 1.22)
    yticks = [int(PGRID*k) for k in range(n_units+1)]
    ax_grid.set_yticks(yticks[:5])
    ax_grid.legend(loc="upper right", fontsize=8.5, framealpha=0.95, edgecolor="#ccc")
    ax_grid.spines["bottom"].set_visible(False)
    plt.setp(ax_grid.get_xticklabels(), visible=False)

    # Gantt panel
    ax_gantt.set_facecolor("#f9f9f9")
    ax_gantt.set_ylim(-0.15, GANTT_YMAX)
    ax_gantt.yaxis.grid(False)

    for u in range(n_units):
        for (t0, t1) in grid_wins(u):
            t1d = t1 + DT15
            ax_gantt.add_patch(mpatches.FancyBboxPatch(
                (mdates.date2num(t0), unit_y0[u] - 0.03),
                mdates.date2num(t1d) - mdates.date2num(t0),
                N_PORTS*ROW_H + 0.06,
                boxstyle="square,pad=0",
                fc=C["bg_chg"], ec="none", alpha=0.55, zorder=1))

        for p in range(N_PORTS):
            ax_gantt.add_patch(mpatches.FancyBboxPatch(
                (mdates.date2num(X0), bar_y(u, p)),
                mdates.date2num(X1) - mdates.date2num(X0),
                PORT_H,
                boxstyle="square,pad=0",
                fc="white", ec="#e8e8e8", lw=0.4, zorder=2))

        for (t_s, t_e, ev, p) in unit_bars[u]:
            t_ed  = t_e + DT15
            dur_h = (t_ed - t_s).total_seconds() / 3600
            stat, _ = status_of(ev)
            is_part = (stat == "partial")
            fc = veh_color[ev]

            ax_gantt.add_patch(mpatches.FancyBboxPatch(
                (mdates.date2num(t_s), bar_y(u, p)),
                mdates.date2num(t_ed) - mdates.date2num(t_s), PORT_H,
                boxstyle="square,pad=0",
                fc=fc, ec="white", lw=0.5, alpha=0.90, zorder=3))

            if is_part:
                ax_gantt.add_patch(mpatches.FancyBboxPatch(
                    (mdates.date2num(t_s), bar_y(u, p)),
                    mdates.date2num(t_ed) - mdates.date2num(t_s), PORT_H,
                    boxstyle="square,pad=0",
                    fc="none", ec="#333333", lw=0.0, hatch="////", alpha=0.35, zorder=4))
                ax_gantt.add_patch(mpatches.FancyBboxPatch(
                    (mdates.date2num(t_s), bar_y(u, p)),
                    mdates.date2num(t_ed) - mdates.date2num(t_s), PORT_H,
                    boxstyle="square,pad=0",
                    fc="none", ec=C["partial"], lw=1.5, alpha=0.85, zorder=5))

            if dur_h >= 0.35:
                midx = mdates.date2num(t_s + (t_ed - t_s) / 2)
                midy = row_center(u, p)
                ax_gantt.text(midx, midy, vname(ev),
                    ha="center", va="center", fontsize=7.5,
                    fontweight="bold", color="white", clip_on=True, zorder=6)

        if u > 0:
            sep_y = unit_y0[u] + N_PORTS*ROW_H + UNIT_G*0.5
            ax_gantt.axhline(sep_y - UNIT_G*0.1, color=C["sep"], lw=1.0, alpha=0.4, zorder=6)

        ax_gantt.text(-0.005, unit_center(u),
            f"Unit {u}\n({N_PORTS} ports)",
            transform=ax_gantt.get_yaxis_transform(),
            ha="right", va="center", fontsize=9,
            fontweight="bold", color=unit_colors[u])

        for p in range(N_PORTS):
            ax_gantt.text(-0.001, row_center(u, p), f"P{p}",
                transform=ax_gantt.get_yaxis_transform(),
                ha="right", va="center", fontsize=7, color="#888")

    ax_gantt.set_yticks([])
    ax_gantt.set_ylabel("")
    ax_gantt.spines["left"].set_visible(False)

    ax_gantt.set_xlim(mdates.date2num(X0), mdates.date2num(X1))
    for ax in [ax_soc, ax_grid, ax_gantt]:
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax_gantt.tick_params(axis="x", labelsize=9)
    ax_gantt.set_xlabel(f"Time — Pacific  ({date_str})", fontsize=10)

    # Vehicle key legend
    legend_ax = fig1.add_axes([0.815, 0.07, 0.175, 0.86])
    legend_ax.set_axis_off()
    legend_ax.text(0.0, 1.0, "Vehicle Key",
        fontsize=10, fontweight="bold", va="top", transform=legend_ax.transAxes)
    legend_ax.text(0.0, 0.965, "Hatched = partial service",
        fontsize=7.5, va="top", color=C["partial"], transform=legend_ax.transAxes)

    n_evs = len(sorted_evs)
    row_h = min(0.062, 0.88 / max(n_evs, 1))
    for i, ev in enumerate(sorted_evs):
        stat, _ = status_of(ev)
        is_part = (stat == "partial")
        ypos    = 0.92 - i * row_h

        rect = mpatches.FancyBboxPatch((0.0, ypos - 0.015), 0.13, 0.035,
            boxstyle="square,pad=0",
            fc=veh_color[ev],
            ec=C["partial"] if is_part else "white",
            lw=1.5 if is_part else 0.3,
            transform=legend_ax.transAxes, clip_on=False)
        legend_ax.add_patch(rect)
        if is_part:
            rect2 = mpatches.FancyBboxPatch((0.0, ypos - 0.015), 0.13, 0.035,
                boxstyle="square,pad=0",
                fc="none", ec="#333", lw=0.0, hatch="////", alpha=0.3,
                transform=legend_ax.transAxes, clip_on=False)
            legend_ax.add_patch(rect2)

        fs   = max(6.5, 8.5 - n_evs * 0.15)
        tag  = " *" if is_part else ""
        clr  = C["partial"] if is_part else "#222"
        legend_ax.text(0.17, ypos,
            f"{vname(ev)}{tag}  #{vid4(ev)}",
            fontsize=fs, va="center", fontweight="bold" if is_part else "normal",
            color=clr, transform=legend_ax.transAxes)
        legend_ax.text(0.17, ypos - row_h*0.38,
            model_of(ev),
            fontsize=max(5.5, fs-1.5), va="center", color="#666",
            transform=legend_ax.transAxes)

    legend_ax.text(0.0, 0.92 - n_evs*row_h - 0.02,
        "* = partial (80 kW port cap)",
        fontsize=7, color=C["partial"], transform=legend_ax.transAxes)

    fig1.text(0.44, 0.966,
        "XOS Hub MC02  —  Operational Charging Schedule",
        ha="center", fontsize=15, fontweight="bold", color="#111")
    fig1.text(0.44, 0.951,
        f"{site_label}  |  {date_str}  |  {n_units} Units Deployed  "
        f"|  Battery 280 kWh/unit  |  Grid input 83 kW/unit  "
        f"|  4 ports × 80 kW/port  |  Utility: {utility}",
        ha="center", fontsize=9.5, color=C["annot"])

    out1 = OUT_DIR / f"{site}_{date_tag}_schedule.png"
    fig1.savefig(out1, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"    Figure 1 saved: {out1.name}")
    plt.close(fig1)

    # ── Figure 2: Vehicle Service Summary ──────────────────────────────────────
    rows = []
    for ev in event_ids:
        i    = inf(ev)
        need = float(i.get("energy_needed_kwh_for_visit", 0) or 0)
        delv = delivered.get(ev, 0.0)
        stat, sc = status_of(ev)
        bat  = float(i.get("battery_capacity_kwh", 0) or 0)
        soc0 = float(i.get("assumed_initial_soc_percent", 0) or 0)
        soc1 = min(100.0, soc0 + 100*delv/bat) if bat > 0 else soc0
        arr  = t_pac(pd.Timestamp(i["arrival_time"]))  if "arrival_time"  in i else None
        dep  = t_pac(pd.Timestamp(i["departure_time"])) if "departure_time" in i else None
        dwell = (dep - arr).total_seconds()/3600 if arr and dep else 0.0
        rows.append(dict(
            ev=ev, vid=vid4(ev), model=model_of(ev),
            need=need, delv=delv, stat=stat, sc=sc,
            vc=veh_color[ev], vn=vname(ev), vnum=veh_num[ev],
            soc0=soc0, soc1=soc1, bat=bat, dwell=dwell,
            arr=arr.strftime("%H:%M") if arr else ""))
    rows.sort(key=lambda r: r["vnum"])

    fig2_h = max(8.0, len(rows) * 0.62 + 2.5)
    fig2, ax = plt.subplots(figsize=(15, fig2_h))
    fig2.patch.set_facecolor("white")
    ax.set_facecolor("white")

    max_need = max(r["need"] for r in rows) or 1.0
    n = len(rows)
    BAR_W = 0.65

    for i, r in enumerate(rows):
        y       = n - i - 1
        is_part = (r["stat"] == "partial")
        is_unsv = (r["stat"] == "unserved")

        ax.barh(y, r["need"], height=BAR_W, left=0,
                color="#e8e8e8", edgecolor="white", lw=0.4, zorder=2)

        if r["delv"] > 0:
            ax.barh(y, r["delv"], height=BAR_W, left=0,
                    color=r["vc"], edgecolor="white", lw=0.4, alpha=0.90, zorder=3)
            if is_part:
                ax.barh(y, r["delv"], height=BAR_W, left=0,
                        color="none", edgecolor="#333", lw=0.0,
                        hatch="////", alpha=0.25, zorder=4)

        gap = max(r["need"] - r["delv"], 0)
        if gap > 0.5:
            ax.barh(y, gap, height=BAR_W, left=r["delv"],
                    color="#f5c6a0" if is_part else "#dddddd",
                    edgecolor="#bbb", lw=0.3, alpha=0.6,
                    hatch="///", zorder=3)

        if r["delv"] > 5:
            ax.text(2, y, r["vn"],
                    ha="left", va="center", fontsize=8.5, fontweight="bold",
                    color="white", zorder=6)

        ax.text(-2, y,
                f"{r['vn']}  #{r['vid']}  {r['model']}",
                ha="right", va="center", fontsize=8.5, fontweight="bold",
                color=C["partial"] if is_part else ("#c0392b" if is_unsv else "#222"))
        ax.text(-2, y - 0.28,
                f"Arrive {r['arr']}  dwell {r['dwell']:.1f}h  "
                f"SoC {r['soc0']:.0f}%→{r['soc1']:.0f}%",
                ha="right", va="center", fontsize=7, color="#666")

        if is_unsv:
            tag, clr = "UNSERVED", "#c0392b"
        elif is_part:
            tag, clr = "PARTIAL *", C["partial"]
        else:
            tag, clr = "FULL", "#1e8449"
        ax.text(r["need"] + max_need*0.01, y,
                f"{r['delv']:.0f} / {r['need']:.0f} kWh  [{tag}]",
                ha="left", va="center", fontsize=8, color=clr, fontweight="bold")

    ax.set_xlim(-max_need*0.58, max_need*1.55)
    ax.set_ylim(-0.8, n)
    ax.set_xlabel("Energy (kWh)", fontsize=11)
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    ax.legend(handles=[
        mpatches.Patch(fc="#aaaaaa",
                       label="Energy delivered (bar color = vehicle color from schedule)"),
        mpatches.Patch(fc="#f5c6a0", hatch="///", ec="#bbb",
                       label="Unmet energy gap (partial/unserved)"),
    ], loc="lower right", fontsize=9, framealpha=0.95, edgecolor="#ccc")
    ax.text(0.70, 0.97, "* = partial service: 80 kW CCS1 cap × 1 h dwell ≤ 76 kWh max",
        transform=ax.transAxes, fontsize=8, color=C["partial"], va="top")

    cost_parts = [f"CapEx ${capex_cost:.0f}/day"]
    if energy_cost:   cost_parts.append(f"Energy ({utility}) ${energy_cost:.0f}/day")
    if demand_cost:   cost_parts.append(f"Demand ${demand_cost:.0f}/day")
    if peak_win_cost: cost_parts.append(f"Peak-win ${peak_win_cost:.0f}/day")
    cost_line = "Cost:  " + "  +  ".join(cost_parts) + f"  =  Total ${daily_cost:.0f}/day"
    fig2.text(0.5, 0.01, cost_line, ha="center", va="bottom", fontsize=8,
             bbox=dict(boxstyle="round,pad=0.4", fc="#eaf4ea", ec="#27ae60", alpha=0.9))

    fig2.text(0.5, 0.97, "Vehicle Service Summary",
        ha="center", fontsize=14, fontweight="bold", color="#111")
    fig2.text(0.5, 0.954,
        f"{site_label}  |  {date_str}  |  {n} Charging Events  "
        f"|  V1–V{n} sorted by arrival time  |  {n_units} XOS Units",
        ha="center", fontsize=9.5, color=C["annot"])

    plt.tight_layout(rect=[0, 0.04, 1, 0.95])

    out2 = OUT_DIR / f"{site}_{date_tag}_vehicle_summary.png"
    fig2.savefig(out2, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"    Figure 2 saved: {out2.name}")
    plt.close(fig2)


# ── Entry point ────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", required=True, choices=list(SITE_META),
                        help="Site slug: northgate | fresno | glendale | glendale_smud | san_diego")
    parser.add_argument("--date", required=True,
                        help="Date string YYYY-MM-DD")
    args = parser.parse_args()
    make_figures(args.site, args.date)


if __name__ == "__main__":
    main()
