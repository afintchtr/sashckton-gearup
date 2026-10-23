#!/usr/bin/env python3
"""
Build the customer-snapshot modelling table from data/dev (proposal sections 5.2 and 5.3).

One row = (customer, scoring date). Features use ONLY data up to and including the scoring date.
Label  = confirmed fraud event starting in (scoring date, scoring date + 90 d]   (proposal 5.3 risk target)
Rows of customers whose confirmed fraud already started on/before the scoring date are dropped.

Two modes:
  snapshot (default)  customer x biweekly scoring date, label_fraud_90d   -> proposal 5.3 behavioural scorecard
  day                 customer x day (negatives down-sampled), label_fraud_today -> real-time fraud / sus detection
                      rows after a confirmed fraud event ends are dropped; column `weight` restores prevalence.

Usage: python3 build_features.py --data data/dev --out data/dev/snapshots.parquet
       python3 build_features.py --data data/dev --out data/dev/daydetect.parquet --mode day
"""
import argparse
import glob
import os
import warnings
import zlib
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

WIN_START = pd.Timestamp("2025-07-01")
WIN_END = pd.Timestamp("2026-09-30")
D = (WIN_END - WIN_START).days + 1
SNAP_FIRST, SNAP_LAST, SNAP_STEP = pd.Timestamp("2025-10-01"), pd.Timestamp("2026-07-01"), 14  # last = WIN_END - 91 d
HORIZON = 90
CATS = ["ecommerce", "food", "transport", "bills", "transfer_p2p", "ewallet_topup", "gambling_crypto"]
AGE_ORD = {"18-24": 0, "25-34": 1, "35-44": 2, "45-54": 3, "55+": 4}
warnings.filterwarnings("ignore")


def dense(df, ci, di, col, n, fill=0.0):
    m = np.full((n, D), np.nan, np.float64)
    m[ci, di] = df[col].to_numpy(np.float64)
    return m


