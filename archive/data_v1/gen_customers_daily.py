#!/usr/bin/env python3
"""
Synthetic data generator - Dynamic Customer Limit (SAS Hackathon 2026, Team Gear Up)

Produces three tables (ALL DATA IS SYNTHETIC, no real customer data):
  customers.csv       static attributes, one row per customer
  customer_daily.csv  one row per customer per day since account opening
  _truth.csv          hidden ground truth (persona, scam/mule dates). NEVER use as a model feature.

Design idea: simulate the customer's *intended* transfers first, then apply the baseline tier cap.
"Declined by limit" therefore emerges naturally, and can be recomputed later under a dynamic limit.

Usage:  python3 gen_customers_daily.py --n 5000 --seed 42 --out out
Requires: numpy, pandas
"""
import argparse
import os
import time

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------- constants
CAPS = {1: 10e6, 2: 25e6, 3: 50e6, 4: 100e6, 5: 200e6}  # daily transfer cap per tier (Rp), proposal section 4.2
WIN_START = pd.Timestamp("2025-07-01")
WIN_END = pd.Timestamp("2026-09-30")  # 15 months: 12 dev + 3 out-of-time
RAMADAN_START = pd.Timestamp("2026-02-18")
LEBARAN = pd.Timestamp("2026-03-20")  # approx.; used only to shape seasonality
THR_DAY = pd.Timestamp("2026-03-09")  # holiday bonus paid before Lebaran
K = 10  # max regular transfers per day in the simulation matrix

# persona: share, income median/sigma (Rp/month), segment choices, tx/day, spend ratio range,
#          P(large one-off transfer per day), large transfer size range (x monthly base money),
#          mean night share, new-payee rate per transfer, tenure range (months at WIN_END), inflow style
PERSONAS = {
    "salaried":  dict(share=0.35, inc=(9e6, 0.55),  seg=["payroll"],               lam=1.2, spend=(0.65, 0.95), p_large=0.030, large=(0.3, 1.8), night=0.04, newp=0.03, tenure=(13, 120), style="payday"),
    "gig":       dict(share=0.15, inc=(4.5e6, 0.6), seg=["gig"],                   lam=1.5, spend=(0.80, 1.05), p_large=0.010, large=(0.2, 0.8), night=0.10, newp=0.08, tenure=(10, 72),  style="gig"),
    "sme":       dict(share=0.12, inc=(30e6, 0.7),  seg=["SME"],                   lam=3.5, spend=(0.70, 1.00), p_large=0.050, large=(0.3, 1.5), night=0.06, newp=0.15, tenure=(14, 120), style="biz"),
    "seasonal":  dict(share=0.08, inc=(15e6, 0.6),  seg=["SME", "retail"],         lam=2.0, spend=(0.70, 1.00), p_large=0.030, large=(0.3, 1.2), night=0.05, newp=0.10, tenure=(14, 100), style="biz"),
    "new":       dict(share=0.12, inc=(6e6, 0.7),   seg=["payroll", "gig", "retail"], lam=1.0, spend=(0.70, 1.00), p_large=0.015, large=(0.2, 0.8), night=0.05, newp=0.06, tenure=(1, 5),    style="mixed"),
    "highvalue": dict(share=0.05, inc=(90e6, 0.5),  seg=["payroll", "SME"],        lam=1.5, spend=(0.30, 0.70), p_large=0.050, large=(0.3, 1.2), night=0.03, newp=0.05, tenure=(24, 180), style="payday"),
    "dormant":   dict(share=0.04, inc=(6e6, 0.5),   seg=["retail"],                lam=1.0, spend=(0.70, 1.00), p_large=0.015, large=(0.2, 0.8), night=0.04, newp=0.04, tenure=(14, 120), style="payday"),
    "student":   dict(share=0.05, inc=(2.5e6, 0.5), seg=["retail"],                lam=0.8, spend=(0.80, 1.00), p_large=0.005, large=(0.2, 0.6), night=0.12, newp=0.07, tenure=(6, 40),   style="allowance"),
    "scam":      dict(share=0.03, inc=(8e6, 0.5),   seg=["payroll", "retail"],     lam=1.2, spend=(0.65, 0.95), p_large=0.020, large=(0.2, 1.0), night=0.04, newp=0.03, tenure=(13, 120), style="payday"),
    "mule":      dict(share=0.01, inc=(3e6, 0.4),   seg=["retail"],                lam=0.8, spend=(0.70, 1.00), p_large=0.010, large=(0.2, 0.6), night=0.06, newp=0.05, tenure=(2, 6),    style="allowance"),
}
OCCUPATION = {
    "salaried": ["Karyawan swasta", "PNS", "Pegawai BUMN", "Guru", "Tenaga kesehatan"],
    "gig": ["Driver online", "Freelancer", "Kurir", "Kreator konten"],
    "sme": ["Pemilik warung", "Pedagang", "Pemilik usaha kecil", "Kontraktor"],
    "seasonal": ["Pedagang musiman", "Penjual pakaian", "Katering"],
    "new": ["Karyawan swasta", "Freelancer", "Wiraswasta", "Mahasiswa"],
    "highvalue": ["Manajer", "Profesional", "Pengusaha"],
    "dormant": ["Karyawan swasta", "Ibu rumah tangga", "Wiraswasta"],
    "student": ["Mahasiswa"],
    "scam": ["Karyawan swasta", "Guru", "Ibu rumah tangga", "Wiraswasta"],
    "mule": ["Mahasiswa", "Wiraswasta", "Karyawan swasta", "Freelancer"],
}
REGIONS = ["DKI Jakarta", "Jawa Barat", "Jawa Tengah", "Jawa Timur", "Banten", "Sumatera Utara", "Sumatera Selatan",
           "Sulawesi Selatan", "Bali", "Kalimantan Timur", "DI Yogyakarta", "Lampung"]
