"""
charger_costs_diesel_genset.py
-------------------------------
Cost parameters for the hypothetical diesel mobile DCFC system:
    [Tier 4 Final towable diesel genset] --AC--> [DCFC power cabinet + CCS dispenser] --DC--> [EV]

This is NOT a real product selection — it is a technology comparison baseline for
Task 4482. The system is size-matched one-to-one with Kempower (50/150/250 kW DC output)
so the comparison is symmetric on delivered DC power.

Architecture (Section 0 of the spec):
  - Genset provides AC power to a DCFC power cabinet
  - DCFC cabinet performs AC→DC conversion + ISO 15118 / DIN 70121 protocol handshake
  - DCFC adder cost ($410/kW) is backed out of DGS Kempower hardware prices

Size matching (spec Table, Section 0):
  Kempower 50 kW  DC  →  genset ≈53 kW AC  →  spec 60 kW prime,  Tier 4 Final towable
  Kempower 150 kW DC  →  genset ≈160 kW AC →  spec 175 kW prime, Tier 4 Final towable
  Kempower 250 kW DC  →  genset ≈266 kW AC →  spec 275-300 kW prime, Tier 4 Final towable

Key sources (see spec Tables 1-2):
  D1  Lazard LCOE v11.0 (2017) via NREL/TP-6A20-72509 (2019): $500-800/kW, 250kW-1MW class
  D2  EIA Capital Cost & Performance Char. (Jan 2024), Table 1.2: $921/kW (2023$) utility ICE
  D4  DGS Contract 1-23-61-15A Kempower pricing: $62,154/150kW=$414/kW; $101,946/250kW=$408/kW → ~$410/kW
  D5  NREL/TP-6A20-72509 (2019): $35/kW-yr fixed O&M
  D6  HOMER Energy (NREL-derived): $0.020/kWh variable O&M
  D7  Cummins/Cat datasheets, ISO 3046-1: η=0.35 at ≥75% load
  D8  DOE AFDC Fuel Properties Comparison: 37.66 kWh/gal (LHV, No. 2 diesel)
  D9  EIA Weekly CA No. 2 Diesel Retail Prices (EMD_EPD2D_PTE_SCA_DPG): $6.94/gal (2026-06-08)
  D10 NREL DCFC literature (NREL/TP-5400-91021): η_DCFC = 0.94 AC→DC
  D11 HOMER convention (LACCEI 2021 FP380): 15,000 hr engine life
  D12 Tier 4 Final SCR vendor docs (Cummins/Generac): ~3% DEF by fuel volume
  D13 CARB PERP: TBD — placeholder $500/yr; confirm at ww2.arb.ca.gov/our-work/programs/perp

PLACEHOLDERS (must be updated before final report):
  [PLACEHOLDER-D3] Genset $/kW for 50 kW and 175 kW classes — pending vendor quotes.
      Current values use Lazard/NREL size-adjusted estimates. See D3 in spec.
  [PLACEHOLDER-D9] EIA CA diesel price — update to current-week value before submission.
  [PLACEHOLDER-D13] CARB PERP annual fee — confirm from CARB fee schedule.
"""

from __future__ import annotations

# ── Fundamental constants (all sources in module docstring) ───────────────────

DAYS_PER_MONTH = 30.42
DAYS_PER_YEAR  = 365.0

# D7  Genset thermal efficiency at ≥75% load (ISO 3046-1, Cummins/Cat datasheets)
ETA_GENSET = 0.35

# D10 DCFC power conversion efficiency, AC from genset → DC to vehicle
#     (NREL/TP-5400-91021; applied symmetrically to Kempower comparison)
ETA_DCFC = 0.94

# D8  Diesel LHV (DOE AFDC — No. 2 diesel, LHV, kWh/gal)
LHV_KWH_PER_GAL = 37.66

# D9  California retail diesel price (EIA, week of 2026-06-08)  [PLACEHOLDER-D9]
DIESEL_PRICE_PER_GAL = 6.94   # [PLACEHOLDER-D9] update to current EIA CA diesel price

# D6  Variable O&M (HOMER/NREL convention for diesel gensets)
VAR_OM_PER_KWH = 0.020

# D5  Fixed O&M rate (NREL/TP-6A20-72509)
FIXED_OM_PER_KW_YR = 35.0

