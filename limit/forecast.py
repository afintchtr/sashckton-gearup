"""Capacity forecast (proposal 6.2): next-30-day peak daily need, per customer.
  MVP rule   : 1.2 x P95 of daily outflow over the trailing 90 days, floored at the current tier's midpoint
  Pilot model: quantile regression (tau = 0.95) of log1p(max daily INTENDED outflow over the next 30 days)
Target is the intended need (what the customer tried to send), not realised outflow, which the current limit censors."""
import numpy as np, pandas as pd, xgboost as xgb
TIER_CAP = np.array([0, 10e6, 25e6, 50e6, 100e6, 200e6])            # index = tier (0 unused)
FEATURES = ["out_30", "out_7", "avg_out_90", "p95_out_30", "p95_out_90", "out_trend_30", "out_to_income_30", "max_out_7_to_income", "spike_ratio_7_vs_90",
            "near_limit_days_90", "declined_cnt_90", "n_out_7", "inflow_30", "declared_income", "balance", "tenure_months", "ramadan_overlap", "school_overlap"]
RAMADAN = ("2026-02-18", "2026-03-27"); SCHOOL = ("2026-07-10", "2026-07-25")


def calendar_overlap(snap_dates, window, horizon=30):
    s = (pd.to_datetime(snap_dates) + pd.Timedelta(days=1)).values; e = (pd.to_datetime(snap_dates) + pd.Timedelta(days=horizon)).values
    a, b = np.datetime64(window[0]), np.datetime64(window[1])
    return ((np.minimum(e, b) - np.maximum(s, a)) / np.timedelta64(1, "D")).clip(0, None)


def add_calendar(df, date_col="snap_date"):
    df = df.copy(); df["ramadan_overlap"] = calendar_overlap(df[date_col], RAMADAN); df["school_overlap"] = calendar_overlap(df[date_col], SCHOOL); return df


def tier_mid(tier):
    t = np.asarray(tier, int); return (TIER_CAP[t - 1] + TIER_CAP[t]) / 2


def rule_forecast(p95_out_90, tier):
    return np.maximum(1.2 * np.asarray(p95_out_90), tier_mid(tier))


def tier_from_amount(amount):
    """Smallest tier whose daily cap covers the amount (T5 if above T4)."""
    return np.clip(np.searchsorted(TIER_CAP[1:], np.asarray(amount, float), side="left") + 1, 1, 5)


class QuantileForecast:
    def __init__(self, alpha=0.95, **kw):
        self.alpha = alpha
        self.params = dict(objective="reg:quantileerror", quantile_alpha=alpha, n_estimators=300, learning_rate=0.05, max_depth=5, subsample=0.8, tree_method="hist", n_jobs=-1)
        self.params.update(kw)
    def fit(self, X, demand): self.m = xgb.XGBRegressor(**self.params).fit(X[FEATURES], np.log1p(demand)); return self
    def predict(self, X): return np.expm1(self.m.predict(X[FEATURES])).clip(min=0)
    def save(self, path): self.m.get_booster().save_model(path)
    @classmethod
    def load(cls, path, alpha=0.95):
        c = cls(alpha); c.m = xgb.XGBRegressor(); c.m.load_model(path); return c
