"""
Rolling-window feature computation, shared by the daily batch job and the real-time scorer.

Principle: every model feature is a function of
    (a) the customer's last 90 *daily records* ending on the as-of day (the 'daily record' = one row of customer_daily),
    (b) static profile, (c) the as-of date.
So the feature store only has to keep a 90-day ring buffer of daily records per customer (plus today's running record);
this module turns it into the exact 68-feature vector the model was trained on.  Training used the same arithmetic
(build_features.py, mode 'day'); serving/test_rolling_parity.py proves both give identical values.

Daily record conventions that MUST hold in the store (they hold in the training data):
  * one record for EVERY day since the account was opened (idle days = zeros, end_balance carried forward);
  * the as-of day's record is the day-to-date aggregate (end-of-day in the batch, partial during the day in real time).
Known quirk reproduced on purpose for parity: training measured tenure_months / hist_days from max(open_date, 2025-07-01)
(observation-window start), not from the true account-opening date.  Retrain with true tenure before real use.
"""
import numpy as np
import pandas as pd

WIN_START = pd.Timestamp("2025-07-01")
W = 90                                           # ring-buffer length (days)
CATS = ["ecommerce", "food", "transport", "bills", "transfer_p2p", "ewallet_topup", "gambling_crypto"]
AGE_ORD = {"18-24": 0, "25-34": 1, "35-44": 2, "45-54": 3, "55+": 4}
DAILY_COLS = (["inflow_amt", "outflow_amt", "n_outflow", "night_outflow_amt", "n_new_payee", "n_distinct_payee", "top_payee_share",
               "ftp_large", "n_geo_mismatch", "new_device", "cred_change", "end_balance", "n_fraud_alert", "n_dispute"]
              + [f"out_{c}" for c in CATS])
STATIC_COLS = ["declared_income", "age_band", "gender", "region", "occupation", "segment", "shared_device_flag", "open_date", "kyc_date"]


def to_arrays(hist, customer_ids, as_of):
    """hist: DataFrame [customer_id, date, DAILY_COLS...]; one request per (customer_ids[i], as_of[i]) - customers may repeat.
    Returns dict col -> (n, 90) float64 aligned so that column 89 = as-of day (NaN = no record)."""
    n = len(customer_ids)
    req = pd.DataFrame({"rid": np.arange(n), "customer_id": list(customer_ids), "as_of": pd.to_datetime(list(as_of))})
    m = req.merge(hist[["customer_id", "date"] + DAILY_COLS], on="customer_id")
    pos = ((W - 1) - (m.as_of - m.date).dt.days).to_numpy()
    m, pos = m[(pos >= 0) & (pos < W)], pos[(pos >= 0) & (pos < W)]
    out = {}
    for c in DAILY_COLS:
        arr = np.full((n, W), np.nan)
        arr[m.rid.to_numpy(), pos] = m[c].to_numpy(np.float64)
        out[c] = arr
    return out


