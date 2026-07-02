"""
xos_hub_soc_simulation.py
==========================
Time-series SOC simulation for the Xos Hub MC02 mobile DCFC trailer.
Implements the add-one-until-covered unit sizing rule.

Decision (UC Davis meeting, Jun 16 2026):
  No MILP / optimization for XOS.  Use a time-series SOC simulation:
  track battery state of charge per unit each 15-min step, charge from
  grid when idle, discharge to vehicles when dispensing.  Add XOS units
  one at a time until all vehicles are served.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MATHEMATICAL FORMULATION
(included here for the quarterly report formulation section)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Notation
--------
K        : number of XOS Hub MC02 units deployed at the site
V        : set of vehicle charging events  (index v)
T        : discrete time steps of size Δt = 0.25 h  (index t)
B        : XOS battery capacity = 280 kWh  (TAI specification)
SoC_min  : minimum state of charge = 0.20  (operational floor, XOS manual)
SoC_max  : maximum state of charge = 1.00
P_grid   : maximum grid-to-battery charge rate = 83 kW
             (480 V × 100 A × √3, XOS MC02 spec)
P_port   : maximum battery-to-vehicle discharge rate per port = 80 kW
η_c      : grid-to-battery charging efficiency  = 0.95
η_d      : battery-to-vehicle discharge efficiency = 0.95
E_v      : energy required by vehicle event v  [kWh at vehicle battery]
a_v, d_v : arrival and departure timestamps of vehicle v
ε        : served-tolerance = 0.10 kWh
           (vehicle is considered fully served if remaining need ≤ ε)

4-port assumption (updated Jun 17 2026):
  Each XOS unit has 4 CCS1 ports, all of which may be active simultaneously.
  Each active port delivers a constant P_port kW regardless of how many other
  ports are simultaneously in use on the same unit.  Grid charging is suspended
  while any port on the unit is dispensing (no simultaneous charge+discharge).

Single-unit assignment constraint (updated Jun 30 2026):
  Once a vehicle event v is assigned to unit k at its first active timestep,
  it draws all energy exclusively from unit k for its entire dwell window.
  It cannot switch units mid-charge (physically impossible with CCS1).
  If unit k is depleted or all 4 ports on unit k are already busy this step,
  vehicle v waits until the next 15-min step.

State variable
--------------
SoC_k[t] ∈ [SoC_min, SoC_max] : battery state of charge of unit k at step t
  (dimensionless fraction; stored energy = SoC_k[t] × B)

Effective vehicle charging power
----------------------------------
  P_eff_v = min(P_port,  P_dc_max_v)  [kW at vehicle connector]

where P_dc_max_v is the vehicle model's maximum DC charge acceptance rate.
If P_dc_max_v = 0 (AC-only vehicle), the vehicle cannot be served by XOS.

State transition at step t
---------------------------
Case A — unit k is serving vehicle v:
  energy delivered to vehicle  = P_eff_v × Δt × η_d          [kWh]
  energy drawn from battery    = P_eff_v × Δt / η_d           [kWh]
                                 (= P_eff_v × Δt if η_d ≈ 1)

  SoC_k[t+1] = SoC_k[t]  −  P_eff_v × Δt / (η_d × B)
  SoC_k[t+1] = max(SoC_k[t+1], SoC_min)    [hard floor]

Case B — unit k is idle (not serving any vehicle at step t):
  energy stored in battery     = P_grid × η_c × Δt            [kWh]

  SoC_k[t+1] = SoC_k[t]  +  P_grid × η_c × Δt / B
  SoC_k[t+1] = min(SoC_k[t+1], SoC_max)    [hard ceiling]

Note: XOS always recharges itself when idle — daytime recharging is
      included, not only overnight.

Greedy dispatch algorithm at each step t
-----------------------------------------
  1. Identify active vehicles:
       A(t) = { v ∈ V  :  a_v < t + Δt  AND  d_v > t  AND  rem_v > ε  AND  P_eff_v > 0 }
     where rem_v = remaining energy need of vehicle v.

  2. Sort A(t) by urgency (descending):
       urg_v = rem_v / max(d_v − t, Δt)   [kW, like a required charge rate]

  3. Maintain per-unit free-port count:  ports_left[k] = N_ports  ∀k  (resets each step)

  4. For each v ∈ A(t) in urgency order:
       (a) If ports_left[k] = 0 for all k → stop (no free ports this step)
       (b) Pick  k* = argmax_{k : ports_left[k]>0} SoC_k[t]  (highest SOC with free port)
       (c) Compute usable energy of k*:
               usable_k* = (SoC_k*[t] − SoC_min) × B × η_d   [kWh to vehicle]
       (d) If usable_k* < ε → set ports_left[k*] = 0, continue to next unit candidate
       (e) Energy delivered this step:
               e_del = min( P_eff_v × Δt × η_d,  rem_v,  usable_k* )
       (f) Apply transition Case A for unit k*, vehicle v.
           Update: delivered_v += e_del,  rem_v -= e_del,  ports_left[k*] -= 1

  5. Apply transition Case B (grid charging) for all k with no active ports this step.

Coverage criterion
-------------------
  Vehicle v is "served" ⟺  rem_v ≤ ε  when t = d_v.

Add-one-until-covered sizing rule
------------------------------------
  For K = 1, 2, 3, ..., K_max:
    1. Initialise: SoC_k[0] = SoC_max  for all k = 1..K
    2. Run the greedy dispatch simulation for the full day.
    3. If  ∀v ∈ V : rem_v ≤ ε  →  return K  (minimum sufficient unit count)
  If no K ≤ K_max suffices, return K_max and flag partial coverage.

Cost structure per XOS unit (for quarterly report)
----------------------------------------------------
  C_total = purchase + electrical_upgrade + permit + maintenance × life + warranty + EOL
  C_annual = C_total / life_years
  C_daily  = C_annual / 365

  XOS MC02 assumptions (Jun 2026):
    Purchase      : $245,437.50   (Caltrans informal quote)
    Electrical    : $20,000–$30,000  (480 V 3-phase upgrade, no trenching)
    Permit        : $2,000
    Maintenance   : $6,000/yr  (assumed; awaiting vendor quote)
    Warranty      : ~$10,000–$12,000/yr  (FreeWire proxy: $30–35k / 3yr, distributed over 10 yr)
    End-of-life   : TBD (search required)
    Life          : 10 years
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ──────────────────────────────────────────────────────────────────────────────
# PARAMETERS  (TAI spec + XOS MC02 manual)
# ──────────────────────────────────────────────────────────────────────────────

B_KWH      = 280.0    # battery capacity [kWh] — TAI spec (NOT 282 from XOS manual)
SOC_MIN    = 0.20     # minimum SoC operational floor (from XOS manual)
SOC_MAX    = 1.00     # maximum SoC
P_GRID_KW  = 83.0     # max grid-to-battery input power [kW]  (480V × 100A × √3)
P_PORT_KW  = 80.0     # max battery-to-vehicle discharge per port [kW]
ETA_C      = 0.95     # charging efficiency  (grid → battery)
ETA_D      = 0.95     # discharging efficiency (battery → vehicle)
DT_H       = 0.25     # time-step size [h]  (15 min)
ENERGY_TOL = 0.10     # "served" tolerance [kWh]
MAX_UNITS  = 20       # upper bound for add-one-until-covered search
N_PORTS    = 4        # CCS1 ports per XOS Hub MC02 unit (all simultaneously active)

# ──────────────────────────────────────────────────────────────────────────────
# PATHS
# ──────────────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
OUTPUT_DIR = BASE_DIR / "xos_sim_outputs"

# Default events file (same format as the MILP)
DEFAULT_EVENTS_CSV = BASE_DIR / "z2z_milp_events_northgate_2025_06_30.csv"


# ──────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ──────────────────────────────────────────────────────────────────────────────

def load_events(csv_path: Path) -> pd.DataFrame:
    """
    Load and clean a charging-events CSV.

    Expected columns (same format as the fixed-charger MILP):
      charging_event_id, vehicle_id, ev_equivalent_model,
      arrival_time, departure_time,
      energy_needed_kwh_for_visit,
      max_ac_charge_kw, max_dc_charge_kw
    """
    df = pd.read_csv(csv_path)

    for col in ("arrival_time", "departure_time"):
        df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    df = df.dropna(subset=["arrival_time", "departure_time"])
    df = df[df["departure_time"] > df["arrival_time"]].copy()

    if "energy_needed_kwh_for_visit" not in df.columns:
        raise KeyError("Column 'energy_needed_kwh_for_visit' not found.")
    df["energy_needed_kwh_for_visit"] = pd.to_numeric(
        df["energy_needed_kwh_for_visit"], errors="coerce"
    ).fillna(0.0)
    df = df[df["energy_needed_kwh_for_visit"] > 0].copy()

    for col in ("max_ac_charge_kw", "max_dc_charge_kw"):
        if col not in df.columns:
            warnings.warn(f"Column '{col}' not found; filling with 0.")
            df[col] = 0.0
        else:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    if "charging_event_id" not in df.columns:
        df["charging_event_id"] = [f"event_{i+1:04d}" for i in range(len(df))]

    df = df.sort_values("arrival_time").reset_index(drop=True)
    print(f"  Loaded {len(df)} events from {csv_path.name}")
    return df


# ──────────────────────────────────────────────────────────────────────────────
# EFFECTIVE POWER
# ──────────────────────────────────────────────────────────────────────────────

def compute_p_eff(events_df: pd.DataFrame) -> Dict[str, float]:
    """
    Effective XOS discharge power for each vehicle event [kW at vehicle connector].

    P_eff_v = min(P_port, max_dc_charge_kw_v)
    If max_dc_charge_kw_v = 0 the vehicle cannot be charged by XOS → P_eff = 0.
    """
    p: Dict[str, float] = {}
    for _, row in events_df.iterrows():
        v   = row["charging_event_id"]
        mdc = float(row.get("max_dc_charge_kw", 0) or 0)
        p[v] = min(P_PORT_KW, mdc) if mdc > 0 else 0.0
    return p


# ──────────────────────────────────────────────────────────────────────────────
# SINGLE-DAY SIMULATION
# ──────────────────────────────────────────────────────────────────────────────

def simulate_one_day(
    events_df: pd.DataFrame,
    n_units:   int,
    p_eff:     Dict[str, float],
    verbose:   bool = False,
) -> dict:
    """
    Greedy SOC simulation for one day with n_units XOS Hub MC02 units.

    Parameters
    ----------
    events_df : cleaned charging-events DataFrame
    n_units   : number of XOS units to deploy
    p_eff     : effective discharge power per vehicle [kW]  (from compute_p_eff)
    verbose   : print per-step details

    Returns
    -------
    result dict with keys:
      all_served          bool
      n_served            int
      n_total             int
      n_xos_incompatible  int  (vehicles with P_eff = 0)
      delivered           dict[event_id -> kWh]
      remaining           dict[event_id -> kWh]
      total_energy_delivered_kwh  float
      total_energy_required_kwh   float
      peak_dispatch_kw    float  (max power delivered to vehicles simultaneously)
      peak_grid_kw        float  (max grid draw for battery charging)
      soc_history         list[dict]  one row per time step
      dispatch_log        list[dict]  one row per vehicle-unit assignment
    """
    # ── Time grid ───────────────────────────────────────────────────────────
    t_start = events_df["arrival_time"].min().floor("15min")
    t_end   = events_df["departure_time"].max().ceil("15min")
    time_steps = pd.date_range(t_start, t_end, freq="15min", tz="UTC")

    # ── Initialise unit states ──────────────────────────────────────────────
    soc = [SOC_MAX] * n_units   # start fully charged

    # ── Initialise vehicle state ────────────────────────────────────────────
    ev_ids = events_df["charging_event_id"].tolist()

    remaining: Dict[str, float] = {}
    delivered: Dict[str, float] = {}
    ev_info:   Dict[str, dict]  = {}

    for _, row in events_df.iterrows():
        v = row["charging_event_id"]
        remaining[v] = float(row["energy_needed_kwh_for_visit"])
        delivered[v] = 0.0
        ev_info[v]   = {
            "arr": row["arrival_time"],
            "dep": row["departure_time"],
        }

    n_incompatible = sum(1 for v in ev_ids if p_eff.get(v, 0) <= 0)

    # ── Logs ────────────────────────────────────────────────────────────────
    soc_history:  List[dict] = []
    dispatch_log: List[dict] = []

    peak_dispatch_kw = 0.0
    peak_grid_kw     = 0.0

    # ── Single-unit assignment: locked on first active step, never changes ──
    ev_unit_assignment: Dict[str, int] = {}

    # ── Step loop ───────────────────────────────────────────────────────────
    for ti, t in enumerate(time_steps):
        t_next = t + pd.Timedelta(hours=DT_H)

        # 1. Active vehicles at this step
        active: List[Tuple[float, str]] = []
        for v in ev_ids:
            if (remaining[v] > ENERGY_TOL
                    and p_eff.get(v, 0) > 0
                    and ev_info[v]["arr"] < t_next
                    and ev_info[v]["dep"] > t):
                time_left_h = max(
                    (ev_info[v]["dep"] - t).total_seconds() / 3600, DT_H
                )
                urgency = remaining[v] / time_left_h
                active.append((urgency, v))

        active.sort(reverse=True)   # most urgent first

        # 2. Dispatch — 4-port model: each unit serves up to N_PORTS vehicles/step
        #    Each active port delivers constant P_PORT_KW regardless of how many
        #    other ports are simultaneously active on the same unit.
        ports_left: List[int] = [N_PORTS] * n_units   # free ports per unit this step
        serving_multi: Dict[int, List[str]] = {k: [] for k in range(n_units)}

        step_dispatch_kw = 0.0

        for _, v in active:
            # ── Determine which unit serves this vehicle ─────────────────────
            if v in ev_unit_assignment:
                # Already assigned — must stay on same unit (CCS1 physical constraint)
                k = ev_unit_assignment[v]
                if ports_left[k] == 0:
                    # All 4 ports on assigned unit are busy this step; vehicle waits
                    continue
            else:
                # First active step for this vehicle: pick best available unit
                candidates = [i for i in range(n_units) if ports_left[i] > 0]
                if not candidates:
                    break
                # Assign to unit with highest SoC (most energy available)
                k = max(candidates, key=lambda i: soc[i])
                ev_unit_assignment[v] = k

            usable_to_vehicle = (soc[k] - SOC_MIN) * B_KWH * ETA_D   # kWh at vehicle

            if usable_to_vehicle < ENERGY_TOL:
                # Assigned unit is depleted; vehicle waits this step
                continue

            pv = p_eff[v]
            # Prorate for partial time steps: vehicle may arrive or depart mid-step.
            # overlap_h is the fraction of [t, t_next] when the vehicle is actually present.
            step_eff_h = (
                min(t_next, ev_info[v]["dep"]) - max(t, ev_info[v]["arr"])
            ).total_seconds() / 3600.0
            e_del = min(
                pv * step_eff_h * ETA_D,    # power-limited, prorated for overlap
                remaining[v],               # energy-need limited
                usable_to_vehicle,          # battery-floor limited
            )

            if e_del < ENERGY_TOL:
                ports_left[k] = 0
                continue

            soc_before = soc[k]

            # Battery decreases by (energy at vehicle) / η_d
            soc[k] -= e_del / (ETA_D * B_KWH)
            soc[k]  = max(soc[k], SOC_MIN)

            delivered[v]  += e_del
            remaining[v]   = max(remaining[v] - e_del, 0.0)

            step_dispatch_kw += pv
            ports_left[k]    -= 1
            serving_multi[k].append(v)

            dispatch_log.append({
                "step_idx":               ti,
                "time_utc":               t.isoformat(),
                "unit":                   k,
                "event_id":               v,
                "soc_before":             round(soc_before, 4),
                "soc_after":              round(soc[k], 4),
                "power_kw":               round(pv, 2),
                "energy_to_vehicle_kwh":  round(e_del, 4),
                "remaining_kwh":          round(remaining[v], 4),
            })

        peak_dispatch_kw = max(peak_dispatch_kw, step_dispatch_kw)

        # 3. Charge units with no active ports from grid
        step_grid_kw = 0.0
        for k in range(n_units):
            if not serving_multi[k]:   # all ports idle → recharge from grid
                room_kwh   = (SOC_MAX - soc[k]) * B_KWH
                charged    = min(P_GRID_KW * ETA_C * DT_H, room_kwh)
                soc[k]    += charged / B_KWH
                soc[k]     = min(soc[k], SOC_MAX)
                if charged > 1e-6:
                    step_grid_kw += charged / (ETA_C * DT_H)   # kW from grid

        peak_grid_kw = max(peak_grid_kw, step_grid_kw)

        # 4. Record SOC history (include grid draw so pipeline can compute energy cost)
        row_soc: dict = {"step_idx": ti, "time_utc": t.isoformat(),
                         "grid_kw": round(step_grid_kw, 2)}
        for k in range(n_units):
            row_soc[f"soc_unit_{k}"] = round(soc[k], 4)
        soc_history.append(row_soc)

        if verbose:
            serv_str = "  ".join(
                f"U{k}->[{','.join(serving_multi[k])}]"
                for k in range(n_units) if serving_multi[k]
            )
            print(
                f"  t={t.strftime('%H:%M')} "
                f"dispatch={step_dispatch_kw:.0f}kW "
                f"grid={step_grid_kw:.0f}kW "
                f"serving=[{serv_str or '—'}]"
            )

    # ── Results ─────────────────────────────────────────────────────────────
    all_served = all(remaining[v] <= ENERGY_TOL for v in ev_ids)
    n_served   = sum(1 for v in ev_ids if remaining[v] <= ENERGY_TOL)

    return {
        "all_served":                  all_served,
        "n_served":                    n_served,
        "n_total":                     len(ev_ids),
        "n_xos_incompatible":          n_incompatible,
        "delivered":                   delivered,
        "remaining":                   remaining,
        "total_energy_delivered_kwh":  round(sum(delivered.values()), 3),
        "total_energy_required_kwh":   round(
            sum(float(r) for r in events_df["energy_needed_kwh_for_visit"]), 3
        ),
        "peak_dispatch_kw":            round(peak_dispatch_kw, 1),
        "peak_grid_kw":                round(peak_grid_kw, 1),
        "soc_history":                 soc_history,
        "dispatch_log":                dispatch_log,
    }


# ──────────────────────────────────────────────────────────────────────────────
# ADD-ONE-UNTIL-COVERED SIZING
# ──────────────────────────────────────────────────────────────────────────────

def find_min_xos_units(
    events_df: pd.DataFrame,
    p_eff:     Dict[str, float],
    max_units: int = MAX_UNITS,
    verbose:   bool = False,
) -> Tuple[int, dict]:
    """
    Run the add-one-until-covered sizing rule.

    Returns (min_units_needed, result_dict_for_that_count).
    If no count ≤ max_units fully covers all vehicles,
    returns (max_units, partial_result) and prints a warning.
    """
    print(f"\n  Running add-one-until-covered (max_units={max_units}) ...")

    for k in range(1, max_units + 1):
        result = simulate_one_day(events_df, k, p_eff, verbose=verbose)
        pct = result["n_served"] / max(result["n_total"], 1) * 100
        print(
            f"    K={k:2d}  served={result['n_served']}/{result['n_total']} "
            f"({pct:.0f}%)  "
            f"energy_delivered={result['total_energy_delivered_kwh']:.1f}/"
            f"{result['total_energy_required_kwh']:.1f} kWh"
        )
        if result["all_served"]:
            print(f"  -> Minimum sufficient units: {k}")
            return k, result

    print(f"  [WARNING] {max_units} units do not serve all vehicles — returning partial result.")
    final = simulate_one_day(events_df, max_units, p_eff, verbose=verbose)
    return max_units, final


# ──────────────────────────────────────────────────────────────────────────────
# COST SUMMARY
# ──────────────────────────────────────────────────────────────────────────────

def compute_unit_cost_summary(n_units: int) -> dict:
    """
    Compute a per-unit and fleet cost summary for the XOS scenario.

    All figures in USD.  Ranges are expressed as (low, high) tuples.
    End-of-life cost is marked TBD pending research.
    """
    PURCHASE           = 245_437.50
    ELECTRICAL_LOW     = 20_000
    ELECTRICAL_HIGH    = 30_000
    PERMIT             = 2_000
    ANNUAL_MAINT       = 6_000        # assumed; update from Xos rep
    LIFE_YEARS         = 10
    # Warranty proxy: FreeWire ~$30–35k / 3yr → ~$10k–$11.7k/yr; distribute over 10yr
    WARRANTY_TOTAL_LOW  = 30_000
    WARRANTY_TOTAL_HIGH = 35_000
    EOL_COST           = None         # TBD — research required

    total_low  = (PURCHASE + ELECTRICAL_LOW  + PERMIT
                  + ANNUAL_MAINT * LIFE_YEARS
                  + WARRANTY_TOTAL_LOW)
    total_high = (PURCHASE + ELECTRICAL_HIGH + PERMIT
                  + ANNUAL_MAINT * LIFE_YEARS
                  + WARRANTY_TOTAL_HIGH)

    annual_low  = total_low  / LIFE_YEARS
    annual_high = total_high / LIFE_YEARS
    daily_low   = annual_low  / 365
    daily_high  = annual_high / 365

    return {
        "n_units":              n_units,
        "purchase_per_unit":    PURCHASE,
        "electrical_low":       ELECTRICAL_LOW,
        "electrical_high":      ELECTRICAL_HIGH,
        "permit_per_unit":      PERMIT,
        "annual_maint":         ANNUAL_MAINT,
        "life_years":           LIFE_YEARS,
        "warranty_total_low":   WARRANTY_TOTAL_LOW,
        "warranty_total_high":  WARRANTY_TOTAL_HIGH,
        "eol_cost":             EOL_COST,
        "lifecycle_per_unit_low":  round(total_low,  2),
        "lifecycle_per_unit_high": round(total_high, 2),
        "annual_per_unit_low":     round(annual_low,  2),
        "annual_per_unit_high":    round(annual_high, 2),
        "daily_per_unit_low":      round(daily_low,   4),
        "daily_per_unit_high":     round(daily_high,  4),
        "fleet_lifecycle_low":     round(total_low  * n_units, 2),
        "fleet_lifecycle_high":    round(total_high * n_units, 2),
        "fleet_annual_low":        round(annual_low  * n_units, 2),
        "fleet_annual_high":       round(annual_high * n_units, 2),
    }


# ──────────────────────────────────────────────────────────────────────────────
# OUTPUT / EXPORT
# ──────────────────────────────────────────────────────────────────────────────

def export_results(
    events_df:   pd.DataFrame,
    n_units:     int,
    result:      dict,
    cost_summary: dict,
    output_dir:  Path,
    label:       str = "",
) -> None:
    """
    Write CSV outputs and a summary text file to output_dir.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    tag = f"_{label}" if label else ""

    # 1. Per-vehicle results (with serviceability diagnostics)
    ev_rows = []
    for _, row in events_df.iterrows():
        v    = row["charging_event_id"]
        req  = float(row["energy_needed_kwh_for_visit"])
        dlv  = result["delivered"].get(v, 0.0)
        rem  = result["remaining"].get(v, 0.0)
        served = rem <= ENERGY_TOL

        # Serviceability diagnostics
        arr = row["arrival_time"]
        dep = row["departure_time"]
        dwell_h = (dep - arr).total_seconds() / 3600.0
        max_del = dwell_h * P_PORT_KW * ETA_D          # kWh deliverable in full dwell @ 80 kW
        phys_ok = req <= max_del + ENERGY_TOL           # physically serviceable within time window

        if served:
            reason = "served"
        elif not phys_ok:
            reason = "time_window_infeasible"           # needs more than dwell * 80 kW * 0.95
        else:
            reason = "scheduler_or_energy_limited"      # physically ok but not fully served

        ev_rows.append({
            "charging_event_id":            v,
            "vehicle_id":                   row.get("vehicle_id", ""),
            "ev_equivalent_model":          row.get("ev_equivalent_model", ""),
            "arrival_time":                 arr,
            "departure_time":               dep,
            "dwell_hours":                  round(dwell_h, 4),
            "energy_needed_kwh":            round(req, 3),
            "max_deliverable_kwh_at_80kw":  round(max_del, 3),
            "physically_serviceable":       phys_ok,
            "energy_delivered_kwh":         round(dlv, 3),
            "energy_remaining_kwh":         round(rem, 3),
            "served_by_simulation":         served,
            "reason_unserved":              reason,
            "xos_compatible":               result["n_xos_incompatible"] == 0 or
                                            row.get("max_dc_charge_kw", 0) > 0,
        })
    ev_df = pd.DataFrame(ev_rows)
    ev_df.to_csv(output_dir / f"xos_event_results{tag}.csv", index=False)
    print(f"  Saved: xos_event_results{tag}.csv")

    # 2. SOC time-series
    soc_df = pd.DataFrame(result["soc_history"])
    soc_df.to_csv(output_dir / f"xos_soc_timeseries{tag}.csv", index=False)
    print(f"  Saved: xos_soc_timeseries{tag}.csv")

    # 3. Dispatch log
    if result["dispatch_log"]:
        disp_df = pd.DataFrame(result["dispatch_log"])
        disp_df.to_csv(output_dir / f"xos_dispatch_log{tag}.csv", index=False)
        print(f"  Saved: xos_dispatch_log{tag}.csv")

    # 4. Summary text
    cs = cost_summary
    eol_str = f"${cs['eol_cost']:,}" if cs["eol_cost"] else "TBD (research needed)"
    lines = [
        "=" * 70,
        "XOS HUB MC02 — SIZING SIMULATION RESULTS",
        "=" * 70,
        f"Date/Label               : {label or 'N/A'}",
        f"Events file              : {events_df.attrs.get('source', 'N/A')}",
        f"Simulation time          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "--- XOS Parameters ---",
        f"  Battery capacity (TAI) : {B_KWH:.0f} kWh",
        f"  SoC operating range    : {SOC_MIN*100:.0f}% – {SOC_MAX*100:.0f}%",
        f"  Usable energy / unit   : {(SOC_MAX - SOC_MIN) * B_KWH:.0f} kWh",
        f"  Grid charge rate       : {P_GRID_KW:.0f} kW  (480 V, 100 A, 3-phase)",
        f"  Port discharge rate    : {P_PORT_KW:.0f} kW/port  ({N_PORTS} ports per unit, simultaneous)",
        f"  η_charge / η_discharge : {ETA_C} / {ETA_D}",
        f"  Time step              : {DT_H*60:.0f} min",
        "",
        "--- Sizing Result ---",
        f"  Minimum XOS units      : {n_units}",
        f"  Vehicles served        : {result['n_served']} / {result['n_total']}",
        f"  XOS-incompatible veh.  : {result['n_xos_incompatible']}  (AC-only; excluded from sizing)",
        f"  All vehicles served    : {result['all_served']}",
        "",
        "--- Energy ---",
        f"  Total required         : {result['total_energy_required_kwh']:.2f} kWh",
        f"  Total delivered        : {result['total_energy_delivered_kwh']:.2f} kWh",
        f"  Total unmet            : {result['total_energy_required_kwh'] - result['total_energy_delivered_kwh']:.2f} kWh",
        "",
        "--- Power ---",
        f"  Peak dispatch (→ veh.) : {result['peak_dispatch_kw']:.1f} kW",
        f"  Peak grid draw (charging XOS batteries) : {result['peak_grid_kw']:.1f} kW",
        f"  Max grid if all {n_units} units charging : {n_units * P_GRID_KW:.0f} kW",
        "",
        "--- Cost Summary (per unit) ---",
        f"  Purchase               : ${cs['purchase_per_unit']:>12,.2f}",
        f"  Electrical upgrade     : ${cs['electrical_low']:>12,} – ${cs['electrical_high']:,}",
        f"  Permit                 : ${cs['permit_per_unit']:>12,}",
        f"  Maintenance (10 yr)    : ${cs['annual_maint'] * cs['life_years']:>12,}",
        f"  Warranty               : ${cs['warranty_total_low']:>12,} – ${cs['warranty_total_high']:,}",
        f"  End-of-life            : {eol_str}",
        f"  Lifecycle (per unit)   : ${cs['lifecycle_per_unit_low']:>12,.0f} – ${cs['lifecycle_per_unit_high']:,.0f}",
        f"  Annual (per unit)      : ${cs['annual_per_unit_low']:>12,.0f} – ${cs['annual_per_unit_high']:,.0f}",
        f"  Daily CapEx (per unit) : ${cs['daily_per_unit_low']:>12.2f} – ${cs['daily_per_unit_high']:.2f}",
        "",
        f"--- Fleet Cost  ({n_units} units) ---",
        f"  Lifecycle total        : ${cs['fleet_lifecycle_low']:>12,.0f} – ${cs['fleet_lifecycle_high']:,.0f}",
        f"  Annual total           : ${cs['fleet_annual_low']:>12,.0f} – ${cs['fleet_annual_high']:,.0f}",
        "",
        "NOTE: End-of-life cost is TBD — search for battery recycling / decommissioning",
        "      cost and add to lifecycle totals.",
        "NOTE: Warranty proxy from FreeWire ($30–35k / 3yr); update once Xos rep",
        "      provides actual service contract pricing.",
        "=" * 70,
    ]
    summary_path = output_dir / f"xos_sim_summary{tag}.txt"
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Saved: {summary_path.name}")
    print("\n" + "\n".join(lines))


