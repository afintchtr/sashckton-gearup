#!/usr/bin/env python3
"""
Synthetic data generator v2 - Dynamic Customer Limit + fraud/mule gate (SAS Hackathon 2026, Team Gear Up)

ALL DATA IS SYNTHETIC. Extends gen_customers_daily.py so it covers what the proposal (sections 5-6) needs:

  customers.csv          static profile, one row per customer
  customer_daily/        parquet parts, one row per customer per day since account opening
  fraud_events.csv       fraud ground truth with confirmation (label source, proposal 5.3)
  _truth.csv             hidden persona / dates. NEVER use as a model feature.

What is new vs v1
  * scale (default 50,000 customers), written in parallel chunks as parquet
  * three fraud typologies: scam victim, mule, account takeover (ATO) - each with a "stealth" variant
    (~35%) whose signals are weak, so a model cannot separate them trivially
  * proposal 5.2 / 6.3 signals: device, credential change, payee diversity / concentration, merchant mix,
    geo mismatch, fraud alerts, disputes, first-time payee on a large amount
  * hard negatives: legit phone changes, travel, night owls, gambling-heavy customers, big one-off transfers
    to new payees, false alerts
  * profile only weakly related to fraud (age / region tilt), tenure no longer a shortcut for mules
  * fraud_events with a 'reported' flag: ~10% of fraud is never confirmed (label noise)

Usage:  python3 gen_v2.py --n 50000 --seed 7 --out ../data/dev
Requires: numpy, pandas, pyarrow
"""
import argparse
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------- constants
CAPS = {1: 10e6, 2: 25e6, 3: 50e6, 4: 100e6, 5: 200e6}  # daily transfer cap per tier (Rp), proposal 4.2
WIN_START = pd.Timestamp("2025-07-01")
WIN_END = pd.Timestamp("2026-09-30")
RAMADAN_START = pd.Timestamp("2026-02-18")
LEBARAN = pd.Timestamp("2026-03-20")
THR_DAY = pd.Timestamp("2026-03-09")
EVENT_LO = pd.Timestamp("2025-10-15")  # earliest fraud event: leaves >= 90 d of feature history
K = 10
CATS = ["ecommerce", "food", "transport", "bills", "transfer_p2p", "ewallet_topup", "gambling_crypto"]
CAT_BASE = np.array([0.18, 0.22, 0.12, 0.18, 0.22, 0.07, 0.01])
FRAUD_PERSONAS = ("scam", "mule", "ato")
ID_PREFIX = "C"  # set via --prefix; keeps customer ids of different datasets from colliding
STEALTH_RATE = 0.45
ANOM_RATE = 1 / 25  # legit 'look-alike' days per customer-day (travel, big purchase, late-night transfer, new payee...)
REPORT_RATE = 0.90