# D11 Engine service life in operating hours (HOMER / LACCEI 2021 FP380)
ENGINE_LIFE_HRS = 15_000

# D12 DEF consumption as fraction of diesel volume (Tier 4 Final SCR, Cummins/Generac)
DEF_FRAC = 0.03

# DEF retail price (industry typical; update if site-specific pricing available)
DEF_PRICE_PER_GAL = 2.50

# D13 CARB PERP annual registration fee  [PLACEHOLDER-D13]
PERP_FEE_PER_YR = 500.0   # [PLACEHOLDER-D13] confirm from CARB fee schedule

# LF  Session average load factor (prime-power convention confirmed by Frank, 2026-07-02)
LOAD_FACTOR = 0.75

# D4  DCFC power-conversion + dispenser adder (backed out of DGS Kempower prices, spec D4)
DCFC_ADDER_PER_KW_DC = 410.0   # $/kW of delivered DC power


# ── Genset $/kW by size class ──────────────────────────────────────────────────
#
# Sources:
#   250 kW class: Lazard LCOE v11.0 midpoint $650/kW (range $500-800) [D1]
#   175 kW class: NREL/Homer small diesel convention ~$700/kW          [PLACEHOLDER-D3]
#   60 kW class:  NREL/Homer small diesel ~$900/kW (small-unit premium) [PLACEHOLDER-D3]
#
# Size premium logic: small diesel gensets cost more per kW than large ones.
# Lazard range ($500-800/kW) applies to 250kW-1MW. Sub-100kW units typically
# run 1.2-1.8× the $/kW of the 250kW class. Values here are literature estimates;
# replace with vendor quotes when available (pending — see spec D3).

_GENSET_CAPEX_PER_KW = {
    60:   900.0,   # [PLACEHOLDER-D3] 60 kW prime — NREL/Homer small diesel est.
    175:  700.0,   # [PLACEHOLDER-D3] 175 kW prime — NREL/Homer mid-size est.
    300:  650.0,   # [D1] 300 kW prime — Lazard LCOE v11.0 midpoint ($500-800/kW)
}


def build_diesel_configs() -> dict:
    """
    Return diesel system configs size-matched to Kempower (spec Section 0).

    Each config:
        matched_to      : Kempower charger type this matches on DC output
        dc_power_kw     : Delivered DC power (matches Kempower DC rating)
        genset_prime_kw : Spec'd genset prime rating (nearest standard size up
                          from dc_power_kw / ETA_DCFC)
        genset_capex_per_kw : $/kW of genset prime capacity [D1/D3]
        dcfc_adder_per_kw   : $/kW of DC output for DCFC cabinet [D4]
        life_years      : Equipment life for CapEx amortization (matches Kempower 8 yr)
        source_note     : Citation tag

    CapEx formula (symmetric with Kempower straight-line):
        capex = genset_prime_kw × genset_capex_per_kw   <- genset component
              + dc_power_kw     × DCFC_ADDER_PER_KW_DC  <- DCFC cabinet component
        C_daily = [capex / (life_years × 12)
                   + (fixed_om_yr + perp_yr) / 12] / 30.42
    """
    return {
        "Diesel_50kW": {
            "matched_to":           "Kempower_50kW",
            "dc_power_kw":          50.0,
            "genset_prime_kw":      60,
            "genset_capex_per_kw":  _GENSET_CAPEX_PER_KW[60],
            "dcfc_adder_per_kw":    DCFC_ADDER_PER_KW_DC,
            "life_years":           8,
            "source_note":          "[D1][PLACEHOLDER-D3] Lazard/NREL est.; pending vendor quote",
        },
        "Diesel_150kW": {
            "matched_to":           "Kempower_150kW",
            "dc_power_kw":          150.0,
            "genset_prime_kw":      175,
            "genset_capex_per_kw":  _GENSET_CAPEX_PER_KW[175],
            "dcfc_adder_per_kw":    DCFC_ADDER_PER_KW_DC,
            "life_years":           8,
            "source_note":          "[D1][PLACEHOLDER-D3] Lazard/NREL est.; pending vendor quote",
        },
        "Diesel_250kW": {
            "matched_to":           "Kempower_250kW",
            "dc_power_kw":          250.0,
            "genset_prime_kw":      300,
            "genset_capex_per_kw":  _GENSET_CAPEX_PER_KW[300],
            "dcfc_adder_per_kw":    DCFC_ADDER_PER_KW_DC,
            "life_years":           8,
            "source_note":          "[D1] Lazard LCOE v11.0 midpoint",
        },
    }