def process(args):
    part, cust, events, mode, neg_keep = args
    rng = np.random.default_rng(zlib.crc32(os.path.basename(part).encode()))
    x = pd.read_parquet(part)
    ids, ci = np.unique(x["customer_id"].to_numpy(), return_inverse=True)
    di = (x["date"] - WIN_START).dt.days.to_numpy()
    n = len(ids)
    c = cust.loc[ids]
    inc = c["declared_income"].to_numpy(float)
    cap = x.groupby(ci)["cap_applied"].first().to_numpy(float)
    open_idx = np.maximum(0, (pd.to_datetime(c["open_date"]) - WIN_START).dt.days.to_numpy())

    def M(col):
        return dense(x, ci, di, col, n)

    OUT, INF, BAL = M("outflow_amt"), M("inflow_amt"), M("end_balance")
    NOUT, NIGHT = M("n_outflow"), M("night_outflow_amt")
    z = lambda a: np.nan_to_num(a)
    cs = lambda a: np.concatenate([np.zeros((n, 1)), np.cumsum(z(a), axis=1)], axis=1)
    C = {k: cs(v) for k, v in dict(
        out=OUT, inf=INF, nout=NOUT, night=NIGHT, declined=M("n_declined_limit"), newp=M("n_new_payee"),
        dist=M("n_distinct_payee"), ftp=M("ftp_large"), geo=M("n_geo_mismatch"), dev=M("new_device"),
        cred=M("cred_change"), alert=M("n_fraud_alert"), dispute=M("n_dispute")).items()}
    TOPW = M("top_payee_share")
    FD = z(M("_fraud_day"))
    DEV1, CRED1, FTP1, GEO1, NEWP1, DIST1 = M("new_device"), M("cred_change"), M("ftp_large"), M("n_geo_mismatch"), M("n_new_payee"), M("n_distinct_payee")
    ACT = (z(NOUT) > 0).astype(float)
    C["top"], C["act"] = cs(np.where(ACT > 0, z(TOPW), 0.0)), cs(ACT)
    for k in CATS:
        C[k] = cs(M(f"out_{k}"))

    # confirmed fraud: first confirmed event start per customer
    ev = events[events["customer_id"].isin(ids) & (events["reported"] == 1)].sort_values("event_start")
    ev = ev.drop_duplicates("customer_id").set_index("customer_id")
    ev_idx = np.full(n, 10 ** 6)
    ev_end = np.full(n, 10 ** 6)
    ev_type = np.array([""] * n, dtype=object)
    ev_stealth = np.zeros(n, int)
    pos = {cid: i for i, cid in enumerate(ids)}
    for cid, r in ev.iterrows():
        i = pos[cid]
        ev_idx[i] = (pd.Timestamp(r["event_start"]) - WIN_START).days
        ev_end[i] = (pd.Timestamp(r["event_end"]) - WIN_START).days
        ev_type[i], ev_stealth[i] = r["fraud_type"], int(r["stealth"])

    kyc = pd.to_datetime(c["kyc_date"])
    opend = pd.to_datetime(c["open_date"])
    static = pd.DataFrame({
        "customer_id": ids,
        "declared_income": inc,
        "age_ord": c["age_band"].map(AGE_ORD).to_numpy(),
        "gender": c["gender"].to_numpy(),
        "region": c["region"].to_numpy(),
        "occupation": c["occupation"].to_numpy(),
        "segment": c["segment"].to_numpy(),
        "baseline_tier": c["baseline_tier"].to_numpy(),
        "shared_device_flag": c["shared_device_flag"].to_numpy(),
    })

    def wsum(k, t, w):
        return C[k][:, t + 1] - C[k][:, max(0, t + 1 - w)]

    sd = lambda a, b: np.where(b > 0, a / np.where(b > 0, b, 1), 0.0)
    rows = []
    if mode in ("day", "sim"):
        grid = range((SNAP_FIRST - WIN_START).days, D)
    else:
        grid = range((SNAP_FIRST - WIN_START).days, (SNAP_LAST - WIN_START).days + 1, SNAP_STEP)
    for t in grid:
        hist = t - open_idx + 1
        ok = (hist >= 30) if mode == "sim" else (hist >= 30) & ((ev_end >= t) if mode == "day" else (ev_idx > t))
        if not ok.any():
            continue
        lo90 = max(0, t - 89)
        o90, o30, o7 = wsum("out", t, 90), wsum("out", t, 30), wsum("out", t, 7)
        d90 = np.minimum(hist, 90)
        d30 = np.minimum(hist, 30)
        sl = slice(lo90, t + 1)
        f = {}
        # --- stability
        f["tenure_months"] = (t - open_idx) / 30.4
        f["kyc_age_months"] = (WIN_START + pd.Timedelta(days=t) - kyc).dt.days.to_numpy() / 30.4
        wins = []
        for k in range(3):
            v = C["inf"][:, t + 1 - 30 * k] - C["inf"][:, max(0, t + 1 - 30 * (k + 1))]
            wins.append(np.where(hist >= 30 * (k + 1), v, np.nan))
        W = np.stack(wins, 1)
        f["inflow_cv_90"] = np.where((~np.isnan(W)).sum(1) >= 2, np.nanstd(W, 1) / (np.nanmean(W, 1) + 1), np.nan)
        f["regular_inflow_windows_90"] = np.nansum(W >= 0.5 * inc[:, None], 1)
        f["inflow_30"] = wsum("inf", t, 30)
        f["min_balance_90"] = np.nanmin(BAL[:, sl], 1)
        f["days_low_balance_90"] = np.nansum(BAL[:, sl] < 0.1 * inc[:, None], 1)
        f["balance"] = BAL[:, t]
        f["balance_to_income"] = BAL[:, t] / inc
        # --- demand
        f["out_30"] = o30
        f["out_7"] = o7
        f["avg_out_90"] = o90 / d90
        f["p95_out_30"] = np.nanpercentile(OUT[:, max(0, t - 29):t + 1], 95, axis=1)
        f["p95_out_90"] = np.nanpercentile(OUT[:, sl], 95, axis=1)
        prev30 = C["out"][:, max(0, t + 1 - 30)] - C["out"][:, max(0, t + 1 - 60)]
        f["out_trend_30"] = o30 / (prev30 + 1e6)
        f["out_to_income_30"] = o30 / inc
        f["near_limit_days_90"] = np.nansum(OUT[:, sl] >= 0.8 * cap[:, None], 1)      # limit-model feature
        f["declined_cnt_90"] = wsum("declined", t, 90)                                 # limit-model feature
        # --- behavioural consistency
        n30, n7, n90 = wsum("nout", t, 30), wsum("nout", t, 7), wsum("nout", t, 90)
        f["n_out_7"] = n7
        f["new_payee_rate_30"] = sd(wsum("newp", t, 30), n30)
        f["new_payee_cnt_7"] = wsum("newp", t, 7)
        f["night_share_30"] = sd(wsum("night", t, 30), o30)
        f["night_share_7"] = sd(wsum("night", t, 7), o7)
        f["night_share_1"] = sd(wsum("night", t, 1), wsum("out", t, 1))
        f["payee_concentration_90"] = sd(wsum("top", t, 90), wsum("act", t, 90))
        f["distinct_payee_30"] = wsum("dist", t, 30)
        f["distinct_per_tx_30"] = sd(wsum("dist", t, 30), n30)
        f["device_cnt_90"] = 1 + wsum("dev", t, 90)
        # --- merchant mix
        sh30 = np.stack([sd(wsum(k, t, 30), o30) for k in CATS], 1)
        lo, hi_ = max(0, t + 1 - 90), max(0, t + 1 - 30)
        sh60p = np.stack([sd(C[k][:, hi_] - C[k][:, lo], C["out"][:, hi_] - C["out"][:, lo]) for k in CATS], 1)
        for j, k in enumerate(CATS):
            f[f"mix_{k}_30"] = sh30[:, j]
        f["mix_shift_30_vs_prev60"] = np.abs(sh30 - sh60p).sum(1)
        # --- risk history
        f["fraud_alerts_90"] = wsum("alert", t, 90)
        f["disputes_90"] = wsum("dispute", t, 90)
        # --- recent context (gate inputs)
        f["new_device_7"] = wsum("dev", t, 7)
        f["cred_change_3"] = wsum("cred", t, 3)
        f["ftp_large_7"] = wsum("ftp", t, 7)
        f["ftp_large_30"] = wsum("ftp", t, 30)
        f["geo_mismatch_share_7"] = sd(wsum("geo", t, 7), n7)
        f["geo_mismatch_share_30"] = sd(wsum("geo", t, 30), n30)
        o3 = OUT[:, max(0, t - 2):t + 1]
        b3 = BAL[:, max(0, t - 2):t + 1]
        f["out_to_balance_max_3d"] = np.nanmax(o3 / (b3 + o3 + 1), 1)
        f["spike_ratio_7_vs_90"] = (o7 / np.minimum(hist, 7)) / (o90 / d90 + 1e6)
        f["max_out_7_to_income"] = np.nanmax(OUT[:, max(0, t - 6):t + 1], 1) / inc

        # --- same-day / pass-through signals (gate inputs, proposal 6.3)
        o1 = OUT[:, t]
        f["out_1"] = z(o1)
        f["n_out_1"] = z(NOUT[:, t])
        f["new_payee_1"] = z(NEWP1[:, t])
        f["distinct_payee_1"] = z(DIST1[:, t])
        f["top_payee_share_1"] = z(TOPW[:, t])
        f["ftp_large_1"] = z(FTP1[:, t])
        f["geo_mismatch_share_1"] = sd(z(GEO1[:, t]), z(NOUT[:, t]))
        f["new_device_1"] = z(DEV1[:, t])
        f["cred_change_1"] = z(CRED1[:, t])
        f["out_to_balance_1"] = z(o1) / (z(BAL[:, t]) + z(o1) + 1)
        f["out_1_vs_avg30"] = z(o1) / (o30 / d30 + 1e6)
        f["inflow_1"] = z(INF[:, t])
        f["passthrough_7"] = sd(o7, wsum("inf", t, 7))
        f["out_to_in_30"] = sd(o30, f["inflow_30"])
        for k in ("transfer_p2p", "ewallet_topup", "gambling_crypto"):
            f[f"mix_{k}_1"] = sd(C[k][:, t + 1] - C[k][:, t], z(o1))

        snap = pd.DataFrame(f)
        snap.insert(0, "customer_id", ids)
        snap.insert(1, "snap_date", WIN_START + pd.Timedelta(days=t))
        snap["hist_days"] = hist
        if mode in ("day", "sim"):
            lab = FD[:, t].astype("int8")
            snap["label_fraud_today"] = lab
            keep = ok & ((lab == 1) | (rng.random(n) < neg_keep))
            snap["weight"] = np.where(lab == 1, 1.0, 1.0 / neg_keep).astype("float32")
        else:
            lab = ((ev_idx > t) & (ev_idx <= t + HORIZON)).astype("int8")
            snap["label_fraud_90d"] = lab
            keep = ok
        snap["_fraud_type"] = np.where(lab == 1, ev_type, "")
        snap["_stealth"] = np.where(lab == 1, ev_stealth, 0)
        rows.append(snap[keep])
    if not rows:
        return None
    s = pd.concat(rows, ignore_index=True).merge(static, on="customer_id", how="left")
    num = s.select_dtypes("float64").columns
    s[num] = s[num].astype("float32")
    for col in ("gender", "region", "occupation", "segment"):
        s[col] = s[col].astype("category")
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/dev")
    ap.add_argument("--out", default="data/dev/snapshots.parquet")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--mode", choices=["snapshot", "day", "sim"], default="snapshot")
    ap.add_argument("--neg-keep", type=float, default=0.03, help="day mode: share of non-fraud days kept")
    a = ap.parse_args()
    cust = pd.read_csv(os.path.join(a.data, "customers.csv")).set_index("customer_id")
    events = pd.read_csv(os.path.join(a.data, "fraud_events.csv"))
    parts = sorted(glob.glob(os.path.join(a.data, "customer_daily", "part-*.parquet")))
    out = []
    with ProcessPoolExecutor(a.workers) as ex:
        for i, r in enumerate(ex.map(process, [(p, cust, events, a.mode, a.neg_keep) for p in parts])):
            if r is not None:
                out.append(r)
            print(f"  part {i + 1}/{len(parts)}", flush=True)
    s = pd.concat(out, ignore_index=True)
    s.to_parquet(a.out, index=False)
    lc = "label_fraud_today" if a.mode in ("day", "sim") else "label_fraud_90d"
    pos = s[lc].sum()
    print(f"rows={len(s):,} cols={s.shape[1]} positives={pos:,} ({pos / len(s):.2%}) "
          f"fraud customers={s.loc[s[lc] == 1, 'customer_id'].nunique():,}")


if __name__ == "__main__":
    main()
