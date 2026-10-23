"""
Feature store: a 90-day ring buffer of DAILY RECORDS per customer + today's running record, on SQLite.

Why this shape: every model feature is a function of the last 90 daily records (see rolling.py).  Keeping the daily records
(not the features) means the batch job, the real-time scorer and the training pipeline all derive features with the same code.

Daily record = one row per customer per day since account opening (same schema as customer_daily):
    * closed days (yesterday and before) are written by the 02:00 batch from core banking, or caught up by ensure_days();
    * today's record is updated incrementally by ingest(event) as transactions stream in.

Event types: transfer | inflow | device_new | credential_change | fraud_alert | dispute
"""
import sqlite3
import threading

import numpy as np
import pandas as pd

import rolling as R

NIGHT_START, NIGHT_END = 22, 5                       # night window 22:00-05:00 (proposal 6.3)
CAT_COLS = [f"out_{c}" for c in R.CATS]
DAILY_ALL = ["customer_id", "date"] + R.DAILY_COLS + ["max_attempt", "n_declined_limit"]
TIER_CAP = {1: 10e6, 2: 25e6, 3: 50e6, 4: 100e6, 5: 200e6}   # proposal 4.2


class FeatureStore:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        cols = ", ".join(f"{c} REAL DEFAULT 0" for c in DAILY_ALL[2:])
        with self.lock:
            self.db.executescript(f"""
            CREATE TABLE IF NOT EXISTS customers (customer_id TEXT PRIMARY KEY, declared_income REAL, age_band TEXT, gender TEXT, region TEXT,
                occupation TEXT, segment TEXT, shared_device_flag INTEGER, open_date TEXT, kyc_date TEXT, baseline_tier INTEGER, current_tier INTEGER);
            CREATE TABLE IF NOT EXISTS daily (customer_id TEXT, date TEXT, {cols}, PRIMARY KEY (customer_id, date));
            CREATE TABLE IF NOT EXISTS payees_known (customer_id TEXT, payee_id TEXT, PRIMARY KEY (customer_id, payee_id));
            CREATE TABLE IF NOT EXISTS payee_day (customer_id TEXT, date TEXT, payee_id TEXT, amount REAL, PRIMARY KEY (customer_id, date, payee_id));
            """)

    # ------------------------------------------------------------------ profiles
    def upsert_customers(self, df):
        df = df.copy()
        df["current_tier"] = df.get("current_tier", df["baseline_tier"])
        cols = ["customer_id", "declared_income", "age_band", "gender", "region", "occupation", "segment", "shared_device_flag", "open_date", "kyc_date", "baseline_tier", "current_tier"]
        with self.lock:
            self.db.executemany(f"INSERT OR REPLACE INTO customers ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", df[cols].astype(object).values.tolist())
            self.db.commit()

    def profile(self, customer_id):
        r = self.db.execute("SELECT * FROM customers WHERE customer_id=?", (customer_id,)).fetchone()
        if r is None:
            raise KeyError(customer_id)
        d = dict(zip([c[0] for c in self.db.execute("SELECT * FROM customers LIMIT 0").description], r))
        d["cap"] = TIER_CAP[int(d["current_tier"])]
        return d

    def customers_count(self):
        return self.db.execute("SELECT COUNT(*) FROM customers").fetchone()[0]

    # ------------------------------------------------------------------ daily records
    def load_history(self, df):
        """Bulk-load closed daily records (batch / seeding). df has customer_id, date and the daily columns."""
        d = df.copy()
        for c in DAILY_ALL[2:]:
            if c not in d:
                d[c] = 0.0
        d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
        with self.lock:
            self.db.executemany(f"INSERT OR REPLACE INTO daily ({','.join(DAILY_ALL)}) VALUES ({','.join('?' * len(DAILY_ALL))})", d[DAILY_ALL].astype(object).values.tolist())
            self.db.commit()

    def _row(self, cid, day):
        r = self.db.execute(f"SELECT {','.join(DAILY_ALL)} FROM daily WHERE customer_id=? AND date=?", (cid, day)).fetchone()
        return None if r is None else dict(zip(DAILY_ALL, r))

    def _put(self, row):
        self.db.execute(f"INSERT OR REPLACE INTO daily ({','.join(DAILY_ALL)}) VALUES ({','.join('?' * len(DAILY_ALL))})", [row[c] for c in DAILY_ALL])

    def ensure_days(self, cid, upto):
        """Create idle-day records (zeros, balance carried forward) from the last stored day up to `upto`. Matches training: a record exists for every day."""
        upto = pd.Timestamp(upto).normalize()
        with self.lock:
            last = self.db.execute("SELECT MAX(date) FROM daily WHERE customer_id=?", (cid,)).fetchone()[0]
            if last is None:
                prof = self.profile(cid)
                start, bal = pd.Timestamp(prof["open_date"]), 0.0
            else:
                start = pd.Timestamp(last) + pd.Timedelta(days=1)
                bal = self._row(cid, last)["end_balance"]
            for d in pd.date_range(start, upto, freq="D"):
                row = {c: 0.0 for c in DAILY_ALL}
                row.update(customer_id=cid, date=d.strftime("%Y-%m-%d"), end_balance=bal)
                self._put(row)
            self.db.commit()

    def history(self, customer_ids, as_of, days=R.W):
        as_of = pd.Timestamp(as_of).normalize()
        lo = (as_of - pd.Timedelta(days=days - 1)).strftime("%Y-%m-%d")
        q = ",".join("?" * len(customer_ids))
        with self.lock:
            df = pd.read_sql(f"SELECT * FROM daily WHERE customer_id IN ({q}) AND date BETWEEN ? AND ?", self.db, params=[*customer_ids, lo, as_of.strftime("%Y-%m-%d")])
        df["date"] = pd.to_datetime(df["date"])
        return df

    def prune(self, before):
        with self.lock:
            self.db.execute("DELETE FROM daily WHERE date < ?", (pd.Timestamp(before).strftime("%Y-%m-%d"),))
            self.db.execute("DELETE FROM payee_day WHERE date < ?", (pd.Timestamp(before).strftime("%Y-%m-%d"),))
            self.db.commit()

    # ------------------------------------------------------------------ streaming ingestion
    def ingest(self, ev):
        """Apply one transaction/event to today's running record of the customer."""
        cid, ts = ev["customer_id"], pd.Timestamp(ev["ts"])
        day = ts.strftime("%Y-%m-%d")
        prof = self.profile(cid)
        with self.lock:
            self.ensure_days(cid, ts)
            row = self._row(cid, day)
            t, amt = ev["type"], float(ev.get("amount", 0.0) or 0.0)
            if t == "transfer":
                declined = bool(ev.get("declined_by_limit", False))
                row["max_attempt"] = max(row["max_attempt"], amt)
                if declined:
                    row["n_declined_limit"] += 1
                else:
                    cat = ev.get("category", "transfer_p2p")
                    pid = ev["payee_id"]
                    known = self.db.execute("SELECT 1 FROM payees_known WHERE customer_id=? AND payee_id=?", (cid, pid)).fetchone() is not None
                    row["outflow_amt"] += amt
                    row["n_outflow"] += 1
                    if NIGHT_START <= ts.hour or ts.hour < NIGHT_END:
                        row["night_outflow_amt"] += amt
                    if f"out_{cat}" in row:
                        row[f"out_{cat}"] += amt
                    if not known:
                        row["n_new_payee"] += 1
                        self.db.execute("INSERT OR IGNORE INTO payees_known VALUES (?,?)", (cid, pid))
                    if ev.get("geo_mismatch"):
                        row["n_geo_mismatch"] += 1
                    self.db.execute("INSERT INTO payee_day VALUES (?,?,?,?) ON CONFLICT(customer_id,date,payee_id) DO UPDATE SET amount=amount+excluded.amount", (cid, day, pid, amt))
                    pd_ = self.db.execute("SELECT COUNT(*), MAX(amount) FROM payee_day WHERE customer_id=? AND date=?", (cid, day)).fetchone()
                    row["n_distinct_payee"], row["top_payee_share"] = pd_[0], pd_[1] / row["outflow_amt"]
                    bal = ev.get("balance_after")
                    row["end_balance"] = float(bal) if bal is not None else row["end_balance"] - amt
                row["ftp_large"] = float(row["n_new_payee"] > 0 and row["max_attempt"] > 0.5 * prof["cap"])
            elif t == "inflow":
                row["inflow_amt"] += amt
                bal = ev.get("balance_after")
                row["end_balance"] = float(bal) if bal is not None else row["end_balance"] + amt
            elif t == "device_new":
                row["new_device"] = 1.0
            elif t == "credential_change":
                row["cred_change"] = 1.0
            elif t == "fraud_alert":
                row["n_fraud_alert"] += 1
            elif t == "dispute":
                row["n_dispute"] += 1
            else:
                raise ValueError(f"unknown event type {t}")
            self._put(row)
            self.db.commit()
        return row

    # ------------------------------------------------------------------ features for scoring
    def features_for(self, customer_id, as_of):
        """68-feature vector (+ today's record) for one customer as of `as_of` (day-to-date)."""
        as_of = pd.Timestamp(as_of)
        prof = self.profile(customer_id)
        self.ensure_days(customer_id, as_of)
        hist = self.history([customer_id], as_of)
        H = R.to_arrays(hist, [customer_id], [as_of.normalize()])
        static = pd.DataFrame([prof])
        feats = R.compute_features(H, static, [as_of.normalize()])
        today = hist[hist.date == as_of.normalize()].iloc[0].to_dict()
        a30 = hist[hist.date > as_of.normalize() - pd.Timedelta(days=30)]
        return feats, today, prof, dict(alerts_30=float(a30.n_fraud_alert.sum()), disputes_30=float(a30.n_dispute.sum()))
