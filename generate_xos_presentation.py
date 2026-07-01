"""
generate_xos_presentation.py
============================
Generate a multi-slide PDF presentation of XOS Hub MC02 results
for all 4 Caltrans sites (Northgate, Fresno, Glendale, San Diego).

Slides:
  1  Title
  2  Project Overview
  3  XOS Hub MC02 Specs
  4  A1 vs A2 Scenario Explanation
  5  All-Sites Summary Table
  6  Coverage Curves — all 4 sites (K-sweep)
  7  A2 Grid Input Power & Energy Profile — all sites
  8  Top-10 Worst Days — Northgate
  9  Top-10 Worst Days — Fresno
 10  Top-10 Worst Days — Glendale
 11  Top-10 Worst Days — San Diego
 12  Shima Coverage Analysis — all sites
 13  Conclusions

Output: scenario_outputs/XOS_Hub_MC02_Presentation.pdf
"""
from __future__ import annotations
import sys
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import FancyBboxPatch

sys.stdout.reconfigure(encoding="utf-8")

BASE  = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT   = BASE / "scenario_outputs"
PDF   = OUT / "XOS_Hub_MC02_Presentation.pdf"

W, H  = 16, 9   # slide size (inches, 16:9)

SITES = [
    ("northgate", "Northgate"),
    ("fresno",    "Fresno"),
    ("glendale",  "Glendale"),
    ("san_diego", "San Diego"),
]

SITE_COLORS = {
    "northgate": "#1f77b4",
    "fresno":    "#ff7f0e",
    "glendale":  "#2ca02c",
    "san_diego": "#d62728",
}

# ── style helpers ──────────────────────────────────────────────────────────────

TITLE_BG  = "#1a3a5c"
TITLE_FG  = "white"
HDR_BG    = "#2c5f8a"
HDR_FG    = "white"
BODY_BG   = "#f5f8fc"
ACCENT    = "#e8722a"
DARK_GRAY = "#333333"
MID_GRAY  = "#666666"
LIGHT_GRAY= "#cccccc"

def _fig(title_text=None):
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor(BODY_BG)
    if title_text:
        fig.text(0.5, 0.965, title_text,
                 ha="center", va="top", fontsize=18, fontweight="bold",
                 color=TITLE_BG)
        fig.add_axes([0, 0.935, 1, 0.003]).set_facecolor(ACCENT)
        plt.gca().axis("off")
    return fig


def _footer(fig, slide_n, total):
    fig.text(0.01, 0.012, "Caltrans Mobile DCFC Analysis — XOS Hub MC02",
             ha="left", va="bottom", fontsize=7.5, color=MID_GRAY)
    fig.text(0.99, 0.012, f"Slide {slide_n} / {total}   |   {datetime.now().strftime('%Y-%m-%d')}",
             ha="right", va="bottom", fontsize=7.5, color=MID_GRAY)
    fig.add_axes([0, 0, 1, 0.002]).set_facecolor(TITLE_BG)
    plt.gca().axis("off")


def _table(ax, col_labels, rows, col_widths=None, header_bg=HDR_BG,
           row_colors=None, fontsize=10):
    ax.axis("off")
    n_rows = len(rows); n_cols = len(col_labels)
    col_widths = col_widths or [1/n_cols]*n_cols
    row_h = 1.0 / (n_rows + 1.5)
    col_x = [sum(col_widths[:i]) for i in range(n_cols)]

    # Header
    for ci, (lbl, cx, cw) in enumerate(zip(col_labels, col_x, col_widths)):
        ax.add_patch(FancyBboxPatch((cx, 1 - row_h), cw, row_h,
            boxstyle="square,pad=0", facecolor=header_bg, edgecolor="white",
            linewidth=0.8, transform=ax.transAxes, clip_on=True))
        ax.text(cx + cw/2, 1 - row_h/2, lbl,
                ha="center", va="center", fontsize=fontsize, fontweight="bold",
                color=HDR_FG, transform=ax.transAxes, clip_on=True)

    # Rows
    for ri, row in enumerate(rows):
        bg = (row_colors[ri] if row_colors else
              ("#e8f0f8" if ri % 2 == 0 else "white"))
        y_top = 1 - (ri + 2) * row_h
        for ci, (val, cx, cw) in enumerate(zip(row, col_x, col_widths)):
            ax.add_patch(FancyBboxPatch((cx, y_top), cw, row_h,
                boxstyle="square,pad=0", facecolor=bg, edgecolor=LIGHT_GRAY,
                linewidth=0.4, transform=ax.transAxes, clip_on=True))
            ax.text(cx + cw/2, y_top + row_h/2, str(val),
                    ha="center", va="center", fontsize=fontsize,
                    color=DARK_GRAY, transform=ax.transAxes, clip_on=True)


