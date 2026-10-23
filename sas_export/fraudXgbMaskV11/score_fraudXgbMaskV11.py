import math
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

import settings

with open(Path(settings.pickle_path) / "fraudXgbMaskV11.pickle", "rb") as _f:
    _model = pickle.load(_f)

_FEATURES = ['kyc_age_months', 'inflow_cv_90', 'regular_inflow_windows_90', 'inflow_30', 'min_balance_90', 'days_low_balance_90', 'balance', 'balance_to_income', 'out_30', 'out_7', 'avg_out_90', 'p95_out_30', 'p95_out_90', 'out_trend_30', 'out_to_income_30', 'n_out_7', 'new_payee_rate_30', 'new_payee_cnt_7', 'night_share_30', 'night_share_7', 'night_share_1', 'payee_concentration_90', 'distinct_payee_30', 'distinct_per_tx_30', 'device_cnt_90', 'mix_ecommerce_30', 'mix_food_30', 'mix_transport_30', 'mix_bills_30', 'mix_transfer_p2p_30', 'mix_ewallet_topup_30', 'mix_gambling_crypto_30', 'mix_shift_30_vs_prev60', 'new_device_7', 'cred_change_3', 'ftp_large_7', 'ftp_large_30', 'geo_mismatch_share_7', 'geo_mismatch_share_30', 'out_to_balance_max_3d', 'spike_ratio_7_vs_90', 'max_out_7_to_income', 'out_1', 'n_out_1', 'new_payee_1', 'distinct_payee_1', 'top_payee_share_1', 'ftp_large_1', 'geo_mismatch_share_1', 'new_device_1', 'cred_change_1', 'out_to_balance_1', 'out_1_vs_avg30', 'inflow_1', 'passthrough_7', 'out_to_in_30', 'mix_transfer_p2p_1', 'mix_ewallet_topup_1', 'mix_gambling_crypto_1', 'declared_income', 'occupation', 'segment', 'shared_device_flag']
_CATEGORIES = {'occupation': ['Driver online', 'Freelancer', 'Guru', 'Ibu rumah tangga', 'Karyawan swasta', 'Katering', 'Kontraktor', 'Kreator konten', 'Kurir', 'Mahasiswa', 'Manajer', 'PNS', 'Pedagang', 'Pedagang musiman', 'Pegawai BUMN', 'Pemilik usaha kecil', 'Pemilik warung', 'Pengusaha', 'Penjual pakaian', 'Profesional', 'Tenaga kesehatan', 'Wiraswasta'], 'segment': ['SME', 'gig', 'payroll', 'retail']}
_NEG_KEEP = 0.03
_T_SUSPICIOUS = 0.00247490918263793
_T_HIGH_RISK = 0.04664194583892822
_GROUPS = {'device': ['device_cnt_90', 'new_device_7', 'cred_change_3', 'new_device_1', 'cred_change_1'], 'geo': ['geo_mismatch_share_7', 'geo_mismatch_share_30', 'geo_mismatch_share_1'], 'merchant': ['mix_ecommerce_30', 'mix_food_30', 'mix_transport_30', 'mix_bills_30', 'mix_transfer_p2p_30', 'mix_ewallet_topup_30', 'mix_gambling_crypto_30', 'mix_shift_30_vs_prev60', 'mix_transfer_p2p_1', 'mix_ewallet_topup_1', 'mix_gambling_crypto_1'], 'payee': ['new_payee_rate_30', 'new_payee_cnt_7', 'payee_concentration_90', 'distinct_payee_30', 'distinct_per_tx_30', 'ftp_large_7', 'ftp_large_30', 'new_payee_1', 'distinct_payee_1', 'top_payee_share_1', 'ftp_large_1'], 'malam': ['night_share_30', 'night_share_7', 'night_share_1'], 'saldo': ['min_balance_90', 'days_low_balance_90', 'balance', 'balance_to_income', 'out_to_income_30', 'out_to_balance_max_3d', 'out_to_balance_1', 'passthrough_7', 'out_to_in_30'], 'profil': ['declared_income', 'shared_device_flag', 'occupation', 'segment', 'kyc_age_months']}


def _frame(row):
    "Scalars (MAS row-by-row) or pandas Series (Model Manager batch) -> one-row-per-case DataFrame."
    d = {}
    for name in _FEATURES:
        v = row[name]
        if not isinstance(v, pd.Series):
            v = pd.Series([v])
        v = v.reset_index(drop=True)
        if name in _CATEGORIES:
            d[name] = pd.Categorical(v.where(v.isin(_CATEGORIES[name]), None), categories=_CATEGORIES[name])
        else:
            d[name] = pd.to_numeric(v, errors="coerce").astype(float)
    return pd.DataFrame(d)


