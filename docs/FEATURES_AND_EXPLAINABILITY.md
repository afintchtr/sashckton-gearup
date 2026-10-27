# Fitur dan explainability model fraud (gate saja, v1, v1.1)
Diperbarui 27 Oktober 2026 oleh `docs/build_explainability_doc.py`. Semua data sintetis; angka pengaruh fitur dihitung pada **tabel latihan** (in-sample), jadi menjelaskan apa yang dipelajari model, bukan seberapa baik ia menggeneralisasi.

## 1. Tiga pembanding
| | Gate saja | v1 | v1.1 (aktif) |
|---|---|---|---|
| Jenis | aturan if-then | XGBoost, 68 fitur | XGBoost, 63 fitur |
| Atribut terproteksi (gender, usia, wilayah) | tidak | **ya** | tidak |
| `tenure_months`, `hist_days` | tidak | ya | tidak (diukur dari 1 Juli 2025, bukan umur rekening) |
| PR-AUC test set terkunci | 0.008 | 0.752 | 0.734 |
| ROC-AUC | 0.843 | 0.999 | 0.999 |
| Recall at alert | 0.050 | 0.862 | 0.856 |
| Precision at alert | 0.016 | 0.275 | 0.272 |

v1.1 sama dengan v1 dikurangi lima fitur: `age_ord`, `gender`, `region` (terproteksi) serta `tenure_months`, `hist_days` (cacat pengukuran). Ambang band: suspicious >= 0,00247, high_risk >= 0,04664 pada skor terkoreksi prior.

## 2. Gate saja (aturan, tanpa model)
Sumber: `serving/gate.py`. Tiap aturan menghasilkan kode yang tercatat di `gate_hits`.

| Kode | Kondisi | Dampak |
|---|---|---|
| `FRAUD_SCORE_H` | band model = high_risk | blokir kenaikan + **hold** |
| `NEW_DEVICE` | `new_device_7` > 0 | blokir kenaikan |
| `CREDENTIAL_CHANGE` | `cred_change_3` > 0 | blokir kenaikan |
| `FIRST_PAYEE_LARGE` | payee baru dengan nominal > 50% batas harian | blokir + step-up |
| `NIGHT_VELOCITY` | > 30% batas harian bergerak pukul 22.00-05.00 | blokir + step-up (di proposal: hold; diubah karena presisi hold 2,8%) |
| `OUTFLOW_BALANCE` | pengeluaran 24 jam > 50% saldo, saldo > Rp50 juta | blokir + step-up (di proposal: hold; presisi 0,25%) |
| `OPEN_ALERT_OR_DISPUTE` | alert atau sengketa dalam 30 hari | blokir kenaikan |

**Pembanding "gate saja" di evaluasi** (`rule_gate` di notebook `07b_final_test_v1_1.ipynb`) bukan persis `gate.py`. Ia menjumlahkan tujuh kondisi biner: `new_device_7 > 0`, `cred_change_3 > 0`, `ftp_large_7 > 0`, `night_share_7 > 0.3`, `geo_mismatch_share_7 > 0.5`, `out_to_balance_max_3d > 0.5`, `new_payee_cnt_7 >= 2` (ditambah 0,01 x `out_to_balance_1` sebagai pemisah seri), lalu jumlah itu dipakai sebagai skor peringkat. Hasilnya pada test set: PR-AUC 0,008, recall at alert 5%, presisi 1,6% (tabel di bagian 1). Aturan mule buatan tangan pernah dicoba dan ditolak karena menandai 46% hari normal.

## 3. Kamus 63 fitur v1.1
Urut menurut pengaruh pada hari fraud (rata-rata nilai mutlak SHAP, skala log-odds model mentah). `Σ` = peringkat pengaruh keseluruhan (dibobot prevalensi).

