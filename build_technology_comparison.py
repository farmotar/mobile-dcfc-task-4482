"""
build_technology_comparison.py
-------------------------------
Cross-technology cost comparison: Fixed DCFC vs Kempower vs XOS Hub vs Diesel.

Reads the existing all-days CSV results for each technology and produces:
  1. results/lcod_by_site_technology.csv       — machine-readable summary
  2. results/technology_comparison_summary.xlsx — formatted Excel workbook
  3. results/technology_comparison_summary.md   — markdown table for appendix
  4. results/tornado_{site}.png                 — sensitivity tornado chart per site

Cost basis (all technologies):
  - CapEx:         straight-line amortization, same formula as existing model
  - Energy:        site TOU tariff (electric) or diesel fuel (diesel)
  - Demand charge: included for Kempower, XOS, and Fixed Charger; ZERO for Diesel
                   (diesel genset is the AC supply — no utility connection charge)
  - O&M:           fixed + variable per technology specs

Kempower demand charge handling:
  The Kempower summary CSV (kempower_summary.csv) does NOT include demand charges
  in its total_cost column. This script adds them from:
    - per-day breakdown CSVs (exact) where available
    - utility_rates.py × peak_kw (estimated) for remaining days
  For SMUD (Northgate), peak_win demand requires peak_win_kw, which is only
  available in per-day breakdown CSVs. For non-breakdown days at Northgate,
  global demand only is estimated; peak_win demand is set to 0 (slight undercount).

XOS demand charges:
  Already present in xos_outputs/{site}_all_days_xos.csv as demand_cost + peak_win_cost.

Fixed charger demand charges:
  Already present in fixed_charger_milp_outputs/{site}_all_days_milp.csv as
  demand_global + demand_peak_win.

Sensitivity tornado:
  Diesel: ±$1/gal fuel price, η 0.30-0.38, varOM $0.005-0.03/kWh
  Electric: ±20% electricity tariff, ±20% utilization (energy demand)
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ── Path setup ─────────────────────────────────────────────────────────────────
REPO_DIR   = Path(__file__).parent
BASE_DIR   = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT_DIR    = REPO_DIR / "results"

sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(BASE_DIR))

from charger_costs_diesel_genset import (
    build_diesel_configs,
    total_daily_cost as diesel_daily_cost,
    lcod_per_kwh as diesel_lcod,
    DIESEL_PRICE_PER_GAL,
    ETA_GENSET,
    ETA_DCFC,
    LHV_KWH_PER_GAL,
    VAR_OM_PER_KWH,
)
from utility_rates import SITE_UTILITY, capacity_charge

DAYS_PER_MONTH = 30.42

# ── Site config ────────────────────────────────────────────────────────────────
# (site_key, site_csv_key for kempower dirs, display name)
SITES = [
    ("northgate",     "northgate",  "Northgate"),
    ("fresno",        "fresno",     "Fresno"),
    ("glendale",      "glendale",   "Glendale (PG&E proxy)"),
    ("san_diego",     "san_diego",  "San Diego"),
    ("glendale_smud", "glendale",   "Glendale (SMUD proxy)"),
]

# ── Data loaders ───────────────────────────────────────────────────────────────

def _smud_daily_demand(peak_kw: float, peak_win_kw: float = 0.0) -> float:
    return (peak_kw * 6.454 + peak_win_kw * 9.960) / DAYS_PER_MONTH


def _pge_daily_demand(peak_kw: float) -> float:
    return (peak_kw * 1.91) / DAYS_PER_MONTH


def _sdge_daily_demand(peak_kw: float) -> float:
    return (peak_kw * 4.81) / DAYS_PER_MONTH


def _demand_from_peak(site_key: str, peak_kw: float, peak_win_kw: float = 0.0) -> float:
    """Estimate daily demand charge from peak_kw using site utility rates."""
    util = SITE_UTILITY.get(site_key, "")
    if util == "smud":
        return _smud_daily_demand(peak_kw, peak_win_kw)
    if util == "pge_bev2":
        return _pge_daily_demand(peak_kw)
    if util == "sdge_evhp":
        return _sdge_daily_demand(peak_kw)
    return 0.0


def load_fixed_charger(site_key: str) -> pd.DataFrame | None:
    """Load fixed-charger MILP all-days CSV. Demand charges already included."""
    p = BASE_DIR / "fixed_charger_milp_outputs" / f"{site_key}_all_days_milp.csv"
    if not p.exists():
        warnings.warn(f"Fixed charger CSV not found: {p}")
        return None
    df = pd.read_csv(p)
    # Compute total with demand already in columns
    df["total_with_demand"] = (df["capex_daily"]
                               + df["energy_cost"]
                               + df["demand_global"].fillna(0)
                               + df["demand_peak_win"].fillna(0))
    df["e_demanded_kwh"]   = df.get("energy_demanded_kwh", df.get("e_demanded_kwh", np.nan))
    df["lcod_per_kwh"]     = df["total_with_demand"] / df["e_demanded_kwh"].replace(0, np.nan)
    df["technology"]       = "Fixed DCFC"
    df["config"]           = "Fixed DCFC (MILP optimal)"
    df["demand_charge"]    = df["demand_global"].fillna(0) + df["demand_peak_win"].fillna(0)
    df["total_daily_cost"] = df["total_with_demand"]
    return df[["date", "technology", "config", "n_vehicles",
               "e_demanded_kwh", "capex_daily", "energy_cost",
               "demand_charge", "total_daily_cost", "lcod_per_kwh"]]


def load_xos(site_key: str) -> pd.DataFrame | None:
    """Load XOS all-days CSV. Demand charges already included as demand_cost + peak_win_cost."""
    p = REPO_DIR / "xos_outputs" / f"{site_key}_all_days_xos.csv"
    if not p.exists():
        warnings.warn(f"XOS CSV not found: {p}")
        return None
    df = pd.read_csv(p)
    df["demand_charge"] = df.get("demand_cost", 0.0) + df.get("peak_win_cost", 0.0)
    df["total_daily_cost"] = df.get("total_daily_cost",
                                    df["capex_cost"] + df["energy_cost"] + df["demand_charge"])
    # XOS summary CSV has no energy column — pull e_demanded_kwh from diesel CSV (same event CSVs)
    e_col = next((c for c in df.columns if "energy" in c.lower() and "kwh" in c.lower()), None)
    if e_col:
        df["e_demanded_kwh"] = df[e_col]
    else:
        diesel_p = REPO_DIR / "diesel_outputs" / f"{site_key}_all_days_diesel.csv"
        if diesel_p.exists():
            d_ref = pd.read_csv(diesel_p, usecols=["date", "e_demanded_kwh"]).drop_duplicates("date")
            df = df.merge(d_ref, on="date", how="left")
        else:
            df["e_demanded_kwh"] = np.nan
    df["lcod_per_kwh"] = df["total_daily_cost"] / df["e_demanded_kwh"].replace(0, np.nan)
    df["technology"]   = "XOS Hub MC02"
    df["config"]       = "XOS Hub MC02"
    n_veh_col = "n_vehicles" if "n_vehicles" in df.columns else None
    df["n_vehicles"] = df[n_veh_col] if n_veh_col else np.nan
    capex_col = "capex_cost" if "capex_cost" in df.columns else "capex_daily"
    df["capex_daily"]  = df[capex_col]
    energy_col = "energy_cost" if "energy_cost" in df.columns else "grid_energy_cost"
    df["energy_cost"]  = df[energy_col]
    return df[["date", "technology", "config", "n_vehicles",
               "e_demanded_kwh", "capex_daily", "energy_cost",
               "demand_charge", "total_daily_cost", "lcod_per_kwh"]]


def _kempower_breakdown_demand(site_key: str, site_csv_key: str,
                                date_str: str) -> dict | None:
    """
    Load exact demand charges from per-day Kempower breakdown CSV if available.
    Returns {'global': $, 'peak_win': $} as monthly values (divide by 30.42 for daily).
    """
    date_key = date_str.replace("-", "_")
    site_dir_key = "sandiego" if site_csv_key == "san_diego" else site_csv_key
    bdir = (BASE_DIR / "site_outputs" / site_key
            / f"kempower_{site_dir_key}_{date_key}" / "exact_milp_cost_breakdown.csv")
    if not bdir.exists():
        # Try glendale_smud paths
        bdir2 = (BASE_DIR / "site_outputs" / site_csv_key
                 / f"kempower_{site_csv_key}_{date_key}" / "exact_milp_cost_breakdown.csv")
        if not bdir2.exists():
            return None
        bdir = bdir2
    try:
        df = pd.read_csv(bdir).set_index("component")["value"]
        return {
            "global":   float(df.get("global_demand_cost", 0)),
            "peak_win": float(df.get("peak_window_demand_cost", 0)),
        }
    except Exception:
        return None


def load_kempower(site_key: str, site_csv_key: str) -> pd.DataFrame | None:
    """
    Load Kempower all-days summary + add demand charges.

    For SMUD (northgate): demand = (peak_kw × 6.454 + peak_win_kw × 9.960) / 30.42
      peak_win_kw from per-day breakdown CSVs where available; else 0 (undercount flagged).
    For PG&E/SDG&E: demand = peak_kw × subscription_rate / 30.42
    """
    paths_tried = [
        BASE_DIR / "scenario_outputs" / f"{site_key}_analysis" / f"{site_key}_kempower_summary.csv",
        BASE_DIR / "scenario_outputs" / f"{site_csv_key}_analysis" / f"{site_csv_key}_kempower_summary.csv",
    ]
    p = next((x for x in paths_tried if x.exists()), None)
    if p is None:
        warnings.warn(f"Kempower summary not found for {site_key} — skipping")
        return None

    df = pd.read_csv(p)

    # Drop rows with missing cost data (e.g. glendale_smud NaN rows)
    df = df.dropna(subset=["capex_daily", "energy_cost"])
    if df.empty:
        warnings.warn(f"Kempower summary for {site_key} has no valid cost rows")
        return None

    demands = []
    exact_flags = []
    for _, row in df.iterrows():
        date_str  = str(row["date"])
        peak_kw   = float(row.get("peak_kw", 0) or 0)

        bd = _kempower_breakdown_demand(site_key, site_csv_key, date_str)
        if bd is not None:
            d_daily = (bd["global"] + bd["peak_win"]) / DAYS_PER_MONTH
            exact_flags.append(True)
        else:
            # Estimate from peak_kw; peak_win_kw unknown for non-breakdown days
            d_daily = _demand_from_peak(site_key, peak_kw, 0.0)
            exact_flags.append(False)
        demands.append(round(d_daily, 4))

    df["demand_charge"]     = demands
    df["demand_exact"]      = exact_flags
    df["total_daily_cost"]  = df["capex_daily"] + df["energy_cost"] + df["demand_charge"]
    e_col = "e_demanded_kwh" if "e_demanded_kwh" in df.columns else None
    df["e_demanded_kwh"]    = df[e_col] if e_col else np.nan
    df["lcod_per_kwh"]      = df["total_daily_cost"] / df["e_demanded_kwh"].replace(0, np.nan)
    df["technology"]        = "Kempower"
    df["config"]            = "Kempower (mix)"
    n_veh_col = "n_vehicles" if "n_vehicles" in df.columns else None
    df["n_vehicles"] = df[n_veh_col] if n_veh_col else np.nan

    n_est = (~df["demand_exact"]).sum()
    if n_est > 0:
        print(f"    [NOTE] {n_est}/{len(df)} Kempower days at {site_key} use estimated demand "
              f"(peak_win=0; slight undercount for SMUD global-only days)")

    return df[["date", "technology", "config", "n_vehicles",
               "e_demanded_kwh", "capex_daily", "energy_cost",
               "demand_charge", "total_daily_cost", "lcod_per_kwh"]]


def load_diesel(site_key: str) -> pd.DataFrame | None:
    """Load diesel all-days CSV generated by run_diesel_pipeline.py."""
    p = REPO_DIR / "diesel_outputs" / f"{site_key}_all_days_diesel.csv"
    if not p.exists():
        warnings.warn(f"Diesel CSV not found: {p} — run run_diesel_pipeline.py first")
        return None
    df = pd.read_csv(p)
    df["technology"]   = "Diesel DCFC"
    df["demand_charge"] = 0.0
    return df[["date", "technology", "config", "n_vehicles",
               "e_demanded_kwh", "capex_daily", "fuel_cost",
               "demand_charge", "total_daily_cost", "lcod_per_kwh"]].rename(
                   columns={"fuel_cost": "energy_cost"})


# ── Summary statistics ─────────────────────────────────────────────────────────

def compute_summary(df: pd.DataFrame) -> dict:
    """Compute p90, mean, and worst-10 stats for a technology×site slice."""
    n = len(df)
    if n == 0:
        return {}
    costs = df["total_daily_cost"].dropna()
    lcods = df["lcod_per_kwh"].dropna()
    lcods_valid = lcods.dropna()
    return {
        "n_days":          n,
        "mean_cost_day":   round(costs.mean(), 2),
        "p90_cost_day":    round(np.percentile(costs, 90), 2),
        "mean_lcod_kwh":   round(lcods_valid.mean(), 5) if len(lcods_valid) else None,
        "p90_lcod_kwh":    round(np.percentile(lcods_valid, 90), 5) if len(lcods_valid) else None,
        "mean_demand_day": round(df["demand_charge"].mean(), 2),
    }


# ── Tornado chart ──────────────────────────────────────────────────────────────

def build_tornado(site_key: str, site_label: str,
                  base_costs: dict, out_dir: Path) -> None:
    """
    Sensitivity tornado chart for one site.
    base_costs: {technology_config_label: base_p90_cost_day}
    """
    diesel_configs = build_diesel_configs()

    fig, axes = plt.subplots(1, len(base_costs), figsize=(5 * len(base_costs), 7),
                              sharey=False)
    if len(base_costs) == 1:
        axes = [axes]

    colors = {"low": "#2166ac", "high": "#d01c8b"}

    for ax, (label, base) in zip(axes, base_costs.items()):
        bars = []

        if "Diesel" in label:
            # Diesel sensitivity parameters
            cfg_name = label.split("(")[1].rstrip(")")
            cfg      = diesel_configs.get(cfg_name)
            if cfg is None:
                ax.set_visible(False)
                continue

            # Approximate p90 energy from base cost (rough — for tornado illustration)
            e_approx = base / 10   # very rough; tornado shows relative swing

            sensitivities = [
                ("Diesel price\n-$1/gal", "low",  lambda: diesel_daily_cost(cfg, 500)["fuel_cost"] * (DIESEL_PRICE_PER_GAL - 1) / DIESEL_PRICE_PER_GAL),
                ("Diesel price\n+$1/gal", "high", lambda: diesel_daily_cost(cfg, 500)["fuel_cost"] * (DIESEL_PRICE_PER_GAL + 1) / DIESEL_PRICE_PER_GAL),
                ("η_genset=0.38\n(high eff)", "low",  None),
                ("η_genset=0.30\n(low eff)",  "high", None),
                ("VarOM=$0.005/kWh", "low",  None),
                ("VarOM=$0.030/kWh", "high", None),
            ]

            swing_labels = [
                "Diesel price ±$1/gal",
                "η_genset 0.30–0.38",
                "VarOM $0.005–0.030/kWh",
            ]
            swings = []
            # Diesel price swing
            gallons_500 = 500 / (ETA_GENSET * ETA_DCFC * LHV_KWH_PER_GAL)
            fuel_swing  = gallons_500 * 1.0   # $1/gal × gallons
            swings.append(fuel_swing)
            # η swing (500 kWh day)
            cost_lo = 500 / (0.38 * ETA_DCFC * LHV_KWH_PER_GAL) * DIESEL_PRICE_PER_GAL
            cost_hi = 500 / (0.30 * ETA_DCFC * LHV_KWH_PER_GAL) * DIESEL_PRICE_PER_GAL
            swings.append(abs(cost_hi - cost_lo) / 2)
            # VarOM swing (500 kWh day)
            swings.append(500 * (0.030 - 0.005) / 2)

        else:
            # Electric technologies — tariff ±20%, utilization ±20%
            swing_labels = ["Tariff ±20%", "Energy demand ±20%"]
            swings       = [base * 0.20, base * 0.20]

        # Sort by swing descending
        order   = sorted(range(len(swings)), key=lambda i: swings[i], reverse=True)
        slabels = [swing_labels[i] for i in order]
        sswings = [swings[i]       for i in order]

        y_pos = range(len(slabels))
        ax.barh(list(y_pos), sswings,  left=[base - s for s in sswings],
                height=0.4, color=colors["low"],  alpha=0.85, label="-")
        ax.barh(list(y_pos), sswings,  left=[base]     * len(sswings),
                height=0.4, color=colors["high"], alpha=0.85, label="+")
        ax.axvline(base, color="black", lw=1.5, ls="--")

        ax.set_yticks(list(y_pos))
        ax.set_yticklabels(slabels, fontsize=9)
        ax.set_xlabel("$/day", fontsize=9)
        ax.set_title(label, fontsize=9, fontweight="bold")
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))

    fig.suptitle(f"Sensitivity Tornado — {site_label}\n(base = p90 $/day)",
                 fontsize=11, fontweight="bold")
    plt.tight_layout()
    out_path = out_dir / f"tornado_{site_key}.png"
    plt.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"    -> Tornado: {out_path.name}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    diesel_configs = build_diesel_configs()

    all_rows   = []   # for lcod_by_site_technology.csv
    summary_rows = []

    print(f"\n{'='*65}")
    print("  TECHNOLOGY COMPARISON BUILD")
    print(f"{'='*65}\n")
    print("  [PLACEHOLDER-D3] Diesel 50/175 kW genset $/kW from Lazard/NREL estimates.")
    print("  [PLACEHOLDER-D9] Update EIA CA diesel price before final submission.\n")

    for site_key, site_csv_key, site_label in SITES:
        print(f"  {site_label.upper()}")

        frames = {}

        # Fixed charger
        df_fx = load_fixed_charger(site_key)
        if df_fx is not None:
            frames["Fixed DCFC"] = df_fx

        # XOS
        df_xos = load_xos(site_key)
        if df_xos is not None:
            frames["XOS Hub MC02"] = df_xos

        # Kempower
        df_kmp = load_kempower(site_key, site_csv_key)
        if df_kmp is not None:
            frames["Kempower"] = df_kmp

        # Diesel (one per config)
        df_diesel = load_diesel(site_key)
        if df_diesel is not None:
            for cfg_name in diesel_configs:
                sub = df_diesel[df_diesel["config"] == cfg_name].copy()
                if not sub.empty:
                    frames[f"Diesel ({cfg_name})"] = sub

        # Compute summary per technology
        base_costs_tornado = {}
        for tech_label, df in frames.items():
            stats = compute_summary(df)
            if not stats:
                continue
            summary_rows.append({
                "site":         site_key,
                "site_label":   site_label,
                "technology":   tech_label,
                **stats,
            })
            all_rows.append(df.assign(site=site_key, site_label=site_label,
                                      technology_label=tech_label))
            base_costs_tornado[tech_label] = stats["p90_cost_day"]
            lcod_str = f"${stats['mean_lcod_kwh']:.4f}/kWh" if stats["mean_lcod_kwh"] is not None else "N/A"
            print(f"    {tech_label:<28}  p90=${stats['p90_cost_day']:>9,.2f}/day  "
                  f"mean=${stats['mean_cost_day']:>9,.2f}/day  "
                  f"LCOD={lcod_str}")

        # Tornado chart
        if base_costs_tornado:
            build_tornado(site_key, site_label, base_costs_tornado, OUT_DIR)
        print()

    # ── Write outputs ──────────────────────────────────────────────────────────

    # 1. Machine-readable summary
    df_summary = pd.DataFrame(summary_rows)
    csv_path   = OUT_DIR / "lcod_by_site_technology.csv"
    df_summary.to_csv(csv_path, index=False)
    print(f"  -> {csv_path.name}")

    # 2. All-days combined
    if all_rows:
        df_all = pd.concat(all_rows, ignore_index=True)
        df_all.to_csv(OUT_DIR / "all_days_all_technologies.csv", index=False)

    # 3. Markdown summary table
    md_lines = [
        "## Technology Cost Comparison — p90 Daily Cost by Site",
        "",
        "| Site | Technology | N days | p90 $/day | Mean $/day | Mean LCOD $/kWh | Mean demand $/day |",
        "|------|-----------|--------|----------|----------|----------------|------------------|",
    ]
    for _, r in df_summary.iterrows():
        md_lines.append(
            f"| {r['site_label']} | {r['technology']} | {r['n_days']} "
            f"| ${r['p90_cost_day']:,.2f} | ${r['mean_cost_day']:,.2f} "
            f"| ${r['mean_lcod_kwh']:.4f} | ${r['mean_demand_day']:,.2f} |"
        )
    md_lines += [
        "",
        "_Notes:_",
        "- Diesel: no utility demand charge (genset provides AC; no grid connection).",
        "- Kempower: demand charges estimated from peak_kw; SMUD peak-window demand",
        "  exact only for days with per-day breakdown CSV (otherwise 0 — slight undercount).",
        "- XOS: demand charges computed from peak grid draw (n_units × 83 kW) × site rate.",
        "- Fixed DCFC: demand charges from MILP optimization output.",
        "- [PLACEHOLDER-D3] Diesel 50/150 kW genset $/kW from Lazard/NREL estimates; pending vendor quotes.",
        "- [PLACEHOLDER-D9] Diesel price $6.94/gal (EIA CA, 2026-06-08); update to current week.",
        "- [PLACEHOLDER-D13] CARB PERP fee = $500/yr placeholder; confirm from CARB fee schedule.",
    ]
    md_path = OUT_DIR / "technology_comparison_summary.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"  -> {md_path.name}")

    # 4. Excel workbook
    xlsx_path = OUT_DIR / "technology_comparison_summary.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        df_summary.to_excel(writer, sheet_name="Summary_p90", index=False)
        for site_key, _, _ in SITES:
            sub = df_summary[df_summary["site"] == site_key]
            if not sub.empty:
                sub.to_excel(writer, sheet_name=site_key[:31], index=False)
    print(f"  -> {xlsx_path.name}")

    print(f"\nAll outputs written to: {OUT_DIR}")


if __name__ == "__main__":
    main()
