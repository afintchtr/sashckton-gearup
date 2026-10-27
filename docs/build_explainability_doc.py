"""Generate docs/FEATURES_AND_EXPLAINABILITY.md and artifacts/feature_importance_*.csv.
Global importance = mean |SHAP contribution| (XGBoost pred_contribs, log-odds of the raw model) on the TRAINING table data/dev/daydetect.parquet:
all fraud rows + 60,000 random normal rows re-weighted to the table's prevalence.  In-sample by construction: it explains what the model learned, not how well it generalises.
Usage: python docs/build_explainability_doc.py"""
import json, os, sys
import numpy as np, pandas as pd, xgboost as xgb

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "bundle", "fraud_xgb_mask_v1_1"))
from score import FraudScorer

# feature -> (group, meaning).  Windows end on the scoring day; "1" = that day only.
G = {"stab": "Stabilitas keuangan", "dem": "Kebutuhan transaksi", "beh": "Konsistensi perilaku", "mix": "Bauran kategori belanja", "ctx": "Konteks terbaru (juga masukan gate)", "day": "Sinyal hari ini", "pro": "Profil"}
F = {
    "kyc_age_months": ("stab", "bulan sejak tanggal KYC (bisa bernilai negatif pada sebagian baris latih, apa adanya di data)"),
    "inflow_cv_90": ("stab", "variasi arus masuk antar tiga jendela 30 hari (simpangan baku / (rata-rata+1)); kosong bila < 2 jendela"),
    "regular_inflow_windows_90": ("stab", "jumlah jendela 30 hari (maks 3) dengan arus masuk >= 50% penghasilan deklarasi"),
    "inflow_30": ("stab", "total arus masuk 30 hari"),
    "min_balance_90": ("stab", "saldo akhir hari terendah dalam 90 hari"),
    "days_low_balance_90": ("stab", "jumlah hari (90 hari) saldo < 10% penghasilan deklarasi"),
    "balance": ("stab", "saldo akhir hari"),
    "balance_to_income": ("stab", "saldo / penghasilan deklarasi"),
    "out_30": ("dem", "total pengeluaran 30 hari"), "out_7": ("dem", "total pengeluaran 7 hari"),
    "avg_out_90": ("dem", "rata-rata pengeluaran harian 90 hari"),
    "p95_out_30": ("dem", "persentil ke-95 pengeluaran harian, 30 hari"), "p95_out_90": ("dem", "persentil ke-95 pengeluaran harian, 90 hari"),
    "out_trend_30": ("dem", "pengeluaran 30 hari terakhir / 30 hari sebelumnya (+1 juta)"),
    "out_to_income_30": ("dem", "pengeluaran 30 hari / penghasilan deklarasi"),
    "n_out_7": ("beh", "jumlah transaksi keluar 7 hari"),
    "new_payee_rate_30": ("beh", "porsi transaksi 30 hari ke payee baru"),
    "new_payee_cnt_7": ("beh", "jumlah payee baru dalam 7 hari"),
    "night_share_30": ("beh", "porsi nominal keluar pukul 22.00-05.00, 30 hari"), "night_share_7": ("beh", "idem, 7 hari"), "night_share_1": ("beh", "idem, hari ini"),
    "payee_concentration_90": ("beh", "rata-rata porsi payee teratas pada hari aktif, 90 hari"),
    "distinct_payee_30": ("beh", "jumlah payee berbeda, 30 hari"),
    "distinct_per_tx_30": ("beh", "payee berbeda / jumlah transaksi, 30 hari"),
    "device_cnt_90": ("beh", "1 + jumlah perangkat baru dalam 90 hari"),
    "mix_ecommerce_30": ("mix", "porsi nominal keluar ke e-commerce, 30 hari"), "mix_food_30": ("mix", "porsi ke makanan"), "mix_transport_30": ("mix", "porsi ke transportasi"),
    "mix_bills_30": ("mix", "porsi ke tagihan"), "mix_transfer_p2p_30": ("mix", "porsi ke transfer antar-orang"), "mix_ewallet_topup_30": ("mix", "porsi ke top-up e-wallet"),
    "mix_gambling_crypto_30": ("mix", "porsi ke judi/kripto"),
    "mix_shift_30_vs_prev60": ("mix", "total selisih mutlak porsi kategori: 30 hari terakhir vs 60 hari sebelumnya (perubahan pola belanja)"),
    "new_device_7": ("ctx", "jumlah perangkat baru, 7 hari"), "cred_change_3": ("ctx", "jumlah perubahan kredensial/telepon/email, 3 hari"),
    "ftp_large_7": ("ctx", "transfer pertama ke payee baru bernominal besar, 7 hari"), "ftp_large_30": ("ctx", "idem, 30 hari"),
    "geo_mismatch_share_7": ("ctx", "porsi transaksi dengan lokasi tidak cocok, 7 hari"), "geo_mismatch_share_30": ("ctx", "idem, 30 hari"),
    "out_to_balance_max_3d": ("ctx", "nilai maksimum 3 hari dari pengeluaran / (saldo + pengeluaran)"),
    "spike_ratio_7_vs_90": ("ctx", "rata-rata pengeluaran harian 7 hari / rata-rata 90 hari"),
    "max_out_7_to_income": ("ctx", "pengeluaran harian terbesar 7 hari / penghasilan deklarasi"),
    "out_1": ("day", "total pengeluaran hari ini"), "n_out_1": ("day", "jumlah transaksi keluar hari ini"), "new_payee_1": ("day", "payee baru hari ini"),
    "distinct_payee_1": ("day", "payee berbeda hari ini"), "top_payee_share_1": ("day", "porsi payee teratas dari pengeluaran hari ini"),
    "ftp_large_1": ("day", "transfer pertama ke payee baru bernominal besar hari ini"), "geo_mismatch_share_1": ("day", "porsi transaksi lokasi tidak cocok hari ini"),
    "new_device_1": ("day", "perangkat baru hari ini"), "cred_change_1": ("day", "perubahan kredensial hari ini"),
    "out_to_balance_1": ("day", "pengeluaran hari ini / (saldo + pengeluaran)"), "out_1_vs_avg30": ("day", "pengeluaran hari ini / rata-rata harian 30 hari"),
    "inflow_1": ("day", "arus masuk hari ini"), "passthrough_7": ("day", "pengeluaran 7 hari / arus masuk 7 hari (dana hanya lewat)"),
    "out_to_in_30": ("day", "pengeluaran 30 hari / arus masuk 30 hari"),
    "mix_transfer_p2p_1": ("day", "porsi transfer antar-orang hari ini"), "mix_ewallet_topup_1": ("day", "porsi top-up e-wallet hari ini"), "mix_gambling_crypto_1": ("day", "porsi judi/kripto hari ini"),
    "declared_income": ("pro", "penghasilan yang dideklarasikan"), "occupation": ("pro", "pekerjaan (kategori)"), "segment": ("pro", "segmen nasabah (kategori)"),
    "shared_device_flag": ("pro", "perangkat dipakai bersama nasabah lain"),
}