| Fitur | Kelompok | Arti | SHAP hari fraud (rata-rata mutlak) | Σ |
|---|---|---|---|---|
| `inflow_1` | Sinyal hari ini | arus masuk hari ini | 1.28 | 2 |
| `top_payee_share_1` | Sinyal hari ini | porsi payee teratas dari pengeluaran hari ini | 0.99 | 1 |
| `new_payee_rate_30` | Konsistensi perilaku | porsi transaksi 30 hari ke payee baru | 0.92 | 11 |
| `max_out_7_to_income` | Konteks terbaru (juga masukan gate) | pengeluaran harian terbesar 7 hari / penghasilan deklarasi | 0.73 | 7 |
| `occupation` | Profil | pekerjaan (kategori) | 0.61 | 3 |
| `out_1_vs_avg30` | Sinyal hari ini | pengeluaran hari ini / rata-rata harian 30 hari | 0.53 | 10 |
| `spike_ratio_7_vs_90` | Konteks terbaru (juga masukan gate) | rata-rata pengeluaran harian 7 hari / rata-rata 90 hari | 0.46 | 14 |
| `distinct_payee_1` | Sinyal hari ini | payee berbeda hari ini | 0.44 | 9 |
| `new_payee_cnt_7` | Konsistensi perilaku | jumlah payee baru dalam 7 hari | 0.44 | 19 |
| `new_device_7` | Konteks terbaru (juga masukan gate) | jumlah perangkat baru, 7 hari | 0.43 | 8 |
| `n_out_1` | Sinyal hari ini | jumlah transaksi keluar hari ini | 0.43 | 4 |
| `out_to_balance_max_3d` | Konteks terbaru (juga masukan gate) | nilai maksimum 3 hari dari pengeluaran / (saldo + pengeluaran) | 0.42 | 6 |
| `night_share_7` | Konsistensi perilaku | idem, 7 hari | 0.41 | 5 |
| `night_share_1` | Konsistensi perilaku | idem, hari ini | 0.30 | 15 |
| `cred_change_3` | Konteks terbaru (juga masukan gate) | jumlah perubahan kredensial/telepon/email, 3 hari | 0.27 | 37 |
| `mix_transfer_p2p_1` | Sinyal hari ini | porsi transfer antar-orang hari ini | 0.23 | 27 |
| `mix_shift_30_vs_prev60` | Bauran kategori belanja | total selisih mutlak porsi kategori: 30 hari terakhir vs 60 hari sebelumnya (perubahan pola belanja) | 0.23 | 39 |
| `out_to_income_30` | Kebutuhan transaksi | pengeluaran 30 hari / penghasilan deklarasi | 0.23 | 18 |
| `balance_to_income` | Stabilitas keuangan | saldo / penghasilan deklarasi | 0.21 | 12 |
| `balance` | Stabilitas keuangan | saldo akhir hari | 0.18 | 16 |
| `segment` | Profil | segmen nasabah (kategori) | 0.18 | 17 |
| `night_share_30` | Konsistensi perilaku | porsi nominal keluar pukul 22.00-05.00, 30 hari | 0.16 | 13 |
| `out_1` | Sinyal hari ini | total pengeluaran hari ini | 0.16 | 20 |
| `mix_ewallet_topup_1` | Sinyal hari ini | porsi top-up e-wallet hari ini | 0.15 | 36 |
| `mix_gambling_crypto_30` | Bauran kategori belanja | porsi ke judi/kripto | 0.15 | 24 |
| `out_trend_30` | Kebutuhan transaksi | pengeluaran 30 hari terakhir / 30 hari sebelumnya (+1 juta) | 0.15 | 26 |
| `out_to_balance_1` | Sinyal hari ini | pengeluaran hari ini / (saldo + pengeluaran) | 0.14 | 38 |
| `geo_mismatch_share_7` | Konteks terbaru (juga masukan gate) | porsi transaksi dengan lokasi tidak cocok, 7 hari | 0.14 | 32 |
| `mix_transfer_p2p_30` | Bauran kategori belanja | porsi ke transfer antar-orang | 0.13 | 35 |
| `distinct_payee_30` | Konsistensi perilaku | jumlah payee berbeda, 30 hari | 0.13 | 25 |
| `declared_income` | Profil | penghasilan yang dideklarasikan | 0.13 | 21 |
| `new_payee_1` | Sinyal hari ini | payee baru hari ini | 0.10 | 30 |
| `avg_out_90` | Kebutuhan transaksi | rata-rata pengeluaran harian 90 hari | 0.10 | 22 |
| `mix_gambling_crypto_1` | Sinyal hari ini | porsi judi/kripto hari ini | 0.10 | 42 |
| `inflow_cv_90` | Stabilitas keuangan | variasi arus masuk antar tiga jendela 30 hari (simpangan baku / (rata-rata+1)); kosong bila < 2 jendela | 0.08 | 31 |
| `passthrough_7` | Sinyal hari ini | pengeluaran 7 hari / arus masuk 7 hari (dana hanya lewat) | 0.07 | 23 |
| `inflow_30` | Stabilitas keuangan | total arus masuk 30 hari | 0.07 | 29 |
| `mix_ewallet_topup_30` | Bauran kategori belanja | porsi ke top-up e-wallet | 0.07 | 46 |
| `payee_concentration_90` | Konsistensi perilaku | rata-rata porsi payee teratas pada hari aktif, 90 hari | 0.06 | 34 |
| `kyc_age_months` | Stabilitas keuangan | bulan sejak tanggal KYC (bisa bernilai negatif pada sebagian baris latih, apa adanya di data) | 0.06 | 28 |
| `geo_mismatch_share_1` | Sinyal hari ini | porsi transaksi lokasi tidak cocok hari ini | 0.06 | 51 |
| `mix_food_30` | Bauran kategori belanja | porsi ke makanan | 0.05 | 48 |
| `ftp_large_7` | Konteks terbaru (juga masukan gate) | transfer pertama ke payee baru bernominal besar, 7 hari | 0.05 | 56 |
| `out_to_in_30` | Sinyal hari ini | pengeluaran 30 hari / arus masuk 30 hari | 0.04 | 41 |
| `mix_transport_30` | Bauran kategori belanja | porsi ke transportasi | 0.04 | 43 |
| `distinct_per_tx_30` | Konsistensi perilaku | payee berbeda / jumlah transaksi, 30 hari | 0.04 | 40 |
| `min_balance_90` | Stabilitas keuangan | saldo akhir hari terendah dalam 90 hari | 0.04 | 33 |
| `ftp_large_1` | Sinyal hari ini | transfer pertama ke payee baru bernominal besar hari ini | 0.04 | 54 |
| `mix_bills_30` | Bauran kategori belanja | porsi ke tagihan | 0.03 | 45 |
| `p95_out_30` | Kebutuhan transaksi | persentil ke-95 pengeluaran harian, 30 hari | 0.03 | 47 |
| `mix_ecommerce_30` | Bauran kategori belanja | porsi nominal keluar ke e-commerce, 30 hari | 0.03 | 50 |
| `n_out_7` | Konsistensi perilaku | jumlah transaksi keluar 7 hari | 0.03 | 44 |
| `p95_out_90` | Kebutuhan transaksi | persentil ke-95 pengeluaran harian, 90 hari | 0.02 | 49 |
| `cred_change_1` | Sinyal hari ini | perubahan kredensial hari ini | 0.02 | 59 |
| `new_device_1` | Sinyal hari ini | perangkat baru hari ini | 0.02 | 53 |
| `geo_mismatch_share_30` | Konteks terbaru (juga masukan gate) | idem, 30 hari | 0.01 | 57 |
| `days_low_balance_90` | Stabilitas keuangan | jumlah hari (90 hari) saldo < 10% penghasilan deklarasi | 0.01 | 52 |
| `out_30` | Kebutuhan transaksi | total pengeluaran 30 hari | 0.01 | 60 |
| `out_7` | Kebutuhan transaksi | total pengeluaran 7 hari | 0.01 | 55 |
| `regular_inflow_windows_90` | Stabilitas keuangan | jumlah jendela 30 hari (maks 3) dengan arus masuk >= 50% penghasilan deklarasi | 0.00 | 61 |
| `device_cnt_90` | Konsistensi perilaku | 1 + jumlah perangkat baru dalam 90 hari | 0.00 | 58 |
| `ftp_large_30` | Konteks terbaru (juga masukan gate) | idem, 30 hari | 0.00 | 62 |
| `shared_device_flag` | Profil | perangkat dipakai bersama nasabah lain | 0.00 | 63 |

