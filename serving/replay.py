"""Turn one daily record (customer_daily row) into the stream of events a core-banking feed would have produced.
Approximate by design (daily records carry no intraday detail): totals, counts, new/distinct payees, top-payee share, night amount,
geo mismatches, categories, device/credential events, alerts and the end balance are reproduced; exact timing and per-payee split are synthetic."""
import numpy as np, pandas as pd
CATS = ["ecommerce", "food", "transport", "bills", "transfer_p2p", "ewallet_topup", "gambling_crypto"]


def day_to_events(row, cap, seed=0):
    r = np.random.default_rng(seed)
    cid, day = row["customer_id"], pd.Timestamp(row["date"]).normalize()
    at = lambda h, m=0: (day + pd.Timedelta(hours=h, minutes=m)).isoformat()
    ev = []
    if row["inflow_amt"] > 0: ev.append(dict(customer_id=cid, ts=at(8), type="inflow", amount=float(row["inflow_amt"])))
    if row["new_device"] > 0: ev.append(dict(customer_id=cid, ts=at(7, 30), type="device_new"))
    if row["cred_change"] > 0: ev.append(dict(customer_id=cid, ts=at(7, 40), type="credential_change"))
    n, total = int(row["n_outflow"]), float(row["outflow_amt"])
    if n > 0 and total > 0:
        d = int(np.clip(row["n_distinct_payee"], 1, n)); p = int(np.clip(row["n_new_payee"], 0, d))
        payees = [f"N{day:%Y%m%d}_{i}" for i in range(p)] + [f"K{j}" for j in range(d - p)]
        top = float(row["top_payee_share"]) if d > 1 else 1.0
        per_payee = [top * total] + [(1 - top) * total / (d - 1)] * (d - 1) if d > 1 else [total]
        assign = list(range(d)) + [0] * (n - d)                              # transfer k -> payee index
        cnt = np.bincount(assign, minlength=d)
        amounts = [per_payee[a] / cnt[a] for a in assign]
        # categories: give each transfer the category with the largest remaining amount
        left = {c: float(row[f"out_{c}"]) for c in CATS}; cats = []
        for amt in amounts:
            c = max(left, key=left.get); cats.append(c); left[c] -= amt
        # night: latest transfers carry the night amount
        night_left, hours = float(row["night_outflow_amt"]), [0] * n
        for k in range(n - 1, -1, -1):
            if night_left > 0.25 * amounts[k]: hours[k], night_left = 1, night_left - amounts[k]
        geo = set(r.choice(n, size=int(min(row["n_geo_mismatch"], n)), replace=False).tolist()) if row["n_geo_mismatch"] > 0 else set()
        for k in range(n):
            hh, mm = (23, 10 + k) if hours[k] else (int(np.clip(9 + k * 11 // max(n, 1), 9, 20)), 5 * k)
            ev.append(dict(customer_id=cid, ts=at(hh, mm % 60), type="transfer", amount=float(amounts[k]), payee_id=payees[assign[k]], category=cats[k], geo_mismatch=k in geo))
        newp_big = any(amounts[assign.index(i)] > 0.5 * cap for i in range(p)) if p else False
        if row["ftp_large"] > 0 and not newp_big:                              # large attempt to a new payee that the limit declined
            ev.append(dict(customer_id=cid, ts=at(12), type="transfer", amount=0.55 * cap, payee_id=f"N{day:%Y%m%d}_x", declined_by_limit=True))
    for _ in range(int(row["n_fraud_alert"])): ev.append(dict(customer_id=cid, ts=at(15), type="fraud_alert"))
    for _ in range(int(row["n_dispute"])): ev.append(dict(customer_id=cid, ts=at(16), type="dispute"))
    ev.sort(key=lambda e: e["ts"])
    if ev: ev[-1]["balance_after"] = float(row["end_balance"]) if ev[-1]["type"] in ("transfer", "inflow") else None
    return ev
