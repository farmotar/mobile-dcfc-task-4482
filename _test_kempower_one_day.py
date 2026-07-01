"""Quick test: run Kempower MILP for one Northgate day."""
import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")
from pathlib import Path
from run_kempower_pipeline import _run_kempower_day, _kempower_fig, _load_events, SITES

BASE_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
site_label = "Northgate"
csv_stem   = "z2z_milp_events_northgate"
date_str   = "2025-07-17"
date_tag   = date_str.replace("-","_")
csv_path   = BASE_DIR / f"{csv_stem}_{date_tag}.csv"
kmp_dir    = BASE_DIR / "scenario_outputs" / "northgate_analysis" / "per_day" / date_str / "kempower"
fig_out    = BASE_DIR / "scenario_outputs" / "northgate_analysis" / "per_day" / date_str / f"kempower_day_view_{date_str}.png"

print(f"CSV exists: {csv_path.exists()}")
print(f"Running MILP for {date_str}...")
result = _run_kempower_day(csv_path, kmp_dir, date_str, site_label)
if result is None:
    print("RESULT IS NONE"); sys.exit(1)
if "error" in result:
    print(f"ERROR: {result['error']}"); sys.exit(1)

print(f"  Chargers: {result['n_chargers']} ({result['mix']})")
print(f"  Vehicles: {result['n_vehicles']}  Full: {result['n_full']}  Partial: {result['n_partial']}")
print(f"  Peak kW:  {result['peak_kw']}")
print(f"  Total cost: ${result['total_cost']:.2f}")

print("Generating figure...")
ev_ext = _load_events(csv_path, date_str, csv_stem)
if ev_ext is None or ev_ext.empty:
    print("No events for figure"); sys.exit(1)
_kempower_fig(date_str, result["_res"], ev_ext, site_label, fig_out)
print(f"  Figure saved: {fig_out}")
print("Test PASSED.")
