## Technology Cost Comparison — p90 Daily Cost by Site

| Site | Technology | N days | p90 $/day | Mean $/day | Mean LCOD $/kWh | Mean demand $/day |
|------|-----------|--------|----------|----------|----------------|------------------|
| Northgate | Fixed DCFC | 310 | $16,890.84 | $10,195.46 | $5.7815 | $9,450.75 |
| Northgate | XOS Hub MC02 | 310 | $1,242.02 | $733.47 | $0.3946 | $123.20 |
| Northgate | Kempower | 306 | $951.87 | $636.24 | $0.3075 | $142.29 |
| Northgate | Diesel (Diesel_50kW) | 310 | $2,271.91 | $1,428.74 | $0.6255 | $0.00 |
| Northgate | Diesel (Diesel_150kW) | 310 | $2,320.43 | $1,477.26 | $0.6840 | $0.00 |
| Northgate | Diesel (Diesel_250kW) | 310 | $2,371.28 | $1,528.11 | $0.7453 | $0.00 |
| Fresno | Fixed DCFC | 313 | $2,515.27 | $1,464.55 | $1.6223 | $1,008.08 |
| Fresno | XOS Hub MC02 | 313 | $701.43 | $415.99 | $0.6674 | $10.15 |
| Fresno | Kempower | 313 | $540.26 | $322.59 | $0.3859 | $24.74 |
| Fresno | Diesel (Diesel_50kW) | 313 | $1,133.02 | $646.60 | $0.6893 | $0.00 |
| Fresno | Diesel (Diesel_150kW) | 313 | $1,181.55 | $695.12 | $0.8427 | $0.00 |
| Fresno | Diesel (Diesel_250kW) | 313 | $1,232.40 | $745.97 | $1.0034 | $0.00 |
| Glendale (PG&E proxy) | Fixed DCFC | 255 | $1,730.38 | $1,146.26 | $2.8100 | $871.81 |
| Glendale (PG&E proxy) | XOS Hub MC02 | 255 | $309.97 | $198.04 | $0.4759 | $5.97 |
| Glendale (PG&E proxy) | Kempower | 255 | $303.06 | $195.47 | $0.3988 | $19.78 |
| Glendale (PG&E proxy) | Diesel (Diesel_50kW) | 255 | $574.36 | $342.35 | $0.6799 | $0.00 |
| Glendale (PG&E proxy) | Diesel (Diesel_150kW) | 255 | $622.88 | $390.87 | $0.8193 | $0.00 |
| Glendale (PG&E proxy) | Diesel (Diesel_250kW) | 255 | $673.74 | $441.72 | $0.9654 | $0.00 |
| San Diego | Fixed DCFC | 337 | $16,748.31 | $10,626.32 | $2.8999 | $8,721.73 |
| San Diego | XOS Hub MC02 | 339 | $2,617.64 | $1,180.58 | $0.3106 | $59.03 |
| San Diego | Diesel (Diesel_50kW) | 339 | $5,891.74 | $3,734.84 | $0.6240 | $0.00 |
| San Diego | Diesel (Diesel_150kW) | 339 | $5,940.26 | $3,783.36 | $0.6803 | $0.00 |
| San Diego | Diesel (Diesel_250kW) | 339 | $5,991.11 | $3,834.21 | $0.7393 | $0.00 |
| Glendale (SMUD proxy) | Fixed DCFC | 255 | $4,924.39 | $3,299.46 | $8.2380 | $3,053.57 |
| Glendale (SMUD proxy) | XOS Hub MC02 | 255 | $334.08 | $205.60 | $0.5046 | $29.97 |
| Glendale (SMUD proxy) | Kempower | 239 | $325.80 | $217.76 | $0.4494 | $65.06 |
| Glendale (SMUD proxy) | Diesel (Diesel_50kW) | 255 | $574.36 | $342.35 | $0.6799 | $0.00 |
| Glendale (SMUD proxy) | Diesel (Diesel_150kW) | 255 | $622.88 | $390.87 | $0.8193 | $0.00 |
| Glendale (SMUD proxy) | Diesel (Diesel_250kW) | 255 | $673.74 | $441.72 | $0.9654 | $0.00 |

_Notes:_
- Diesel: no utility demand charge (genset provides AC; no grid connection).
- Kempower: demand charges estimated from peak_kw; SMUD peak-window demand
  exact only for days with per-day breakdown CSV (otherwise 0 — slight undercount).
- XOS: demand charges computed from peak grid draw (n_units × 83 kW) × site rate.
- Fixed DCFC: demand charges from MILP optimization output.
- [PLACEHOLDER-D3] Diesel 50/150 kW genset $/kW from Lazard/NREL estimates; pending vendor quotes.
- [PLACEHOLDER-D9] Diesel price $6.94/gal (EIA CA, 2026-06-08); update to current week.
- [PLACEHOLDER-D13] CARB PERP fee = $500/yr placeholder; confirm from CARB fee schedule.