# persona: share, income (median, sigma), segments, tx/day, spend ratio, P(large one-off/day), large size (x base),
#          night share mean, new-payee rate per transfer, tenure range (months at WIN_END), inflow style
PERSONAS = {
    "salaried":  dict(share=0.357, inc=(9e6, 0.55),  seg=["payroll"],               lam=1.2, spend=(0.65, 0.95), p_large=0.030, large=(0.3, 1.8), night=0.04, newp=0.03, tenure=(13, 120), style="payday"),
    "gig":       dict(share=0.150, inc=(4.5e6, 0.6), seg=["gig"],                   lam=1.5, spend=(0.80, 1.05), p_large=0.010, large=(0.2, 0.8), night=0.10, newp=0.08, tenure=(10, 72),  style="gig"),
    "sme":       dict(share=0.120, inc=(30e6, 0.7),  seg=["SME"],                   lam=3.5, spend=(0.70, 1.00), p_large=0.050, large=(0.3, 1.5), night=0.06, newp=0.15, tenure=(14, 120), style="biz"),
    "seasonal":  dict(share=0.080, inc=(15e6, 0.6),  seg=["SME", "retail"],         lam=2.0, spend=(0.70, 1.00), p_large=0.030, large=(0.3, 1.2), night=0.05, newp=0.10, tenure=(14, 100), style="biz"),
    "new":       dict(share=0.120, inc=(6e6, 0.7),   seg=["payroll", "gig", "retail"], lam=1.0, spend=(0.70, 1.00), p_large=0.015, large=(0.2, 0.8), night=0.05, newp=0.06, tenure=(1, 5),    style="mixed"),
    "highvalue": dict(share=0.050, inc=(90e6, 0.5),  seg=["payroll", "SME"],        lam=1.5, spend=(0.30, 0.70), p_large=0.050, large=(0.3, 1.2), night=0.03, newp=0.05, tenure=(24, 180), style="payday"),
    "dormant":   dict(share=0.040, inc=(6e6, 0.5),   seg=["retail"],                lam=1.0, spend=(0.70, 1.00), p_large=0.015, large=(0.2, 0.8), night=0.04, newp=0.04, tenure=(14, 120), style="payday"),
    "student":   dict(share=0.050, inc=(2.5e6, 0.5), seg=["retail"],                lam=0.8, spend=(0.80, 1.00), p_large=0.005, large=(0.2, 0.6), night=0.12, newp=0.07, tenure=(6, 40),   style="allowance"),
    "scam":      dict(share=0.020, inc=(8e6, 0.5),   seg=["payroll", "retail"],     lam=1.2, spend=(0.65, 0.95), p_large=0.020, large=(0.2, 1.0), night=0.04, newp=0.03, tenure=(13, 120), style="payday"),
    "mule":      dict(share=0.005, inc=(3e6, 0.5),   seg=["retail"],                lam=0.8, spend=(0.70, 1.00), p_large=0.010, large=(0.2, 0.6), night=0.06, newp=0.05, tenure=(4, 100),  style="allowance"),
    "ato":       dict(share=0.008, inc=(9e6, 0.55),  seg=["payroll", "retail"],     lam=1.2, spend=(0.65, 0.95), p_large=0.030, large=(0.3, 1.5), night=0.04, newp=0.03, tenure=(13, 120), style="payday"),
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
    "scam": ["Karyawan swasta", "Guru", "Ibu rumah tangga", "Wiraswasta", "Pegawai BUMN"],
    "mule": ["Mahasiswa", "Wiraswasta", "Karyawan swasta", "Freelancer", "Kurir"],
    "ato": ["Karyawan swasta", "PNS", "Pegawai BUMN", "Guru", "Manajer", "Profesional"],
}
REGIONS = ["DKI Jakarta", "Jawa Barat", "Jawa Tengah", "Jawa Timur", "Banten", "Sumatera Utara", "Sumatera Selatan",
           "Sulawesi Selatan", "Bali", "Kalimantan Timur", "DI Yogyakarta", "Lampung"]
REGION_P = np.array([14, 18, 12, 14, 7, 7, 4, 5, 3, 3, 3, 4], float)
REGION_P /= REGION_P.sum()
# mild geographic tilt of fraud exposure (kept weak on purpose: profile must not be a shortcut)
REGION_TILT = {
    "scam": {"DKI Jakarta": 1.4, "Jawa Barat": 1.3, "Banten": 1.3},
    "ato": {"DKI Jakarta": 1.3, "Banten": 1.2, "Jawa Barat": 1.2},
    "mule": {"Sumatera Utara": 1.4, "Jawa Timur": 1.3, "Lampung": 1.3},
}
# fraud customers borrow profile attributes (income, segment, occupation) from a legit "host" persona so the
# static profile cannot identify them. Behaviour still follows the fraud persona's own config.
HOST = {
    "scam": {"salaried": 0.60, "sme": 0.12, "highvalue": 0.06, "seasonal": 0.06, "gig": 0.08, "dormant": 0.08},
    "ato":  {"salaried": 0.60, "sme": 0.12, "highvalue": 0.06, "seasonal": 0.06, "gig": 0.08, "dormant": 0.08},
    "mule": {"student": 0.25, "gig": 0.25, "new": 0.20, "salaried": 0.30},
}
AGE_BANDS = ["18-24", "25-34", "35-44", "45-54", "55+"]
AGE_P = {
    "default": [0.10, 0.30, 0.28, 0.20, 0.12],
    "student": [0.95, 0.05, 0, 0, 0],
    "gig": [0.20, 0.45, 0.22, 0.10, 0.03],
    "mule": [0.35, 0.40, 0.15, 0.07, 0.03],
    "scam": [0.06, 0.18, 0.22, 0.26, 0.28],
    "new": [0.25, 0.40, 0.20, 0.10, 0.05],
    "highvalue": [0.0, 0.15, 0.35, 0.35, 0.15],
}


