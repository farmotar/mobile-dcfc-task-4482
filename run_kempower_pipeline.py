"""
run_kempower_pipeline.py
========================
Run Kempower MILP sizing for all days at all 4 sites, then generate
Kempower day-view figures (Gantt + power demand + vehicle legend).

Usage:
    python run_kempower_pipeline.py [site]        # single site
    python run_kempower_pipeline.py               # all 4 sites

Outputs per day  -> per_day/{date}/kempower/
    exact_milp_selected_charger_mix.csv
    exact_milp_event_results.csv
    exact_milp_charging_schedule.csv
    exact_milp_site_power_profile.csv
    exact_milp_cost_breakdown.csv
    (plus plots from scenario_runner)

Figures          -> per_day/{date}/kempower_day_view_{date}.png

Site summary     -> {site}_analysis/kempower_summary.csv
                    {site}_analysis/kempower_report.txt
"""
from __future__ import annotations

import io, sys, glob, re, math, contextlib, traceback
from pathlib import Path
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
import scenario_runner as sr

BASE_DIR = Path(r"D:\Geotab_EV_Parameters\charger_sizing_test")
TZ       = "America/Los_Angeles"

SITES = {
    "northgate": ("Northgate", "z2z_milp_events_northgate"),
    "fresno":    ("Fresno",    "z2z_milp_events_fresno"),
    "glendale":  ("Glendale",  "z2z_milp_events_glendale"),
    "san_diego": ("San Diego", "z2z_milp_events_san_diego"),
}

TYPE_COLOR = {
    "Kempower_50kW":  "#2166ac",
    "Kempower_150kW": "#1a9641",
    "Kempower_250kW": "#d73027",
}
TYPE_POWER = {
    "Kempower_50kW":  "50 kW",
    "Kempower_150kW": "150 kW",
    "Kempower_250kW": "250 kW",
}
TYPE_ORDER = ["Kempower_50kW", "Kempower_150kW", "Kempower_250kW"]


# ── helpers ────────────────────────────────────────────────────────────────────

def _vid_label(eid: str, date_str: str) -> str:
    parts = eid.split("_")
    dc    = parts[-2]; num = int(parts[-1][1:])
    tc    = date_str.replace("-", "")
    return f"V{num}{'p' if dc < tc else ''}"


def _assign_lanes(sched_df: pd.DataFrame, mix_df: pd.DataFrame) -> pd.DataFrame:
    """Assign each vehicle to a charger lane (one lane per charger unit per type)."""
    sched_df = sched_df.copy()
    sched_df["time_step_start"] = pd.to_datetime(sched_df["time_step_start"], utc=True)
    sched_df["time_step_end"]   = pd.to_datetime(sched_df["time_step_end"],   utc=True)

    veh = (sched_df.groupby(["charging_event_id", "charger_type"])
                   .agg(charge_start=("time_step_start", "min"),
                        charge_end=("time_step_end",   "max"),
                        energy_del=("energy_delivered_kwh", "sum"))
                   .reset_index()
                   .sort_values("charge_start"))

    lane_ends: dict[str, list] = {}
    for ct in mix_df["charger_type"]:
        n = int(mix_df.loc[mix_df["charger_type"] == ct, "count"].values[0])
        lane_ends[ct] = [pd.Timestamp.min.tz_localize("UTC")] * n

    assigned = []
    for _, row in veh.iterrows():
        ct   = row["charger_type"]
        ends = lane_ends.get(ct, [pd.Timestamp.min.tz_localize("UTC")])
        best = min(range(len(ends)), key=lambda i: ends[i])
        assigned.append(best)
        ends[best] = row["charge_end"]
    veh["lane_within_type"] = assigned

    base: dict[str, int] = {}
    offset = 0
    for ct in TYPE_ORDER:
        base[ct] = offset
        n = int(mix_df.loc[mix_df["charger_type"] == ct, "count"].values[0]) if ct in mix_df["charger_type"].values else 0
        offset += n
    veh["lane"] = veh.apply(lambda r: base.get(r["charger_type"], 0) + r["lane_within_type"], axis=1)
    return veh


