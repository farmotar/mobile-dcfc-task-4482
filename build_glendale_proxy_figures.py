"""
build_glendale_proxy_figures.py
================================
Generates comparison figures for Glendale with both utility proxies:
  Proxy A — PG&E BEV-2 Secondary (subscription demand)
  Proxy B — SMUD C&I 21–299 kW (two-tier demand charges)

For all three charger technologies: XOS Hub MC02, Kempower DCFC, Fixed Charger MILP.

Output: appendix_a_figures/presentation_style/P11_* through P14_*
"""
from __future__ import annotations
import re
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches

import sys
sys.stdout.reconfigure(encoding="utf-8")

# ── Paths ──────────────────────────────────────────────────────────────────────
CHARGER_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
SCENARIO    = CHARGER_DIR / "scenario_outputs"
REPO_DIR    = Path(__file__).resolve().parent
XOS_OUT     = REPO_DIR / "xos_outputs"
FIX_OUT     = CHARGER_DIR / "fixed_charger_milp_outputs"
FIGS        = CHARGER_DIR / "appendix_a_figures" / "presentation_style"
FIGS.mkdir(parents=True, exist_ok=True)

DAYS_PER_MO = 30.42

# Colors for proxy types
C_PGE  = "#f46d43"   # orange — PG&E BEV-2 proxy
C_SMUD = "#2166ac"   # blue   — SMUD proxy
C_CAP  = "#2166ac"   # CapEx
C_NRG  = "#4dac26"   # Energy
C_DEM  = "#d01c8b"   # Demand


# ── Data loaders ───────────────────────────────────────────────────────────────

def load_kempower(site_key: str) -> pd.DataFrame:
    """Load Kempower per-day data from per-day breakdown + summary CSV."""
    summ_path = SCENARIO / f"{site_key}_analysis" / f"{site_key}_kempower_summary.csv"
    per_day   = SCENARIO / f"{site_key}_analysis" / "per_day"
    if not summ_path.exists():
        print(f"  [SKIP] {summ_path.name} not found"); return pd.DataFrame()

    summ = pd.read_csv(summ_path)
    summ["date"] = pd.to_datetime(summ["date"])

    # Pull cost components from per-day breakdown files
    cost_rows = []
    for cb_path in sorted(per_day.glob("*/kempower/exact_milp_cost_breakdown.csv")):
        date_str = cb_path.parent.parent.name
        cb = pd.read_csv(cb_path)
        def val(comp):
            r = cb[cb.component == comp]
            return float(r["value"].iloc[0]) if not r.empty else 0.0
        cost_rows.append({
            "date":                   pd.to_datetime(date_str),
            "capex_bd":               val("daily_capex_cost"),
            "energy_bd":              val("energy_cost"),
            "global_demand_cost":     val("global_demand_cost"),
            "peak_window_demand_cost":val("peak_window_demand_cost"),
            "p_max_kw":               val("P_max_kw"),
        })

    if cost_rows:
        cdf = pd.DataFrame(cost_rows)
        summ = summ.merge(cdf, on="date", how="left")

    # Resolve capex and energy (summary may be empty for smud)
    summ["capex_final"]  = summ["capex_daily"].where(summ["capex_daily"].notna() & (summ["capex_daily"] != ""),
                                                      summ.get("capex_bd", np.nan))
    summ["energy_final"] = summ["energy_cost"].where(summ["energy_cost"].notna() & (summ["energy_cost"].astype(str) != ""),
                                                      summ.get("energy_bd", np.nan))
    for col in ["capex_bd","energy_bd","global_demand_cost","peak_window_demand_cost","p_max_kw"]:
        if col not in summ.columns:
            summ[col] = 0.0
        summ[col] = pd.to_numeric(summ[col], errors="coerce").fillna(0.0)

    summ["capex_final"]  = pd.to_numeric(summ["capex_final"],  errors="coerce").fillna(summ["capex_bd"])
    summ["energy_final"] = pd.to_numeric(summ["energy_final"], errors="coerce").fillna(summ["energy_bd"])
    summ["demand_daily"] = (summ["global_demand_cost"] + summ["peak_window_demand_cost"]) / DAYS_PER_MO
    summ["total_allin"]  = summ["capex_final"] + summ["energy_final"] + summ["demand_daily"]
    return summ.sort_values("date").reset_index(drop=True)


