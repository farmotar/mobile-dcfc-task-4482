"""
charger_costs_xos_hub.py
-------------------------
Hardware specs and cost parameters for the Xos Hub MC02 mobile DCFC trailer.

Source:
  Unit price:  Caltrans informal quote — $245,437.50 per unit
  Specs:       Xos Hub MC02 User Manual (Section 5, Specifications)
  O&M:         Assumed $6,000/yr (no published data; to be updated once
               Xos representative provides actual service contract pricing)
  Life:        10 years / 3,000 cycles at 70% DoD (from User Manual Section 5)

  Install cost is now modelled as a TIERED INFRASTRUCTURE COST (see
  electrical_infra_cost() below) rather than a flat $5,000/unit assumption.
  The previous $5,000/unit covered only a basic cable hook-up and did not
  account for panel upgrades, switchgear, conduit runs, or breakers.

Xos Hub MC02 — Key Electrical Specs (from manual)
  Battery chemistry       : LFP (Lithium Iron Phosphate)
  Nominal capacity        : 282 kWh
  Usable capacity (≥20%) : 225.6 kWh   (keeps SoC ≥ 20% per operational constraint)
  Charge heads            : 4 × CCS1
  Per-port max output     : 80 kW (constant); parallel: 150 kW for 2 heads
  Hub max output (battery): 150 kW continuous
  Hub max output (grid)   : 230 kW continuous  (with 480V 100A grid input)
  Max grid input power    : ~83 kW  (480V × 100A × √3 ≈ 83 kW at unity PF)
  Grid input voltage      : 480 V 3-phase (L-L)

Operational model:
  - Hub charges its internal battery from the grid during low-demand / off-peak windows
  - Hub discharges to vehicles from battery during peak-demand windows
  - Hub maintains SoC ≥ 20% at all times (56.4 kWh minimum reserve)
  - Hub can ALSO charge vehicles while simultaneously accepting grid power
    (grid supplements battery output up to 230 kW total), but the primary
    use case modelled here is: grid-charge off-peak → discharge to vehicles peak
  - Kempower chargers (same scenario): always grid-connected, no battery dynamics

XOS_HUB_SPECS is a flat dict (not nested like charger_specs) because the Hub
is not a simple "charger" — it is an energy storage system with ports.
The MILP uses these values to build battery constraints.

Building-side electrical infrastructure model (electrical_infra_cost):
  Scope  : Building-side ONLY. Does NOT include utility transformer upgrades,
            service entrance upgrade from the utility, or civil/trenching work.
  Each XOS unit requires: 480V 3-phase, 100A dedicated circuit (~83 kW).
  Costs are broken into:
    1. shared_infra  — one-time: panel/switchboard upgrade, engineering, permits.
                       This cost is incurred once regardless of unit count.
    2. circuit_cost  — per unit: 100A breaker + conduit (~50 ft avg) + #2 AWG
                       wire + 480V outlet + labour. Scales linearly with K.
    3. tier_upgrade  — step-function cost each time K units exhaust the current
                       panel capacity tier (every 4 units = every 400A of demand).
                       e.g. adding unit 5 requires upgrading from 400A to 800A panel.
  Estimates (low / mid / high) reflect uncertainty in site conditions:
    low  — building already has 480V switchgear with spare capacity
    mid  — building has 480V service but panel must be replaced/expanded
    high — building needs full 480V infrastructure (currently 208V or lower)
"""

from __future__ import annotations
import math

# ── Per-unit hardware parameters ───────────────────────────────────────────────

XOS_HUB_SPECS: dict = {
    # ── Cost parameters ───────────────────────────────────────────────────────
    "purchase_cost":    245_437.50,   # $/unit  (Caltrans informal quote)
    # install_cost is now a PLACEHOLDER ONLY. Use electrical_infra_cost(n_units)
    # for the full building-side cost. Old $5,000 only covered a basic cable hook-up
    # and did not include panel upgrades, switchgear, conduit runs, or breakers.
    "install_cost":       5_000.00,   # $/unit  PLACEHOLDER — see electrical_infra_cost()
    "annual_maint":       6_000.00,   # $/unit/yr  confirmed by Farhang
    "annual_warranty":   10_000.00,   # $/unit/yr  provided by Farhang
    "life_years":            10,      # years (User Manual Section 5: 10 yr / 3,000 cycles)

    # ── Electrical parameters ─────────────────────────────────────────────────
    "n_ports":               4,       # CCS1 charge heads per Hub unit
    "power_per_port_kw":    80.0,     # max kW per charge head (constant power)
    "power_hub_battery_kw": 150.0,   # max total output from Hub in battery-only mode (kW)
    "power_hub_grid_kw":    230.0,   # max total output with grid supplement (kW)
    "power_grid_input_kw":   83.0,   # max grid-to-battery charge rate (~480V × 100A × √3)

    # ── Battery parameters ────────────────────────────────────────────────────
    "capacity_kwh":         282.0,    # nominal battery capacity (kWh)
    "soc_min":                0.20,   # minimum SoC (operational constraint — never below 20%)
    "soc_max":                1.00,   # maximum SoC
    "eta_charge":             0.95,   # round-trip charge efficiency (grid → battery)
    "eta_discharge":          0.95,   # discharge efficiency (battery → vehicle)

    # ── Derived convenience values ────────────────────────────────────────────
    # usable_kwh = capacity_kwh × (soc_max - soc_min)
    # = 282 × 0.80 = 225.6 kWh usable per unit
}