def _run_kempower_day(csv_path: Path, out_dir: Path, date_str: str,
                      site_label: str) -> dict | None:
    """Run Kempower MILP for one day, return summary dict (or None on failure)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            res = sr.run_kempower_only(csv_path, out_dir, date_str, site_label)
        if not res:
            return None
    except Exception as e:
        return {"error": str(e)}

    mix_df   = res["mix_df"]
    event_df = res["event_df"]
    cost_df  = res["cost_df"]

    # Charger mix
    n_total_chargers = int(mix_df["count"].sum())
    mix_str = " + ".join(
        f"{int(r['count'])}×{r['charger_type'].replace('Kempower_','')}"
        for _, r in mix_df.iterrows() if int(r["count"]) > 0
    )

    # Vehicle stats
    n_vehicles = len(event_df)
    has_del = "delivered_energy_kwh" in event_df.columns
    has_unm = "unmet_energy_kwh"     in event_df.columns
    del_s   = event_df["delivered_energy_kwh"] if has_del else pd.Series([0.0] * n_vehicles)
    unm_s   = event_df["unmet_energy_kwh"]     if has_unm else pd.Series([0.0] * n_vehicles)
    n_full     = int((unm_s <= 0.1).sum())
    n_partial  = int(((del_s > 0.1) & (unm_s > 0.1)).sum())
    n_unserved = n_vehicles - n_full - n_partial

    # Energy
    e_del   = float(event_df["delivered_energy_kwh"].sum()) if "delivered_energy_kwh" in event_df.columns else 0.0
    e_unmet = float(event_df["unmet_energy_kwh"].sum())     if "unmet_energy_kwh"     in event_df.columns else 0.0
    e_dem   = e_del + e_unmet

    # Cost — cost_breakdown CSV uses "value" column (not "value_usd")
    val_col = "value" if "value" in cost_df.columns else (
              "value_usd" if "value_usd" in cost_df.columns else None)
    def _cost_val(component):
        if val_col is None or "component" not in cost_df.columns: return 0.0
        row = cost_df[cost_df["component"] == component]
        return float(row[val_col].iloc[0]) if not row.empty else 0.0
    capex_daily = _cost_val("daily_capex_cost")
    energy_cost = _cost_val("energy_cost")
    total_cost  = capex_daily + energy_cost   # daily opex (excl monthly demand)

    # Peak power
    power_df = res.get("power_df", pd.DataFrame())
    peak_kw  = float(power_df["P_total_kw"].max()) if not power_df.empty and "P_total_kw" in power_df.columns else 0.0

    return {
        "date": date_str,
        "n_chargers":   n_total_chargers,
        "mix":          mix_str,
        "n_vehicles":   n_vehicles,
        "n_full":       n_full,
        "n_partial":    n_partial,
        "n_unserved":   n_unserved,
        "svc_rate_pct": round(100 * n_full / max(n_vehicles, 1), 1),
        "e_demanded_kwh":  round(e_dem, 1),
        "e_delivered_kwh": round(e_del, 1),
        "e_unmet_kwh":     round(e_unmet, 1),
        "capex_daily":  round(capex_daily, 2),
        "energy_cost":  round(energy_cost, 2),
        "total_cost":   round(total_cost, 2),
        "peak_kw":      round(peak_kw, 1),
        "_res":         res,
    }


# ── figure ─────────────────────────────────────────────────────────────────────

def _kempower_fig(date_str: str, res: dict, events_ext: pd.DataFrame,
                  site_label: str, out_path: Path) -> None:
    """3-panel Kempower day-view: Gantt / power demand / vehicle legend."""
    mix_df   = res["mix_df"]
    sched_df = res["schedule_df"]
    event_df = res["event_df"]
    power_df = res["power_df"]

    # Parse timestamps
    event_df = event_df.copy()
    event_df["arrival_time"]   = pd.to_datetime(event_df["arrival_time"],   utc=True)
    event_df["departure_time"] = pd.to_datetime(event_df["departure_time"], utc=True)

    power_df = power_df.copy()
    power_df["time_utc"] = pd.to_datetime(power_df["time_step_start"], utc=True)
    power_df["time_pac"] = power_df["time_utc"].dt.tz_convert(TZ)
    power_kw_col = "P_total_kw" if "P_total_kw" in power_df.columns else power_df.columns[-1]

    # Lane assignment
    lane_df = _assign_lanes(sched_df, mix_df)
    evt_short = event_df[["charging_event_id", "ev_equivalent_model",
                           "arrival_time", "departure_time"]].copy()
    if "delivered_energy_kwh" in event_df.columns:
        evt_short["energy_del"] = event_df["delivered_energy_kwh"].values
    lane_df = lane_df.merge(evt_short, on="charging_event_id", how="left")

    # Vehicle lookups
    all_vids  = events_ext["charging_event_id"].tolist()
    vid_model = {r["charging_event_id"]: str(r.get("ev_equivalent_model","") or "")
                 for _, r in events_ext.iterrows()}
    vid_dwell = {r["charging_event_id"]: (pd.to_datetime(r["arrival_time"], utc=True),
                                           pd.to_datetime(r["departure_time"], utc=True))
                 for _, r in events_ext.iterrows()}
    cmap      = plt.cm.get_cmap("tab20", max(len(all_vids), 20))
    vid_color = {v: cmap(i) for i, v in enumerate(all_vids)}

    # x-axis: minutes from midnight Pacific
    t_ref = pd.Timestamp(date_str, tz=TZ)
    def to_x(t) -> float:
        tl = t.tz_convert(TZ) if hasattr(t, "tz_convert") else t
        return (tl - t_ref).total_seconds() / 60.0

    all_times  = (list(events_ext["arrival_time"]) +
                  list(events_ext["departure_time"]) +
                  list(power_df["time_pac"]))
    t_gs = min(pd.to_datetime(t, utc=True) for t in all_times).tz_convert(TZ).floor("1h")
    t_ge = max(pd.to_datetime(t, utc=True) for t in all_times).tz_convert(TZ).ceil("1h")
    x_beg = (t_gs - t_ref).total_seconds() / 60.0
    x_end = (t_ge - t_ref).total_seconds() / 60.0

    t_tick = t_gs.ceil("2h"); tick_xs, tick_labels = [], []
    while t_tick <= t_ge:
        tick_xs.append((t_tick - t_ref).total_seconds() / 60.0)
        tick_labels.append(t_tick.strftime("%H:%M"))
        t_tick += pd.Timedelta(hours=2)

    n_lanes = int(mix_df["count"].sum())
    leg_h   = max(2.0, math.ceil(len(all_vids) / 5) * 0.38 + 0.5)
    gantt_h = max(n_lanes * 0.62, 3.5)
    fig_h   = max(16, gantt_h + 4.0 + leg_h + 1.5)

    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(26, fig_h),
        gridspec_kw={"height_ratios": [gantt_h, 4.0, leg_h]})
    fig.subplots_adjust(hspace=0.10, left=0.09, right=0.99, top=0.97, bottom=0.02)

    # ── build lane labels ──────────────────────────────────────────────────────
    lane_labels = []
    for ct in TYPE_ORDER:
        sub = mix_df[mix_df["charger_type"] == ct]
        if sub.empty: continue
        n = int(sub["count"].values[0])
        for i in range(n):
            lane_labels.append(f"{TYPE_POWER[ct]} #{i+1}")

    ax1.set_title(
        f"{site_label}  |  {date_str}  |  Kempower DCFC  —  MILP optimal charger mix",
        fontsize=12, fontweight="bold", pad=6)

    # ── Gantt bars ──────────────────────────────────────────────────────────────
    for _, row in lane_df.iterrows():
        lane   = int(row["lane"])
        ct     = row["charger_type"]
        color  = TYPE_COLOR.get(ct, "steelblue")
        vid    = row["charging_event_id"]
        c_s    = row["charge_start"]
        c_e    = row["charge_end"]
        x_cs   = to_x(c_s); x_ce = to_x(c_e)

        # Dwell window (light)
        if vid in vid_dwell:
            arr_t, dep_t = vid_dwell[vid]
            ax1.barh(lane, max(to_x(dep_t) - to_x(arr_t), 1), left=to_x(arr_t),
                     height=0.60, color=color, alpha=0.15,
                     edgecolor=color, linewidth=0.6, zorder=1)
        # Charge bar (solid)
        ax1.barh(lane, max(x_ce - x_cs, 1), left=x_cs, height=0.60,
                 color=color, alpha=0.85, edgecolor="white", linewidth=0.2, zorder=3)
        # V-label
        lbl = _vid_label(vid, date_str)
        ax1.text((x_cs + x_ce) / 2, lane, lbl,
                 ha="center", va="center", fontsize=5.8,
                 color="white", fontweight="bold", clip_on=True, zorder=4)

    ax1.set_xlim(x_beg, x_end)
    ax1.set_ylim(-0.5, n_lanes - 0.5)
    ax1.set_yticks(range(n_lanes))
    ax1.set_yticklabels(lane_labels, fontsize=8)
    ax1.set_xticks(tick_xs)
    ax1.set_xticklabels(tick_labels, fontsize=8, rotation=30, ha="right")
    ax1.invert_yaxis()
    ax1.grid(axis="x", linestyle=":", alpha=0.35, color="gray")
    ax1.set_ylabel("Kempower charger lane", fontsize=9)

    patches_kmp = [mpatches.Patch(color=TYPE_COLOR[ct], label=TYPE_POWER[ct])
                   for ct in TYPE_ORDER if ct in mix_df["charger_type"].values and
                   int(mix_df.loc[mix_df["charger_type"]==ct,"count"].values[0]) > 0]
    dwell_p = mpatches.Patch(facecolor="gray", alpha=0.20, edgecolor="gray",
                              linewidth=0.8, label="Dwell window")
    ax1.legend(handles=patches_kmp + [dwell_p], loc="upper right",
               fontsize=8, ncol=4, framealpha=0.90)

    # ── Power demand ──────────────────────────────────────────────────────────
    kx = [to_x(t) for t in power_df["time_pac"]]
    ky = power_df[power_kw_col].tolist()
    peak_kw = max(ky) if ky else 0
    ax2.plot(kx, ky, color="#2166ac", linewidth=1.8,
             label=f"Kempower grid draw (peak {peak_kw:,.0f} kW)")
    ax2.fill_between(kx, ky, alpha=0.12, color="#2166ac")

    pk0 = (t_ref + pd.Timedelta(hours=16) - t_ref).total_seconds() / 60
    pk1 = (t_ref + pd.Timedelta(hours=21) - t_ref).total_seconds() / 60
    if pk0 < x_end:
        ax2.axvspan(pk0, min(pk1, x_end), color="#fee08b", alpha=0.30,
                    label="SMUD peak 16–21h")

    ax2.set_xlim(x_beg, x_end)
    ax2.set_ylim(0, max(peak_kw, 1) * 1.15)
    ax2.set_xticks(tick_xs)
    ax2.set_xticklabels(tick_labels, fontsize=8.5, rotation=30, ha="right")
    ax2.set_ylabel("Site grid draw (kW)", fontsize=9)
    ax2.set_xlabel(f"Time (Pacific)  —  {date_str}", fontsize=9)
    ax2.legend(loc="upper left", fontsize=8.5, framealpha=0.92)
    ax2.grid(axis="both", linestyle=":", alpha=0.30)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))

    # ── Vehicle legend ─────────────────────────────────────────────────────────
    ax3.axis("off")
    ax3.set_title("Vehicle legend  (solid bar = charging window,  light bar = on-site dwell)",
                  fontsize=8.5, fontweight="bold", pad=3, loc="left")

    lrows = sorted([
        (lbl.endswith("p"),
         int(lbl.rstrip("p")[1:]) if lbl.rstrip("p")[1:].isdigit() else 999,
         lbl, vid_model.get(eid, ""), vid_color[eid])
        for eid in all_vids
        for lbl in [_vid_label(eid, date_str)]
    ])
    N_COLS = 5; PATCH_W = 0.024; PATCH_H = 0.052
    n_rows = max(1, math.ceil(len(lrows) / N_COLS))
    COL_W  = 1.0 / N_COLS
    for idx, (_, _, lbl, model, color) in enumerate(lrows):
        col = idx % N_COLS; row_i = idx // N_COLS
        x0  = col * COL_W + 0.005
        y0  = 1.0 - (row_i + 1) * (1.0 / (n_rows + 0.5))
        ax3.add_patch(mpatches.FancyBboxPatch(
            (x0, y0), PATCH_W, PATCH_H, boxstyle="round,pad=0.002",
            facecolor=color, edgecolor="none",
            transform=ax3.transAxes, clip_on=True, zorder=3))
        ax3.text(x0 + PATCH_W + 0.007, y0 + PATCH_H / 2, f"{lbl}: {model}",
                 ha="left", va="center", fontsize=7.0,
                 transform=ax3.transAxes, clip_on=True)

    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ── per-site runner ────────────────────────────────────────────────────────────

def run_kempower_site(
    site: str,
    site_label: str,
    csv_stem: str,
    force: bool = False,
    per_day_subdir: str = "kempower",
    summary_suffix: str = "",
) -> None:
    out_dir = BASE_DIR / "scenario_outputs" / f"{site}_analysis"
    per_day = out_dir / "per_day"
    if not per_day.exists():
        print(f"  [!] per_day folder missing for {site_label} — skipping")
        return

    day_dirs = sorted(d for d in per_day.iterdir() if d.is_dir())
    n = len(day_dirs)
    print(f"\n{'='*70}")
    print(f"  KEMPOWER — {site_label}: {n} days")
    print(f"{'='*70}")

    rows = []
    ok = skip = fail = already = 0

    for i, day_dir in enumerate(day_dirs, 1):
        date_str = day_dir.name
        date_tag = date_str.replace("-", "_")
        csv_path = BASE_DIR / f"{csv_stem}_{date_tag}.csv"
        kmp_dir  = day_dir / per_day_subdir
        fig_out  = day_dir / f"kempower_day_view_{date_str}.png"

        print(f"  [{i:3d}/{n}] {date_str}", end="  ", flush=True)

        if not csv_path.exists():
            print("no-csv"); skip += 1; continue

        # Skip if already done (unless force=True, e.g. rerunning under corrected rates)
        if not force and fig_out.exists() and (kmp_dir / "exact_milp_selected_charger_mix.csv").exists():
            print("already done"); already += 1
            # Still collect summary from saved CSV
            try:
                m = pd.read_csv(kmp_dir / "exact_milp_selected_charger_mix.csv")
                e = pd.read_csv(kmp_dir / "exact_milp_event_results.csv")
                p = pd.read_csv(kmp_dir / "exact_milp_site_power_profile.csv")
                n_ch = int(m["count"].sum())
                mix_str = " + ".join(f"{int(r['count'])}×{r['charger_type'].replace('Kempower_','')}"
                                     for _, r in m.iterrows() if int(r["count"]) > 0)
                n_v    = len(e)
                del_s  = e["delivered_energy_kwh"] if "delivered_energy_kwh" in e.columns else pd.Series([0.0]*n_v)
                unm_s  = e["unmet_energy_kwh"]     if "unmet_energy_kwh"     in e.columns else pd.Series([0.0]*n_v)
                n_full = int((unm_s <= 0.1).sum())
                n_part = int(((del_s > 0.1) & (unm_s > 0.1)).sum())
                n_uns  = n_v - n_full - n_part
                e_del  = float(del_s.sum())
                e_unm  = float(unm_s.sum())
                p_kw   = float(p["P_total_kw"].max()) if "P_total_kw" in p.columns else 0.0
                rows.append({"date": date_str, "n_chargers": n_ch, "mix": mix_str,
                              "n_vehicles": n_v, "n_full": n_full,
                              "n_partial": n_part, "n_unserved": n_uns,
                              "svc_rate_pct": round(100*n_full/max(n_v,1),1),
                              "e_demanded_kwh": round(e_del+e_unm,1),
                              "e_delivered_kwh": round(e_del,1),
                              "e_unmet_kwh": round(e_unm,1),
                              "peak_kw": round(p_kw,1)})
            except Exception:
                pass
            continue

        # Run MILP
        try:
            result = _run_kempower_day(csv_path, kmp_dir, date_str, site_label)
        except Exception as e_outer:
            print(f"CRASH: {e_outer}"); fail += 1; continue

        if result is None:
            print("empty"); skip += 1; continue
        if "error" in result:
            print(f"ERR: {result['error'][:60]}"); fail += 1; continue

        mix_str = result["mix"] or "—"
        print(f"K={result['n_chargers']} ({mix_str})  "
              f"svc={result['n_full']}/{result['n_vehicles']}", end="  ", flush=True)

        # Generate figure
        try:
            ev_ext = _load_events(csv_path, date_str, csv_stem)
            if ev_ext is not None and not ev_ext.empty:
                _kempower_fig(date_str, result["_res"], ev_ext, site_label, fig_out)
                print("fig saved")
            else:
                print("fig skipped (no events)")
        except Exception as ef:
            print(f"fig ERR: {ef}")

        row_out = {k: v for k, v in result.items() if k != "_res"}
        rows.append(row_out)
        ok += 1

    print(f"\n  Done: {ok} new  |  {already} already done  |  {skip} skipped  |  {fail} errors")

    if rows:
        df = pd.DataFrame(rows)
        df.to_csv(out_dir / f"{site}_kempower_summary{summary_suffix}.csv", index=False)
        _write_report(df, site_label, out_dir)
        print(f"  Summary → {out_dir / f'{site}_kempower_summary{summary_suffix}.csv'}")


def _load_events(csv_path: Path, date_str: str, csv_stem: str) -> pd.DataFrame | None:
    stem_parts    = csv_path.stem.rsplit("_", 3)
    site_csv_stem = "_".join(stem_parts[:-3]) if len(stem_parts) > 3 else csv_path.stem
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ev = sr.load_site_day_data(csv_path)
            ev = sr.apply_multiday_rule(ev, date_str,
                                        site_csv_dir=csv_path.parent,
                                        site_csv_stem=site_csv_stem)
        return sr._xos_extended_dwell(ev)
    except Exception:
        return None


def _write_report(df: pd.DataFrame, site_label: str, out_dir: Path) -> None:
    lines = [
        "=" * 70,
        f"  {site_label.upper()} — KEMPOWER MILP SIZING SUMMARY",
        f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"  Days: {len(df)}",
        "",
        f"  Charger count — min/avg/max:  "
        f"{df['n_chargers'].min()} / {df['n_chargers'].mean():.1f} / {df['n_chargers'].max()}",
        f"  Total vehicles: {df['n_vehicles'].sum()}",
        f"  Fully served:   {df['n_full'].sum()} "
        f"({100*df['n_full'].sum()/max(df['n_vehicles'].sum(),1):.1f}%)",
        f"  Partial:        {df['n_partial'].sum()}",
        f"  Unserved:       {df['n_unserved'].sum()}",
        f"  Daily cost  — avg: ${df['total_cost'].mean():.2f}  max: ${df['total_cost'].max():.2f}"
        if "total_cost" in df.columns else "",
        f"  Peak grid kW — avg: {df['peak_kw'].mean():.0f}  max: {df['peak_kw'].max():.0f}",
        "",
        "  Most common charger mixes:",
    ]
    for mix, cnt in df["mix"].value_counts().head(5).items():
        lines.append(f"    {cnt:4d} days — {mix}")
    lines.append("=" * 70)

    rpt = "\n".join(lines)
    (out_dir / f"{site_label.lower().replace(' ','_')}_kempower_report.txt").write_text(
        rpt, encoding="utf-8")
    print(rpt)


# ── entry point ────────────────────────────────────────────────────────────────

def main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"
    for site, (label, stem) in SITES.items():
        if target != "all" and target != site:
            continue
        run_kempower_site(site, label, stem)
    print("\nKempower pipeline complete.")


if __name__ == "__main__":
    main()
