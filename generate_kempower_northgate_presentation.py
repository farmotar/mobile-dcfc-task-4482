"""
generate_kempower_northgate_presentation.py
===========================================
12-slide PDF presentation of Kempower fixed-DCFC results for Northgate.
Mirrors the structure of the XOS presentation.

Slides:
  1  Title
  2  Kempower System Overview
  3  Charger Specs + MILP Methodology
  4  Northgate Season Summary Table
  5  Daily Charger-Mix Distribution (frequency chart)
  6  Service Rate & Energy Profile (full year)
  7  Cost Profile (full year)
  8  Top-10 Worst Days — table + bar charts
  9  Worst-Day Deep Dive (mix breakdown for top 5)
 10  Kempower vs XOS A2 Comparison
 11  Conclusions & Recommendations
 12  End

Output: scenario_outputs/Kempower_Northgate_Presentation.pdf
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

BASE    = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT     = BASE / "scenario_outputs"
ANA_DIR = OUT / "northgate_analysis"
PDF     = OUT / "Kempower_Northgate_Presentation.pdf"

W, H = 16, 9

# ── colours ────────────────────────────────────────────────────────────────────
TITLE_BG  = "#1a3a1a"      # dark green (Kempower brand-ish)
HDR_BG    = "#2d6a2d"
HDR_FG    = "white"
BODY_BG   = "#f5faf5"
ACCENT    = "#5cb85c"
DARK_GRAY = "#333333"
MID_GRAY  = "#666666"
LIGHT_GRAY= "#cccccc"

TYPE_COLOR = {
    "50kW":  "#2166ac",
    "150kW": "#1a9641",
    "250kW": "#d73027",
}
TYPE_FULL = {
    "50kW":  "Kempower 50 kW",
    "150kW": "Kempower 150 kW",
    "250kW": "Kempower 250 kW",
}

# ── load data ──────────────────────────────────────────────────────────────────
kdf  = pd.read_csv(ANA_DIR / "northgate_kempower_summary.csv")
kdf["date"] = pd.to_datetime(kdf["date"])
kdf["dow"]  = kdf["date"].dt.strftime("%a")
kdf["month"]= kdf["date"].dt.strftime("%b %Y")
kdf["date_str"] = kdf["date"].dt.strftime("%Y-%m-%d")

xos_sum  = pd.read_csv(ANA_DIR / "northgate_summary.csv")
xos_cost = pd.read_csv(ANA_DIR / "northgate_cost_detail.csv")
xos_a2   = xos_sum[xos_sum["scenario"] == "A2"].copy()
xos_dc2  = xos_cost[xos_cost["scenario"] == "A2"].copy()

# ── parse mix into component counts ───────────────────────────────────────────
def _parse_mix(mix_str: str) -> dict:
    """Return dict {50kW: n, 150kW: n, 250kW: n} from e.g. '1×50kW + 2×150kW + 1×250kW'."""
    counts = {"50kW": 0, "150kW": 0, "250kW": 0}
    if not isinstance(mix_str, str): return counts
    for part in mix_str.split("+"):
        part = part.strip().replace("×", "x").replace("\xd7", "x")
        if "x" in part:
            n_s, t = part.split("x", 1)
            t = t.strip()
            for key in counts:
                if key in t: counts[key] = int(n_s.strip()); break
    return counts

for key in ("50kW", "150kW", "250kW"):
    kdf[f"n_{key}"] = kdf["mix"].apply(lambda m: _parse_mix(m)[key])

# ── helpers ────────────────────────────────────────────────────────────────────
def _fig(title_text=None):
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor(BODY_BG)
    if title_text:
        fig.text(0.5, 0.965, title_text, ha="center", va="top",
                 fontsize=18, fontweight="bold", color=TITLE_BG)
        ax_line = fig.add_axes([0, 0.935, 1, 0.003])
        ax_line.set_facecolor(ACCENT); ax_line.axis("off")
    return fig


def _footer(fig, n, total):
    fig.text(0.01, 0.012, "Caltrans Northgate — Kempower Fixed DCFC Analysis",
             ha="left", va="bottom", fontsize=7.5, color=MID_GRAY)
    fig.text(0.99, 0.012, f"Slide {n} / {total}   |   {datetime.now().strftime('%Y-%m-%d')}",
             ha="right", va="bottom", fontsize=7.5, color=MID_GRAY)
    ax_b = fig.add_axes([0, 0, 1, 0.002])
    ax_b.set_facecolor(TITLE_BG); ax_b.axis("off")


def _table(ax, col_labels, rows, col_widths=None, row_colors=None, fontsize=10):
    ax.axis("off")
    n_cols = len(col_labels)
    col_widths = col_widths or [1/n_cols]*n_cols
    n_rows = len(rows)
    row_h  = 1.0 / (n_rows + 1.5)
    col_x  = [sum(col_widths[:i]) for i in range(n_cols)]

    for ci, (lbl, cx, cw) in enumerate(zip(col_labels, col_x, col_widths)):
        ax.add_patch(FancyBboxPatch((cx, 1-row_h), cw, row_h,
            boxstyle="square,pad=0", facecolor=HDR_BG, edgecolor="white",
            linewidth=0.8, transform=ax.transAxes, clip_on=True))
        ax.text(cx+cw/2, 1-row_h/2, lbl, ha="center", va="center",
                fontsize=fontsize, fontweight="bold", color=HDR_FG,
                transform=ax.transAxes, clip_on=True)

    for ri, row in enumerate(rows):
        bg  = row_colors[ri] if row_colors else ("#e8f5e8" if ri%2==0 else "white")
        y0  = 1 - (ri+2)*row_h
        for ci, (val, cx, cw) in enumerate(zip(row, col_x, col_widths)):
            ax.add_patch(FancyBboxPatch((cx, y0), cw, row_h,
                boxstyle="square,pad=0", facecolor=bg, edgecolor=LIGHT_GRAY,
                linewidth=0.4, transform=ax.transAxes, clip_on=True))
            ax.text(cx+cw/2, y0+row_h/2, str(val), ha="center", va="center",
                    fontsize=fontsize, color=DARK_GRAY,
                    transform=ax.transAxes, clip_on=True)


# ══════════════════════════════════════════════════════════════════════════════
# SLIDES
# ══════════════════════════════════════════════════════════════════════════════

TOTAL = 12

def s1_title(pdf):
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor(TITLE_BG)
    fig.text(0.5, 0.64, "Kempower Fixed DCFC Analysis",
             ha="center", fontsize=32, fontweight="bold", color="white")
    fig.text(0.5, 0.54, "Northgate Maintenance Station",
             ha="center", fontsize=22, color="#aaddaa")
    fig.text(0.5, 0.45, "MILP-Optimal Charger Mix Sizing  |  307 Operating Days  |  May 2025 – Apr 2026",
             ha="center", fontsize=13, color="#ccddcc")
    ax_l = fig.add_axes([0.1, 0.39, 0.8, 0.003])
    ax_l.set_facecolor(ACCENT); ax_l.axis("off")
    fig.text(0.5, 0.31, "Charger types:  50 kW  ·  150 kW  ·  250 kW  (Kempower DGS contract pricing)",
             ha="center", fontsize=12, color="white")
    n_veh = kdf["n_vehicles"].sum(); n_full = kdf["n_full"].sum()
    fig.text(0.5, 0.23,
             f"{n_veh:,} vehicles  |  {100*n_full/n_veh:.1f}% fully served  |  "
             f"avg {kdf['n_chargers'].mean():.1f} chargers/day  |  avg ${kdf['total_cost'].mean():.0f}/day",
             ha="center", fontsize=12, color="#aaddaa")
    fig.text(0.5, 0.15, f"Generated: {datetime.now().strftime('%B %d, %Y')}",
             ha="center", fontsize=10, color="#88aa88")
    ax_b = fig.add_axes([0, 0, 1, 0.004])
    ax_b.set_facecolor(ACCENT); ax_b.axis("off")
    _footer(fig, 1, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s2_overview(pdf):
    fig = _fig("Kempower System Overview — Fixed DC Fast Charging")
    ax  = fig.add_axes([0.03, 0.08, 0.94, 0.82])
    ax.axis("off")

    cols = [
        ("WHAT IS KEMPOWER?", [
            "Kempower is a permanent, grid-connected fixed DCFC station.",
            "Unlike the XOS Hub MC02 (mobile battery trailer), Kempower",
            "chargers draw power directly from the grid at all times.",
            "",
            "They are permanently installed at the maintenance site and",
            "do not need to be transported or repositioned.",
            "",
            "Each charger operates independently — multiple vehicles",
            "can charge simultaneously across different charger types.",
            "",
            "DGS contract pricing (California):",
            "  50 kW  → $23,408 purchase",
            "  150 kW → $62,154 purchase",
            "  250 kW → $101,946 purchase",
        ], 0.02, 0.44),
        ("HOW SIZING WORKS (MILP)", [
            "For each operating day, a Mixed-Integer Linear Program (MILP)",
            "solves for the optimal number and type of chargers.",
            "",
            "Objective: minimize total daily cost (CAPEX amortization +",
            "  energy cost) subject to:",
            "  • Each vehicle fully charged within its dwell window",
            "  • No charger exceeds its power rating",
            "  • Integer charger counts",
            "  • Grid power smoothing penalty (avoid spikes)",
            "",
            "The MILP selects from three charger types simultaneously,",
            "mixing them to match the fleet's power and timing needs.",
            "",
            "Solver: Gurobi (with HiGHS as fallback)",
        ], 0.54, 0.44),
    ]

    for title, lines, x0, w in cols:
        ax.add_patch(FancyBboxPatch((x0, 0.02), w, 0.91,
            boxstyle="round,pad=0.01", facecolor="white",
            edgecolor=TITLE_BG, linewidth=1.2,
            transform=ax.transAxes, clip_on=False))
        ax.text(x0+w/2, 0.91, title, ha="center", va="center",
                fontsize=12, fontweight="bold", color=TITLE_BG,
                transform=ax.transAxes)
        ax.add_patch(FancyBboxPatch((x0+0.01, 0.84), w-0.02, 0.002,
            boxstyle="square,pad=0", facecolor=ACCENT,
            transform=ax.transAxes, clip_on=False))
        for i, line in enumerate(lines):
            ax.text(x0+0.025, 0.80-i*0.058, line,
                    ha="left", va="top", fontsize=9.3, color=DARK_GRAY,
                    transform=ax.transAxes)

    _footer(fig, 2, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s3_specs(pdf):
    fig = _fig("Kempower Charger Specifications & Cost Model")

    # Spec table
    ax_s = fig.add_axes([0.03, 0.10, 0.42, 0.80])
    specs = [
        ("", "50 kW", "150 kW", "250 kW"),
        ("Power rating",     "50 kW",    "150 kW",   "250 kW"),
        ("Type",             "DC CCS",   "DC CCS",   "DC CCS"),
        ("Purchase cost",    "$23,408",  "$62,154",  "$101,946"),
        ("Install cost",     "~$10,000", "~$18,000", "~$30,000"),
        ("Life span",        "15 yr",    "15 yr",    "15 yr"),
        ("Daily CAPEX",      "$18.10",   "$32.70",   "$46.49"),
        ("Elec. infra/unit", "~$10-25k", "~$15-30k", "~$25-50k"),
        ("Best for",  "Light EVs\nshort dwell", "Medium\nfleet mix", "Heavy EVs\nhigh demand"),
    ]
    # Draw manually: header row + data rows
    ax_s.axis("off")
    row_h = 1.0 / (len(specs) + 0.5)
    cws   = [0.36, 0.21, 0.21, 0.22]
    cols_h= ["", "50 kW", "150 kW", "250 kW"]
    col_x = [0, 0.36, 0.57, 0.78]
    type_colors = {"50 kW": "#2166ac", "150 kW": "#1a9641", "250 kW": "#d73027"}
    for ci, (h, cx, cw) in enumerate(zip(cols_h, col_x, cws)):
        c = type_colors.get(h, HDR_BG)
        ax_s.add_patch(FancyBboxPatch((cx, 1-row_h), cw, row_h,
            boxstyle="square,pad=0", facecolor=c, edgecolor="white",
            linewidth=0.8, transform=ax_s.transAxes, clip_on=True))
        ax_s.text(cx+cw/2, 1-row_h/2, h, ha="center", va="center",
                  fontsize=10.5, fontweight="bold", color="white",
                  transform=ax_s.transAxes, clip_on=True)

    for ri, row in enumerate(specs[1:]):
        bg = "#e8f5e8" if ri%2==0 else "white"
        y0 = 1-(ri+2)*row_h
        for ci, (val, cx, cw) in enumerate(zip(row, col_x, cws)):
            ax_s.add_patch(FancyBboxPatch((cx, y0), cw, row_h,
                boxstyle="square,pad=0", facecolor=bg, edgecolor=LIGHT_GRAY,
                linewidth=0.4, transform=ax_s.transAxes, clip_on=True))
            ax_s.text(cx+cw/2, y0+row_h/2, val, ha="center", va="center",
                      fontsize=9.5, color=DARK_GRAY,
                      transform=ax_s.transAxes, clip_on=True)
    ax_s.set_title("Charger Specifications & Economics", fontsize=11,
                    fontweight="bold", color=TITLE_BG, pad=8)

    # Right: cost model explanation
    ax_c = fig.add_axes([0.50, 0.10, 0.47, 0.80])
    ax_c.axis("off")
    ax_c.set_title("Daily Cost Model", fontsize=11, fontweight="bold",
                    color=TITLE_BG, pad=8)

    cost_items = [
        ("CAPEX (amortized)", "#2d6a2d",
         "Purchase + installation cost spread over 15-year lifespan.\n"
         "Scaled by number of each charger type selected.\n"
         "50 kW: $18.10/day  |  150 kW: $32.70/day  |  250 kW: $46.49/day"),
        ("Energy Cost (TOU)", "#1a5f9a",
         "SMUD C&I Time-of-Use rates applied to grid draw:\n"
         "  Summer on-peak (16–21h):  $0.2341/kWh\n"
         "  Summer off-peak:          $0.1215/kWh\n"
         "  Winter on-peak:           $0.1932/kWh\n"
         "  Winter shoulder:          $0.1477/kWh"),
        ("Demand Charges (monthly)", "#8b3a3a",
         "Global demand: $6.454/kW-month (peak draw any hour)\n"
         "Peak-window demand: $9.960/kW-month (M–F 16–21h)\n"
         "Note: not included in daily total_cost shown here\n"
         "(reported separately as monthly charge)"),
        ("Smoothing Penalty (MILP)", "#5c5c00",
         "MILP penalizes large power step changes to avoid\n"
         "grid spikes. This is an optimization artifact and does\n"
         "not represent a real cost — excluded from cost totals."),
    ]

    y = 0.87
    for title, color, text in cost_items:
        ax_c.add_patch(FancyBboxPatch((0, y-0.19), 1.0, 0.20,
            boxstyle="round,pad=0.005", facecolor=color+"15",
            edgecolor=color, linewidth=1.0,
            transform=ax_c.transAxes, clip_on=False))
        ax_c.text(0.03, y, title, ha="left", va="top",
                  fontsize=10, fontweight="bold", color=color,
                  transform=ax_c.transAxes)
        ax_c.text(0.03, y-0.045, text, ha="left", va="top",
                  fontsize=8.5, color=DARK_GRAY,
                  transform=ax_c.transAxes)
        y -= 0.23

    _footer(fig, 3, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s4_summary(pdf):
    fig = _fig("Northgate — Season Summary (307 Days, MILP Optimal Mix)")

    # Key stats boxes across top
    stats = [
        ("307", "Operating days"),
        (f"{kdf['n_vehicles'].sum():,}", "Total vehicles"),
        (f"{100*kdf['n_full'].sum()/kdf['n_vehicles'].sum():.1f}%", "Fully served"),
        (f"{kdf['n_partial'].sum():,}", "Partial-service vehicles"),
        (f"{kdf['n_unserved'].sum():,}", "Unserved vehicles"),
        (f"{kdf['n_chargers'].mean():.1f}", "Avg chargers/day"),
        (f"${kdf['total_cost'].mean():.0f}", "Avg daily cost (excl demand)"),
        (f"${kdf['total_cost'].max():.0f}", "Max daily cost (worst day)"),
        (f"{kdf['peak_kw'].mean():.0f} kW", "Avg peak grid draw"),
        (f"{kdf['peak_kw'].max():.0f} kW", "Max peak grid draw"),
    ]

    n_stat = len(stats)
    w_each = 1.0 / (n_stat / 2)
    for i, (val, lbl) in enumerate(stats):
        col = i % 5; row = i // 5
        x0 = 0.02 + col * 0.196; y0 = 0.75 if row == 0 else 0.53

        ax_s = fig.add_axes([x0, y0, 0.185, 0.18])
        ax_s.axis("off")
        ax_s.add_patch(FancyBboxPatch((0,0), 1, 1,
            boxstyle="round,pad=0.04", facecolor="white",
            edgecolor=ACCENT, linewidth=1.5,
            transform=ax_s.transAxes, clip_on=False))
        ax_s.text(0.5, 0.65, val, ha="center", va="center",
                  fontsize=16, fontweight="bold", color=TITLE_BG,
                  transform=ax_s.transAxes)
        ax_s.text(0.5, 0.22, lbl, ha="center", va="center",
                  fontsize=8.5, color=MID_GRAY,
                  transform=ax_s.transAxes)

    # Monthly breakdown table
    ax_t = fig.add_axes([0.02, 0.09, 0.96, 0.40])
    monthly = (kdf.groupby("month").agg(
        days=("date","count"),
        vehicles=("n_vehicles","sum"),
        full=("n_full","sum"),
        avg_K=("n_chargers","mean"),
        max_K=("n_chargers","max"),
        cost_avg=("total_cost","mean"),
        cost_max=("total_cost","max"),
        peak_max=("peak_kw","max"),
    ).reset_index())
    monthly["svc"] = (100*monthly["full"]/monthly["vehicles"]).round(1).astype(str)+"%"
    monthly["avg_K_s"] = monthly["avg_K"].round(1).astype(str)
    monthly["cost_avg_s"] = "$"+monthly["cost_avg"].round(0).astype(int).astype(str)
    monthly["cost_max_s"] = "$"+monthly["cost_max"].round(0).astype(int).astype(str)
    monthly["peak_s"]   = monthly["peak_max"].round(0).astype(int).astype(str)+" kW"

    rows_m = [[r["month"], str(r["days"]), str(r["vehicles"]), r["svc"],
               r["avg_K_s"], str(r["max_K"]),
               r["cost_avg_s"], r["cost_max_s"], r["peak_s"]]
              for _, r in monthly.iterrows()]
    _table(ax_t,
           ["Month", "Days", "Vehicles", "Svc%", "Avg\nChargers", "Max\nChargers",
            "Cost Avg", "Cost Max", "Peak Grid"],
           rows_m,
           col_widths=[0.13, 0.07, 0.09, 0.08, 0.09, 0.09, 0.09, 0.09, 0.10],
           fontsize=8.8)

    _footer(fig, 4, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s5_mix_distribution(pdf):
    fig = _fig("Daily Charger-Mix Distribution — Northgate (307 Days)")

    # Left: stacked bar by month
    ax1 = fig.add_axes([0.06, 0.12, 0.44, 0.78])
    monthly = kdf.groupby("month")[["n_50kW","n_150kW","n_250kW"]].mean()
    months  = list(monthly.index)
    xs      = np.arange(len(months))
    bw      = 0.6
    b1 = ax1.bar(xs, monthly["n_50kW"],  bw, color=TYPE_COLOR["50kW"],  label="50 kW",  alpha=0.85)
    b2 = ax1.bar(xs, monthly["n_150kW"], bw, bottom=monthly["n_50kW"],
                 color=TYPE_COLOR["150kW"], label="150 kW", alpha=0.85)
    b3 = ax1.bar(xs, monthly["n_250kW"], bw,
                 bottom=monthly["n_50kW"]+monthly["n_150kW"],
                 color=TYPE_COLOR["250kW"], label="250 kW", alpha=0.85)
    ax1.set_xticks(xs)
    ax1.set_xticklabels(months, rotation=45, ha="right", fontsize=8)
    ax1.set_ylabel("Avg chargers/day", fontsize=10)
    ax1.set_title("Average Daily Charger Mix by Month", fontsize=10, color=DARK_GRAY)
    ax1.legend(fontsize=9, framealpha=0.9)
    ax1.grid(axis="y", linestyle=":", alpha=0.35)

    # Right: histogram of total charger count per day
    ax2 = fig.add_axes([0.56, 0.55, 0.40, 0.36])
    counts = kdf["n_chargers"].value_counts().sort_index()
    ax2.bar(counts.index, counts.values, color=ACCENT, alpha=0.80, edgecolor="white")
    ax2.set_xlabel("Chargers deployed (K)", fontsize=9)
    ax2.set_ylabel("Number of days", fontsize=9)
    ax2.set_title("Distribution of Daily Charger Count", fontsize=9.5, color=DARK_GRAY)
    ax2.set_xticks(counts.index)
    ax2.grid(axis="y", linestyle=":", alpha=0.35)
    for x, v in zip(counts.index, counts.values):
        ax2.text(x, v+0.3, str(v), ha="center", va="bottom", fontsize=8, color=DARK_GRAY)

    # Bottom right: service-rate histogram
    ax3 = fig.add_axes([0.56, 0.12, 0.40, 0.36])
    ax3.hist(kdf["svc_rate_pct"], bins=20, color=TYPE_COLOR["150kW"], alpha=0.75,
             edgecolor="white")
    ax3.axvline(kdf["svc_rate_pct"].mean(), color="red", linewidth=1.5,
                linestyle="--", label=f"Mean {kdf['svc_rate_pct'].mean():.1f}%")
    ax3.set_xlabel("Daily service rate (%)", fontsize=9)
    ax3.set_ylabel("Number of days", fontsize=9)
    ax3.set_title("Distribution of Daily Service Rate", fontsize=9.5, color=DARK_GRAY)
    ax3.legend(fontsize=8.5)
    ax3.grid(axis="y", linestyle=":", alpha=0.35)

    _footer(fig, 5, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s6_energy_profile(pdf):
    fig = _fig("Service Rate & Energy Profile — Full Year")

    ax1 = fig.add_axes([0.07, 0.55, 0.88, 0.36])
    ax1.plot(kdf["date"], kdf["svc_rate_pct"], color=TYPE_COLOR["150kW"],
             linewidth=1.0, alpha=0.7, label="Daily service rate (%)")
    ax1.fill_between(kdf["date"], kdf["svc_rate_pct"], alpha=0.12, color=TYPE_COLOR["150kW"])
    ax1.axhline(kdf["svc_rate_pct"].mean(), color="red", linewidth=1.2, linestyle="--",
                label=f"Mean {kdf['svc_rate_pct'].mean():.1f}%")
    ax1.axhline(100, color="gray", linewidth=0.7, linestyle=":", alpha=0.5)
    ax1.set_ylim(0, 110)
    ax1.set_ylabel("Service rate (%)", fontsize=9)
    ax1.set_title("Daily Service Rate (% vehicles fully served)", fontsize=10, color=DARK_GRAY)
    ax1.legend(fontsize=8.5, loc="lower left")
    ax1.grid(axis="both", linestyle=":", alpha=0.30)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:.0f}%"))

    ax2 = fig.add_axes([0.07, 0.12, 0.88, 0.36])
    ax2.bar(kdf["date"], kdf["e_delivered_kwh"], width=0.8,
            color=ACCENT, alpha=0.75, label="Delivered (kWh)")
    ax2.bar(kdf["date"], kdf["e_unmet_kwh"], width=0.8,
            bottom=kdf["e_delivered_kwh"],
            color="#d73027", alpha=0.65, label="Unmet (kWh)")
    ax2.set_ylabel("Energy (kWh)", fontsize=9)
    ax2.set_xlabel("Date", fontsize=9)
    ax2.set_title("Daily Energy Delivered vs Unmet", fontsize=10, color=DARK_GRAY)
    ax2.legend(fontsize=8.5)
    ax2.grid(axis="y", linestyle=":", alpha=0.30)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    _footer(fig, 6, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s7_cost_profile(pdf):
    fig = _fig("Daily Cost & Peak Power Profile — Full Year")

    ax1 = fig.add_axes([0.07, 0.55, 0.88, 0.36])
    ax1.bar(kdf["date"], kdf["capex_daily"], width=0.8,
            color="#5c5c00", alpha=0.80, label="CAPEX (amortized)")
    ax1.bar(kdf["date"], kdf["energy_cost"], width=0.8,
            bottom=kdf["capex_daily"],
            color="#1a5f9a", alpha=0.75, label="Energy cost (TOU)")
    ax1.axhline(kdf["total_cost"].mean(), color="red", linewidth=1.2, linestyle="--",
                label=f"Mean ${kdf['total_cost'].mean():.0f}/day")
    ax1.set_ylabel("Daily cost (USD)", fontsize=9)
    ax1.set_title("Daily Cost Breakdown — CAPEX + Energy (excl demand charges)", fontsize=10, color=DARK_GRAY)
    ax1.legend(fontsize=8.5)
    ax1.grid(axis="y", linestyle=":", alpha=0.30)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))

    ax2 = fig.add_axes([0.07, 0.12, 0.88, 0.36])
    ax2.plot(kdf["date"], kdf["peak_kw"], color="#d73027", linewidth=0.9,
             alpha=0.75, label="Peak grid draw (kW)")
    ax2.fill_between(kdf["date"], kdf["peak_kw"], alpha=0.10, color="#d73027")
    ax2.axhline(kdf["peak_kw"].mean(), color="darkred", linewidth=1.2, linestyle="--",
                label=f"Mean {kdf['peak_kw'].mean():.0f} kW")
    ax2.set_ylabel("Peak grid draw (kW)", fontsize=9)
    ax2.set_xlabel("Date", fontsize=9)
    ax2.set_title("Daily Peak Grid Power Demand", fontsize=10, color=DARK_GRAY)
    ax2.legend(fontsize=8.5)
    ax2.grid(axis="both", linestyle=":", alpha=0.30)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    _footer(fig, 7, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s8_worst_days(pdf):
    # Sort by unserved desc, then partial desc, then svc_rate asc
    worst = kdf.sort_values(
        ["n_unserved","n_partial","svc_rate_pct"],
        ascending=[False, False, True]
    ).head(10).reset_index(drop=True)

    fig = _fig("Top-10 Worst Days — Northgate Kempower (by vehicles unserved / partially served)")

    ax_t = fig.add_axes([0.02, 0.37, 0.96, 0.52])
    rows_t = []; col_bg = []
    for ri, row in worst.iterrows():
        rank = ri + 1
        date = row["date_str"]
        dow  = row["dow"]
        mix  = row["mix"].replace("\xd7","×")
        n_v  = int(row["n_vehicles"])
        n_f  = int(row["n_full"])
        n_p  = int(row["n_partial"])
        n_u  = int(row["n_unserved"])
        svc  = f"{row['svc_rate_pct']:.1f}%"
        cost = f"${row['total_cost']:,.0f}"
        peak = f"{row['peak_kw']:,.0f} kW"
        e_d  = f"{row['e_demanded_kwh']:,.0f} kWh"
        e_un = f"{row['e_unmet_kwh']:,.0f} kWh"
        rows_t.append([f"#{rank}", f"{date}\n({dow})", mix,
                        str(n_v), f"{n_f}/{n_p}/{n_u}", svc,
                        cost, peak, e_d, e_un])
        col_bg.append("#fde8e8" if rank<=3 else ("#fff3e0" if rank<=6 else "#f5f8fc"))

    _table(ax_t,
           ["Rank","Date","Charger Mix","Vehicles\nTotal",
            "Full/Part/Unsrv","Svc%","Daily\nCost","Peak\nGrid",
            "E Demanded","E Unmet"],
           rows_t,
           col_widths=[0.055,0.090,0.170,0.075,0.100,0.060,0.080,0.080,0.095,0.090],
           row_colors=col_bg, fontsize=8.5)

    # Bar charts
    ax_b = fig.add_axes([0.05, 0.09, 0.54, 0.24])
    xs = np.arange(len(worst))
    bw = 0.28
    ax_b.bar(xs-bw, worst["n_vehicles"], bw, color="#555", alpha=0.70, label="Total")
    ax_b.bar(xs,    worst["n_full"],     bw, color=ACCENT,           alpha=0.80, label="Fully served")
    ax_b.bar(xs+bw, worst["n_partial"]+worst["n_unserved"], bw,
             color="#d73027", alpha=0.70, label="Partial+Unserved")
    ax_b.set_xticks(xs)
    ax_b.set_xticklabels([f"#{i+1}" for i in range(len(worst))], fontsize=8)
    ax_b.set_ylabel("Vehicles", fontsize=9)
    ax_b.set_title("Vehicles per worst day", fontsize=9, color=DARK_GRAY)
    ax_b.legend(fontsize=7.5, ncol=3)
    ax_b.grid(axis="y", linestyle=":", alpha=0.35)

    ax_c = fig.add_axes([0.63, 0.09, 0.34, 0.24])
    ax_c.bar(xs, worst["total_cost"], color="#1a5f9a", alpha=0.75, width=0.6)
    ax_c.set_xticks(xs)
    ax_c.set_xticklabels([f"#{i+1}" for i in range(len(worst))], fontsize=8)
    ax_c.set_ylabel("Daily cost (USD)", fontsize=9)
    ax_c.set_title("Daily cost per worst day", fontsize=9, color=DARK_GRAY)
    ax_c.yaxis.set_major_formatter(plt.FuncFormatter(lambda v,_: f"${v:,.0f}"))
    ax_c.grid(axis="y", linestyle=":", alpha=0.35)

    _footer(fig, 8, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s9_mix_breakdown(pdf):
    worst5 = kdf.sort_values(
        ["n_unserved","n_partial","svc_rate_pct"],
        ascending=[False,False,True]
    ).head(5).reset_index(drop=True)

    fig = _fig("Worst-Day Deep Dive — Charger Mix & Energy Breakdown (Top 5 Days)")

    for i, (_, row) in enumerate(worst5.iterrows()):
        x0 = 0.03 + i * 0.196
        ax = fig.add_axes([x0, 0.10, 0.175, 0.80])
        ax.axis("off")

        date = row["date_str"]; dow = row["dow"]
        ax.text(0.5, 0.97, f"#{i+1}  {date}", ha="center", va="top",
                fontsize=9, fontweight="bold", color=TITLE_BG,
                transform=ax.transAxes)
        ax.text(0.5, 0.91, dow, ha="center", va="top",
                fontsize=8, color=MID_GRAY, transform=ax.transAxes)

        # Pie: mix composition
        ax_p = fig.add_axes([x0+0.005, 0.52, 0.165, 0.30])
        sizes, labels_p, colors_p = [], [], []
        for kw, col in TYPE_COLOR.items():
            n = int(row[f"n_{kw}"])
            if n > 0:
                sizes.append(n); labels_p.append(f"{n}×{kw}"); colors_p.append(col)
        if sizes:
            ax_p.pie(sizes, labels=labels_p, colors=colors_p,
                     autopct="%d", textprops={"fontsize": 7.5},
                     startangle=90)
        ax_p.set_title(f"Mix (K={int(row['n_chargers'])})", fontsize=8,
                        color=DARK_GRAY, pad=2)

        # Energy bar
        ax_e = fig.add_axes([x0+0.010, 0.30, 0.155, 0.18])
        ax_e.barh(0, row["e_delivered_kwh"], color=ACCENT, height=0.4, label="Delivered")
        ax_e.barh(0, row["e_unmet_kwh"], color="#d73027", height=0.4,
                  left=row["e_delivered_kwh"], label="Unmet")
        ax_e.set_xlim(0, row["e_demanded_kwh"]*1.05)
        ax_e.set_yticks([])
        ax_e.set_xlabel("kWh", fontsize=7)
        ax_e.set_title(f"Energy: {row['e_delivered_kwh']:,.0f}/{row['e_demanded_kwh']:,.0f} kWh",
                        fontsize=7.5, color=DARK_GRAY, pad=2)
        ax_e.xaxis.set_major_formatter(plt.FuncFormatter(lambda v,_: f"{v:,.0f}"))

        # Stats text
        stats_txt = [
            f"Vehicles: {int(row['n_vehicles'])}",
            f"Full: {int(row['n_full'])}  ({row['svc_rate_pct']:.0f}%)",
            f"Partial: {int(row['n_partial'])}",
            f"Unserved: {int(row['n_unserved'])}",
            f"Peak: {row['peak_kw']:,.0f} kW",
            f"Cost: ${row['total_cost']:,.0f}",
        ]
        for j, s in enumerate(stats_txt):
            ax.text(0.05, 0.27-j*0.044, s, ha="left", va="top",
                    fontsize=8.5, color=DARK_GRAY, transform=ax.transAxes)

    _footer(fig, 9, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s10_comparison(pdf):
    fig = _fig("Kempower vs XOS A2 — Head-to-Head Comparison (Northgate)")

    # Merge on date
    xos_a2_m  = xos_a2.rename(columns={"K":"xos_K","n_fully_served":"xos_full",
                                          "n_vehicles":"xos_veh","n_partial":"xos_part",
                                          "n_unserved":"xos_uns",
                                          "service_rate_pct":"xos_svc"})
    xos_dc2_m = xos_dc2.rename(columns={"total_daily_excl_demand":"xos_cost",
                                          "peak_grid_kw":"xos_peak"})
    merged = kdf.merge(xos_a2_m[["date","xos_K","xos_full","xos_veh","xos_svc"]],
                       left_on="date_str", right_on="date", how="inner")
    merged = merged.merge(xos_dc2_m[["date","xos_cost","xos_peak"]],
                          left_on="date_str", right_on="date", how="left")

    # Top: summary comparison table
    ax_t = fig.add_axes([0.03, 0.66, 0.94, 0.25])
    comp_rows = [
        ["Avg chargers / hubs per day",
         f"{kdf['n_chargers'].mean():.1f}",
         f"{xos_a2['K'].mean():.1f}",
         f"Kempower uses {xos_a2['K'].mean()-kdf['n_chargers'].mean():.1f} fewer units"],
        ["% days with full vehicle service",
         f"{(kdf['svc_rate_pct']==100).mean()*100:.1f}%",
         f"{(xos_a2['service_rate_pct']==100).mean()*100:.1f}%" if 'service_rate_pct' in xos_a2 else "—",
         ""],
        ["Overall vehicle service rate",
         f"{100*kdf['n_full'].sum()/kdf['n_vehicles'].sum():.1f}%",
         f"{100*xos_a2['n_fully_served'].sum()/xos_a2['n_vehicles'].sum():.1f}%",
         "XOS serves more vehicles overall (mobile can self-discharge & recharge)"],
        ["Avg daily cost (excl demand)",
         f"${kdf['total_cost'].mean():.0f}",
         f"${xos_dc2['total_daily_excl_demand'].mean():.0f}",
         f"Kempower is ${xos_dc2['total_daily_excl_demand'].mean()-kdf['total_cost'].mean():.0f}/day cheaper"],
        ["Max daily cost (worst day)",
         f"${kdf['total_cost'].max():.0f}",
         f"${xos_dc2['total_daily_excl_demand'].max():.0f}",
         ""],
        ["Avg peak grid draw",
         f"{kdf['peak_kw'].mean():.0f} kW",
         f"{xos_dc2['peak_grid_kw'].mean():.0f} kW",
         "Kempower draws more from grid (no battery buffer)"],
        ["Max peak grid draw",
         f"{kdf['peak_kw'].max():.0f} kW",
         f"{xos_dc2['peak_grid_kw'].max():.0f} kW",
         ""],
    ]
    _table(ax_t,
           ["Metric", "Kempower\n(fixed DCFC)", "XOS Hub MC02\n(Scenario A2)", "Note"],
           comp_rows,
           col_widths=[0.32, 0.16, 0.16, 0.36],
           fontsize=9)

    # Bottom left: cost scatter
    ax1 = fig.add_axes([0.05, 0.09, 0.40, 0.50])
    if not merged.empty:
        ax1.scatter(merged["xos_cost"], merged["total_cost"],
                    alpha=0.45, s=18, color=ACCENT, edgecolors="none")
        mn = min(merged["xos_cost"].min(), merged["total_cost"].min())
        mx = max(merged["xos_cost"].max(), merged["total_cost"].max())
        ax1.plot([mn, mx], [mn, mx], "k--", linewidth=0.8, alpha=0.5, label="Equal cost")
        ax1.set_xlabel("XOS A2 daily cost ($)", fontsize=9)
        ax1.set_ylabel("Kempower daily cost ($)", fontsize=9)
        ax1.set_title("Daily cost: Kempower vs XOS A2\n(points below diagonal = Kempower cheaper)",
                       fontsize=9, color=DARK_GRAY)
        ax1.legend(fontsize=8); ax1.grid(linestyle=":", alpha=0.30)
        ax1.xaxis.set_major_formatter(plt.FuncFormatter(lambda v,_: f"${v:,.0f}"))
        ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v,_: f"${v:,.0f}"))

    # Bottom right: service rate comparison
    ax2 = fig.add_axes([0.55, 0.09, 0.40, 0.50])
    if not merged.empty:
        ax2.scatter(merged["xos_svc"], merged["svc_rate_pct"],
                    alpha=0.45, s=18, color=TYPE_COLOR["150kW"], edgecolors="none")
        ax2.plot([0,100],[0,100],"k--",linewidth=0.8,alpha=0.5,label="Equal service rate")
        ax2.set_xlabel("XOS A2 service rate (%)", fontsize=9)
        ax2.set_ylabel("Kempower service rate (%)", fontsize=9)
        ax2.set_title("Service rate: Kempower vs XOS A2\n(points above diagonal = Kempower serves more)",
                       fontsize=9, color=DARK_GRAY)
        ax2.set_xlim(0, 105); ax2.set_ylim(0, 105)
        ax2.legend(fontsize=8); ax2.grid(linestyle=":", alpha=0.30)
        ax2.xaxis.set_major_formatter(plt.FuncFormatter(lambda v,_: f"{v:.0f}%"))
        ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda v,_: f"{v:.0f}%"))

    _footer(fig, 10, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


def s11_conclusions(pdf):
    fig = _fig("Conclusions & Recommendations — Northgate Kempower")
    ax  = fig.add_axes([0.03, 0.06, 0.94, 0.86])
    ax.axis("off")

    sections = [
        ("#2d6a2d", "Kempower Fixed DCFC Performance at Northgate", [
            f"307 days analyzed  |  {kdf['n_vehicles'].sum():,} vehicles  |  "
            f"{100*kdf['n_full'].sum()/kdf['n_vehicles'].sum():.1f}% fully served",
            f"Optimal mix: avg {kdf['n_chargers'].mean():.1f} chargers/day "
            f"(typically 1–3×50kW + 2–3×150kW + 1–3×250kW)",
            f"Daily cost avg ${kdf['total_cost'].mean():.0f} (excl demand)  |  "
            f"Worst day ${kdf['total_cost'].max():.0f}  |  "
            f"Peak grid {kdf['peak_kw'].max():.0f} kW",
        ]),
        ("#1a3a7a", "Kempower vs XOS A2 Trade-offs", [
            f"Cost: Kempower is ~${xos_dc2['total_daily_excl_demand'].mean()-kdf['total_cost'].mean():.0f}/day cheaper "
            f"(${kdf['total_cost'].mean():.0f} vs ${xos_dc2['total_daily_excl_demand'].mean():.0f}) "
            "— no mobile battery means lower amortized CAPEX",
            f"Service rate: XOS A2 serves more vehicles "
            f"({100*xos_a2['n_fully_served'].sum()/xos_a2['n_vehicles'].sum():.1f}% vs "
            f"{100*kdf['n_full'].sum()/kdf['n_vehicles'].sum():.1f}%) — battery buffer handles "
            "peak demand without requiring more grid capacity",
            f"Peak grid: Kempower draws more peak power ({kdf['peak_kw'].mean():.0f} kW avg vs "
            f"{xos_dc2['peak_grid_kw'].mean():.0f} kW for XOS) — no battery to absorb peaks",
            "Kempower: fixed infrastructure, no logistics, unlimited availability "
            "— XOS: mobile, can be redeployed, but requires transport & maintenance",
        ]),
        ("#7a1a1a", "Why Some Vehicles Remain Unserved (10.5% gap)", [
            "Short dwell windows — some vehicles arrive and depart too quickly for full charge",
            "Concurrent demand peaks — dwell windows overlap and total demand exceeds installed capacity",
            "MILP optimizes for minimum cost, which may leave some difficult vehicles unserved",
            "Solution: increase charger count cap, extend dwell windows, or add 1–2 larger 250kW units",
        ]),
        ("#5c4a00", "Recommendations", [
            "Northgate: Kempower mix of 2×150kW + 3×250kW covers ~90% of days at ~$500/day",
            "For 100% coverage on peak days: add 1–2×250kW chargers (handles 95%+ of days)",
            "Consider hybrid: 2–3 permanent Kempower units + XOS Hub MC02 for overflow days",
            "Grid service upgrade needed: worst-day peak of 1,630 kW requires ~2 MVA service",
        ]),
    ]

    y = 0.97
    for color, title, bullets in sections:
        ax.text(0.01, y, title, ha="left", va="top", fontsize=11.5,
                fontweight="bold", color=color, transform=ax.transAxes)
        y -= 0.050
        for b in bullets:
            ax.text(0.022, y, f"• {b}", ha="left", va="top", fontsize=9.0,
                    color=DARK_GRAY, transform=ax.transAxes)
            y -= 0.054
        y -= 0.018

    _footer(fig, 11, TOTAL)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)


# ══════════════════════════════════════════════════════════════════════════════
# BUILD PDF
# ══════════════════════════════════════════════════════════════════════════════

with PdfPages(PDF) as pdf:
    meta = pdf.infodict()
    meta["Title"]   = "Kempower Fixed DCFC Analysis — Northgate"
    meta["Author"]  = "Caltrans EV Fleet Analysis"
    meta["Subject"] = "Kempower MILP charger sizing, daily figures, XOS comparison"

    s1_title(pdf);        print("  Slide  1: Title")
    s2_overview(pdf);     print("  Slide  2: Overview")
    s3_specs(pdf);        print("  Slide  3: Specs + cost model")
    s4_summary(pdf);      print("  Slide  4: Season summary")
    s5_mix_distribution(pdf); print("  Slide  5: Mix distribution")
    s6_energy_profile(pdf); print("  Slide  6: Energy profile")
    s7_cost_profile(pdf); print("  Slide  7: Cost profile")
    s8_worst_days(pdf);   print("  Slide  8: Worst days table")
    s9_mix_breakdown(pdf);print("  Slide  9: Mix breakdown top 5")
    s10_comparison(pdf);  print("  Slide 10: Kempower vs XOS A2")
    s11_conclusions(pdf); print("  Slide 11: Conclusions")

    fig = _fig("")
    _footer(fig, 12, TOTAL)
    fig.text(0.5, 0.5, "— End of Presentation —",
             ha="center", va="center", fontsize=18, color=LIGHT_GRAY)
    pdf.savefig(fig, bbox_inches="tight"); plt.close(fig)
    print("  Slide 12: End")

print(f"\nSaved: {PDF}")