# ──────────────────────────────────────────────────────────────────────────────
# MULTI-DAY RUNNER
# ──────────────────────────────────────────────────────────────────────────────

def run_all_days(
    events_csv_list: List[Path],
    output_dir:      Path,
    max_units:       int = MAX_UNITS,
) -> pd.DataFrame:
    """
    Run the XOS sizing simulation for a list of event CSV files (one per day).

    Returns a summary DataFrame with one row per day.
    Useful for finding the unit count that covers ~90–95% of days.
    """
    rows = []
    for csv_path in sorted(events_csv_list):
        date_tag = csv_path.stem.replace("z2z_milp_events_northgate_", "").replace("_", "-")
        print(f"\n{'='*60}")
        print(f"  Date: {date_tag}  |  {csv_path.name}")
        print(f"{'='*60}")

        try:
            df = load_events(csv_path)
        except Exception as exc:
            print(f"  [SKIP] Cannot load: {exc}")
            rows.append({"date": date_tag, "status": "load_error", "min_units": None})
            continue

        if len(df) == 0:
            print(f"  [SKIP] No events.")
            rows.append({"date": date_tag, "status": "no_events", "min_units": 0})
            continue

        peff = compute_p_eff(df)
        n_incompat = sum(1 for v in peff if peff[v] <= 0)
        n_compat   = len(df) - n_incompat

        if n_compat == 0:
            print(f"  [SKIP] All {n_incompat} vehicles are AC-only (incompatible with XOS).")
            rows.append({"date": date_tag, "status": "all_incompatible", "min_units": 0})
            continue

        k, result = find_min_xos_units(df, peff, max_units=max_units)

        day_output = output_dir / f"xos_sim_{date_tag.replace('-', '_')}"
        export_results(df, k, result,
                       compute_unit_cost_summary(k),
                       day_output, label=date_tag)

        rows.append({
            "date":                      date_tag,
            "status":                    "ok" if result["all_served"] else "partial",
            "min_units":                 k,
            "all_served":                result["all_served"],
            "n_served":                  result["n_served"],
            "n_total":                   result["n_total"],
            "n_xos_incompatible":        result["n_xos_incompatible"],
            "total_energy_required_kwh": result["total_energy_required_kwh"],
            "total_energy_delivered_kwh": result["total_energy_delivered_kwh"],
            "peak_dispatch_kw":          result["peak_dispatch_kw"],
            "peak_grid_kw":              result["peak_grid_kw"],
        })

    summary_df = pd.DataFrame(rows)
    out_csv = output_dir / "xos_multi_day_summary.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(out_csv, index=False)
    print(f"\n[DONE] Multi-day summary saved -> {out_csv}")

    if "min_units" in summary_df.columns:
        ok_days = summary_df[summary_df["status"] == "ok"]
        if len(ok_days) > 0:
            for k_val in sorted(ok_days["min_units"].dropna().unique()):
                pct = (ok_days["min_units"] <= k_val).mean() * 100
                print(f"  {k_val} XOS unit(s) covers {pct:.1f}% of ok days")

    return summary_df


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Entry point.
    Usage:
      python xos_hub_soc_simulation.py                   # runs default events CSV (single day)
      python xos_hub_soc_simulation.py <events.csv>      # runs specified CSV
      python xos_hub_soc_simulation.py --all-days        # runs all northgate z2z CSVs
    """
    print("=" * 70)
    print("  XOS HUB MC02 — SOC SIMULATION & SIZING")
    print("=" * 70)
    print(f"  Battery capacity (TAI spec)  : {B_KWH:.0f} kWh")
    print(f"  Usable energy per unit       : {(SOC_MAX - SOC_MIN) * B_KWH:.0f} kWh  "
          f"(SoC {SOC_MIN*100:.0f}%–{SOC_MAX*100:.0f}%)")
    print(f"  Grid charge rate             : {P_GRID_KW:.0f} kW  (480V, 3-phase)")
    print(f"  Port discharge rate          : {P_PORT_KW:.0f} kW  (1 port active per unit)")
    print()

    # ── Mode: all-days sweep ────────────────────────────────────────────────
    if "--all-days" in sys.argv:
        all_csvs = sorted(BASE_DIR.glob("z2z_milp_events_northgate_*.csv"))
        if not all_csvs:
            print(f"[ERROR] No z2z_milp_events_northgate_*.csv files found in {BASE_DIR}")
            sys.exit(1)
        print(f"Found {len(all_csvs)} event CSV files — running all-day sweep.")
        run_all_days(all_csvs, OUTPUT_DIR / "all_days")
        return

    # ── Mode: single day ────────────────────────────────────────────────────
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
        csv_path = Path(sys.argv[1])
    else:
        csv_path = DEFAULT_EVENTS_CSV

    if not csv_path.exists():
        print(f"[ERROR] Events CSV not found: {csv_path}")
        sys.exit(1)

    events_df = load_events(csv_path)
    events_df.attrs["source"] = csv_path.name

    date_label = csv_path.stem.replace("z2z_milp_events_northgate_", "").replace("_", "-")

    print(f"\n  Events: {len(events_df)}")
    print(f"  Energy range: "
          f"{events_df['energy_needed_kwh_for_visit'].min():.1f} – "
          f"{events_df['energy_needed_kwh_for_visit'].max():.1f} kWh")
    print(f"  Dwell range: "
          f"{((events_df['departure_time']-events_df['arrival_time']).dt.total_seconds()/3600).min():.2f} – "
          f"{((events_df['departure_time']-events_df['arrival_time']).dt.total_seconds()/3600).max():.2f} h")

    peff = compute_p_eff(events_df)
    n_incompat = sum(1 for v in peff if peff[v] <= 0)
    print(f"  XOS-incompatible (AC-only)   : {n_incompat}")
    print(f"  XOS-compatible               : {len(events_df) - n_incompat}")

    # Sizing
    min_units, result = find_min_xos_units(events_df, peff)

    # Cost
    cost = compute_unit_cost_summary(min_units)

    # Export
    out_dir = OUTPUT_DIR / date_label.replace("-", "_") if date_label else OUTPUT_DIR
    export_results(events_df, min_units, result, cost, out_dir, label=date_label)


if __name__ == "__main__":
    main()