### Pengaruh per kelompok (dibobot prevalensi)
| Kelompok | Jumlah fitur | Porsi pengaruh |
|---|---|---|
| Sinyal hari ini | 17 | 40.8% |
| Konsistensi perilaku | 10 | 16.6% |
| Konteks terbaru (juga masukan gate) | 9 | 15.7% |
| Profil | 4 | 9.7% |
| Stabilitas keuangan | 8 | 8.6% |
| Kebutuhan transaksi | 7 | 4.8% |
| Bauran kategori belanja | 8 | 3.9% |

## 4. Apa yang dipelajari model per tipe fraud
Lima fitur teratas per tipe (mean |SHAP| pada hari fraud tipe itu):

| Tipe | Fitur teratas (rata-rata mutlak SHAP) |
|---|---|
| Scam | `top_payee_share_1` (1.76), `distinct_payee_1` (0.86), `new_payee_rate_30` (0.72), `out_1_vs_avg30` (0.72), `n_out_1` (0.56) |
| ATO | `cred_change_3` (1.52), `new_device_7` (1.49), `out_1_vs_avg30` (0.72), `top_payee_share_1` (0.68), `inflow_1` (0.68) |
| Mule | `inflow_1` (1.89), `new_payee_rate_30` (1.17), `max_out_7_to_income` (0.92), `occupation` (0.69), `new_payee_cnt_7` (0.59) |