# ----------------------------------------------------------------------------- customers
def make_customers(n, rng):
    names = list(PERSONAS)
    counts = {p: int(round(PERSONAS[p]["share"] * n)) for p in names}
    counts["salaried"] += n - sum(counts.values())
    persona = np.concatenate([[p] * counts[p] for p in names])
    rng.shuffle(persona)
    rows = []
    for i, p in enumerate(persona):
        cfg = PERSONAS[p]
        hp = HOST.get(p)
        h = rng.choice(list(hp), p=list(hp.values())) if hp else p
        hcfg = PERSONAS[h]
        inc = max(1.0e6, round(float(np.exp(rng.normal(np.log(hcfg["inc"][0]), hcfg["inc"][1]))), -5))
        ten = rng.uniform(1.0, 5.5) if p == "new" else rng.uniform(*cfg["tenure"])
        open_date = max((WIN_END - pd.Timedelta(days=int(ten * 30.4))).normalize(), pd.Timestamp("2016-01-01"))
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
        if ten < 12:
            t = 1
        if rng.random() < 0.55:
            kyc = max(open_date, WIN_END - pd.Timedelta(days=int(rng.uniform(30, 730))))
        else:
            kyc = open_date + pd.Timedelta(days=int(rng.uniform(0, 30)))
        rp = REGION_P.copy()
        for k, v in REGION_TILT.get(p, {}).items():
            rp[REGIONS.index(k)] *= v
        rp /= rp.sum()
        rows.append(dict(
            customer_id=f"{ID_PREFIX}{i + 1:06d}",
            open_date=open_date,
            tenure_months=round((WIN_END - open_date).days / 30.4, 1),
            segment=rng.choice(hcfg["seg"]),
            declared_income=inc,
            occupation=rng.choice(OCCUPATION[h]),
            region=rng.choice(REGIONS, p=rp),
            age_band=rng.choice(AGE_BANDS, p=AGE_P.get(p, AGE_P["default"])),
            gender=rng.choice(["F", "M"]),
            kyc_date=kyc,
            baseline_tier=int(t),
            shared_device_flag=int(rng.random() < 0.08),
            _persona=p,
            _host=h,
            _stealth=int(p in FRAUD_PERSONAS and rng.random() < STEALTH_RATE),
            _judol=int(rng.random() < 0.03),      # legit but gambling-heavy (hard negative)
            _nightowl=int(rng.random() < 0.06),   # legit night-time spender (hard negative)
        ))
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- per-customer simulation
def sim_customer(c, rng, D, days, dow, dom, dim, month_idx, ev_lo_idx):
    p = c["_persona"]
    host = c["_host"]
    cfg = PERSONAS[host]  # baseline behaviour = the host persona; fraud only adds overlays
    inc = c["declared_income"]
    cap = CAPS[c["baseline_tier"]]
    stealth = bool(c["_stealth"])
    open_idx = max(0, (c["open_date"] - WIN_START).days)
    life = np.arange(D) - open_idx
    alive = life >= 0
    events = []
    truth = dict(growth_rate_monthly=0.0, fraud_start=pd.NaT, fraud_end=pd.NaT, dormant_from=pd.NaT, dormant_to=pd.NaT)

    active = alive.copy()
    mule_flag = np.zeros(D, bool)
    scam_flag = np.zeros(D, bool)
    ato_flag = np.zeros(D, bool)
    post_event = np.zeros(D, bool)
    new_dev = np.zeros(D, int)
    cred = np.zeros(D, int)
    alert = np.zeros(D, int)
    dispute = np.zeros(D, int)

    lo = max(open_idx + 40, ev_lo_idx)
    hi = D - 25
    ts = ms = me = None
    if p == "dormant":
        b = D - int(rng.uniform(10, 45))
        a = max(open_idx + 30, b - int(rng.uniform(150, 240)))
        active[a:b] = False
        truth["dormant_from"], truth["dormant_to"] = days[a], days[b]
    if p == "mule":
        ms = int(rng.uniform(lo, max(lo + 1, hi - 15)))
        me = min(D, ms + int(rng.uniform(6, 20)))
        mule_flag[ms:me] = True
        active[me:] = False
        L = me - ms
        ev_s, ev_e = ms, me - 1
        if rng.random() < (0.3 if stealth else 0.55):  # account handed over: new device at start
            new_dev[ms] = 1
            if rng.random() < 0.5:
                cred[ms] = 1
    if p == "scam":
        ts = int(rng.uniform(lo, hi))
        L = int(rng.integers(1, 4))
        scam_flag[ts:ts + L] = True
        post_event[ts + L:ts + L + 7] = True
        ev_s, ev_e = ts, ts + L - 1
    if p == "ato":
        ts = int(rng.uniform(lo, hi))
        L = int(rng.integers(1, 3))
        ato_flag[ts:ts + L] = True
        post_event[ts + L:ts + L + 7] = True
        ev_s, ev_e = ts, ts + L - 1
        off = int(rng.integers(0, 2))
        new_dev[max(0, ts - off)] = 1
        if rng.random() < (0.25 if stealth else 0.85):
            cred[max(0, ts - int(rng.integers(0, 2)))] = 1
    if p in FRAUD_PERSONAS:
        reported = bool(rng.random() < REPORT_RATE)
        events.append(dict(customer_id=c["customer_id"], fraud_type=p, event_start=days[ev_s], event_end=days[ev_e],
                           stealth=int(stealth), reported=int(reported),
                           confirm_date=days[min(D - 1, ev_e + int(rng.uniform(3, 30)))] if reported else pd.NaT))
        truth["fraud_start"], truth["fraud_end"] = days[ev_s], days[ev_e]
        # alerts / disputes follow the event (lagged -> only usable as history, never as a same-day feature)
        if rng.random() < (0.2 if stealth else 0.55):
            alert[min(D - 1, ev_s + int(rng.integers(0, 3)))] += 1
        if reported and rng.random() < 0.75:
            dispute[min(D - 1, ev_e + int(rng.integers(1, 8)))] += 1

    # precursor windows (weak, partly invisible for stealth fraud): scam grooming, mule warm-up
    precursor = np.zeros(D, bool)
    if p == "scam":
        precursor[max(0, ts - int(rng.uniform(7, 30))):ts] = True
    if p == "mule":
        precursor[max(0, ms - 14):ms] = True
    pre_w = 0.5 if stealth else 1.0

    # legit noise on every customer: phone replaced, credential reset, false alerts, rare disputes
    pdev = 1 / (180 if c["shared_device_flag"] else 450)
    nd = (rng.random(D) < pdev) & active
    new_dev = np.maximum(new_dev, nd.astype(int))
    cr = ((rng.random(D) < 1 / 700) | (nd & (rng.random(D) < 0.2))) & active
    cred = np.maximum(cred, cr.astype(int))
    alert += ((rng.random(D) < 1 / 900) & active).astype(int)
    dispute += ((rng.random(D) < 1 / 2000) & active).astype(int)

    # growth
    g = rng.uniform(0.02, 0.05) if rng.random() < 0.15 else rng.normal(0.003, 0.004)
    if host == "new" or p == "mule":
        g = rng.normal(0.003, 0.004)
    truth["growth_rate_monthly"] = round(float(g), 4)
    growth = (1 + g) ** (np.maximum(life, 0) / 30.0)

    # seasonality
    ramadan = (days >= RAMADAN_START) & (days <= LEBARAN)
    post_leb = (days > LEBARAN) & (days <= LEBARAN + pd.Timedelta(days=7))
    school = (month_idx == 7) & (dom >= 10) & (dom <= 25)
    out_season = np.where(ramadan, 1.25, 1.0) * np.where(post_leb, 1.4, 1.0)
    in_season = np.ones(D)
    if host == "seasonal":
        out_season = out_season * np.where(ramadan | post_leb, 1.7, 1.0) * np.where(school, 1.4, 1.0)
        in_season = np.where(ramadan, 2.4, 1.0) * np.where(post_leb, 1.8, 1.0) * np.where(school, 1.4, 1.0)

    # inflows
    style = cfg["style"]
    if style == "mixed":
        style = "payday" if rng.random() < 0.6 else "gig"
    infl = np.zeros(D)
    n_in = np.zeros(D, int)
    if style == "payday":
        d0 = int(rng.choice([1, 20, 25, 25, 28, 30]))
        pay = (dom == np.minimum(d0, dim)) & active
        infl[pay] = inc * rng.uniform(0.95, 1.05, pay.sum()) * growth[pay]
        n_in[pay] = 1
        if host in ("salaried", "highvalue") and rng.random() < 0.7:
            thr = (days == THR_DAY) & active
            infl[thr] += inc * rng.uniform(0.8, 1.0)
            n_in[thr] += 1
        small = (rng.random(D) < 0.12) & active
        infl[small] += np.exp(rng.normal(np.log(0.04 * inc), 0.7, small.sum()))
        n_in[small] += 1
        if host == "highvalue":
            big = (rng.random(D) < 0.015) & active
            infl[big] += inc * rng.uniform(0.5, 2.0, big.sum())
            n_in[big] += 1
    elif style == "gig":
        mmv = np.exp(rng.normal(0, 0.55, 13))
        mmv[rng.random(13) < 0.12] *= 0.15
        mm = mmv[month_idx.clip(1, 12)]
        lam = 0.5
        n = rng.poisson(lam, D) * active
        mean_amt = inc / (30 * lam)
        for i in np.nonzero(n)[0]:
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

    if p == "mule":  # pass-through inflow from many sources; stealth mules receive fewer, larger transfers
        for i in np.nonzero(mule_flag)[0]:
            k = int(rng.poisson(2 if stealth else 3)) + 2
            infl[i] += np.exp(rng.normal(np.log(0.6e6 if stealth else 0.35e6), 0.7, k)).sum()
            n_in[i] += k

    # intended outflows
    biz = style == "biz"
    base_money = inc * (2.0 if biz else 1.0)
    spend = rng.uniform(*cfg["spend"])
    lam_n = cfg["lam"]
    dow_m = np.where(dow >= 5, 0.8, 1.0)
    lam_d = lam_n * dow_m * out_season * active * np.where(post_event, 0.2, 1.0) * np.where(precursor & (p == "mule"), 1 + 0.8 * pre_w, 1.0)
    n_reg = np.minimum(rng.poisson(lam_d), K)
    mean_txn = base_money * spend / (30 * lam_n) * growth
    sig = 0.9
    amts = np.exp(rng.normal(0, sig, (D, K))) * (mean_txn * np.exp(-sig ** 2 / 2))[:, None]
    amts = amts * (np.arange(K)[None, :] < n_reg[:, None])
    pl = cfg["p_large"] * np.where(school & (host in ("salaried", "highvalue")), 4.0, 1.0) * active
    large_on = rng.random(D) < pl
    large_amt = base_money * rng.uniform(cfg["large"][0], cfg["large"][1], D) * growth * large_on
    anom = (rng.random(D) < ANOM_RATE) & active & ~(mule_flag | scam_flag | ato_flag | post_event)
    aa = anom & (rng.random(D) < 0.5)
    big = base_money * rng.uniform(0.2, 0.8, D) * aa
    # legit windfall (loan disbursement, property sale, bonus) moved on within 1-3 days: looks like a mule pass-through
    for d_ in np.nonzero((rng.random(D) < 1 / 200) & active & ~(mule_flag | scam_flag | ato_flag | post_event))[0]:
        infl[d_] += inc * rng.uniform(1.0, 4.0)
        n_in[d_] += 1
        for k_ in range(1, int(rng.integers(2, 4))):
            if d_ + k_ < D:
                big[d_ + k_] += inc * rng.uniform(0.6, 2.0)
                aa[d_ + k_] = True
    quiet = mule_flag & (rng.random(D) < 0.5)  # lull days inside a mule run: label stays fraud, behaviour looks normal
    tot_int = amts.sum(1) + large_amt + big
    m_int = np.maximum(np.maximum(amts.max(1), large_amt), big)
    n_int = n_reg + large_on.astype(int) + aa.astype(int)

    # night share (night-owl customers are legit hard negatives)
    nb = 0.22 if c["_nightowl"] else cfg["night"]
    night_share = rng.beta(2.0, 2.0 * (1 - nb) / nb, D)
    if p == "scam":
        night_share = np.where(precursor, np.maximum(night_share, rng.uniform(0.08, 0.3, D) * pre_w), night_share)

    # balance simulation
    bal = inc * rng.uniform(0.3, 1.5) * (3.0 if host == "highvalue" else 1.0)
    if p == "mule":
        bal *= 0.3
    out = {k: np.zeros(D) for k in ("realized", "intended", "maxatt", "end_bal", "declined_amt")}
    n_real = np.zeros(D, int)
    n_decl = np.zeros(D, int)
    for d in range(open_idx, D):
        fin = infl[d]
        funds = bal + fin
        tot, m, n = tot_int[d], m_int[d], int(n_int[d])
        if mule_flag[d] and not quiet[d]:
            tot = funds * (rng.uniform(0.35, 0.6) if stealth else rng.uniform(0.6, 0.9))
            n = int(rng.integers(1, 3) if stealth else rng.integers(2, 5))
            m = tot * rng.uniform(0.5, 1.0)
        elif scam_flag[d]:
            tot = min(funds, cap * (rng.uniform(0.25, 0.7) if stealth else rng.uniform(0.7, 1.6)))
            n = int(rng.integers(1, 3) if stealth else rng.integers(2, 4))
            m = tot * rng.uniform(0.5, 0.9)
        elif ato_flag[d]:
            tot = min(funds * (rng.uniform(0.3, 0.6) if stealth else rng.uniform(0.55, 0.95)),
                      cap * (rng.uniform(0.2, 0.6) if stealth else rng.uniform(0.6, 1.4)))
            n = int(rng.integers(1, 4))
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

    lab_day = mule_flag | scam_flag | ato_flag  # label days (incl. mule lulls)
    mule_flag = mule_flag & ~quiet
    fday = mule_flag | scam_flag | ato_flag     # days with active fraud behaviour
    strong = fday & (not stealth)             # days where signature signals are switched on

    # night share overrides
    ns = night_share.copy()
    if p == "scam":
        ns[scam_flag] = rng.uniform(0.02, 0.2, scam_flag.sum()) if stealth else rng.uniform(0.3, 0.85, scam_flag.sum())
    if p == "ato":
        ns[ato_flag] = rng.uniform(0.05, 0.3, ato_flag.sum()) if stealth else rng.uniform(0.35, 0.9, ato_flag.sum())
    if p == "mule":
        ns[mule_flag] = rng.uniform(0.02, 0.15, mule_flag.sum()) if stealth else rng.uniform(0.15, 0.5, mule_flag.sum())
    a_night = anom & (rng.random(D) < 0.6)
    ns[a_night] = rng.uniform(0.3, 0.8, a_night.sum())
    night_out = out["realized"] * ns

    # new payees: legit large transfers (school fees, car, rent) often go to a new payee
    n_newp = rng.poisson(cfg["newp"] * n_real * np.where(precursor, 1 + 2.0 * pre_w, 1.0))
    n_newp += ((rng.random(D) < 0.25) & large_on & (n_real > 0)).astype(int)
    if p == "scam":
        n_newp[scam_flag] = rng.integers(0, 2, scam_flag.sum()) if stealth else rng.integers(1, 3, scam_flag.sum())
    if p == "ato":
        n_newp[ato_flag] = rng.integers(0, 2, ato_flag.sum()) if stealth else rng.integers(1, 3, ato_flag.sum())
    if p == "mule":
        n_newp[mule_flag] = (rng.poisson(0.7, mule_flag.sum()) if stealth else rng.poisson(2, mule_flag.sum()) + 1)
    n_newp[anom & (rng.random(D) < 0.6)] += rng.integers(1, 3)
    n_newp = np.minimum(n_newp, n_real)
    ftp_large = ((n_newp > 0) & (out["maxatt"] > 0.5 * cap)).astype(int)

    # payee diversity / concentration
    dist = np.where(n_real <= 1, n_real, 1 + rng.binomial(np.maximum(n_real - 1, 0), 0.55))
    dist = np.minimum(np.maximum(dist, n_newp), n_real)
    if p in ("mule", "ato"):
        dist = np.where(fday, n_real, dist)
    top = np.where(dist <= 1, 1.0, np.clip(1 / np.maximum(dist, 1) + rng.beta(2, 3, D) * (1 - 1 / np.maximum(dist, 1)), 0, 1))
    top = np.where(n_real > 0, top, 0.0)
    if p == "scam":
        top[scam_flag] = rng.uniform(0.45, 0.8, scam_flag.sum()) if stealth else rng.uniform(0.7, 0.98, scam_flag.sum())

    # geo mismatch: legit travel periods are hard negatives
    trip = np.zeros(D, bool)
    for _ in range(rng.poisson(1.5)):
        s0 = int(rng.integers(0, D))
        trip[s0:s0 + int(rng.integers(3, 11))] = True
    pg = np.where(trip, 0.7, 0.02)
    if p == "ato":
        pg = np.where(ato_flag, 0.3 if stealth else 0.9, pg)
    if p == "mule":
        pg = np.where(mule_flag, 0.3, pg)
    pg = np.where(anom & (rng.random(D) < 0.4), 0.7, pg)
    n_geo = rng.binomial(n_real, pg)

    # merchant mix
    w = rng.dirichlet(CAT_BASE * 25)
    if c["_judol"]:
        w = w * 0.8
        w[6] += 0.2
    mix = rng.dirichlet(w * 40 + 1e-3, size=D)
    if strong.any():
        mix[strong] = rng.dirichlet(np.array([0.1, 0.1, 0.1, 0.1, 6.0, 3.0, 1.0]), size=int(strong.sum()))
    ew = anom & (rng.random(D) < 0.4)
    if ew.any():
        mix[ew] = rng.dirichlet(np.array([0.3, 0.3, 0.2, 0.2, 4.0, 2.0, 0.5]), size=int(ew.sum()))
    new_dev = np.maximum(new_dev, (anom & (rng.random(D) < 0.15)).astype(int))
    realized = out["realized"]
    cat_amt = np.round(realized[:, None] * mix, -3).astype("int64")

    sl = slice(open_idx, D)
    df = pd.DataFrame({
        "customer_id": c["customer_id"],
        "date": days[sl],
        "inflow_amt": np.round(infl[sl], -3).astype("int64"),
        "n_inflow": n_in[sl].astype("int32"),
        "intended_amount": np.round(out["intended"][sl], -3).astype("int64"),
        "max_attempt_amount": np.round(out["maxatt"][sl], -3).astype("int64"),
        "outflow_amt": np.round(realized[sl], -3).astype("int64"),
        "n_outflow": n_real[sl].astype("int32"),
        "n_declined_limit": n_decl[sl].astype("int32"),
        "declined_amt": np.round(out["declined_amt"][sl], -3).astype("int64"),
        "night_outflow_amt": np.round(night_out[sl], -3).astype("int64"),
        "n_new_payee": n_newp[sl].astype("int32"),
        "n_distinct_payee": dist[sl].astype("int32"),
        "top_payee_share": top[sl].astype("float32"),
        "ftp_large": ftp_large[sl].astype("int8"),
        "n_geo_mismatch": n_geo[sl].astype("int32"),
        "new_device": new_dev[sl].astype("int8"),
        "cred_change": cred[sl].astype("int8"),
        "n_fraud_alert": alert[sl].astype("int8"),
        "n_dispute": dispute[sl].astype("int8"),
        **{f"out_{k}": cat_amt[sl, j] for j, k in enumerate(CATS)},
        "end_balance": np.round(out["end_bal"][sl], -3).astype("int64"),
        "tier_applied": np.int8(c["baseline_tier"]),
        "cap_applied": int(cap),
        "_fraud_day": (lab_day[sl] & bool(events[0]["reported"] if events else 0)).astype("int8"),
    })
    return df, truth, events