def usable_kwh() -> float:
    """Usable energy per Hub unit respecting the 20% SoC floor."""
    return (XOS_HUB_SPECS["capacity_kwh"]
            * (XOS_HUB_SPECS["soc_max"] - XOS_HUB_SPECS["soc_min"]))


def daily_capex(install_cost_override: float | None = None) -> float:
    """
    Daily CapEx for one Xos Hub unit ($/unit/day).

    Formula:
        C = [(purchase + install) / (life*12) + (maint + warranty) / 12] / 30.42

    install_cost_override : pass the amortised per-unit infra cost from
        electrical_infra_cost(n, "mid")["per_unit_avg"] for a fleet-size-aware
        estimate. If None, uses the placeholder $5,000 install_cost.
    """
    DAYS_PER_MONTH = 30.42
    s       = XOS_HUB_SPECS
    install = install_cost_override if install_cost_override is not None else s["install_cost"]
    monthly_capex = (s["purchase_cost"] + install) / (s["life_years"] * 12)
    monthly_recur = (s["annual_maint"] + s.get("annual_warranty", 0)) / 12
    return (monthly_capex + monthly_recur) / DAYS_PER_MONTH


# ── Building-side electrical infrastructure cost model ────────────────────────

# Cost assumptions (building-side ONLY — excludes utility transformer/service upgrade)
#
# Each XOS unit requires: 480V 3-phase, 100A dedicated circuit (~83 kW demand).
#
# low  — building already has 480V switchgear with spare breaker slots.
#         Only add circuits. Minimal shared work.
# mid  — building has 480V service but the panel must be replaced or expanded.
#         Typical for a Caltrans yard that has some 480V but not enough capacity.
# high — building currently runs 208V or lower. Needs full 480V infrastructure:
#         new transformer (building-side), new switchgear, all new circuits.
#
# TIER SIZE: Each XOS unit demands 100A at 480V 3-phase.
#   A standard 400A switchboard handles 4 units.
#   A 800A switchboard handles 8 units. Etc.
#   Each time units exceed a tier boundary, a panel upgrade is required.
#   Tier upgrade cost is a one-time step-function charge, NOT per-unit.

_INFRA_PARAMS = {
    #                        low        mid        high
    "shared_base":         (20_000,   40_000,    80_000),  # one-time: panel + engineering + permit
    "per_unit_circuit":    ( 6_000,    8_500,    12_000),  # breaker + conduit + wire + outlet + labour
    "tier_upgrade":        (12_000,   20_000,    35_000),  # cost each time 4-unit tier is exceeded
    "units_per_tier":       4,                             # 4 units × 100A = 400A per tier
}


def electrical_infra_cost(n_units: int,
                          estimate: str = "mid") -> dict:
    """
    Building-side electrical infrastructure cost for n_units XOS Hub MC02 units.

    DOES NOT include:
      - Utility transformer upgrade
      - Service entrance upgrade from the utility company
      - Civil / trenching work (XOS trailer is self-contained)
      - Permit fees to the utility (only building permit included in shared_base)

    Parameters
    ----------
    n_units : int
        Number of XOS Hub MC02 units to install at the site.
    estimate : "low" | "mid" | "high"
        low  — building already has 480V switchgear with spare capacity.
        mid  — panel must be replaced/expanded (typical Caltrans yard).
        high — full 480V infrastructure needed (building currently ≤208V).

    Returns
    -------
    dict
        shared_infra    : one-time panel + engineering + permit ($)
        circuit_cost    : n_units × per-unit circuit cost ($)
        tier_upgrades   : number of panel capacity tier jumps × tier_upgrade ($)
        total           : sum of all three ($)
        per_unit_avg    : total / n_units — average install cost per unit ($)
    """
    idx = {"low": 0, "mid": 1, "high": 2}[estimate]
    p   = _INFRA_PARAMS

    shared   = p["shared_base"][idx]
    per_unit = p["per_unit_circuit"][idx]
    t_cost   = p["tier_upgrade"][idx]
    t_size   = p["units_per_tier"]

    # Number of tier upgrades needed BEYOND the first tier (which is part of shared_base)
    n_tier_upgrades = max(0, math.ceil(n_units / t_size) - 1)

    circuit_total = n_units * per_unit
    tier_total    = n_tier_upgrades * t_cost
    grand_total   = shared + circuit_total + tier_total

    return {
        "n_units":          n_units,
        "estimate":         estimate,
        "shared_infra":     shared,
        "circuit_cost":     circuit_total,
        "n_tier_upgrades":  n_tier_upgrades,
        "tier_upgrades":    tier_total,
        "total":            grand_total,
        "per_unit_avg":     grand_total / max(n_units, 1),
    }