# ── load data ──────────────────────────────────────────────────────────────────

def _load(site):
    d   = OUT / f"{site}_analysis"
    wd  = d / "worst_days"
    return {
        "summary":   pd.read_csv(d  / f"{site}_summary.csv"),
        "cost":      pd.read_csv(d  / f"{site}_cost_detail.csv"),
        "coverage":  pd.read_csv(wd / "coverage_analysis.csv"),
        "ksweep":    pd.read_csv(wd / "k_sweep_coverage.csv"),
    }

DATA = {s: _load(s) for s, _ in SITES}


# ══════════════════════════════════════════════════════════════════════════════
# SLIDES
# ══════════════════════════════════════════════════════════════════════════════

def slide_title(pdf, n, total):
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor(TITLE_BG)

    # Big title
    fig.text(0.5, 0.62, "Mobile DCFC Fleet Charging Analysis",
             ha="center", va="center", fontsize=32, fontweight="bold", color=TITLE_FG)
    fig.text(0.5, 0.52, "XOS Hub MC02 — Scenarios A1 & A2",
             ha="center", va="center", fontsize=22, color="#aad4f5")
    fig.text(0.5, 0.43, "Worst-Day Analysis & Coverage Optimization Across 4 Caltrans Sites",
             ha="center", va="center", fontsize=14, color="#ccddee")

    # Divider
    fig.add_axes([0.1, 0.37, 0.8, 0.003]).set_facecolor(ACCENT)
    plt.gca().axis("off")

    # Sub-info
    fig.text(0.5, 0.30, "Sites:  Northgate  ·  Fresno  ·  Glendale  ·  San Diego",
             ha="center", va="center", fontsize=13, color=TITLE_FG)
    fig.text(0.5, 0.23, f"Analysis Period: May 2025 – Apr 2026   |   Total days: 1,214",
             ha="center", va="center", fontsize=12, color="#aad4f5")
    fig.text(0.5, 0.16, f"Generated: {datetime.now().strftime('%B %d, %Y')}",
             ha="center", va="center", fontsize=10, color="#8899aa")

    # Footer bar
    fig.add_axes([0, 0, 1, 0.004]).set_facecolor(ACCENT)
    plt.gca().axis("off")
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def slide_overview(pdf, n, total):
    fig = _fig("Project Overview")
    ax  = fig.add_axes([0.04, 0.08, 0.92, 0.82])
    ax.axis("off")

    # Two-column layout with text blocks
    cols = [
        ("OBJECTIVE", [
            "Size mobile DCFC deployments for Caltrans EV fleets",
            "at 4 maintenance sites across California.",
            "",
            "For each site-day: find the minimum number of",
            "XOS Hub MC02 units needed to maximally serve all",
            "arriving electric vehicles within their dwell windows.",
            "",
            "Compare two operating scenarios (A1 vs A2) and",
            "identify worst-case days requiring the most resources.",
        ], 0.02, 0.50),
        ("METHODOLOGY", [
            "Data:  Zone-to-zone Geotab trip records, SMUD TOU rates",
            "",
            "Simulation:  15-min discrete-time state machine per hub",
            "  • Adaptive-K search: min K to fully serve all vehicles",
            "  • Proactive recharge: hub self-charges when all ports idle",
            "    and a vehicle needing ≥5 kWh is approaching",
            "",
            "Cost model:  CAPEX amortization + energy (TOU) +",
            "  SMUD demand charges (global + peak window)",
            "",
            "Coverage analysis (Shima method): fix K from worst day,",
            "measure % of all days that K can fully serve.",
        ], 0.52, 0.46),
    ]

    for title, lines, x0, w in cols:
        # box
        ax.add_patch(FancyBboxPatch((x0, 0.02), w, 0.90,
            boxstyle="round,pad=0.01", facecolor="white",
            edgecolor=TITLE_BG, linewidth=1.2,
            transform=ax.transAxes, clip_on=False))
        ax.text(x0 + w/2, 0.90, title,
                ha="center", va="center", fontsize=13, fontweight="bold",
                color=TITLE_BG, transform=ax.transAxes)
        ax.add_patch(FancyBboxPatch((x0+0.01, 0.83), w-0.02, 0.002,
            boxstyle="square,pad=0", facecolor=ACCENT,
            transform=ax.transAxes, clip_on=False))
        for i, line in enumerate(lines):
            ax.text(x0 + 0.025, 0.78 - i*0.075, line,
                    ha="left", va="top", fontsize=9.5, color=DARK_GRAY,
                    transform=ax.transAxes)

    _footer(fig, n, total)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def slide_xos_specs(pdf, n, total):
    fig = _fig("XOS Hub MC02 — Specifications & Operating Logic")

    # Left: spec table
    ax_spec = fig.add_axes([0.03, 0.10, 0.40, 0.80])
    specs = [
        ("Battery capacity",     "280 kWh"),
        ("Usable (SOC 20–100%)", "225.6 kWh"),
        ("Grid charge rate",     "83 kW"),
        ("Charge efficiency η_c","95%"),
        ("Discharge efficiency η_d","95%"),
        ("CCS1 ports per unit",  "4 ports"),
        ("Max power per port",   "80 kW"),
        ("Max units deployed",   "20 units"),
        ("Time step",            "15 min"),
    ]
    _table(ax_spec, ["Parameter", "Value"], specs,
           col_widths=[0.62, 0.38], fontsize=10.5)
    ax_spec.set_title("Hardware Specifications", fontsize=12,
                       fontweight="bold", color=TITLE_BG, pad=8)

    # Right: A1 vs A2 diagram
    ax_d = fig.add_axes([0.47, 0.10, 0.51, 0.80])
    ax_d.axis("off")

    scenarios = [
        ("Scenario A1\n— Always Grid-Connected —", "#1f77b4", [
            "Hub remains connected to grid at all times.",
            "Vehicles stay on the charging port even while",
            "  the hub battery is being recharged from grid.",
            "Grid draws from BOTH recharge + vehicle service",
            "  can overlap (additive grid demand).",
            "Higher peak grid draw, lower latency for vehicles.",
        ]),
        ("Scenario A2\n— Disconnect at 20% SOC —", "#d62728", [
            "Hub disconnects from vehicles when battery",
            "  drops to SOC_MIN = 20%.",
            "Hub recharges from grid (no vehicle service).",
            "Vehicles wait or are deferred until SOC recovers.",
            "Lower peak grid load, more vehicle queuing.",
            "Proactive recharge: hub self-charges when idle",
            "  if SOC < 95% and no vehicle needs ≥5 kWh.",
        ]),
    ]

    y0s = [0.92, 0.42]
    for (title, color, lines), y0 in zip(scenarios, y0s):
        ax_d.add_patch(FancyBboxPatch((0, y0-0.42), 1.0, 0.44,
            boxstyle="round,pad=0.01", facecolor=color+"18",
            edgecolor=color, linewidth=1.5,
            transform=ax_d.transAxes, clip_on=False))
        ax_d.text(0.5, y0, title, ha="center", va="top",
                  fontsize=11.5, fontweight="bold", color=color,
                  transform=ax_d.transAxes)
        for i, line in enumerate(lines):
            ax_d.text(0.06, y0 - 0.07 - i*0.058, line,
                      ha="left", va="top", fontsize=9.2, color=DARK_GRAY,
                      transform=ax_d.transAxes)
    ax_d.set_title("Operating Scenarios", fontsize=12,
                    fontweight="bold", color=TITLE_BG, pad=8)

    _footer(fig, n, total)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def slide_site_summary(pdf, n, total):
    fig = _fig("All-Sites Summary — XOS Hub MC02 (Scenario A2)")

    # Build rows from data
    rows = []
    for site, label in SITES:
        df  = DATA[site]["summary"]
        dc  = DATA[site]["cost"]
        ks  = DATA[site]["ksweep"]
        a2  = df[df["scenario"] == "A2"]
        dc2 = dc[dc["scenario"] == "A2"]
        if a2.empty: continue

        n_days   = len(a2)
        n_veh    = int(a2["n_vehicles"].sum())
        n_full   = int(a2["n_fully_served"].sum())
        pct_full = f"{100*n_full/max(n_veh,1):.1f}%"
        k_avg    = f"{a2['K'].mean():.1f}"
        k_max    = int(a2['K'].max())
        cost_avg = f"${dc2['total_daily_excl_demand'].mean():.0f}"
        cost_max = f"${dc2['total_daily_excl_demand'].max():.0f}"
        peak_avg = f"{dc2['peak_grid_kw'].mean():.0f} kW"
        peak_max = f"{dc2['peak_grid_kw'].max():.0f} kW"
        # coverage ceiling
        ceil_row = ks[ks["pct_fully_covered"] == ks["pct_fully_covered"].max()]
        ceil_k   = int(ceil_row["K"].min())
        ceil_pct = f"{ks['pct_fully_covered'].max():.1f}%"

        rows.append([label, str(n_days), f"{n_veh:,}", pct_full,
                     k_avg, str(k_max), cost_avg, cost_max,
                     peak_max, f"K={ceil_k} → {ceil_pct}"])

    cols = ["Site", "Days", "Vehicles", "Fully\nServed",
            "Avg K", "Max K", "Cost\nAvg/day", "Cost\nWorst day",
            "Peak Grid\n(worst)", "Coverage\nCeiling"]
    cw   = [0.10, 0.07, 0.09, 0.08, 0.07, 0.07, 0.09, 0.10, 0.10, 0.13]

    ax = fig.add_axes([0.02, 0.12, 0.96, 0.73])
    _table(ax, cols, rows, col_widths=cw, fontsize=10)
    ax.set_title("", pad=0)

    # Key insight box
    axi = fig.add_axes([0.02, 0.04, 0.96, 0.08])
    axi.axis("off")
    axi.add_patch(FancyBboxPatch((0, 0), 1, 1,
        boxstyle="round,pad=0.01", facecolor="#fff3cd",
        edgecolor="#e8722a", linewidth=1.5,
        transform=axi.transAxes, clip_on=False))
    axi.text(0.5, 0.55,
        "Key insight: San Diego operates at a fundamentally different scale — "
        "avg K=15.3, routinely hitting K=20 cap, only 77% fully served even at max fleet. "
        "Glendale is the lightest site: K=6 covers 89.8% of all days.",
        ha="center", va="center", fontsize=9.5, color="#7a4800",
        transform=axi.transAxes, style="italic")

    _footer(fig, n, total)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def slide_coverage_curves(pdf, n, total):
    fig = _fig("Coverage Curves — K-Sweep (% Days Fully Served vs Hub Count)")

    ax = fig.add_axes([0.07, 0.12, 0.88, 0.78])

    for site, label in SITES:
        ks  = DATA[site]["ksweep"]
        col = SITE_COLORS[site]
        Ks  = ks["K"].values
        pct = ks["pct_fully_covered"].values
        ax.plot(Ks, pct, color=col, linewidth=2.2, marker="o",
                markersize=4, label=label, zorder=3)
        # Mark plateau
        max_pct = float(pct.max())
        ceil_k  = int(Ks[pct >= max_pct * 0.999][0])
        ax.annotate(f"K={ceil_k}\n{max_pct:.1f}%",
                    xy=(ceil_k, max_pct),
                    xytext=(ceil_k + 0.4, max_pct - 4),
                    fontsize=8.5, color=col, fontweight="bold",
                    arrowprops=dict(arrowstyle="->", color=col, lw=1.2))

    ax.set_xlabel("Number of XOS Hub MC02 Units (K)", fontsize=11)
    ax.set_ylabel("Days 100% Fully Served (%)", fontsize=11)
    ax.set_xlim(0.5, 21)
    ax.set_ylim(0, 105)
    ax.set_xticks(range(1, 21))
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.grid(axis="both", linestyle=":", alpha=0.40, color="gray")
    ax.legend(fontsize=11, framealpha=0.92, loc="lower right")
    ax.axhline(100, color="black", linewidth=0.8, linestyle="--", alpha=0.4)

    # Annotation: dwell-window ceiling
    ax.text(14, 48,
            "Plateau = dwell-window ceiling:\nvehicles cannot be fully charged\n"
            "regardless of hub count\n(short on-site time limits charging)",
            fontsize=9, color=MID_GRAY, style="italic",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="white",
                      edgecolor=LIGHT_GRAY, alpha=0.92))

    _footer(fig, n, total)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def slide_worst_days(pdf, n, total, site, site_label):
    cov = DATA[site]["coverage"]
    dc  = DATA[site]["cost"]
    sm  = DATA[site]["summary"]

    a2_cost = dc[dc["scenario"] == "A2"]
    a2_sum  = sm[sm["scenario"] == "A2"]

    fig = _fig(f"Top-10 Worst Days — {site_label}  (Scenario A2, sorted by vehicle count)")

    # Top table: worst-day stats
    ax_t = fig.add_axes([0.02, 0.35, 0.96, 0.54])

    rows = []
    col_bg = []
    for _, row in cov.iterrows():
        rank = int(row["rank"])
        date = str(row["worst_date"])
        K    = int(row["K_fixed"]) if "K_fixed" in row else "—"

        dc_row = a2_cost[a2_cost["date"] == date]
        sm_row = a2_sum[a2_sum["date"]   == date]

        n_veh  = int(row["n_vehicles_worst"]) if "n_vehicles_worst" in row else (
                 int(sm_row["n_vehicles"].values[0]) if not sm_row.empty else "—")
        n_full = int(sm_row["n_fully_served"].values[0]) if not sm_row.empty else "—"
        n_part = int(sm_row["n_partial"].values[0])      if not sm_row.empty else "—"
        n_uns  = int(sm_row["n_unserved"].values[0])     if not sm_row.empty else "—"
        svc    = (f"{float(sm_row['service_rate_pct'].values[0]):.1f}%"
                  if not sm_row.empty and "service_rate_pct" in sm_row.columns else "—")
        cost   = (f"${float(dc_row['total_daily_excl_demand'].values[0]):,.0f}"
                  if not dc_row.empty else "—")
        peak   = (f"{float(dc_row['peak_grid_kw'].values[0]):,.0f} kW"
                  if not dc_row.empty else "—")
        e_dem  = (f"{float(sm_row['energy_demanded_kwh'].values[0]):,.0f} kWh"
                  if not sm_row.empty and "energy_demanded_kwh" in sm_row.columns else "—")
        shima  = f"{float(row['pct_fully_covered']):.1f}%" if "pct_fully_covered" in row else "—"
        dow    = pd.Timestamp(date).strftime("%a")

        rows.append([f"#{rank}", f"{date}\n({dow})", f"K={K}", str(n_veh),
                     f"{n_full}/{n_part}/{n_uns}", svc, cost, peak, e_dem, shima])
        col_bg.append("#fde8e8" if rank <= 3 else ("#fff3e0" if rank <= 6 else "#f5f8fc"))

    cols_t = ["Rank", "Date", "Hubs\nK", "Vehicles\nTotal",
               "Served /\nPartial / Unserv", "Svc\nRate",
               "Daily\nCost", "Peak\nGrid", "Energy\nDemanded",
               "Shima: K fixed\ncovers all days"]
    cw_t = [0.055, 0.095, 0.065, 0.075, 0.105, 0.065,
             0.085, 0.085, 0.095, 0.115]
    _table(ax_t, cols_t, rows, col_widths=cw_t,
           row_colors=col_bg, fontsize=8.8)

    # Bottom: mini bar chart — n_vehicles per rank
    ax_b = fig.add_axes([0.07, 0.09, 0.55, 0.22])
    ranks   = [int(r["rank"]) for _, r in cov.iterrows()]
    dates   = [str(r["worst_date"]) for _, r in cov.iterrows()]
    n_vehs  = [int(r["n_vehicles_worst"]) if "n_vehicles_worst" in r else 0
               for _, r in cov.iterrows()]
    n_fulls = [int(a2_sum[a2_sum["date"]==d]["n_fully_served"].values[0])
               if not a2_sum[a2_sum["date"]==d].empty else 0 for d in dates]
    bar_w = 0.35
    xs = np.arange(len(ranks))
    ax_b.bar(xs - bar_w/2, n_vehs,  bar_w, color="#d62728", alpha=0.75, label="Total vehicles")
    ax_b.bar(xs + bar_w/2, n_fulls, bar_w, color="#2ca02c", alpha=0.75, label="Fully served")
    ax_b.set_xticks(xs)
    ax_b.set_xticklabels([f"#{r}" for r in ranks], fontsize=8)
    ax_b.set_ylabel("Vehicles", fontsize=9)
    ax_b.set_title("Vehicle count per worst day", fontsize=9, color=DARK_GRAY)
    ax_b.legend(fontsize=8, framealpha=0.9)
    ax_b.grid(axis="y", linestyle=":", alpha=0.35)

    # Right: Shima pct bar chart
    ax_s = fig.add_axes([0.66, 0.09, 0.32, 0.22])
    shima_pcts = [float(r["pct_fully_covered"]) if "pct_fully_covered" in r else 0 for _, r in cov.iterrows()]
    ax_s.barh(xs, shima_pcts, color=SITE_COLORS[site], alpha=0.75)
    ax_s.set_yticks(xs)
    ax_s.set_yticklabels([f"#{r}" for r in ranks], fontsize=8)
    ax_s.set_xlabel("% of all days fully covered", fontsize=8.5)
    ax_s.set_title("Shima: K fixed at worst-day level", fontsize=9, color=DARK_GRAY)
    ax_s.set_xlim(0, 105)
    ax_s.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax_s.grid(axis="x", linestyle=":", alpha=0.35)
    ax_s.invert_yaxis()

    _footer(fig, n, total)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def slide_shima_all_sites(pdf, n, total):
    fig = _fig("Shima Coverage Analysis — What K from the Worst Day Covers Across All Days")

    axes_pos = [(0.03, 0.10, 0.46, 0.40),
                (0.52, 0.10, 0.46, 0.40),
                (0.03, 0.54, 0.46, 0.40),
                (0.52, 0.54, 0.46, 0.40)]

    for (site, label), pos in zip(SITES, axes_pos):
        ax  = fig.add_axes(pos)
        cov = DATA[site]["coverage"]
        ks  = DATA[site]["ksweep"]

        ranks = [int(r["rank"]) for _, r in cov.iterrows()]
        Ks    = [int(r["K_fixed"]) if "K_fixed" in r else 0 for _, r in cov.iterrows()]
        pcts  = [float(r["pct_fully_covered"]) if "pct_fully_covered" in r else 0
                 for _, r in cov.iterrows()]

        bars = ax.bar(ranks, pcts, color=SITE_COLORS[site], alpha=0.75, width=0.7)

        # Mark full-sweep ceiling
        ceil_pct = ks["pct_fully_covered"].max()
        ax.axhline(ceil_pct, color="gray", linewidth=1.2, linestyle="--",
                   label=f"K-sweep ceiling: {ceil_pct:.1f}%")

        for b, pct in zip(bars, pcts):
            ax.text(b.get_x() + b.get_width()/2, pct + 0.8, f"{pct:.1f}%",
                    ha="center", va="bottom", fontsize=7.5, fontweight="bold",
                    color=SITE_COLORS[site])

        ax.set_title(f"{label}", fontsize=11, fontweight="bold",
                     color=SITE_COLORS[site])
        ax.set_xlabel("Worst-day rank (K used)", fontsize=8.5)
        ax.set_ylabel("% days fully covered", fontsize=8.5)
        ax.set_ylim(0, 110)
        ax.set_xticks(ranks)
        ax.set_xticklabels([f"#{r}\nK={k}" for r, k in zip(ranks, Ks)], fontsize=7)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.legend(fontsize=7.5, framealpha=0.9)
        ax.grid(axis="y", linestyle=":", alpha=0.30)

    _footer(fig, n, total)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def slide_grid_power(pdf, n, total):
    """Grid input power & energy profiles for XOS A2 — all 4 sites."""
    fig = _fig("XOS A2 — Grid Input Power & Energy Profile (Full Year, All Sites)")

    # 4-site grid: 2 rows × 2 cols
    positions = [(0.06, 0.52, 0.42, 0.40),
                 (0.55, 0.52, 0.42, 0.40),
                 (0.06, 0.08, 0.42, 0.40),
                 (0.55, 0.08, 0.42, 0.40)]

    for (site, label), pos in zip(SITES, positions):
        dc  = DATA[site]["cost"]
        sm  = DATA[site]["summary"]
        a2c = dc[dc["scenario"] == "A2"].copy()
        a2s = sm[sm["scenario"] == "A2"].copy()
        a2c["date"] = pd.to_datetime(a2c["date"])
        a2s["date"] = pd.to_datetime(a2s["date"])
        a2c = a2c.sort_values("date")
        a2s = a2s.sort_values("date")

        col = SITE_COLORS[site]

        # Merge to get K and energy data aligned
        merged = a2c.merge(a2s[["date","K","energy_demanded_kwh",
                                  "energy_delivered_kwh","energy_unmet_kwh"]],
                           on=["date","K"], how="left")

        ax = fig.add_axes(pos)

        # Primary: peak grid draw (kW) — area fill
        ax.fill_between(merged["date"], merged["peak_grid_kw"],
                        alpha=0.18, color=col)
        ax.plot(merged["date"], merged["peak_grid_kw"],
                color=col, linewidth=1.0, alpha=0.85,
                label=f"Peak grid draw (A2)")

        # Theoretical max: K × 83 kW grid charge rate
        ax.plot(merged["date"], merged["K"] * 83,
                color=col, linewidth=1.0, alpha=0.45, linestyle="--",
                label=f"Max recharge (K×83 kW)")

        # On-peak-window peak (darker)
        ax.plot(merged["date"], merged["peak_win_kw"],
                color="darkorange", linewidth=0.8, alpha=0.70,
                label="Peak 16–21h window")

        # Mean line
        mean_kw = merged["peak_grid_kw"].mean()
        ax.axhline(mean_kw, color="gray", linewidth=0.9, linestyle=":",
                   label=f"Mean {mean_kw:.0f} kW")

        ax.set_title(f"{label}  —  A2 Grid Input Power",
                     fontsize=9.5, fontweight="bold", color=col)
        ax.set_ylabel("Grid draw (kW)", fontsize=8)
        ax.set_ylim(0, merged["peak_grid_kw"].max() * 1.18)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.tick_params(axis="x", labelsize=7, rotation=30)
        ax.tick_params(axis="y", labelsize=7.5)
        ax.grid(axis="both", linestyle=":", alpha=0.30)
        ax.legend(fontsize=6.8, loc="upper left", framealpha=0.88, ncol=2)

        # Right y-axis: total grid kWh
        ax2 = ax.twinx()
        ax2.bar(merged["date"], merged["total_grid_kwh"],
                width=0.8, color=col, alpha=0.10)
        ax2.bar(merged["date"], merged["vehicle_kwh_delivered"],
                width=0.8, color=col, alpha=0.20)
        ax2.set_ylabel("kWh / day", fontsize=7, color=MID_GRAY)
        ax2.tick_params(axis="y", labelsize=7, labelcolor=MID_GRAY)
        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    # Explanatory note
    fig.text(0.5, 0.96,
             "Solid line = peak grid draw  |  Dashed = theoretical max (K × 83 kW/hub)  "
             "|  Orange = on-peak window (16–21h)  |  Bars = grid kWh (light) vs vehicle kWh (darker)",
             ha="center", fontsize=8, color=MID_GRAY, style="italic")

    _footer(fig, n, total)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def slide_conclusions(pdf, n, total):
    fig = _fig("Conclusions & Recommendations")
    ax  = fig.add_axes([0.03, 0.08, 0.94, 0.84])
    ax.axis("off")

    # Build key metrics from data
    metrics = {}
    for site, label in SITES:
        ks = DATA[site]["ksweep"]
        sm = DATA[site]["summary"]
        dc = DATA[site]["cost"]
        a2 = sm[sm["scenario"] == "A2"]
        dc2= dc[dc["scenario"] == "A2"]
        max_cov  = ks["pct_fully_covered"].max()
        ceil_k   = int(ks.loc[ks["pct_fully_covered"] == max_cov, "K"].min())
        ceil_pct = max_cov
        metrics[site] = {
            "label":    label,
            "ceil_k":   ceil_k,
            "ceil_pct": ceil_pct,
            "k_max":    int(a2["K"].max()),
            "k_avg":    a2["K"].mean(),
            "svc":      100 * a2["n_fully_served"].sum() / max(a2["n_vehicles"].sum(), 1),
            "cost_avg": dc2["total_daily_excl_demand"].mean(),
        }

    sections = [
        ("Site-Level Findings", TITLE_BG, [
            f"Glendale (lightest):  K={metrics['glendale']['ceil_k']} hubs cover "
            f"{metrics['glendale']['ceil_pct']:.1f}% of days  |  avg daily cost "
            f"${metrics['glendale']['cost_avg']:.0f}  |  XOS mobile hubs are well-suited",
            f"Fresno:               K={metrics['fresno']['ceil_k']} hubs cover "
            f"{metrics['fresno']['ceil_pct']:.1f}% of days  |  avg daily cost "
            f"${metrics['fresno']['cost_avg']:.0f}  |  good mobile hub candidate",
            f"Northgate:            K={metrics['northgate']['ceil_k']} hubs cover "
            f"{metrics['northgate']['ceil_pct']:.1f}% of days  |  avg daily cost "
            f"${metrics['northgate']['cost_avg']:.0f}  |  heavy but manageable",
            f"San Diego (heaviest): K={metrics['san_diego']['k_max']} hubs (cap hit)  |  "
            f"only {metrics['san_diego']['ceil_pct']:.1f}% days fully covered  |  "
            f"avg daily cost ${metrics['san_diego']['cost_avg']:.0f}  —  fixed infra recommended",
        ]),
        ("XOS A1 vs A2 Operating Scenario", "#2c5f8a", [
            "A1 (always grid-connected): higher simultaneous grid draw, vehicles served without interruption.",
            "A2 (disconnect at 20% SOC): lower peak grid load, some vehicles queued during hub recharge.",
            "A2 is preferred for grid-constrained sites; A1 for minimum vehicle wait time.",
            "Proactive recharge (both): hub self-charges during idle gaps → improves SOC headroom for next arrivals.",
        ]),
        ("Dwell-Window Ceiling Effect", "#5c2a7a", [
            "Adding more hubs beyond the coverage plateau K has no effect — the constraint is vehicle dwell time.",
            "Vehicles with dwell windows too short for full charging cannot be served regardless of hub count.",
            "Solution: extend dwell windows (scheduling), or deploy faster chargers (Kempower fixed DCFC).",
        ]),
        ("Recommendations", "#7a1a1a", [
            "Glendale / Fresno: 6–10 XOS Hub MC02 units, Scenario A2 — cost-effective mobile deployment.",
            "Northgate: 13 units for ~52% full coverage; consider hybrid XOS + 1-2 fixed 250kW Kempower.",
            "San Diego: XOS mobile hubs alone are insufficient; permanent fixed DCFC infrastructure required.",
            "All sites: schedule vehicles to maximize dwell windows to lift the coverage ceiling.",
        ]),
    ]

    y = 0.98
    for title, color, bullets in sections:
        ax.text(0.01, y, title, ha="left", va="top", fontsize=12,
                fontweight="bold", color=color, transform=ax.transAxes)
        y -= 0.052
        for b in bullets:
            ax.text(0.025, y, f"• {b}", ha="left", va="top", fontsize=9.2,
                    color=DARK_GRAY, transform=ax.transAxes,
                    wrap=True)
            y -= 0.058
        y -= 0.025

    _footer(fig, n, total)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ══════════════════════════════════════════════════════════════════════════════