REGION_P = np.array([14, 18, 12, 14, 7, 7, 4, 5, 3, 3, 3, 4], float)
REGION_P /= REGION_P.sum()
AGE_BANDS = ["18-24", "25-34", "35-44", "45-54", "55+"]
AGE_P = {  # age-band probabilities by persona (default = broad)
    "default": [0.10, 0.30, 0.28, 0.20, 0.12],
    "student": [0.95, 0.05, 0, 0, 0],
    "gig": [0.20, 0.45, 0.22, 0.10, 0.03],
    "mule": [0.50, 0.40, 0.07, 0.03, 0.0],
    "new": [0.25, 0.40, 0.20, 0.10, 0.05],
    "highvalue": [0.0, 0.15, 0.35, 0.35, 0.15],
}


# ----------------------------------------------------------------------------- customers
def make_customers(n, rng):
    names = list(PERSONAS)
    counts = {p: int(round(PERSONAS[p]["share"] * n)) for p in names}
    counts["salaried"] += n - sum(counts.values())  # absorb rounding
    persona = np.concatenate([[p] * counts[p] for p in names])
    rng.shuffle(persona)
    rows = []
    for i, p in enumerate(persona):
        cfg = PERSONAS[p]
        inc = float(np.exp(rng.normal(np.log(cfg["inc"][0]), cfg["inc"][1])))
        inc = max(1.0e6, round(inc, -5))
        ten = rng.uniform(*cfg["tenure"])  # months at WIN_END
        if p == "new":
            ten = rng.uniform(1.0, 5.5)
        open_date = (WIN_END - pd.Timedelta(days=int(ten * 30.4))).normalize()
        open_date = max(open_date, pd.Timestamp("2016-01-01"))
        # onboarding tier ("card class"): roughly income-driven, noisy; T1 forced for short tenure / mule
        if inc < 3e6:
            t = 1
        elif inc < 12e6:
            t = rng.choice([1, 2], p=[0.45, 0.55])
        elif inc < 30e6:
            t = rng.choice([2, 3], p=[0.55, 0.45])
        elif inc < 80e6:
            t = rng.choice([3, 4], p=[0.5, 0.5])
        else:
            t = rng.choice([4, 5], p=[0.55, 0.45])
        if ten < 12 or p == "mule":
            t = 1
        # KYC date: 55% refreshed within last 24 months, otherwise near account opening (stale for old accounts)
        if rng.random() < 0.55:
            kyc = max(open_date, WIN_END - pd.Timedelta(days=int(rng.uniform(30, 730))))
        else:
            kyc = open_date + pd.Timedelta(days=int(rng.uniform(0, 30)))
        age = rng.choice(AGE_BANDS, p=AGE_P.get(p, AGE_P["default"]))
        rows.append(dict(
            customer_id=f"C{i + 1:06d}",
            open_date=open_date,
            tenure_months=round((WIN_END - open_date).days / 30.4, 1),
            segment=rng.choice(cfg["seg"]),
            declared_income=inc,
            occupation=rng.choice(OCCUPATION[p]),
            region=rng.choice(REGIONS, p=REGION_P),
            age_band=age,
            gender=rng.choice(["F", "M"]),
            kyc_date=kyc,
            baseline_tier=int(t),
            shared_device_flag=int(rng.random() < 0.08),
            _persona=p,
        ))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- per-customer simulation
