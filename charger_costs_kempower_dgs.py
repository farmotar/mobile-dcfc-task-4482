"""
charger_costs_kempower_dgs.py
------------------------------
Charger cost parameters for Kempower mobile DCFC units priced via the
California DGS Contract 1-23-61-15A (National Car Charging LLC).
Source: Attachment_A_-_Contract_Pricing.xlsx

Charger types (Kempower only — no L2 for mobile scenario):
  Group 5  50 kW  (portable / movable)  — B-500 base unit
  Group 6 150 kW  (cabinet)             — S-600/S-601 average
  Group 7 250 kW  (cabinet)             — S-700 / S-701

Installation (from DGS contract):
  Group 5 : $855
  Group 6 : $4,750
  Group 7 : $5,225

Maintenance (all groups): $1,573.20/charger-year  (ChargerHelp! via DGS)

Life years: 8 yr DC  (same assumption as Caltrans DC chargers)

Hardware prices (from Attachment_A):
  B-500 = $21,995   B-501 = $24,820   -> avg $23,407.50  (Group 5)
  S-600 = $62,113   S-601 = $62,194   -> avg $62,153.50  (Group 6)
  S-700 = S-701 = $101,946            -> $101,946.00     (Group 7)

Note: DGS contract allows 1% discount for orders > $100K, 2% for > $500K.
      Prices above are pre-discount list prices.
"""

from __future__ import annotations


def build_charger_specs_kempower_dgs() -> dict:
    """
    Return Kempower DGS charger specs. Drop-in replacement for
    build_charger_specs_caltrans() when running the Kempower-only scenario.

    Only 3 DC types (no L2). The MILP will set N_L2 = 0 by exclusion
    if this dict is used directly, or the scenario runner can filter.

    Daily CapEx formula (same as original):
        C_daily = [(purchase + install) / (life_years * 12)
                   + annual_maint / 12] / 30.42
    """
    return {
        "Kempower_50kW": {
            "ac_dc":         "DC",
            "power_kw":       50.0,
            # DGS Group 5: B-500 ($21,995) + B-501 ($24,820) -> avg $23,407.50
            # Install (Group 5): $855
            # Maintenance: $1,573.20/yr (ChargerHelp via DGS, all groups)
            # Warranty: $2,000/yr (provided by Farhang)
            "purchase_cost":  23_408,   # rounded avg of B-500/B-501
            "install_cost":      855,
            "annual_maint":    1_573,   # rounded from $1,573.20
            "annual_warranty": 2_000,   # provided by Farhang
            "life_years":          8,
        },
        "Kempower_150kW": {
            "ac_dc":         "DC",
            "power_kw":      150.0,
            # DGS Group 6: S-600 ($62,113) + S-601 ($62,194) -> avg $62,153.50
            # Install (Group 6): $4,750
            "purchase_cost":  62_154,   # rounded avg of S-600/S-601
            "install_cost":    4_750,
            "annual_maint":    1_573,
            "annual_warranty": 2_000,   # provided by Farhang
            "life_years":          8,
        },
        "Kempower_250kW": {
            "ac_dc":         "DC",
            "power_kw":      250.0,
            # DGS Group 7: S-700 = S-701 = $101,946
            # Install (Group 7): $5,225
            "purchase_cost":  101_946,
            "install_cost":     5_225,
            "annual_maint":     1_573,
            "annual_warranty":  2_000,   # provided by Farhang
            "life_years":           8,
        },
    }


# ── Quick summary printout ─────────────────────────────────────────────────────

if __name__ == "__main__":
    DAYS_PER_MONTH = 30.42

    specs = build_charger_specs_kempower_dgs()

    def daily(s):
        mc = (s["purchase_cost"] + s["install_cost"]) / (s["life_years"] * 12)
        mm = s["annual_maint"] / 12
        return (mc + mm) / DAYS_PER_MONTH

    print("\nKempower DGS Contract — Daily CapEx")
    print("-" * 65)
    print(f"{'Type':<20} {'Power':>7} {'Purchase':>12} {'Install':>10} "
          f"{'Maint/yr':>10} {'$/day':>8}")
    print("-" * 65)
    for k, s in specs.items():
        d = daily(s)
        print(f"{k:<20} {s['power_kw']:>6.0f}kW  "
              f"${s['purchase_cost']:>10,}  ${s['install_cost']:>8,}  "
              f"${s['annual_maint']:>8,}  ${d:>7.2f}")
    print()

    # Compare to Caltrans fixed chargers
    try:
        from charger_costs_caltrans import build_charger_specs_caltrans
        caltrans = build_charger_specs_caltrans()
        print("Comparison vs. Caltrans fixed chargers:")
        pairs = [
            ("Kempower_50kW",  "DC_50kW"),
            ("Kempower_150kW", "DC_150kW"),
        ]
        for kk, ck in pairs:
            dk = daily(specs[kk])
            dc = daily(caltrans[ck])
            diff = (dk - dc) / dc * 100
            print(f"  {kk:<20} ${dk:.2f}/day  vs  Caltrans {ck} ${dc:.2f}/day "
                  f"  ({diff:+.1f}%)")
        print()
        print("Note: No Caltrans equivalent for Kempower_250kW (Caltrans has 350kW)")
    except ImportError:
        pass