Polanya masuk akal terhadap cara generator mensimulasikannya: ATO didorong perangkat baru dan perubahan kredensial, scam oleh konsentrasi pada satu payee, mule oleh arus masuk di hari yang sama dan payee baru. **Catatan:** ini sebagian mencerminkan asumsi generator sintetis, belum tentu pola fraud nyata.

## 5. Pengaruh fitur yang dibuang dari v1

| Fitur v1 | Peringkat (dari 68) | Peringkat hari fraud | Porsi pengaruh |
|---|---|---|---|
| `age_ord` | 23 | 30 | 1.0% |
| `region` | 36 | 46 | 0.57% |
| `hist_days` | 38 | 49 | 0.55% |
| `tenure_months` | 40 | 51 | 0.52% |
| `gender` | 68 | 68 | 0.01% |

Kelima fitur itu hanya menyumbang sekitar 2.7% dari total pengaruh, dan `gender` nyaris nol. Itu menjelaskan mengapa v1.1 hanya sekitar 0,017 PR-AUC di bawah v1 (selisih tidak signifikan).

## 6. Alat explainability yang tersedia
| Tingkat | Alat | Di mana | Isi |
|---|---|---|---|
| Per prediksi (model) | `reasons` | `/score`, `bundle/*/score.py` | 3 fitur yang paling menaikkan skor (SHAP bawaan XGBoost, `pred_contribs`), format `fitur=nilai (+kontribusi)` |
| Per prediksi (aturan) | `gate_hits` | `/score` | kode aturan yang menyala |
| Per keputusan limit | `reason_codes` (poin scorecard) | `/limit/evaluate`, `limit/scorecard.py` | alasan dengan teks, mis. "unstable monthly inflow (-14 pts)" |
| Global | tabel di dokumen ini, `artifacts/feature_importance_*.csv` | repo | rata-rata \|SHAP\| per fitur |
| Ketahanan | skenario hilang/noise/tanpa payee | notebook 02b dan 07b | PR-AUC bila fitur hilang |
| Keadilan | recall dan flag rate per kelompok | notebook 06 dan 07b | audit gender, usia, wilayah |