# ── Per-day cost calculation ───────────────────────────────────────────────────

def capex_total(cfg: dict) -> float:
    """Total system CapEx: genset (on prime kW) + DCFC cabinet (on DC kW)."""
    return (cfg["genset_prime_kw"] * cfg["genset_capex_per_kw"]
            + cfg["dc_power_kw"]   * cfg["dcfc_adder_per_kw"])


def fixed_om_yr(cfg: dict) -> float:
    """Annual fixed O&M on genset prime kW (D5: $35/kW-yr)."""
    return cfg["genset_prime_kw"] * FIXED_OM_PER_KW_YR


def daily_capex(cfg: dict) -> float:
    """
    Daily amortized CapEx + fixed O&M + PERP — same straight-line formula as
    Kempower/XOS:
        C_daily = [capex / (life*12) + (fixed_om + perp) / 12] / 30.42
    """
    cap     = capex_total(cfg)
    monthly_capex = cap / (cfg["life_years"] * 12)
    monthly_recur = (fixed_om_yr(cfg) + PERP_FEE_PER_YR) / 12
    return (monthly_capex + monthly_recur) / DAYS_PER_MONTH


def fuel_cost_day(e_day_kwh: float) -> float:
    """
    Diesel fuel cost for one operating day given total DC energy delivered.

    gallons = E_day / (η_genset × η_DCFC × LHV_kWh/gal)   [D7, D10, D8]
    fuel_$   = gallons × diesel_price                        [D9]
    """
    gallons = e_day_kwh / (ETA_GENSET * ETA_DCFC * LHV_KWH_PER_GAL)
    return gallons * DIESEL_PRICE_PER_GAL


def def_cost_day(e_day_kwh: float) -> float:
    """DEF cost for one operating day (D12: ~3% of fuel volume)."""
    gallons_fuel = e_day_kwh / (ETA_GENSET * ETA_DCFC * LHV_KWH_PER_GAL)
    gallons_def  = gallons_fuel * DEF_FRAC
    return gallons_def * DEF_PRICE_PER_GAL


def var_om_cost_day(e_day_kwh: float) -> float:
    """Variable O&M for one day (D6: $0.020/kWh delivered)."""
    return e_day_kwh * VAR_OM_PER_KWH


def runtime_hours_day(e_day_kwh: float, dc_power_kw: float) -> float:
    """
    Estimated genset runtime hours for one day.
        runtime = E_day / (P_rated_dc × LF)
    where LF = 0.75 (prime-power load factor, confirmed by Frank 2026-07-02).
    """
    if dc_power_kw <= 0:
        return 0.0
    return e_day_kwh / (dc_power_kw * LOAD_FACTOR)


def engine_replacements(annual_runtime_hr: float, horizon_yr: float = 8.0) -> int:
    """
    Number of engine overhauls/replacements within the analysis horizon.
    Triggered when cumulative runtime exceeds ENGINE_LIFE_HRS (D11: 15,000 h).
    """
    import math
    total_hr = annual_runtime_hr * horizon_yr
    return max(0, math.floor(total_hr / ENGINE_LIFE_HRS))


def total_daily_cost(cfg: dict, e_day_kwh: float) -> dict:
    """
    Full daily cost breakdown for one diesel mobile DCFC system.

    Parameters
    ----------
    cfg       : one entry from build_diesel_configs()
    e_day_kwh : total DC energy delivered on this day (kWh)

    Returns
    -------
    dict with all cost components and totals ($/day)
    Note: diesel has NO utility demand charges (genset is the supply).
    """
    cap   = daily_capex(cfg)
    fuel  = fuel_cost_day(e_day_kwh)
    def_  = def_cost_day(e_day_kwh)
    varom = var_om_cost_day(e_day_kwh)
    rt    = runtime_hours_day(e_day_kwh, cfg["dc_power_kw"])
    total = cap + fuel + def_ + varom

    return {
        "capex_daily":        round(cap,   2),
        "fuel_cost":          round(fuel,  2),
        "def_cost":           round(def_,  2),
        "var_om_cost":        round(varom, 2),
        "demand_charge":      0.0,            # no utility connection → no demand charge
        "total_daily_cost":   round(total, 2),
        "runtime_hours":      round(rt,    3),
        "gallons_day":        round(e_day_kwh / (ETA_GENSET * ETA_DCFC * LHV_KWH_PER_GAL), 3),
    }


