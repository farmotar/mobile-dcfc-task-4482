"""
run_xos_only.py
---------------
Run XOS Hub MC02 simulation for all top-5 days of Northgate, Fresno, Glendale,
and San Diego.  Skip days that already have a completed xos_sim_summary_*.txt.
"""
from __future__ import annotations
import importlib
import sys
from pathlib import Path
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUT_DIR  = BASE_DIR / "site_outputs"

SITE_TOP5 = {
    "northgate": [
        BASE_DIR / "z2z_milp_events_northgate_2025_06_09.csv",
        BASE_DIR / "z2z_milp_events_northgate_2025_07_17.csv",
        BASE_DIR / "z2z_milp_events_northgate_2026_01_29.csv",
        BASE_DIR / "z2z_milp_events_northgate_2026_03_16.csv",
        BASE_DIR / "z2z_milp_events_northgate_2026_04_01.csv",
    ],
    "fresno": sorted((OUT_DIR / "fresno").glob("z2z_milp_events_fresno_*.csv")),
    "glendale": sorted((OUT_DIR / "glendale").glob("z2z_milp_events_glendale_*.csv")),
    "san_diego": sorted((OUT_DIR / "san_diego").glob("z2z_milp_events_sandiego_*.csv")),
}

import sys; sys.path.insert(0, str(BASE_DIR))
xos = importlib.import_module("xos_hub_soc_simulation")

results = []

for site_name, csvs in SITE_TOP5.items():
    site_out = OUT_DIR / site_name
    print(f"\n{'='*60}")
    print(f"  XOS SIMULATION — {site_name.upper()}")
    print(f"{'='*60}")

    for csv_path in csvs:
        if not csv_path.exists():
            print(f"  [SKIP] {csv_path.name} — file not found")
            continue

        date_tag = csv_path.stem.split("_events_")[-1]
        xos_out  = site_out / f"xos_{date_tag}"
        summary  = xos_out / f"xos_sim_summary_{date_tag}.txt"

        if summary.exists():
            print(f"  [SKIP] {date_tag} — already done")
            continue

        xos_out.mkdir(parents=True, exist_ok=True)
        print(f"\n  [{datetime.now().strftime('%H:%M:%S')}] {date_tag} ...")
        try:
            events_df    = xos.load_events(csv_path)
            p_eff        = xos.compute_p_eff(events_df)
            n_units, res = xos.find_min_xos_units(events_df, p_eff)
            cost         = xos.compute_unit_cost_summary(n_units)
            xos.export_results(events_df, n_units, res, cost, xos_out, label=date_tag)
            pct = 100 * res["n_served"] / max(res["n_total"], 1)
            results.append({
                "site": site_name, "date": date_tag,
                "n_units": n_units, "pct_served": round(pct, 1),
                "lc_low_k":  round(cost["fleet_lifecycle_low"]  / 1000, 1),
                "lc_high_k": round(cost["fleet_lifecycle_high"] / 1000, 1),
            })
            print(f"    -> {n_units} XOS units | {pct:.0f}% served | "
                  f"LC ${cost['fleet_lifecycle_low']:,.0f} - ${cost['fleet_lifecycle_high']:,.0f}")
        except Exception as exc:
            print(f"    [ERROR] {exc}")

print("\n" + "="*60)
print("  XOS SUMMARY")
print("="*60)
for r in results:
    print(f"  {r['site']:<12} {r['date']}  {r['n_units']} units  "
          f"{r['pct_served']}% served  LC ${r['lc_low_k']}k-${r['lc_high_k']}k")
print(f"\nDone: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