# BUILD PDF
# ══════════════════════════════════════════════════════════════════════════════

TOTAL = 13

with PdfPages(PDF) as pdf:
    meta = pdf.infodict()
    meta["Title"]   = "XOS Hub MC02 — Caltrans Mobile DCFC Analysis"
    meta["Author"]  = "Caltrans EV Fleet Analysis"
    meta["Subject"] = "Worst-Day Analysis, A1/A2 Scenarios, Coverage Optimization"

    slide_title(pdf, 1, TOTAL)
    print("  Slide  1: Title")

    slide_overview(pdf, 2, TOTAL)
    print("  Slide  2: Overview")

    slide_xos_specs(pdf, 3, TOTAL)
    print("  Slide  3: XOS specs + A1/A2")

    slide_site_summary(pdf, 4, TOTAL)
    print("  Slide  4: Site summary table")

    slide_coverage_curves(pdf, 5, TOTAL)
    print("  Slide  5: Coverage K-sweep curves")

    slide_grid_power(pdf, 6, TOTAL)
    print("  Slide  6: A2 grid input power profile")

    for i, (site, label) in enumerate(SITES, 7):
        slide_worst_days(pdf, i, TOTAL, site, label)
        print(f"  Slide {i:2d}: Worst days — {label}")

    slide_shima_all_sites(pdf, 11, TOTAL)
    print("  Slide 11: Shima all sites")

    slide_conclusions(pdf, 12, TOTAL)
    print("  Slide 12: Conclusions")

    # Blank final
    fig = _fig(""); _footer(fig, 13, TOTAL)
    fig.text(0.5, 0.5, "— End of Presentation —",
             ha="center", va="center", fontsize=18, color=LIGHT_GRAY)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)
    print("  Slide 13: End")

print(f"\nSaved: {PDF}")