def sim_customer(c, rng, D, days, dow, dom, dim, month_idx):
    p = c["_persona"]
    cfg = PERSONAS[p]
    inc = c["declared_income"]
    cap = CAPS[c["baseline_tier"]]
    open_idx = max(0, (c["open_date"] - WIN_START).days)
    life = np.arange(D) - open_idx  # days since opening
    alive = life >= 0
    truth = dict(growth_rate_monthly=0.0, scam_event_date=pd.NaT, mule_start=pd.NaT, mule_end=pd.NaT,
                 dormant_from=pd.NaT, dormant_to=pd.NaT)

    # -- activity mask ----------------------------------------------------------------------
    active = alive.copy()
    mule_flag = np.zeros(D, bool)
    scam_flag = np.zeros(D, bool)
    post_scam = np.zeros(D, bool)
    if p == "dormant":
        b = D - int(rng.uniform(10, 45))  # returns 10-45 days before the end of the window
        a = max(open_idx + 30, b - int(rng.uniform(150, 240)))  # dormant for ~5-8 months
        active[a:b] = False
        truth["dormant_from"], truth["dormant_to"] = days[a], days[b]
    if p == "mule":
        ms = open_idx + int(rng.uniform(20, 55))
        me = min(D, ms + int(rng.uniform(15, 40)))
        mule_flag[ms:me] = True
        active[me:] = False  # account blocked after the mule run
        truth["mule_start"], truth["mule_end"] = days[ms], days[min(me, D - 1)]
    if p == "scam":
        ts = int(rng.uniform(max(open_idx + 40, 120), D - 25))
        scam_flag[ts:ts + int(rng.integers(1, 3))] = True
        post_scam[ts + 2:ts + 9] = True
        truth["scam_event_date"] = days[ts]

    # -- growth: ~15% of customers outgrow their baseline tier over time -----------------------
    g = rng.uniform(0.02, 0.05) if rng.random() < 0.15 else rng.normal(0.003, 0.004)
    if p in ("new", "mule", "scam"):
        g = rng.normal(0.003, 0.004)
    truth["growth_rate_monthly"] = round(float(g), 4)
    growth = (1 + g) ** (np.maximum(life, 0) / 30.0)

    # -- seasonality ---------------------------------------------------------------------------
    ramadan = (days >= RAMADAN_START) & (days <= LEBARAN)
    post_leb = (days > LEBARAN) & (days <= LEBARAN + pd.Timedelta(days=7))
    school = (month_idx == 7) & (dom >= 10) & (dom <= 25)  # July school fees
    out_season = np.where(ramadan, 1.25, 1.0) * np.where(post_leb, 1.4, 1.0)
    in_season = np.ones(D)
    if p == "seasonal":
        out_season = out_season * np.where(ramadan | post_leb, 1.7, 1.0) * np.where(school, 1.4, 1.0)
        in_season = np.where(ramadan, 2.4, 1.0) * np.where(post_leb, 1.8, 1.0) * np.where(school, 1.4, 1.0)

    # -- inflows -------------------------------------------------------------------------------
    style = cfg["style"]
    if style == "mixed":
        style = "payday" if rng.random() < 0.6 else "gig"
    infl = np.zeros(D)
    n_in = np.zeros(D, int)
    if style == "payday":
        d0 = int(rng.choice([1, 20, 25, 25, 28, 30]))
        paydom = np.minimum(d0, dim)
        pay = (dom == paydom) & active
        infl[pay] = inc * rng.uniform(0.95, 1.05, pay.sum()) * growth[pay]
        n_in[pay] = 1
        if p in ("salaried", "scam", "highvalue"):  # holiday bonus
            if rng.random() < 0.7:
                thr = (days == THR_DAY) & active
                infl[thr] += inc * rng.uniform(0.8, 1.0)
                n_in[thr] += 1
        small = (rng.random(D) < 0.12) & active
        infl[small] += np.exp(rng.normal(np.log(0.04 * inc), 0.7, small.sum()))
        n_in[small] += 1
        if p == "highvalue":
            big = (rng.random(D) < 0.015) & active
            infl[big] += inc * rng.uniform(0.5, 2.0, big.sum())
            n_in[big] += 1
    elif style == "gig":
        mmv = np.exp(rng.normal(0, 0.55, 13))  # month level multiplier, irregular income
        mmv[rng.random(13) < 0.12] *= 0.15  # occasional near-empty months
        mm = mmv[month_idx.clip(1, 12)]
        lam = 0.5
        n = rng.poisson(lam, D) * active
        mean_amt = inc / (30 * lam)
        for_i = np.nonzero(n)[0]
        for i in for_i:
            infl[i] = np.exp(rng.normal(np.log(mean_amt * mm[i]) - 0.32, 0.8, n[i])).sum() * growth[i]
        n_in = n
    elif style == "biz":
        turnover = inc * 2.0
        lam = 3.0
        wk = np.where(dow >= 5, 0.5, 1.0)
        mm = np.exp(rng.normal(0, 0.15, 13))[month_idx.clip(1, 12)]
        n = rng.poisson(lam * wk * in_season, D) * active
        mean_amt = turnover / (30 * lam * 0.85)
        for i in np.nonzero(n)[0]:
            infl[i] = np.exp(rng.normal(np.log(mean_amt * mm[i]) - 0.4, 0.9, n[i])).sum() * growth[i]
        n_in = n
    elif style == "allowance":
        d0 = int(rng.integers(1, 6))
        pay = (dom == d0) & active
        infl[pay] = inc * rng.uniform(0.9, 1.1, pay.sum())
        n_in[pay] = 1
        small = (rng.random(D) < 0.08) & active
        infl[small] += np.exp(rng.normal(np.log(0.05 * inc), 0.6, small.sum()))
        n_in[small] += 1

    if p == "mule":  # pass-through: many small inbound transfers from many sources
        for i in np.nonzero(mule_flag)[0]:
            k = int(rng.poisson(5)) + 2
            infl[i] += np.exp(rng.normal(np.log(0.45e6), 0.7, k)).sum()
            n_in[i] += k

    # -- intended outflows ---------------------------------------------------------------------
    biz = style == "biz"
    base_money = inc * (2.0 if biz else 1.0)
    spend = rng.uniform(*cfg["spend"])
    lam_n = cfg["lam"]
    dow_m = np.where(dow >= 5, 0.8, 1.0)
    lam_d = lam_n * dow_m * out_season * active * np.where(post_scam, 0.2, 1.0)
    n_reg = np.minimum(rng.poisson(lam_d), K)
    mean_txn = base_money * spend / (30 * lam_n) * growth
    sig = 0.9
    amts = np.exp(rng.normal(0, sig, (D, K))) * (mean_txn * np.exp(-sig ** 2 / 2))[:, None]
    mask = np.arange(K)[None, :] < n_reg[:, None]
    amts = amts * mask
    pl = cfg["p_large"] * np.where(school & (p in ("salaried", "scam", "highvalue")), 4.0, 1.0) * active
    large_on = rng.random(D) < pl
    large_amt = base_money * rng.uniform(cfg["large"][0], cfg["large"][1], D) * growth * large_on
    tot_int = amts.sum(1) + large_amt
    m_int = np.maximum(amts.max(1), large_amt)
    n_int = n_reg + large_on.astype(int)

    # -- night share and new-payee behaviour ----------------------------------------------------
    nb = cfg["night"]
    night_share = rng.beta(2.0, 2.0 * (1 - nb) / nb, D)
    new_rate = cfg["newp"]

    # -- sequential balance simulation ---------------------------------------------------------
    bal = inc * rng.uniform(0.3, 1.5) * (3.0 if p == "highvalue" else 1.0)
    if p == "scam":  # savers: enough balance for a drain to be worth attempting
        bal = inc * rng.uniform(4.0, 10.0)
    if p == "mule":
        bal = inc * rng.uniform(0.05, 0.2)
    out = {k: np.zeros(D) for k in ("realized", "intended", "maxatt", "end_bal", "declined_amt")}
    n_real = np.zeros(D, int)
    n_decl = np.zeros(D, int)
    night_out = np.zeros(D)
    n_newp = np.zeros(D, int)
    for d in range(open_idx, D):
        fin = infl[d]
        funds = bal + fin
        tot, m, n = tot_int[d], m_int[d], int(n_int[d])
        if mule_flag[d]:  # drain nearly everything immediately, large and rapid
            tot = funds * rng.uniform(0.9, 1.0)
            n = int(rng.integers(2, 5))
            m = tot * rng.uniform(0.5, 1.0)
        elif scam_flag[d]:  # social-engineering drain: push up against the ceiling
            tot = min(funds, cap * rng.uniform(0.7, 1.6))
            n = int(rng.integers(2, 4))
            m = tot * rng.uniform(0.5, 0.9)
        if tot > funds:
            f = funds / tot if tot > 0 else 0.0
            tot, m = funds, m * f
        avg = tot / n if n > 0 else 0.0
        realized, decl = tot, 0
        if tot > cap or m > cap:
            if m > cap:
                rest = tot - m
                decl = 1
                realized = min(rest, cap)
                extra = max(0.0, rest - cap)
                if extra > 0 and avg > 0:
                    decl += int(np.ceil(extra / avg))
            else:
                realized = cap
                decl = max(1, int(np.ceil((tot - cap) / avg))) if avg > 0 else 1
        nr = max(0, n - decl)
        if realized <= 0:
            nr = 0
        bal = bal + fin - realized
        out["realized"][d] = realized
        out["intended"][d] = tot
        out["maxatt"][d] = m
        out["end_bal"][d] = bal
        out["declined_amt"][d] = max(0.0, tot - realized)
        n_real[d], n_decl[d] = nr, decl
    # night and new-payee fields (vectorised, after the loop)
    ns = night_share.copy()
    ns[scam_flag] = rng.uniform(0.5, 0.9, scam_flag.sum())
    ns[mule_flag] = rng.uniform(0.3, 0.6, mule_flag.sum())
    night_out = out["realized"] * ns
    lam_np = new_rate * n_real
    n_newp = rng.poisson(lam_np)
    if scam_flag.any():
        n_newp[scam_flag] = rng.integers(1, 4, scam_flag.sum())
    if mule_flag.any():
        n_newp[mule_flag] = rng.poisson(2, mule_flag.sum()) + 1
    n_newp = np.minimum(n_newp, n_real)

    sl = slice(open_idx, D)
    df = pd.DataFrame({
        "customer_id": c["customer_id"],
        "date": days[sl],
        "inflow_amt": np.round(infl[sl], -3).astype("int64"),
        "n_inflow": n_in[sl].astype("int32"),
        "intended_amount": np.round(out["intended"][sl], -3).astype("int64"),
        "max_attempt_amount": np.round(out["maxatt"][sl], -3).astype("int64"),
        "outflow_amt": np.round(out["realized"][sl], -3).astype("int64"),
        "n_outflow": n_real[sl].astype("int32"),
        "n_declined_limit": n_decl[sl].astype("int32"),
        "declined_amt": np.round(out["declined_amt"][sl], -3).astype("int64"),
        "night_outflow_amt": np.round(night_out[sl], -3).astype("int64"),
        "n_new_payee": n_newp[sl].astype("int32"),
        "end_balance": np.round(out["end_bal"][sl], -3).astype("int64"),
        "tier_applied": c["baseline_tier"],
        "cap_applied": int(cap),
    })
    return df, truth


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    rng = np.random.default_rng(a.seed)
    t0 = time.time()

    cust = make_customers(a.n, rng)
    days = pd.date_range(WIN_START, WIN_END, freq="D")
    D = len(days)
    dow = days.dayofweek.values
    dom = days.day.values
    dim = days.days_in_month.values
    month_idx = days.month.values

    frames, truths = [], []
    for _, c in cust.iterrows():
        df, tr = sim_customer(c, rng, D, days, dow, dom, dim, month_idx)
        frames.append(df)
        tr.update(customer_id=c["customer_id"], persona=c["_persona"])
        truths.append(tr)
    daily = pd.concat(frames, ignore_index=True)
    truth = pd.DataFrame(truths)[["customer_id", "persona", "growth_rate_monthly", "scam_event_date",
                                  "mule_start", "mule_end", "dormant_from", "dormant_to"]]

    cust_out = cust.drop(columns=["_persona"])
    for col in ("open_date", "kyc_date"):
        cust_out[col] = cust_out[col].dt.strftime("%Y-%m-%d")
    daily["date"] = daily["date"].dt.strftime("%Y-%m-%d")
    for col in ("scam_event_date", "mule_start", "mule_end", "dormant_from", "dormant_to"):
        truth[col] = pd.to_datetime(truth[col]).dt.strftime("%Y-%m-%d")

    cust_out.to_csv(os.path.join(a.out, "customers.csv"), index=False)
    daily.to_csv(os.path.join(a.out, "customer_daily.csv"), index=False)
    truth.to_csv(os.path.join(a.out, "_truth.csv"), index=False)
    print(f"done in {time.time() - t0:.1f}s: customers={len(cust_out):,} daily rows={len(daily):,}")


if __name__ == "__main__":
    main()