# ---------------- global importance
d = pd.read_parquet(os.path.join(ROOT, "data", "dev", "daydetect.parquet")).rename(columns={"label_fraud_today": "y", "_fraud_type": "ftype"})
for c in d.select_dtypes("category").columns: d[c] = d[c].astype(str)
rng = np.random.default_rng(0)
idx = np.concatenate([np.where(d.y == 1)[0], rng.choice(np.where(d.y == 0)[0], 60000, replace=False)])
s = d.iloc[idx].reset_index(drop=True)
s["w"] = np.where(s.y == 1, 1.0, d.loc[d.y == 0, "weight"].mean() * int((d.y == 0).sum()) / 60000)
imp = {}
for name, bdir in [("v1_1", "fraud_xgb_mask_v1_1"), ("v1", "fraud_xgb_mask_v1")]:
    sc = FraudScorer(os.path.join(ROOT, "bundle", bdir)); X = sc._frame(s)
    C = sc.booster.predict(xgb.DMatrix(X, enable_categorical=True, missing=np.nan), pred_contribs=True)[:, :-1]; A = np.abs(C); w = s.w.to_numpy()
    r = pd.DataFrame({"feature": sc.features, "mean_abs_shap_all": (A * w[:, None]).sum(0) / w.sum(), "mean_abs_shap_fraud": A[s.y.to_numpy() == 1].mean(0),
                      "mean_abs_shap_normal": A[s.y.to_numpy() == 0].mean(0)})
    for t in ["scam", "ato", "mule"]: r[f"fraud_{t}"] = A[(s.ftype == t).to_numpy()].mean(0)
    r["share_all"] = r.mean_abs_shap_all / r.mean_abs_shap_all.sum()
    r["rank_all"] = r.share_all.rank(ascending=False).astype(int); r["rank_fraud"] = r.mean_abs_shap_fraud.rank(ascending=False).astype(int)
    r = r.sort_values("rank_all"); imp[name] = r
    r.round(5).to_csv(os.path.join(ROOT, "artifacts", f"feature_importance_{name}.csv"), index=False)