# ----------------------------------------------------------------------------- chunk worker / main
def _setup():
    days = pd.date_range(WIN_START, WIN_END, freq="D")
    return days, len(days), days.dayofweek.values, days.day.values, days.days_in_month.values, days.month.values


def work(args):
    cust_chunk, seed_seq, part, out_dir = args
    rng = np.random.default_rng(seed_seq)
    days, D, dow, dom, dim, month_idx = _setup()
    ev_lo_idx = (EVENT_LO - WIN_START).days
    frames, truths, evs = [], [], []
    for _, c in cust_chunk.iterrows():
        df, tr, ev = sim_customer(c, rng, D, days, dow, dom, dim, month_idx, ev_lo_idx)
        frames.append(df)
        tr.update(customer_id=c["customer_id"], persona=c["_persona"], stealth=int(c["_stealth"]))
        truths.append(tr)
        evs.extend(ev)
    daily = pd.concat(frames, ignore_index=True)
    daily.to_parquet(os.path.join(out_dir, "customer_daily", f"part-{part:04d}.parquet"), index=False)
    return truths, evs, len(daily)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="../data/dev")
    ap.add_argument("--prefix", default="C", help="customer id prefix (use T for the held-out test set)")
    ap.add_argument("--chunk", type=int, default=1000)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = ap.parse_args()
    global ID_PREFIX
    ID_PREFIX = a.prefix
    os.makedirs(os.path.join(a.out, "customer_daily"), exist_ok=True)
    for f in os.listdir(os.path.join(a.out, "customer_daily")):
        os.remove(os.path.join(a.out, "customer_daily", f))
    t0 = time.time()
    root = np.random.SeedSequence(a.seed)
    cust = make_customers(a.n, np.random.default_rng(root.spawn(1)[0]))
    chunks = [cust.iloc[i:i + a.chunk] for i in range(0, len(cust), a.chunk)]
    seeds = root.spawn(len(chunks) + 1)[1:]
    jobs = [(ch, seeds[i], i, a.out) for i, ch in enumerate(chunks)]
    truths, evs, nrows = [], [], 0
    with ProcessPoolExecutor(a.workers) as ex:
        for i, (tr, ev, nr) in enumerate(ex.map(work, jobs)):
            truths += tr
            evs += ev
            nrows += nr
            print(f"  chunk {i + 1}/{len(jobs)} done  ({time.time() - t0:.0f}s)", flush=True)

    truth = pd.DataFrame(truths)[["customer_id", "persona", "stealth", "growth_rate_monthly", "fraud_start", "fraud_end",
                                  "dormant_from", "dormant_to"]]
    for col in ("fraud_start", "fraud_end", "dormant_from", "dormant_to"):
        truth[col] = pd.to_datetime(truth[col]).dt.strftime("%Y-%m-%d")
    events = pd.DataFrame(evs)
    for col in ("event_start", "event_end", "confirm_date"):
        events[col] = pd.to_datetime(events[col]).dt.strftime("%Y-%m-%d")
    cust_out = cust.drop(columns=["_persona", "_stealth", "_judol", "_nightowl"])
    for col in ("open_date", "kyc_date"):
        cust_out[col] = cust_out[col].dt.strftime("%Y-%m-%d")
    cust_out.to_csv(os.path.join(a.out, "customers.csv"), index=False)
    truth.to_csv(os.path.join(a.out, "_truth.csv"), index=False)
    events.to_csv(os.path.join(a.out, "fraud_events.csv"), index=False)
    print(f"done in {time.time() - t0:.0f}s: customers={len(cust_out):,} daily rows={nrows:,} fraud events={len(events):,}")


if __name__ == "__main__":
    main()