def load_xos_new(site_key: str) -> pd.DataFrame:
    """Load XOS results from new-format all-days CSV (run_xos_pipeline output)."""
    path = XOS_OUT / f"{site_key}_all_days_xos.csv"
    if not path.exists():
        print(f"  [SKIP] {path.name} not found"); return pd.DataFrame()
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df["total_allin"] = pd.to_numeric(df["total_daily_cost"], errors="coerce").fillna(0.0)
    return df.sort_values("date").reset_index(drop=True)


def load_fixed(site_key: str) -> pd.DataFrame:
    """Load Fixed Charger MILP results (demand already applied as monthly proxy)."""
    path = FIX_OUT / f"{site_key}_all_days_milp.csv"
    if not path.exists():
        print(f"  [SKIP] {path.name} not found"); return pd.DataFrame()
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    df["total_allin"] = pd.to_numeric(df["total_op_cost"], errors="coerce").fillna(0.0)
    return df.sort_values("date").reset_index(drop=True)


def parse_mix(mix_str):
    """Parse mix string like '1×150kW + 1×250kW' → (n50, n150, n250)."""
    counts = {"50": 0, "150": 0, "250": 0}
    if pd.isna(mix_str):
        return 0, 0, 0
    for part in re.split(r"\+", str(mix_str)):
        m = re.search(r"(\d+)[^\d]+(\d+)\s*kW", part.strip())
        if m:
            n, p = int(m.group(1)), str(int(m.group(2)))
            if p in counts:
                counts[p] = n
    return counts["50"], counts["150"], counts["250"]