r11, r1 = imp["v1_1"], imp["v1"]
t2 = json.load(open(os.path.join(ROOT, "artifacts", "test2_results.json")))["table"]


def md(df): return "\n".join(["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)] + ["| " + " | ".join(str(v) for v in row) + " |" for row in df.itertuples(index=False)])


L = []
L.append("# Fitur dan explainability model fraud (gate saja, v1, v1.1)\nDiperbarui 27 Oktober 2026 oleh `docs/build_explainability_doc.py`. Semua data sintetis; angka pengaruh fitur dihitung pada **tabel latihan** (in-sample), jadi menjelaskan apa yang dipelajari model, bukan seberapa baik ia menggeneralisasi.\n")
L.append("## 1. Tiga pembanding\n| | Gate saja | v1 | v1.1 (aktif) |\n|---|---|---|---|\n| Jenis | aturan if-then | XGBoost, 68 fitur | XGBoost, 63 fitur |\n| Atribut terproteksi (gender, usia, wilayah) | tidak | **ya** | tidak |\n| `tenure_months`, `hist_days` | tidak | ya | tidak (diukur dari 1 Juli 2025, bukan umur rekening) |")
for k, lab in [("PR_AUC", "PR-AUC test set terkunci"), ("ROC_AUC", "ROC-AUC"), ("recall_at_alert", "Recall at alert"), ("precision_at_alert", "Precision at alert")]:
    L[-1] += f"\n| {lab} | {t2[k]['Aturan gate saja']:.3f} | {t2[k]['v1 (68 fitur, bundel)']:.3f} | {t2[k]['v1.1 (63 fitur, bundel)']:.3f} |"
L.append("\nv1.1 sama dengan v1 dikurangi lima fitur: `age_ord`, `gender`, `region` (terproteksi) serta `tenure_months`, `hist_days` (cacat pengukuran). Ambang band: suspicious >= 0,00247, high_risk >= 0,04664 pada skor terkoreksi prior.\n")
L.append("## 2. Gate saja (aturan, tanpa model)\nSumber: `serving/gate.py`. Tiap aturan menghasilkan kode yang tercatat di `gate_hits`.\n\n| Kode | Kondisi | Dampak |\n|---|---|---|\n| `FRAUD_SCORE_H` | band model = high_risk | blokir kenaikan + **hold** |\n| `NEW_DEVICE` | `new_device_7` > 0 | blokir kenaikan |\n| `CREDENTIAL_CHANGE` | `cred_change_3` > 0 | blokir kenaikan |\n| `FIRST_PAYEE_LARGE` | payee baru dengan nominal > 50% batas harian | blokir + step-up |\n| `NIGHT_VELOCITY` | > 30% batas harian bergerak pukul 22.00-05.00 | blokir + step-up (di proposal: hold; diubah karena presisi hold 2,8%) |\n| `OUTFLOW_BALANCE` | pengeluaran 24 jam > 50% saldo, saldo > Rp50 juta | blokir + step-up (di proposal: hold; presisi 0,25%) |\n| `OPEN_ALERT_OR_DISPUTE` | alert atau sengketa dalam 30 hari | blokir kenaikan |\n\n**Pembanding \"gate saja\" di evaluasi** (`rule_gate` di notebook `07b_final_test_v1_1.ipynb`) bukan persis `gate.py`. Ia menjumlahkan tujuh kondisi biner: `new_device_7 > 0`, `cred_change_3 > 0`, `ftp_large_7 > 0`, `night_share_7 > 0.3`, `geo_mismatch_share_7 > 0.5`, `out_to_balance_max_3d > 0.5`, `new_payee_cnt_7 >= 2` (ditambah 0,01 x `out_to_balance_1` sebagai pemisah seri), lalu jumlah itu dipakai sebagai skor peringkat. Hasilnya pada test set: PR-AUC 0,008, recall at alert 5%, presisi 1,6% (tabel di bagian 1). Aturan mule buatan tangan pernah dicoba dan ditolak karena menandai 46% hari normal.\n")
L.append("## 3. Kamus 63 fitur v1.1\nUrut menurut pengaruh pada hari fraud (rata-rata nilai mutlak SHAP, skala log-odds model mentah). `Σ` = peringkat pengaruh keseluruhan (dibobot prevalensi).\n")
tab = r11.sort_values("rank_fraud").copy()
tab["Kelompok"] = tab.feature.map(lambda f: G[F[f][0]]); tab["Arti"] = tab.feature.map(lambda f: F[f][1])
tab = tab.assign(**{"Fitur": tab.feature.map(lambda f: f"`{f}`"), "SHAP hari fraud (rata-rata mutlak)": tab.mean_abs_shap_fraud.map(lambda v: f"{v:.2f}"), "Σ": tab.rank_all})[["Fitur", "Kelompok", "Arti", "SHAP hari fraud (rata-rata mutlak)", "Σ"]]
L.append(md(tab) + "\n")
gs = r11.assign(g=r11.feature.map(lambda f: G[F[f][0]])).groupby("g").agg(fitur=("feature", "size"), share=("share_all", "sum")).sort_values("share", ascending=False)
L.append("### Pengaruh per kelompok (dibobot prevalensi)\n" + md(gs.reset_index().assign(share=lambda x: (x.share * 100).round(1).astype(str) + "%").rename(columns={"g": "Kelompok", "fitur": "Jumlah fitur", "share": "Porsi pengaruh"})) + "\n")
L.append("## 4. Apa yang dipelajari model per tipe fraud\nLima fitur teratas per tipe (mean |SHAP| pada hari fraud tipe itu):\n")
rows = []
for t, lab in [("scam", "Scam"), ("ato", "ATO"), ("mule", "Mule")]:
    top = r11.sort_values(f"fraud_{t}", ascending=False).head(5); rows.append([lab, ", ".join(f"`{a}` ({b:.2f})" for a, b in zip(top.feature, top[f"fraud_{t}"]))])
L.append(md(pd.DataFrame(rows, columns=["Tipe", "Fitur teratas (rata-rata mutlak SHAP)"])) + "\n\nPolanya masuk akal terhadap cara generator mensimulasikannya: ATO didorong perangkat baru dan perubahan kredensial, scam oleh konsentrasi pada satu payee, mule oleh arus masuk di hari yang sama dan payee baru. **Catatan:** ini sebagian mencerminkan asumsi generator sintetis, belum tentu pola fraud nyata.\n")
L.append("## 5. Pengaruh fitur yang dibuang dari v1\n")
rm = r1[r1.feature.isin(["age_ord", "region", "tenure_months", "hist_days", "gender"])][["feature", "rank_all", "rank_fraud", "share_all"]].copy()
rm["share_all"] = (rm.share_all * 100).round(2).astype(str) + "%"; rm["feature"] = rm.feature.map(lambda f: f"`{f}`")
L.append(md(rm.rename(columns={"feature": "Fitur v1", "rank_all": "Peringkat (dari 68)", "rank_fraud": "Peringkat hari fraud", "share_all": "Porsi pengaruh"})) + f"\n\nKelima fitur itu hanya menyumbang sekitar {r1[r1.feature.isin(['age_ord','region','tenure_months','hist_days','gender'])].share_all.sum()*100:.1f}% dari total pengaruh, dan `gender` nyaris nol. Itu menjelaskan mengapa v1.1 hanya sekitar 0,017 PR-AUC di bawah v1 (selisih tidak signifikan).\n")
L.append("## 6. Alat explainability yang tersedia\n| Tingkat | Alat | Di mana | Isi |\n|---|---|---|---|\n| Per prediksi (model) | `reasons` | `/score`, `bundle/*/score.py` | 3 fitur yang paling menaikkan skor (SHAP bawaan XGBoost, `pred_contribs`), format `fitur=nilai (+kontribusi)` |\n| Per prediksi (aturan) | `gate_hits` | `/score` | kode aturan yang menyala |\n| Per keputusan limit | `reason_codes` (poin scorecard) | `/limit/evaluate`, `limit/scorecard.py` | alasan dengan teks, mis. \"unstable monthly inflow (-14 pts)\" |\n| Global | tabel di dokumen ini, `artifacts/feature_importance_*.csv` | repo | rata-rata \\|SHAP\\| per fitur |\n| Ketahanan | skenario hilang/noise/tanpa payee | notebook 02b dan 07b | PR-AUC bila fitur hilang |\n| Keadilan | recall dan flag rate per kelompok | notebook 06 dan 07b | audit gender, usia, wilayah |\n\n**Tidak tersedia:** penjelasan counterfactual (\"apa yang perlu berubah\"), dan di modul MAS Viya hanya ada 4 keluaran (`EM_EVENTPROBABILITY`, `EM_CLASSIFICATION`, `band`, `quality`) sehingga `reasons` dan `gate_hits` tidak ikut.\n")
L.append("## 7. Hal yang perlu diwaspadai\n* **`occupation` termasuk lima fitur paling berpengaruh** (peringkat " + str(int(r11.set_index('feature').loc['occupation', 'rank_fraud'])) + " pada hari fraud). Pekerjaan bukan atribut terproteksi menurut proposal, tetapi bisa menjadi proksi, dan di generator tiap persona fraud diberi daftar pekerjaan tertentu (misalnya mule: Mahasiswa, Kurir). Jadi pengaruhnya kemungkinan artefak simulasi. Sebaiknya diuji dengan melatih ulang tanpa `occupation` sebelum dipakai di data bank.\n* **Kontribusi SHAP ada dalam skala log-odds model mentah**, sebelum koreksi prior. Angka `(+0.88)` bukan peluang atau persen skor akhir.\n* **`reasons` hanya memuat kontribusi positif.** Nasabah dengan skor sangat kecil tetap mendapat tiga \"alasan\" teratas; itu menjelaskan arah skor, bukan bukti fraud.\n* **Mean |SHAP| mengukur besar pengaruh, bukan arah.** Fitur seperti `top_payee_share_1` besar baik pada hari normal maupun fraud karena bisa menurunkan maupun menaikkan skor.\n* **Fitur yang berkorelasi berbagi pengaruh** (misalnya `night_share_1/7/30`, `new_device_1/7`), sehingga pengaruh tiap fitur tunggal cenderung terlihat lebih kecil.\n* **Fitur berpengaruh hampir nol pada hari fraud:** " + ", ".join(f"`{f}`" for f in r11.sort_values('rank_fraud').tail(5).feature) + ". Kandidat untuk disederhanakan, tetapi pengaruh kecil bukan bukti tidak berguna (bisa tertutup fitur yang berkorelasi).\n* Hanya tabel latihan yang dipakai di sini. Angka performa yang sah tetap dari test set terkunci (`artifacts/test2_results.json`).\n")
L.append("## 8. Dalam arsitektur target (Viya sebagai pusat)\n* **Fitur harus dihitung ulang di Viya** (job harian untuk fitur sampai kemarin, tabel agregat untuk hari berjalan), karena Python bukan runtime. Rumusnya dan hasilnya harus identik dengan fitur latih: pakai `serving/rolling.py` dan `serving/test_rolling_parity.py` sebagai oracle. Kelompok **Sinyal hari ini** menyumbang porsi pengaruh terbesar (bagian 3), jadi fitur hari berjalan harus segar dan transaksi yang sedang diputuskan harus ikut dihitung.\n* **`reasons` (SHAP) dan `gate_hits` tidak keluar dari modul MAS**, yang hanya mengeluarkan `EM_EVENTPROBABILITY`, `EM_CLASSIFICATION`, `band`, `quality`. Di Viya, alasan ke nasabah berasal dari kode aturan gate (rule set) dan band; alasan scorecard dipakai untuk keputusan limit. Opsi yang belum dikerjakan: menambahkan `reason_1` sampai `reason_3` sebagai keluaran kode skor MAS (kontribusi SHAP sudah dapat dihitung dari booster).\n* **Kategori harus cocok.** `occupation` dan `segment` dilatih dengan daftar kategori tetap (22 pekerjaan, 4 segmen); nilai di luar daftar dianggap kosong, sehingga fitur peringkat 5 (`occupation`) hilang bila master nasabah tidak dipetakan ke daftar itu.\n* **Sumber data yang tidak tersedia menurunkan kemampuan model** (PR-AUC DEV dibanding baseline 0,659): tanpa id payee 0,61×, tanpa arus masuk 0,78×, tanpa saldo 0,80×, tanpa perangkat dan kredensial 0,90×, tanpa kategori belanja 0,89×, hanya transfer keluar 0,51×. Pengukuran menghilangkan fitur saat skor pada model yang sama; model yang dilatih ulang untuk sumber yang lebih sedikit bisa lebih baik.\n")
open(os.path.join(HERE, "FEATURES_AND_EXPLAINABILITY.md"), "w").write("\n".join(L))
print("written", sum(len(x) for x in L), "chars")