**Tidak tersedia:** penjelasan counterfactual ("apa yang perlu berubah"), dan di modul MAS Viya hanya ada 4 keluaran (`EM_EVENTPROBABILITY`, `EM_CLASSIFICATION`, `band`, `quality`) sehingga `reasons` dan `gate_hits` tidak ikut.

## 7. Hal yang perlu diwaspadai
* **`occupation` termasuk lima fitur paling berpengaruh** (peringkat 5 pada hari fraud). Pekerjaan bukan atribut terproteksi menurut proposal, tetapi bisa menjadi proksi, dan di generator tiap persona fraud diberi daftar pekerjaan tertentu (misalnya mule: Mahasiswa, Kurir). Jadi pengaruhnya kemungkinan artefak simulasi. Sebaiknya diuji dengan melatih ulang tanpa `occupation` sebelum dipakai di data bank.
* **Kontribusi SHAP ada dalam skala log-odds model mentah**, sebelum koreksi prior. Angka `(+0.88)` bukan peluang atau persen skor akhir.
* **`reasons` hanya memuat kontribusi positif.** Nasabah dengan skor sangat kecil tetap mendapat tiga "alasan" teratas; itu menjelaskan arah skor, bukan bukti fraud.
* **Mean |SHAP| mengukur besar pengaruh, bukan arah.** Fitur seperti `top_payee_share_1` besar baik pada hari normal maupun fraud karena bisa menurunkan maupun menaikkan skor.
* **Fitur yang berkorelasi berbagi pengaruh** (misalnya `night_share_1/7/30`, `new_device_1/7`), sehingga pengaruh tiap fitur tunggal cenderung terlihat lebih kecil.
* **Fitur berpengaruh hampir nol pada hari fraud:** `out_7`, `regular_inflow_windows_90`, `device_cnt_90`, `ftp_large_30`, `shared_device_flag`. Kandidat untuk disederhanakan, tetapi pengaruh kecil bukan bukti tidak berguna (bisa tertutup fitur yang berkorelasi).
* Hanya tabel latihan yang dipakai di sini. Angka performa yang sah tetap dari test set terkunci (`artifacts/test2_results.json`).

## 8. Dalam arsitektur target (Viya sebagai pusat)
* **Fitur harus dihitung ulang di Viya** (job harian untuk fitur sampai kemarin, tabel agregat untuk hari berjalan), karena Python bukan runtime. Rumusnya dan hasilnya harus identik dengan fitur latih: pakai `serving/rolling.py` dan `serving/test_rolling_parity.py` sebagai oracle. Kelompok **Sinyal hari ini** menyumbang porsi pengaruh terbesar (bagian 3), jadi fitur hari berjalan harus segar dan transaksi yang sedang diputuskan harus ikut dihitung.
* **`reasons` (SHAP) dan `gate_hits` tidak keluar dari modul MAS**, yang hanya mengeluarkan `EM_EVENTPROBABILITY`, `EM_CLASSIFICATION`, `band`, `quality`. Di Viya, alasan ke nasabah berasal dari kode aturan gate (rule set) dan band; alasan scorecard dipakai untuk keputusan limit. Opsi yang belum dikerjakan: menambahkan `reason_1` sampai `reason_3` sebagai keluaran kode skor MAS (kontribusi SHAP sudah dapat dihitung dari booster).
* **Kategori harus cocok.** `occupation` dan `segment` dilatih dengan daftar kategori tetap (22 pekerjaan, 4 segmen); nilai di luar daftar dianggap kosong, sehingga fitur peringkat 5 (`occupation`) hilang bila master nasabah tidak dipetakan ke daftar itu.
* **Sumber data yang tidak tersedia menurunkan kemampuan model** (PR-AUC DEV dibanding baseline 0,659): tanpa id payee 0,61×, tanpa arus masuk 0,78×, tanpa saldo 0,80×, tanpa perangkat dan kredensial 0,90×, tanpa kategori belanja 0,89×, hanya transfer keluar 0,51×. Pengukuran menghilangkan fitur saat skor pada model yang sama; model yang dilatih ulang untuk sumber yang lebih sedikit bisa lebih baik.