def savefig(name, fig, dpi=150):
    p = FIGS / name
    fig.savefig(p, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved: {name}")


# ── Load data ─────────────────────────────────────────────────────────────────
print("Loading data …")

kmp_pge  = load_kempower("glendale")
kmp_smud = load_kempower("glendale_smud")
xos_pge  = load_xos_new("glendale")
xos_smud = load_xos_new("glendale_smud")
fix_pge  = load_fixed("glendale")
fix_smud = load_fixed("glendale_smud")

print(f"  Kempower PG&E:  {len(kmp_pge)} days  avg ${kmp_pge['total_allin'].mean():.0f}/day")
print(f"  Kempower SMUD:  {len(kmp_smud)} days  avg ${kmp_smud['total_allin'].mean():.0f}/day")
print(f"  XOS PG&E:       {len(xos_pge)} days  avg ${xos_pge['total_allin'].mean():.0f}/day")
print(f"  XOS SMUD:       {len(xos_smud)} days  avg ${xos_smud['total_allin'].mean():.0f}/day")
print(f"  Fixed PG&E:     {len(fix_pge)} days  avg ${fix_pge['total_allin'].mean():.0f}/day")
print(f"  Fixed SMUD:     {len(fix_smud)} days  avg ${fix_smud['total_allin'].mean():.0f}/day")


# ══════════════════════════════════════════════════════════════════════════════
# P11 — KEMPOWER GLENDALE: PG&E vs SMUD (side-by-side, 3 panels)
# ══════════════════════════════════════════════════════════════════════════════
print("\nGenerating P11 …")

if len(kmp_pge) > 0 and len(kmp_smud) > 0:
    # Charger-mix frequencies
    def mix_freq(kd, max_k=6):
        if "n_chargers" in kd.columns:
            vc = kd["n_chargers"].value_counts().sort_index()
        else:
            kd["_nc"] = kd["mix"].apply(lambda m: sum(parse_mix(m)))
            vc = kd["_nc"].value_counts().sort_index()
        return {k: vc.get(k, 0) for k in range(1, max_k+1)}

    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    fig.patch.set_facecolor("white")

    # ── Panel 1: Daily cost timeseries ──
    ax = axes[0]
    ax.scatter(kmp_pge["date"],  kmp_pge["total_allin"],  color=C_PGE,  s=12, alpha=0.65, label="PG&E BEV-2 (proxy A)", zorder=3)
    ax.scatter(kmp_smud["date"], kmp_smud["total_allin"], color=C_SMUD, s=12, alpha=0.65, label="SMUD C&I (proxy B)",   zorder=3)
    pge_mean  = kmp_pge["total_allin"].mean()
    smud_mean = kmp_smud["total_allin"].mean()
    ax.axhline(pge_mean,  color=C_PGE,  lw=1.5, ls="--", alpha=0.9, label=f"PG&E mean ${pge_mean:.0f}/day")
    ax.axhline(smud_mean, color=C_SMUD, lw=1.5, ls="--", alpha=0.9, label=f"SMUD mean ${smud_mean:.0f}/day")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n'%y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_ylabel("Daily cost ($/day)", fontsize=10)
    ax.set_title("Daily Cost — Full Year", fontsize=10, fontweight="bold")
    ax.legend(fontsize=8, loc="upper left", framealpha=0.92)
    ax.grid(axis="y", linestyle=":", alpha=0.35)

    # ── Panel 2: Charger count distribution ──
    ax = axes[1]
    if "n_chargers" in kmp_pge.columns:
        K_max = max(int(kmp_pge["n_chargers"].max()), int(kmp_smud["n_chargers"].max()), 4)
        k_range = np.arange(1, K_max+1)
        vc_pge  = kmp_pge["n_chargers"].value_counts()
        vc_smud = kmp_smud["n_chargers"].value_counts()
        w = 0.38
        ax.bar(k_range - w/2, [vc_pge.get(k, 0)  for k in k_range], width=w, color=C_PGE,  alpha=0.85, label="PG&E")
        ax.bar(k_range + w/2, [vc_smud.get(k, 0) for k in k_range], width=w, color=C_SMUD, alpha=0.85, label="SMUD")
        ax.set_xlabel("Chargers deployed", fontsize=9)
        ax.set_ylabel("Number of days",    fontsize=9)
        ax.set_title("Daily Charger Count Distribution", fontsize=10, fontweight="bold")
        ax.set_xticks(k_range)
        ax.legend(fontsize=9)
        ax.grid(axis="y", linestyle=":", alpha=0.35)
    else:
        ax.text(0.5, 0.5, "n_chargers not available", ha="center", va="center", transform=ax.transAxes)

    # ── Panel 3: Avg cost breakdown bar chart ──
    ax = axes[2]
    def avg_breakdown(kd):
        cap  = kd["capex_final"].mean() if "capex_final" in kd.columns else kd.get("capex_bd", pd.Series(0)).mean()
        nrg  = kd["energy_final"].mean() if "energy_final" in kd.columns else kd.get("energy_bd", pd.Series(0)).mean()
        dem  = kd["demand_daily"].mean()
        return cap, nrg, dem

    cap_p, nrg_p, dem_p = avg_breakdown(kmp_pge)
    cap_s, nrg_s, dem_s = avg_breakdown(kmp_smud)
    labels  = ["PG&E BEV-2\n(Proxy A)", "SMUD C&I\n(Proxy B)"]
    caps    = [cap_p, cap_s]
    nrgs    = [nrg_p, nrg_s]
    dems    = [dem_p, dem_s]
    totals  = [c+n+d for c,n,d in zip(caps,nrgs,dems)]
    x = np.arange(2)
    b1 = ax.bar(x, caps, color=C_CAP, alpha=0.9, label="CapEx (amort.)")
    b2 = ax.bar(x, nrgs, bottom=caps, color=C_NRG, alpha=0.9, label="Energy (TOU)")
    b3 = ax.bar(x, dems, bottom=[c+n for c,n in zip(caps,nrgs)], color=C_DEM, alpha=0.9, label="Demand (amort.)")
    for xi, tot in enumerate(totals):
        ax.text(xi, tot + max(totals)*0.012, f"${tot:.0f}", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Avg daily cost ($/day)", fontsize=9)
    ax.set_title("Avg Cost Breakdown by Proxy", fontsize=10, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(fontsize=8.5, loc="upper left"); ax.grid(axis="y", linestyle=":", alpha=0.35)
    ax.set_xlim(-0.55, 1.55)

    fig.suptitle(
        "Kempower Fixed DCFC — Glendale Maintenance Station\n"
        "Utility Rate Sensitivity: PG&E BEV-2 (Proxy A) vs. SMUD C&I (Proxy B)  ⚠ GWP actual tariff not confirmed",
        fontsize=12, fontweight="bold")
    fig.tight_layout()
    savefig("P11_kmp_glendale_proxy_comparison.png", fig)


# ══════════════════════════════════════════════════════════════════════════════
# P12 — XOS HUB MC02 GLENDALE: PG&E vs SMUD
# ══════════════════════════════════════════════════════════════════════════════
print("Generating P12 …")

if len(xos_pge) > 0 and len(xos_smud) > 0:
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    fig.patch.set_facecolor("white")

    # ── Panel 1: Daily cost timeseries ──
    ax = axes[0]
    ax.scatter(xos_pge["date"],  xos_pge["total_allin"],  color=C_PGE,  s=12, alpha=0.65, label="PG&E BEV-2 (proxy A)", zorder=3)
    ax.scatter(xos_smud["date"], xos_smud["total_allin"], color=C_SMUD, s=12, alpha=0.65, label="SMUD C&I (proxy B)",   zorder=3)
    for df, c, lbl in [(xos_pge, C_PGE, "PG&E"), (xos_smud, C_SMUD, "SMUD")]:
        mn = df["total_allin"].mean()
        ax.axhline(mn, color=c, lw=1.4, ls="--", alpha=0.85, label=f"{lbl} mean ${mn:.0f}/day")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n'%y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_ylabel("Daily cost ($/day)", fontsize=10)
    ax.set_title("Daily Cost — Full Year", fontsize=10, fontweight="bold")
    ax.legend(fontsize=8, loc="upper left", framealpha=0.92)
    ax.grid(axis="y", linestyle=":", alpha=0.35)

    # ── Panel 2: Unit count distribution ──
    ax = axes[1]
    if "n_xos_units" in xos_pge.columns:
        K_max = max(int(xos_pge["n_xos_units"].max()), int(xos_smud["n_xos_units"].max()), 3)
        k_range = np.arange(1, K_max+1)
        vc_p = xos_pge["n_xos_units"].value_counts()
        vc_s = xos_smud["n_xos_units"].value_counts()
        w = 0.38
        ax.bar(k_range - w/2, [vc_p.get(k, 0) for k in k_range], width=w, color=C_PGE,  alpha=0.85, label="PG&E")
        ax.bar(k_range + w/2, [vc_s.get(k, 0) for k in k_range], width=w, color=C_SMUD, alpha=0.85, label="SMUD")
        ax.set_xlabel("XOS units deployed", fontsize=9)
        ax.set_ylabel("Number of days",     fontsize=9)
        ax.set_title("Daily Unit Count Distribution", fontsize=10, fontweight="bold")
        ax.set_xticks(k_range)
        ax.legend(fontsize=9)
        ax.grid(axis="y", linestyle=":", alpha=0.35)

    # ── Panel 3: Avg cost breakdown ──
    ax = axes[2]
    def xos_breakdown(df):
        cap = pd.to_numeric(df.get("capex_cost", pd.Series(0)), errors="coerce").mean()
        nrg = pd.to_numeric(df.get("energy_cost", pd.Series(0)), errors="coerce").mean()
        dem = (pd.to_numeric(df.get("demand_cost", pd.Series(0)), errors="coerce") +
               pd.to_numeric(df.get("peak_win_cost", pd.Series(0)), errors="coerce")).mean()
        return cap, nrg, dem

    cap_p, nrg_p, dem_p = xos_breakdown(xos_pge)
    cap_s, nrg_s, dem_s = xos_breakdown(xos_smud)
    labels = ["PG&E BEV-2\n(Proxy A)", "SMUD C&I\n(Proxy B)"]
    caps   = [cap_p, cap_s]; nrgs = [nrg_p, nrg_s]; dems = [dem_p, dem_s]
    totals = [c+n+d for c,n,d in zip(caps,nrgs,dems)]
    x = np.arange(2)
    ax.bar(x, caps, color=C_CAP, alpha=0.9, label="CapEx (amort.)")
    ax.bar(x, nrgs, bottom=caps, color=C_NRG, alpha=0.9, label="Energy (TOU)")
    ax.bar(x, dems, bottom=[c+n for c,n in zip(caps,nrgs)], color=C_DEM, alpha=0.9, label="Demand (amort.)")
    for xi, tot in enumerate(totals):
        ax.text(xi, tot + max(totals)*0.012 if max(totals) > 0 else 1,
                f"${tot:.0f}", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Avg daily cost ($/day)", fontsize=9)
    ax.set_title("Avg Cost Breakdown by Proxy", fontsize=10, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(fontsize=8.5, loc="upper left"); ax.grid(axis="y", linestyle=":", alpha=0.35)
    ax.set_xlim(-0.55, 1.55)

    fig.suptitle(
        "XOS Hub MC02 — Glendale Maintenance Station\n"
        "Utility Rate Sensitivity: PG&E BEV-2 (Proxy A) vs. SMUD C&I (Proxy B)  ⚠ GWP actual tariff not confirmed",
        fontsize=12, fontweight="bold")
    fig.tight_layout()
    savefig("P12_xos_glendale_proxy_comparison.png", fig)


# ══════════════════════════════════════════════════════════════════════════════
# P13 — FIXED CHARGER MILP GLENDALE: PG&E vs SMUD
# ══════════════════════════════════════════════════════════════════════════════
print("Generating P13 …")

if len(fix_pge) > 0 and len(fix_smud) > 0:
    fig, axes = plt.subplots(1, 3, figsize=(18, 5.5))
    fig.patch.set_facecolor("white")

    # ── Panel 1: Daily cost timeseries ──
    ax = axes[0]
    ax.scatter(fix_pge["date"],  fix_pge["total_allin"],  color=C_PGE,  s=12, alpha=0.65, label="PG&E BEV-2 (proxy A)", zorder=3)
    ax.scatter(fix_smud["date"], fix_smud["total_allin"], color=C_SMUD, s=12, alpha=0.65, label="SMUD C&I (proxy B)",   zorder=3)
    for df, c, lbl in [(fix_pge, C_PGE, "PG&E"), (fix_smud, C_SMUD, "SMUD")]:
        mn = df["total_allin"].mean()
        ax.axhline(mn, color=c, lw=1.4, ls="--", alpha=0.85, label=f"{lbl} mean ${mn:.0f}/day")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b\n'%y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.set_ylabel("Daily cost ($/day)", fontsize=10)
    ax.set_title("Daily Cost — Full Year", fontsize=10, fontweight="bold")
    ax.legend(fontsize=8, loc="upper left", framealpha=0.92)
    ax.grid(axis="y", linestyle=":", alpha=0.35)

    # ── Panel 2: Config label distribution ──
    ax = axes[1]
    top_configs = set(fix_pge["config_label"].value_counts().head(6).index) | \
                  set(fix_smud["config_label"].value_counts().head(6).index)
    top_configs = sorted(top_configs, key=lambda s: fix_pge["config_label"].value_counts().get(s, 0), reverse=True)[:6]
    vc_p = fix_pge["config_label"].value_counts()
    vc_s = fix_smud["config_label"].value_counts()
    x = np.arange(len(top_configs))
    w = 0.38
    ax.barh(x[::-1] - w/2, [vc_p.get(c, 0) for c in top_configs],
            height=w, color=C_PGE,  alpha=0.85, label="PG&E")
    ax.barh(x[::-1] + w/2, [vc_s.get(c, 0) for c in top_configs],
            height=w, color=C_SMUD, alpha=0.85, label="SMUD")
    ax.set_yticks(x[::-1])
    ax.set_yticklabels([c.replace(" + ", "\n+") for c in top_configs], fontsize=7.5)
    ax.set_xlabel("Number of days", fontsize=9)
    ax.set_title("Most Common Charger Configs", fontsize=10, fontweight="bold")
    ax.legend(fontsize=9, loc="lower right")
    ax.grid(axis="x", linestyle=":", alpha=0.35)

    # ── Panel 3: Avg cost breakdown ──
    ax = axes[2]
    def fix_breakdown(df):
        cap  = pd.to_numeric(df.get("capex_daily",   pd.Series(0)), errors="coerce").mean()
        nrg  = pd.to_numeric(df.get("energy_cost",   pd.Series(0)), errors="coerce").mean()
        dem  = (pd.to_numeric(df.get("demand_global",   pd.Series(0)), errors="coerce") +
                pd.to_numeric(df.get("demand_peak_win", pd.Series(0)), errors="coerce")).mean()
        return cap, nrg, dem

    cap_p, nrg_p, dem_p = fix_breakdown(fix_pge)
    cap_s, nrg_s, dem_s = fix_breakdown(fix_smud)
    labels = ["PG&E BEV-2\n(Proxy A)", "SMUD C&I\n(Proxy B)"]
    caps   = [cap_p, cap_s]; nrgs = [nrg_p, nrg_s]; dems = [dem_p, dem_s]
    totals = [c+n+d for c,n,d in zip(caps,nrgs,dems)]
    x = np.arange(2)
    ax.bar(x, caps, color=C_CAP, alpha=0.9, label="CapEx (amort.)")
    ax.bar(x, nrgs, bottom=caps, color=C_NRG, alpha=0.9, label="Energy (TOU)")
    ax.bar(x, dems, bottom=[c+n for c,n in zip(caps,nrgs)], color=C_DEM, alpha=0.9, label="Demand (monthly proxy)")
    for xi, tot in enumerate(totals):
        ax.text(xi, tot + max(totals)*0.012, f"${tot:.0f}", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9)
    ax.set_ylabel("Avg daily cost ($/day)", fontsize=9)
    ax.set_title("Avg Cost Breakdown by Proxy", fontsize=10, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(fontsize=8.5, loc="upper left"); ax.grid(axis="y", linestyle=":", alpha=0.35)
    ax.set_xlim(-0.55, 1.55)
    ax.text(0.99, 0.02, "† Demand shown as monthly-rate proxy (same methodology as SMUD Northgate)",
            transform=ax.transAxes, fontsize=6.5, ha="right", va="bottom", color="#666")

    fig.suptitle(
        "Fixed Charger MILP — Glendale Maintenance Station\n"
        "Utility Rate Sensitivity: PG&E BEV-2 (Proxy A) vs. SMUD C&I (Proxy B)  ⚠ GWP actual tariff not confirmed",
        fontsize=12, fontweight="bold")
    fig.tight_layout()
    savefig("P13_fixed_glendale_proxy_comparison.png", fig)


# ══════════════════════════════════════════════════════════════════════════════
# P14 — CROSS-TECHNOLOGY SUMMARY: Glendale PG&E vs SMUD (all 3 options)
# ══════════════════════════════════════════════════════════════════════════════
print("Generating P14 …")

fig, axes = plt.subplots(1, 2, figsize=(14, 6))
fig.patch.set_facecolor("white")

# Collect summary stats
techs   = ["XOS Hub\nMC02", "Kempower\nFixed DCFC", "Fixed\nCharger MILP"]
avg_pge = [xos_pge["total_allin"].mean(),  kmp_pge["total_allin"].mean(),  fix_pge["total_allin"].mean()]
avg_smd = [xos_smud["total_allin"].mean(), kmp_smud["total_allin"].mean(), fix_smud["total_allin"].mean()]
p90_pge = [xos_pge["total_allin"].quantile(0.90),  kmp_pge["total_allin"].quantile(0.90),  fix_pge["total_allin"].quantile(0.90)]
p90_smd = [xos_smud["total_allin"].quantile(0.90), kmp_smud["total_allin"].quantile(0.90), fix_smud["total_allin"].quantile(0.90)]

x = np.arange(len(techs))
w = 0.38

# Panel 1: Average daily cost
ax = axes[0]
bp = ax.bar(x - w/2, avg_pge, width=w, color=C_PGE,  alpha=0.88, label="PG&E BEV-2 (Proxy A)", edgecolor="white", lw=0.5)
bs = ax.bar(x + w/2, avg_smd, width=w, color=C_SMUD, alpha=0.88, label="SMUD C&I (Proxy B)",   edgecolor="white", lw=0.5)
for bar, val in list(zip(bp, avg_pge)) + list(zip(bs, avg_smd)):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(avg_pge+avg_smd)*0.012,
            f"${val:,.0f}", ha="center", va="bottom", fontsize=8, fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(techs, fontsize=10)
ax.set_ylabel("Average daily cost ($/day)", fontsize=10)
ax.set_title("Average Daily Cost by Technology and Rate Proxy", fontsize=10, fontweight="bold")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
ax.legend(fontsize=9.5, loc="upper right"); ax.grid(axis="y", linestyle=":", alpha=0.35)

# Panel 2: p90 daily cost
ax = axes[1]
bp = ax.bar(x - w/2, p90_pge, width=w, color=C_PGE,  alpha=0.88, label="PG&E BEV-2 (Proxy A)", edgecolor="white", lw=0.5)
bs = ax.bar(x + w/2, p90_smd, width=w, color=C_SMUD, alpha=0.88, label="SMUD C&I (Proxy B)",   edgecolor="white", lw=0.5)
for bar, val in list(zip(bp, p90_pge)) + list(zip(bs, p90_smd)):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(p90_pge+p90_smd)*0.012,
            f"${val:,.0f}", ha="center", va="bottom", fontsize=8, fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(techs, fontsize=10)
ax.set_ylabel("90th-percentile daily cost ($/day)", fontsize=10)
ax.set_title("90th-Percentile Daily Cost by Technology and Rate Proxy", fontsize=10, fontweight="bold")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
ax.legend(fontsize=9.5, loc="upper right"); ax.grid(axis="y", linestyle=":", alpha=0.35)

fig.suptitle(
    "Glendale Maintenance Station — Cross-Technology Cost Comparison\n"
    "⚠ Both rates are proxies — actual GWP utility tariff not yet confirmed",
    fontsize=13, fontweight="bold")
fig.tight_layout()
savefig("P14_glendale_cross_technology_summary.png", fig)


# ══════════════════════════════════════════════════════════════════════════════
# P15 — SUMMARY TABLE FIGURE (text-based)
# ══════════════════════════════════════════════════════════════════════════════
print("Generating P15 (summary table) …")

def p90(df): return df["total_allin"].quantile(0.90) if len(df) else float("nan")
def svc(df, col="service_rate_pct"):
    c = col if col in df.columns else ("svc_rate_pct" if "svc_rate_pct" in df.columns else None)
    return df[c].mean() if c else float("nan")

rows = [
    # Technology / Proxy / Days / Avg Cost / p90 Cost / Service Rate / Recommended Config
    ["XOS Hub MC02",         "PG&E BEV-2 (Proxy A)", len(xos_pge),  xos_pge["total_allin"].mean(),  p90(xos_pge),
     svc(xos_pge, "service_rate_pct"), f"{int(xos_pge['n_xos_units'].mode()[0])} units (mode)" if "n_xos_units" in xos_pge.columns else "—"],
    ["XOS Hub MC02",         "SMUD C&I (Proxy B)",   len(xos_smud), xos_smud["total_allin"].mean(), p90(xos_smud),
     svc(xos_smud, "service_rate_pct"), f"{int(xos_smud['n_xos_units'].mode()[0])} units (mode)" if "n_xos_units" in xos_smud.columns else "—"],
    ["Kempower Fixed DCFC",  "PG&E BEV-2 (Proxy A)", len(kmp_pge),  kmp_pge["total_allin"].mean(),  p90(kmp_pge),
     svc(kmp_pge, "svc_rate_pct"), kmp_pge["mix"].mode()[0] if "mix" in kmp_pge.columns else "—"],
    ["Kempower Fixed DCFC",  "SMUD C&I (Proxy B)",   len(kmp_smud), kmp_smud["total_allin"].mean(), p90(kmp_smud),
     svc(kmp_smud, "svc_rate_pct"), kmp_smud["mix"].mode()[0] if "mix" in kmp_smud.columns else "—"],
    ["Fixed Charger MILP",   "PG&E BEV-2 (Proxy A)", len(fix_pge),  fix_pge["total_allin"].mean(),  p90(fix_pge),
     svc(fix_pge, "vehicles_served_pct"), fix_pge["config_label"].mode()[0] if "config_label" in fix_pge.columns else "—"],
    ["Fixed Charger MILP",   "SMUD C&I (Proxy B)",   len(fix_smud), fix_smud["total_allin"].mean(), p90(fix_smud),
     svc(fix_smud, "vehicles_served_pct"), fix_smud["config_label"].mode()[0] if "config_label" in fix_smud.columns else "—"],
]

fig, ax = plt.subplots(figsize=(18, 4.5))
fig.patch.set_facecolor("white")
ax.set_axis_off()

cols = ["Technology", "Utility Proxy", "Days\nAnalyzed", "Avg Daily\nCost", "p90 Daily\nCost", "Avg Svc\nRate", "Most Common\nConfig"]
cell_data = [
    [r[0], r[1], str(r[2]),
     f"${r[3]:,.0f}/day" if not np.isnan(r[3]) else "—",
     f"${r[4]:,.0f}/day" if not np.isnan(r[4]) else "—",
     f"{r[5]:.1f}%" if not np.isnan(r[5]) else "—",
     r[6]]
    for r in rows
]

tbl = ax.table(
    cellText=cell_data,
    colLabels=cols,
    cellLoc="center",
    loc="center",
)
tbl.auto_set_font_size(False)
tbl.set_fontsize(9)
tbl.scale(1, 2.2)

# Style header row
for j in range(len(cols)):
    tbl[(0, j)].set_facecolor("#2F5496")
    tbl[(0, j)].set_text_props(color="white", fontweight="bold", fontsize=9)

# Highlight PG&E rows in light orange, SMUD rows in light blue
for i, r in enumerate(rows):
    fc = "#fff3ec" if "PG&E" in r[1] else "#edf3fb"
    for j in range(len(cols)):
        tbl[(i+1, j)].set_facecolor(fc)
        tbl[(i+1, j)].set_text_props(fontsize=8.5)

ax.set_title(
    "Glendale Maintenance Station — Summary Table: All Technologies × Both Utility Proxies\n"
    "⚠ Both PG&E and SMUD are proxies — actual GWP tariff not confirmed",
    fontsize=11, fontweight="bold", pad=12)
fig.tight_layout()
savefig("P15_glendale_summary_table.png", fig)


print(f"\nAll figures saved to: {FIGS}")