def lcod_per_kwh(cfg: dict, e_day_kwh: float) -> float:
    """LCOD ($/kWh delivered) = total_daily_cost / e_day_kwh."""
    if e_day_kwh <= 0:
        return float("nan")
    return total_daily_cost(cfg, e_day_kwh)["total_daily_cost"] / e_day_kwh


def is_feasible(cfg: dict, e_day_kwh: float, available_window_hr: float) -> bool:
    """
    Feasibility check: genset runtime must fit within the site operating window.
    runtime_hours ≤ available_window_hr
    """
    rt = runtime_hours_day(e_day_kwh, cfg["dc_power_kw"])
    return rt <= available_window_hr


# ── Quick summary printout ─────────────────────────────────────────────────────

if __name__ == "__main__":
    configs = build_diesel_configs()

    print("\nDiesel Mobile DCFC — Cost Parameters")
    print("=" * 70)
    print(f"  eta_genset      : {ETA_GENSET:.2f}  [D7, ISO 3046-1 / Cummins datasheets]")
    print(f"  eta_DCFC        : {ETA_DCFC:.2f}  [D10, NREL/TP-5400-91021]")
    print(f"  LHV diesel      : {LHV_KWH_PER_GAL} kWh/gal  [D8, DOE AFDC]")
    print(f"  Diesel price CA : ${DIESEL_PRICE_PER_GAL}/gal  [D9, EIA 2026-06-08]  [PLACEHOLDER-D9]")
    print(f"  Var O&M         : ${VAR_OM_PER_KWH}/kWh  [D6, HOMER/NREL]")
    print(f"  Fixed O&M       : ${FIXED_OM_PER_KW_YR}/kW-yr  [D5, NREL/TP-6A20-72509]")
    print(f"  Engine life     : {ENGINE_LIFE_HRS:,} hr  [D11, HOMER / LACCEI 2021 FP380]")
    print(f"  DEF fraction    : {DEF_FRAC*100:.0f}% of fuel  [D12, Cummins/Generac Tier4 docs]")
    print(f"  PERP fee        : ${PERP_FEE_PER_YR}/yr  [D13, CARB PERP]  [PLACEHOLDER-D13]")
    print(f"  Load factor     : {LOAD_FACTOR}  [confirmed by Frank 2026-07-02]")
    print()
    print(f"  PLACEHOLDERS:")
    print(f"    [PLACEHOLDER-D3] 50 kW and 175 kW genset $/kW from Lazard/NREL estimates.")
    print(f"      Update with vendor quotes (Generac Mobile, Cummins C-series, HIPOWER).")
    print(f"    [PLACEHOLDER-D9] EIA CA diesel price — update to current week before submission.")
    print(f"    [PLACEHOLDER-D13] CARB PERP fee — confirm from ww2.arb.ca.gov/our-work/programs/perp")
    print()

    # Example: 500 kWh demand day
    E = 500.0
    print(f"  Example: {E} kWh delivered day")
    print(f"  {'Config':<16} {'DC kW':>6} {'GenSet kW':>10} {'CapEx/day':>10} "
          f"{'Fuel/day':>10} {'Total/day':>10} {'LCOD $/kWh':>12}")
    print("  " + "-" * 76)
    for name, cfg in configs.items():
        costs = total_daily_cost(cfg, E)
        lcd   = lcod_per_kwh(cfg, E)
        print(f"  {name:<16} {cfg['dc_power_kw']:>6.0f} {cfg['genset_prime_kw']:>10} "
              f"  ${costs['capex_daily']:>8.2f}  ${costs['fuel_cost']:>8.2f}  "
              f"${costs['total_daily_cost']:>8.2f}  ${lcd:>10.4f}")
    print()
    print("  Note: No utility demand charge for diesel (genset provides power, not the grid).")
    print("  Sensitivity parameters: eta=0.30-0.38, diesel price +/-$1/gal, varOM $0.005-0.03/kWh")