def score(kyc_age_months, inflow_cv_90, regular_inflow_windows_90, inflow_30, min_balance_90, days_low_balance_90, balance, balance_to_income, out_30, out_7, avg_out_90, p95_out_30, p95_out_90, out_trend_30, out_to_income_30, n_out_7, new_payee_rate_30, new_payee_cnt_7, night_share_30, night_share_7, night_share_1, payee_concentration_90, distinct_payee_30, distinct_per_tx_30, device_cnt_90, mix_ecommerce_30, mix_food_30, mix_transport_30, mix_bills_30, mix_transfer_p2p_30, mix_ewallet_topup_30, mix_gambling_crypto_30, mix_shift_30_vs_prev60, new_device_7, cred_change_3, ftp_large_7, ftp_large_30, geo_mismatch_share_7, geo_mismatch_share_30, out_to_balance_max_3d, spike_ratio_7_vs_90, max_out_7_to_income, out_1, n_out_1, new_payee_1, distinct_payee_1, top_payee_share_1, ftp_large_1, geo_mismatch_share_1, new_device_1, cred_change_1, out_to_balance_1, out_1_vs_avg30, inflow_1, passthrough_7, out_to_in_30, mix_transfer_p2p_1, mix_ewallet_topup_1, mix_gambling_crypto_1, declared_income, occupation, segment, shared_device_flag):
    "Output: EM_EVENTPROBABILITY, EM_CLASSIFICATION, band, quality"
    row = {'kyc_age_months': kyc_age_months, 'inflow_cv_90': inflow_cv_90, 'regular_inflow_windows_90': regular_inflow_windows_90, 'inflow_30': inflow_30, 'min_balance_90': min_balance_90, 'days_low_balance_90': days_low_balance_90, 'balance': balance, 'balance_to_income': balance_to_income, 'out_30': out_30, 'out_7': out_7, 'avg_out_90': avg_out_90, 'p95_out_30': p95_out_30, 'p95_out_90': p95_out_90, 'out_trend_30': out_trend_30, 'out_to_income_30': out_to_income_30, 'n_out_7': n_out_7, 'new_payee_rate_30': new_payee_rate_30, 'new_payee_cnt_7': new_payee_cnt_7, 'night_share_30': night_share_30, 'night_share_7': night_share_7, 'night_share_1': night_share_1, 'payee_concentration_90': payee_concentration_90, 'distinct_payee_30': distinct_payee_30, 'distinct_per_tx_30': distinct_per_tx_30, 'device_cnt_90': device_cnt_90, 'mix_ecommerce_30': mix_ecommerce_30, 'mix_food_30': mix_food_30, 'mix_transport_30': mix_transport_30, 'mix_bills_30': mix_bills_30, 'mix_transfer_p2p_30': mix_transfer_p2p_30, 'mix_ewallet_topup_30': mix_ewallet_topup_30, 'mix_gambling_crypto_30': mix_gambling_crypto_30, 'mix_shift_30_vs_prev60': mix_shift_30_vs_prev60, 'new_device_7': new_device_7, 'cred_change_3': cred_change_3, 'ftp_large_7': ftp_large_7, 'ftp_large_30': ftp_large_30, 'geo_mismatch_share_7': geo_mismatch_share_7, 'geo_mismatch_share_30': geo_mismatch_share_30, 'out_to_balance_max_3d': out_to_balance_max_3d, 'spike_ratio_7_vs_90': spike_ratio_7_vs_90, 'max_out_7_to_income': max_out_7_to_income, 'out_1': out_1, 'n_out_1': n_out_1, 'new_payee_1': new_payee_1, 'distinct_payee_1': distinct_payee_1, 'top_payee_share_1': top_payee_share_1, 'ftp_large_1': ftp_large_1, 'geo_mismatch_share_1': geo_mismatch_share_1, 'new_device_1': new_device_1, 'cred_change_1': cred_change_1, 'out_to_balance_1': out_to_balance_1, 'out_1_vs_avg30': out_1_vs_avg30, 'inflow_1': inflow_1, 'passthrough_7': passthrough_7, 'out_to_in_30': out_to_in_30, 'mix_transfer_p2p_1': mix_transfer_p2p_1, 'mix_ewallet_topup_1': mix_ewallet_topup_1, 'mix_gambling_crypto_1': mix_gambling_crypto_1, 'declared_income': declared_income, 'occupation': occupation, 'segment': segment, 'shared_device_flag': shared_device_flag}
    batch = isinstance(kyc_age_months, pd.Series)
    X = _frame(row)
    p = _model.predict_proba(X)[:, 1].astype(float)
    cal = p * _NEG_KEEP / (p * _NEG_KEEP + (1 - p))
    band = np.where(cal >= _T_HIGH_RISK, "high_risk", np.where(cal >= _T_SUSPICIOUS, "suspicious", "genuine"))
    miss = X.isna()
    low = (miss.mean(axis=1) > 0.4).values
    for c in _GROUPS.values():
        low = low | miss[c].all(axis=1).values
    cls = np.where(band != "genuine", "1", "0")
    qual = np.where(low, "low", "ok")
    if batch:
        return pd.DataFrame(dict(EM_EVENTPROBABILITY=cal, EM_CLASSIFICATION=cls, band=band, quality=qual))
    return float(cal[0]), str(cls[0]), str(band[0]), str(qual[0])
