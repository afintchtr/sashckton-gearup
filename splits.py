"""
Data access + split protocol for model selection (single source of truth, import this in every notebook).

  DEV   data/dev/    seed 7, customers C......   used for CV, tuning, model comparison
  TEST  data/test/  seed 8, customers T......   held out. load_test() may be called ONCE, at the very end.

Protocol
  * CV            : StratifiedGroupKFold(5), grouped by customer, on DEV snap_date < 2026-07-01
  * forward check : train 2025-10..2026-04  ->  validate 2026-05..2026-06  (temporal drift)
  * dev OOT       : DEV snap_date >= 2026-07-01 (already looked at while building the data: validation only)
  * final test    : data/test, untouched
"""
import hashlib
import json
import os

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedGroupKFold

ROOT = os.path.dirname(os.path.abspath(__file__))
DEV, TEST = os.path.join(ROOT, "data/dev"), os.path.join(ROOT, "data/test")
TEST2 = os.path.join(ROOT, "data/test2")      # second held-out set (seed 10) for model v1.1; TEST was already used for v1
LABEL, WEIGHT, NEG_KEEP = "label_fraud_today", "weight", 0.03
CAT = ["gender", "region", "occupation", "segment"]
# never features: ids, labels, hidden truth, limit-engine inputs (depend on the current limit) and
# alert/dispute history (raised by the fraud system itself after the event -> circular, leaks the label)
EXCLUDE = {"customer_id", "snap_date", WEIGHT, "label_fraud_today", "label_fraud_90d", "_fraud_type", "_stealth",
           "near_limit_days_90", "declined_cnt_90", "baseline_tier", "fraud_alerts_90", "disputes_90"}
CV_END, FWD_TRAIN_END = "2026-07-01", "2026-05-01"
N_FOLDS, SEED = 5, 42


def features(df):
    return [c for c in df.columns if c not in EXCLUDE]


def load_dev(mode="day"):
    f = "daydetect.parquet" if mode == "day" else "snapshots.parquet"
    return pd.read_parquet(os.path.join(DEV, f))


def cv_pool(dev):
    """DEV rows usable for CV / tuning (before the dev OOT window)."""
    return dev[dev.snap_date < CV_END].reset_index(drop=True)


def cv_folds(pool, n=N_FOLDS, seed=SEED):
    """Customer-disjoint folds, stratified on 'customer has a confirmed fraud day'."""
    has_fraud = pool.groupby("customer_id")[LABEL].transform("max")
    sgkf = StratifiedGroupKFold(n_splits=n, shuffle=True, random_state=seed)
    return list(sgkf.split(pool, has_fraud, groups=pool["customer_id"]))


def forward_split(pool):
    tr = np.where(pool.snap_date < FWD_TRAIN_END)[0]
    va = np.where(pool.snap_date >= FWD_TRAIN_END)[0]
    return tr, va


def dev_oot(dev):
    return dev[dev.snap_date >= CV_END].reset_index(drop=True)


def load_test(i_understand_this_is_the_final_evaluation=False):
    """Held-out TEST. Refuses unless explicitly acknowledged, and verifies the data was not modified."""
    if not i_understand_this_is_the_final_evaluation:
        raise RuntimeError("TEST is for the final evaluation of the chosen model only. Pass the flag explicitly.")
    man = json.load(open(os.path.join(TEST, "MANIFEST.json")))
    for f, h in man["sha256"].items():
        got = hashlib.sha256(open(os.path.join(TEST, f), "rb").read()).hexdigest()
        assert got == h, f"{f} changed since it was frozen"
    return pd.read_parquet(os.path.join(TEST, "daydetect.parquet"))


def load_test2(i_understand_this_is_the_final_evaluation=False):
    """Held-out TEST2 (seed 10, ids U......) for model v1.1.  Same guard and integrity check as load_test()."""
    if not i_understand_this_is_the_final_evaluation:
        raise RuntimeError("TEST2 is for the final evaluation of the chosen model only. Pass the flag explicitly.")
    man = json.load(open(os.path.join(TEST2, "MANIFEST.json")))
    for f, h in man["sha256"].items():
        got = hashlib.sha256(open(os.path.join(TEST2, f), "rb").read()).hexdigest()
        assert got == h, f"{f} changed since it was frozen"
    return pd.read_parquet(os.path.join(TEST2, "daydetect.parquet"))