def compute_features(H, static, as_of):
    """H: dict from to_arrays; static: DataFrame (n rows, STATIC_COLS); as_of: n dates.  Returns DataFrame of the 68 model features."""
    n = len(static)
    T = W - 1
    as_of = pd.to_datetime(pd.Series(as_of)).reset_index(drop=True)
    inc = static["declared_income"].to_numpy(float)
    t_abs = (as_of - WIN_START).dt.days.to_numpy()
    open_idx = np.maximum(0, (pd.to_datetime(static["open_date"]) - WIN_START).dt.days.to_numpy())
    hist = t_abs - open_idx + 1
    kyc = pd.to_datetime(static["kyc_date"]).reset_index(drop=True)

    z = np.nan_to_num
    cs = lambda a: np.concatenate([np.zeros((n, 1)), np.cumsum(z(a), axis=1)], axis=1)
    OUT, INF, BAL, NOUT = H["outflow_amt"], H["inflow_amt"], H["end_balance"], H["n_outflow"]
    C = dict(out=cs(OUT), inf=cs(INF), nout=cs(NOUT), night=cs(H["night_outflow_amt"]), newp=cs(H["n_new_payee"]), dist=cs(H["n_distinct_payee"]),
             ftp=cs(H["ftp_large"]), geo=cs(H["n_geo_mismatch"]), dev=cs(H["new_device"]), cred=cs(H["cred_change"]))
    TOPW = H["top_payee_share"]
    ACT = (z(NOUT) > 0).astype(float)
    C["top"], C["act"] = cs(np.where(ACT > 0, z(TOPW), 0.0)), cs(ACT)
    for k in CATS:
        C[k] = cs(H[f"out_{k}"])

    wsum = lambda k, w: C[k][:, T + 1] - C[k][:, T + 1 - w]
    sd = lambda a, b: np.where(b > 0, a / np.where(b > 0, b, 1), 0.0)

    o90, o30, o7 = wsum("out", 90), wsum("out", 30), wsum("out", 7)
    d90, d30 = np.minimum(hist, 90), np.minimum(hist, 30)
    f = {}
    f["tenure_months"] = (t_abs - open_idx) / 30.4
    f["kyc_age_months"] = (as_of - kyc).dt.days.to_numpy() / 30.4
    wins = []
    for k in range(3):
        v = C["inf"][:, T + 1 - 30 * k] - C["inf"][:, T + 1 - 30 * (k + 1)]
        wins.append(np.where(hist >= 30 * (k + 1), v, np.nan))
    Wn = np.stack(wins, 1)
    f["inflow_cv_90"] = np.where((~np.isnan(Wn)).sum(1) >= 2, np.nanstd(Wn, 1) / (np.nanmean(Wn, 1) + 1), np.nan)
    f["regular_inflow_windows_90"] = np.nansum(Wn >= 0.5 * inc[:, None], 1)
    f["inflow_30"] = wsum("inf", 30)
    f["min_balance_90"] = np.nanmin(BAL, 1)
    f["days_low_balance_90"] = np.nansum(BAL < 0.1 * inc[:, None], 1)
    f["balance"] = BAL[:, T]
    f["balance_to_income"] = BAL[:, T] / inc
    f["out_30"], f["out_7"] = o30, o7
    f["avg_out_90"] = o90 / d90
    f["p95_out_30"] = np.nanpercentile(OUT[:, T - 29:], 95, axis=1)
    f["p95_out_90"] = np.nanpercentile(OUT, 95, axis=1)
    prev30 = C["out"][:, T + 1 - 30] - C["out"][:, T + 1 - 60]
    f["out_trend_30"] = o30 / (prev30 + 1e6)
    f["out_to_income_30"] = o30 / inc
    n30, n7 = wsum("nout", 30), wsum("nout", 7)
    f["n_out_7"] = n7
    f["new_payee_rate_30"] = sd(wsum("newp", 30), n30)
    f["new_payee_cnt_7"] = wsum("newp", 7)
    f["night_share_30"] = sd(wsum("night", 30), o30)
    f["night_share_7"] = sd(wsum("night", 7), o7)
    f["night_share_1"] = sd(wsum("night", 1), wsum("out", 1))
    f["payee_concentration_90"] = sd(wsum("top", 90), wsum("act", 90))
    f["distinct_payee_30"] = wsum("dist", 30)
    f["distinct_per_tx_30"] = sd(wsum("dist", 30), n30)
    f["device_cnt_90"] = 1 + wsum("dev", 90)
    sh30 = np.stack([sd(wsum(k, 30), o30) for k in CATS], 1)
    lo, hi_ = T + 1 - 90, T + 1 - 30
    sh60p = np.stack([sd(C[k][:, hi_] - C[k][:, lo], C["out"][:, hi_] - C["out"][:, lo]) for k in CATS], 1)
    for j, k in enumerate(CATS):
        f[f"mix_{k}_30"] = sh30[:, j]
    f["mix_shift_30_vs_prev60"] = np.abs(sh30 - sh60p).sum(1)
    f["new_device_7"] = wsum("dev", 7)
    f["cred_change_3"] = wsum("cred", 3)
    f["ftp_large_7"] = wsum("ftp", 7)
    f["ftp_large_30"] = wsum("ftp", 30)
    f["geo_mismatch_share_7"] = sd(wsum("geo", 7), n7)
    f["geo_mismatch_share_30"] = sd(wsum("geo", 30), n30)
    o3, b3 = OUT[:, T - 2:], BAL[:, T - 2:]
    f["out_to_balance_max_3d"] = np.nanmax(o3 / (b3 + o3 + 1), 1)
    f["spike_ratio_7_vs_90"] = (o7 / np.minimum(hist, 7)) / (o90 / d90 + 1e6)
    f["max_out_7_to_income"] = np.nanmax(OUT[:, T - 6:], 1) / inc
    o1 = OUT[:, T]
    f["out_1"] = z(o1)
    f["n_out_1"] = z(NOUT[:, T])
    f["new_payee_1"] = z(H["n_new_payee"][:, T])
    f["distinct_payee_1"] = z(H["n_distinct_payee"][:, T])
    f["top_payee_share_1"] = z(TOPW[:, T])
    f["ftp_large_1"] = z(H["ftp_large"][:, T])
    f["geo_mismatch_share_1"] = sd(z(H["n_geo_mismatch"][:, T]), z(NOUT[:, T]))
    f["new_device_1"] = z(H["new_device"][:, T])
    f["cred_change_1"] = z(H["cred_change"][:, T])
    f["out_to_balance_1"] = z(o1) / (z(BAL[:, T]) + z(o1) + 1)
    f["out_1_vs_avg30"] = z(o1) / (o30 / d30 + 1e6)
    f["inflow_1"] = z(INF[:, T])
    f["passthrough_7"] = sd(o7, wsum("inf", 7))
    f["out_to_in_30"] = sd(o30, f["inflow_30"])
    for k in ("transfer_p2p", "ewallet_topup", "gambling_crypto"):
        f[f"mix_{k}_1"] = sd(C[k][:, T + 1] - C[k][:, T], z(o1))
    f["hist_days"] = hist
    f["declared_income"] = inc
    f["age_ord"] = static["age_band"].map(AGE_ORD).to_numpy()
    f["shared_device_flag"] = static["shared_device_flag"].to_numpy()
    out = pd.DataFrame(f)
    for c in ("gender", "region", "occupation", "segment"):
        out[c] = static[c].to_numpy()
    num = out.select_dtypes("float64").columns
    out[num] = out[num].astype("float32")
    return out