def electrical_infra_summary(n_units: int) -> None:
    """Print low / mid / high electrical infrastructure cost table for n_units."""
    print(f"\n  Building-side Electrical Infrastructure — {n_units} XOS Hub MC02 units")
    print(f"  Each unit: 480V 3-phase 100A circuit (~83 kW).  Tier size: 4 units / 400A.")
    print(f"  {'Item':<32}  {'Low':>12}  {'Mid':>12}  {'High':>12}")
    print("  " + "-" * 72)

    low  = electrical_infra_cost(n_units, "low")
    mid  = electrical_infra_cost(n_units, "mid")
    high = electrical_infra_cost(n_units, "high")

    rows = [
        ("Panel / switchboard (one-time)", "shared_infra"),
        (f"Circuits × {n_units} units",    "circuit_cost"),
        (f"Tier upgrades × {mid['n_tier_upgrades']}",   "tier_upgrades"),
        ("TOTAL install cost",             "total"),
        ("  -> per unit (amortised)",        "per_unit_avg"),
    ]
    for label, key in rows:
        sep = "-" * 72 if key == "total" else ""
        if sep:
            print("  " + sep)
        fmt = "${:>11,.0f}" if key != "per_unit_avg" else "  ${:>10,.0f}"
        print(f"  {label:<32}  {fmt.format(low[key])}  "
              f"{fmt.format(mid[key])}  {fmt.format(high[key])}")

    print()
    print("  NOTE: Does NOT include utility transformer, service entrance from")
    print("  the utility company, or any civil/trenching work.")
    print("  Confirm with site electrician before finalising report figures.")


# ── Quick summary printout ─────────────────────────────────────────────────────

if __name__ == "__main__":
    s  = XOS_HUB_SPECS
    d  = daily_capex()
    uw = usable_kwh()

    print("\nXos Hub MC02 — Cost and Spec Summary")
    print("=" * 55)
    print(f"  Purchase cost          : ${s['purchase_cost']:>12,.2f}")
    print(f"  Install cost (assumed) : ${s['install_cost']:>12,.2f}")
    print(f"  Annual O&M (assumed)   : ${s['annual_maint']:>12,.2f} /yr")
    print(f"  Service life           :  {s['life_years']} years")
    print(f"  Daily CapEx            : ${d:>12.2f} /unit/day")
    print()
    print(f"  Battery capacity       : {s['capacity_kwh']:.1f} kWh nominal")

    print(f"  Usable energy (>=20%)  : {uw:.1f} kWh per unit")
    print(f"  Charge heads (ports)   : {s['n_ports']} × CCS1")
    print(f"  Max power per port     : {s['power_per_port_kw']:.0f} kW")
    print(f"  Max hub output (batt)  : {s['power_hub_battery_kw']:.0f} kW")
    print(f"  Max hub output (grid)  : {s['power_hub_grid_kw']:.0f} kW")
    print(f"  Max grid input         : {s['power_grid_input_kw']:.0f} kW")
    print()
    print("  Comparison — cost per kW of peak output (battery mode):")
    print(f"    Xos Hub    : ${d / s['power_hub_battery_kw']:.4f}/kW/day  "
          f"({s['power_hub_battery_kw']:.0f} kW battery-only)")
    try:
        from charger_costs_kempower_dgs import build_charger_specs_kempower_dgs
        DAYS = 30.42
        kmp = build_charger_specs_kempower_dgs()
        print("    Kempower (DGS) for reference:")
        for k, c in kmp.items():
            cd = ((c["purchase_cost"]+c["install_cost"])/(c["life_years"]*12)
                  + c["annual_maint"]/12) / DAYS
            print(f"      {k:<20}: ${cd/c['power_kw']:.4f}/kW/day  "
                  f"({c['power_kw']:.0f} kW)")
    except ImportError:
        pass
    print()
    print("  Electrical Infrastructure Cost (building-side only):")
    for k in [1, 2, 4, 6, 8, 12]:
        mid = electrical_infra_cost(k, "mid")
        low = electrical_infra_cost(k, "low")
        high = electrical_infra_cost(k, "high")
        print(f"    {k:2d} units: ${low['total']:>8,.0f} – ${mid['total']:>8,.0f}"
              f" – ${high['total']:>8,.0f}  "
              f"(${mid['per_unit_avg']:,.0f}/unit avg at mid)")
    print()
    print("  Full breakdown for 6 units (likely Northgate sizing):")
    electrical_infra_summary(6)
    print()
    print("NOTE: O&M cost is assumed ($6,000/yr). Update once Xos rep provides")
    print("      actual service contract pricing.")
