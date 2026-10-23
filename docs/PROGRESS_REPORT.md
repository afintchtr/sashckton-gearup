# Laporan Progres: Dynamic Customer Limit (SAS Hackathon 2026, Team Gear Up)
Per 23 Oktober 2026. Semua data **sintetis**. Dokumen ini menjawab: seberapa jauh kita dari proposal, apa yang masih kurang, mana yang layak dikejar atau diabaikan, dan apakah model siap diekspor ke SAS Viya lab. Peta folder ada di [README.md](../README.md).

---

## 1. Ringkasan eksekutif
**Status: MVP untuk kompetisi (bukti konsep end-to-end) sudah ada dan teruji. Belum layak produksi.**

| Lapisan | Ada? | Bukti |
|---|---|---|
| Data sintetis realistis (scam, mule, ATO, varian stealth, hard negative) | Ya | 4 dataset: DEV 50.000, TEST 20.000, TEST2 20.000 (keduanya terkunci SHA-256), SIM 10.000 nasabah |
| Fitur rolling identik antara training dan serving | Ya | 68 dari 68 fitur identik pada 6.000 nasabah-hari |
| Model fraud **v1.1** (tanpa gender, usia, wilayah) + tahan data tidak lengkap | Ya | TEST2: 7 dari 7 kriteria lulus (ditetapkan sebelum data dibuka) |
| Fraud gate §6.3 dan keputusan HOLD / STEP_UP / ALLOW | Ya | 7 dari 8 aturan (mule-account khusus belum) |
| Scorecard risiko §6.1 dan capacity forecast §6.2 | Ya, scorecard lemah | KS 14,8 (target >= 35); forecast coverage 94% |
| Mesin kebijakan limit §4 dan §7 | Ya | 24 uji aturan lolos |
| Service REST + limit service + log keputusan | Ya | paritas, replay, dan lifecycle penawaran-terima-berlaku-banding lolos |
| Bukti dampak statis vs dinamis | Ya | simulasi 10.000 nasabah x 364 hari + 11 uji sensitivitas |
| Pemantauan PSI, KS, migrasi tier, fairness, reason code | Ya | uji alarm palsu dan uji deteksi |
| Paket impor SAS Viya (siap lab) | Disiapkan, **belum dicoba di Viya** | kode skor identik dengan bundel; 25 kasus validasi; preflight |
| Mock UI journey nasabah (Inggris) | Ya | lima journey interaktif dari respons nyata layanan |
| Integrasi Viya sungguhan, feature store produksi, dashboard produksi, business case | Belum | lihat bagian 6 |

**Tiga angka yang paling layak dipajang** (simulasi 10.000 nasabah, 364 hari, model v1.1):
* transaksi sah yang ditolak karena limit **turun 32%** (2.327 -> 1.574 per 1.000 nasabah per tahun); nasabah terdampak 32,0% -> 25,2%; volume sah terselesaikan **+5,5%**;
* fraud yang lolos **turun 43%** berkat fraud gate; limit dinamis menambah hanya **+1,6%** di atas gate (total -42% vs hari ini);
* **tidak ada nasabah di bawah tier awalnya** (lantai non-negosiasi §4.4).

**Tiga hal yang harus jujur diakui:** (1) skorecard 90 hari lemah (KS 14,8 vs target 35); (2) v1.1 sedikit di bawah v1 pada akurasi (selisih -0,017, tidak signifikan; harga kepatuhan §6.4); (3) semua angka berasal dari data sintetis dan simulasi open-loop.

---

## 2. Inventaris komponen
| Komponen | Lokasi | Fungsi |
|---|---|---|
| Generator data v2 + validator | `generator/gen_v2.py`, `validate_v2.py` | tiga tipe fraud, stealth, hard negative, profil yang tidak membedakan fraud |
| Pembuat fitur | `build_features.py` (training), `serving/rolling.py` (serving), `limit/build_targets.py` | fitur §5.2 dan label §5.3 |
| Protokol split | `splits.py` | CV per nasabah, uji maju, TEST dan TEST2 terkunci |
| Notebook model fraud | `notebooks/02`, `02b`, `02c`, `03`, `07`, `07b` | seleksi, ketahanan, keputusan, uji akhir v1 dan v1.1 |
| Model limit | `notebooks/04`, `limit/` | scorecard WoE + forecast kuantil |
| Kebijakan + simulasi | `notebooks/05`, `limit/policy.py`, `limit/simulate.py` | aturan §4, alur §7, bukti dampak |
| Audit dan pemantauan | `notebooks/06`, `notebooks/08`, `monitoring/monitor.py` | §6.4 |
| Service | `serving/` | FastAPI: /events /score /limit/* /batch/daily /metrics |
| Bundel model | `bundle/fraud_xgb_mask_v1_1/` (aktif), `fraud_xgb_mask_v1/` (pembanding) | model.json + schema + skor mandiri + model card |
| Paket SAS | `sas_export/` | pickle, kode skor MAS, metadata, scorecard DATA step, kasus validasi, runbook |
| Mock UI | `mock_ui/index.html` | lima journey nasabah |
| Deploy | `deploy/` (Dockerfile, `k8s/`) | ditulis, **belum di-build/apply** |

---

## 3. Hasil utama

### 3.1 Model fraud v1.1 (TEST2: 19.477 nasabah baru, 594 di antaranya fraud)
| Ukuran | v1.1 (aktif) | v1 (pembanding) |
|---|---|---|
| PR-AUC data lengkap | **0,734** (CI95 0,675-0,787) | 0,752 (0,693-0,808) |
| PR-AUC saat 20% nilai hilang | **0,558** (0,494-0,625) | 0,582 |
| Tanpa fitur payee | 0,466 | 0,502 |
| Aturan gate §6.3 saja | 0,008 | 0,008 |
| Selisih v1.1 - v1 | -0,017 (CI -0,039 .. +0,001), tidak signifikan | |
| Pita high-risk | 0,046% traffic, presisi **54%**, menangkap 78% fraud | |
| Pita suspicious | 0,421% traffic, presisi 1,2%, +16% fraud | fraud lolos sebagai genuine: 6,3% |
| Fairness recall | gender 1,01; usia 1,03; wilayah 1,18 | |
| Titik lemah | ATO stealth: 28% high-risk, 29% lolos sebagai genuine | |

Tujuh kriteria (C1-C7) ditetapkan sebelum TEST2 dimuat; semuanya lulus. v1.1 dipilih karena memenuhi §6.4: **63 fitur, tanpa gender, usia, wilayah, dan `tenure_months`/`hist_days`** (dua yang terakhir dihitung dari awal jendela data, bukan dari pembukaan rekening). Pada DEV Juli-September (3 seed), v1.1 malah lebih baik dan lebih stabil (0,670 ± 0,008 vs 0,661 ± 0,015); pada data baru ia sedikit lebih rendah, dalam batas toleransi.

### 3.2 Scorecard dan forecast (DEV, out-of-time)
| | Hasil | Target proposal |
|---|---|---|
| Scorecard WoE (tanpa atribut terproteksi) | AUC 0,587, Gini 0,175, **KS 14,8** | KS >= 35, Gini >= 0,45 (**tidak tercapai**) |
| XGBoost pada seluruh fitur (pembanding) | AUC 0,635, Gini 0,27 | bentuk scorecard bukan penyebab; sinyalnya lemah |
| Tingkat bad per tier skor | <T2 2,13%, T2 1,15%, T3 1,07%, T4 0,69%, T5 0,54% | monoton, tier bermakna |
| Forecast MVP (1,2 x P95 90 hari) | coverage **76%** | ideal ~95% |
| Forecast kuantil XGBoost (tau 0,95) | coverage **94%** (x1,2: 98%) | pinball 5x lebih kecil |

### 3.3 Mesin kebijakan dan simulasi (10.000 nasabah x 364 hari, model v1.1)
Konfigurasi: hold hanya dari skor model; night velocity dan outflow/balance menjadi step-up; hysteresis 14 evaluasi.

| | S0 statis | S1 statis + gate | S2 dinamis + gate |
|---|---|---|---|
| Transaksi sah ditolak limit (per 1.000 nasabah-tahun) | 2.327 | 2.327 | **1.574** (-32%) |
| Nasabah sah terdampak | 32,0% | 32,0% | **25,2%** |
| Volume sah terselesaikan (Rp miliar) | 3.798 | 3.798 | 4.008 (+5,5%) |
| Fraud lolos (Rp juta) | 7.239 | 4.106 (-43%) | 4.173 (+1,6% vs S1) |
| False hold (per 1.000 nasabah-tahun) | 0 | 80,8 | 80,8 |

Prompt step-up pada hari sah: 1.903 per 1.000 nasabah-tahun (pita suspicious 1.370, night velocity 287, outflow/balance 319). 31,9% nasabah pernah naik tier; median 25 hari sampai kenaikan pertama; 0 nasabah di bawah baseline; 0,75 perubahan tier per nasabah-tahun; cap rata-rata pada hari fraud pertama naik dari Rp29,2 juta ke Rp34,4 juta (+18%, perlu dipantau).

Sensitivitas (penolakan sah turun / fraud S2 vs S1 / churn tier):
| Skenario | Penolakan turun | Fraud S2 vs S1 | Churn |
|---|---|---|---|
| Dasar (hysteresis 14 hari) | 32,4% | +1,6% | 0,75 |
| Hysteresis 3 hari (literal proposal) | 29,7% | +1,2% | 1,02 |
| Hysteresis 30 hari | 34,4% | +2,0% | 0,61 |
| Penawaran diterima 40% / 90% | 18,1% / 41,6% | +0,7% / +1,9% | 0,43 / 0,97 |
| Hold efektif 25% / 100% (pengurangan fraud) | 32,4% | 20% / 86% | 0,75 |
| Tanpa step-down oleh gate | 32,7% | **+6,1%** | 0,74 |

Fairness hasil tier (kelompok n >= 300): gender seimbang (rasio kenaikan 0,99); **usia 0,60** (18-24: 23% naik; 45-54: 38%) dan **wilayah 0,76** di bawah ambang pemantauan 0,8. Sebagian besar kemungkinan karena komposisi (tenure dan pendapatan), tetapi perlu ditelusuri sebelum pilot.

### 3.4 Pemantauan (notebook 08)
| Uji | Hasil |
|---|---|
| Alarm palsu: DEV Juli-September dan nasabah simulasi baru | PSI skor 0,002 dan 0,0002 (stabil); `kyc_age_months` bergeser seiring waktu sehingga dikecualikan dari status drift |
| Deteksi: sumber payee mati (30% baris) | PSI skor 0,033 dan KS turun 1,4 poin: **dua pemicu proposal tidak menyala**; alarm nilai kosong (11 fitur) dan PSI fitur menyala -> *INVESTIGATE DATA PIPELINE* |
| Deteksi: perilaku bergeser seperti Lebaran | PSI skor 0,234 (< 0,25), KS turun 2,0 poin: pemicu proposal tidak menyala; 6 fitur drift -> *REVIEW* |
| Perubahan kecil 5% | *OK* |
| KS vs PR-AUC bulanan | Bulan pelatihan: KS ~98-99, PR-AUC ~0,92; di luar sampel (Juli-Agustus): KS ~96, PR-AUC ~0,65-0,71. KS nyaris tidak bergerak padahal PR-AUC turun sepertiga: KS tumpul pada kejadian langka |
| Fairness kuartalan (recall min/max) | semua >= 0,8 kecuali wilayah 2026Q3 (0,796, kelompok kecil) |
| Distribusi reason code | PSI maksimum 0,026 |

**Rekomendasi pemantauan:** tambahkan ke dua pemicu proposal: penurunan relatif PR-AUC (> 20% vs baseline di luar sampel), alarm nilai kosong (kenaikan > 5 poin persentase), dan jumlah fitur drift (>= 5 -> review). Model yang robust menyembunyikan gangguan input dari pemantau berbasis skor.

### 3.5 Service
* Paritas fitur training vs serving: 68 dari 68 identik (6.000 baris, 695 hari fraud).
* Replay event nyata melalui API (model v1.1) vs jalur referensi: Spearman 0,94, kesepakatan pita 97,7%; hari fraud tertangkap lewat jalur event 62% (100% pada catatan harian eksak).
* Latensi HTTP `/score` dengan reason code: p50 sekitar 34 ms, p95 sekitar 40 ms. Skor massal 65x lebih cepat setelah vektorisasi.
* Batch harian pada 319 nasabah demo: 91 (28,5%) mendapat penawaran kenaikan.

---

## 4. Temuan yang mengubah cerita proposal
1. **Aturan MVP forecast di proposal tidak cukup.** P95 outflow x 1,2 hanya menutup 76% kebutuhan puncak 30 hari; kuantil regresi menutup 94%.
2. **Dua aturan hold di §6.3 jauh lebih bising daripada model** (presisi: model high-risk 51-55%, night velocity 2,8%, outflow/balance 0,3%). Sudah diubah menjadi step-up: hold salah turun dari 795 ke sekitar 81 per 1.000 nasabah-tahun.
3. **Keamanan datang dari gate, kenyamanan dari limit dinamis.** Pengurangan fraud (-43%) berasal dari gate; limit dinamis menjaga eksposur tetap kecil (+1,6%) sambil mengurangi penolakan 32%.
4. **Step-down oleh gate berguna:** tanpa itu eksposur naik +6,1%.
5. **Hysteresis 3 evaluasi harian terlalu pendek:** churn 1,02 vs 0,75 per nasabah-tahun dengan 14 evaluasi (penyimpangan dari angka proposal, perlu diinformasikan).
6. **Atribut terproteksi tidak dibutuhkan:** model tanpa gender, usia, wilayah (+ tenure cacat) setara pada DEV dan hanya -0,017 pada data baru (tidak signifikan).
7. **Pemicu retrain proposal (PSI skor, KS) tidak cukup** untuk model yang robust dan kejadian langka (lihat 3.4).
8. **Tuning hyperparameter tidak menolong model boosting** (overfit ke noise); hanya Logistic Regression yang membaik.

---

## 5. Keselarasan dengan proposal (per bagian)
OK = selesai dan teruji | SEBAGIAN | CELAH

| Bagian | Status | Keterangan |
|---|---|---|
| 4.2 Tier dan syarat masuk | OK | batas skor dikalibrasi ulang ke persentil populasi (batas ilustratif tidak membedakan nasabah pada scorecard ini) |
| 4.3 Aturan kebijakan | OK | cooldown, naik 1 tier, jeda 24 jam, hysteresis (default 14), cold start, self-cap, pin reviewer: 24 uji |
| 4.4 Kasus tepi | SEBAGIAN | baru, data tipis, dormant, self-cap, banding, lantai baseline: OK. Musiman: fitur kalender ada, baseline "tahun lalu" belum (data 15 bulan). Rekening bersama: hanya fitur `shared_device_flag`. |
| 5.1 Sumber data | SEBAGIAN | tidak ada limit-request count, chargeback, blocking event, mule flag |
| 5.2 Kelompok fitur | SEBAGIAN | semua kelompok ada; risk history hanya 90 hari (proposal 180) |
| 5.3 Label | OK | risiko 90 hari, demand (puncak 30 hari), bisnis (declined-by-limit 30 hari) |
| 6.1 Scorecard | SEBAGIAN | LR + WoE + poin + reason code + challenger GBM ada; **KS/Gini jauh di bawah target** |
| 6.2 Forecast | OK | MVP dan pilot; MVP tidak cukup |
| 6.3 Fraud gate | SEBAGIAN | 7 dari 8 aturan (night velocity dan outflow/balance menjadi step-up); model mule-account khusus belum |
| 6.4 Tata kelola | SEBAGIAN | champion/challenger, model card, **tanpa atribut terproteksi**, fairness model dan tier, PSI/KS/migrasi/reason code ada. Belum: retrain terjadwal, registry Model Manager |
| 7 Decision flow | OK | seluruh pseudo-logika di `limit/policy.py` |
| 8 Arsitektur | SEBAGIAN | feature store (SQLite), scoring (FastAPI sebagai pengganti MAS), limit service, notifikasi (pesan). Belum: ESP, Model Manager, Intelligent Decisioning, Visual Analytics |
| 9 Journey nasabah | SEBAGIAN | mock UI lima journey (tawaran, hold, turun, atur sendiri, transparansi); belum UI nyata |
| 2.3/10 Bukti dampak | SEBAGIAN | simulasi sintetis ada; angka internal bank tidak mungkin dari data sintetis |

---

## 6. Gap: kejar atau abaikan?
| # | Gap | Upaya (hari) | Status / rekomendasi |
|---|---|---|---|
| G1 | Model fraud tanpa atribut terproteksi | 0,5 | **SELESAI** (v1.1, TEST2 lulus) |
| G2 | Fairness hasil tier per kelompok | 0,25 | **SELESAI**; temuan usia 0,60 dan wilayah 0,76 perlu ditelusuri (G15) |
| G3 | Pemantauan ringan | 0,5 | **SELESAI** (notebook 08 + modul); tambah pemicu baru ke runbook operasi |
| G4 | Impor ke SAS Viya lab | 1-2 (di lab) | **Paket siap** (preflight lulus, 25 kasus validasi, runbook); impor menunggu akses lab |
| G5 | Model mule-account khusus | 0,75 | Opsional; mule tercakup di skor fraud |
| G6 | Mock UI journey nasabah | 1 | **SELESAI** |
| G7 | Scorecard KS 35 / Gini 0,45 | tak mungkin | **Abaikan** di data sintetis; uji ulang di data bank |
| G8 | Simulasi closed-loop | 1,5 | Abaikan; sebut sebagai keterbatasan |
| G9 | Model intraday | 2 | Abaikan; data tidak punya detail intraday |
| G10 | Feature store produksi, mode shadow | 2,5 | Abaikan; hanya untuk pilot nyata |
| G11 | ESP, Visual Analytics, node Scorecard Model Studio | 3 | Abaikan; cukup di peta jalan |
| G12 | Angka internal bank untuk §2.3 | di luar kendali | Placeholder jelas |
| G13 | Aturan hold bising -> step-up | 0,25 | **SELESAI** |
| G14 | Hysteresis lebih panjang | 0,1 | **SELESAI** |
| G15 | Telusuri rasio kenaikan tier antar usia (0,60) dan wilayah (0,76) | 0,25 | **Kejar** sebelum pilot |
| G16 | **Business case** (masalah, solusi, dampak, biaya, risiko, peta jalan) | 0,5 | **Belum**; menunggu konfirmasi format dan angka internal |

Urutan yang saya sarankan: **G16**, lalu **G4** bila lab tersedia, lalu G15 dan G5 bila waktu ada.

---

## 7. Model: sudah OK? bisa diekspor ke SAS Viya lab?
**Model fraud v1.1: OK untuk kompetisi.** Lulus seluruh kriteria TEST2, tahan data tidak lengkap, stabil antar seed, adil menurut recall per kelompok, dan tidak memakai atribut terproteksi.

**Scorecard:** OK sebagai komponen yang bisa dijelaskan, tetapi lemah. Jangan mengklaim memenuhi target KS/Gini proposal. **Forecast kuantil: OK** (coverage 94%).

**Ekspor ke SAS Viya lab: paket siap dan diuji lokal, belum dicoba di Viya.** Jalur resmi: `sasctl.pzmm` (pickle + metadata + kode skor) ke Model Manager, lalu publish ke MAS.

| Hal | Status |
|---|---|
| Pickle dan metadata (`inputVar` 63 variabel, `outputVar`, `ModelProperties`, `fileMetadata`, `requirements`) | dibuat `sasctl` 1.12.0 |
| Kode skor MAS | diuji lokal: selisih skor 4,4e-08; pita dan kualitas identik 100%, termasuk 15% input kosong |
| 25 kasus validasi (5 dengan nilai kosong) dan `verify_validation_cases.py` | lulus lokal; skrip ini dijalankan **di lingkungan Python lab sebelum mendaftarkan model** |
| `preflight.py` | SIAP DIIMPOR |
| Scorecard sebagai DATA step | identik dengan Python (0,000 poin selisih, 5.000 baris) |
| Mesin kebijakan | bukan model: diimplementasikan ulang di Intelligent Decisioning memakai tabel aturan di notebook 05 |
| PMML | tidak disarankan (laporan komunitas: masalah pada ensemble pohon) |
| ONNX | hanya lewat jalur ASTORE; XGBoost dengan fitur kategorikal tidak tertangani konverter standar |

Risiko terbesar di lab: versi `xgboost`/`pandas`/`numpy` di lingkungan Python MAS (lihat `requirements.json`), penanganan kategori (kode skor sudah dibuat khusus), dan konvensi MAS. Langkah dan tabel masalah ada di `sas_export/LAB_RUNBOOK.md`.

---

## 8. Apa yang tidak boleh diklaim
* Bahwa limit dinamis **menurunkan fraud**. Yang menurunkan fraud adalah fraud gate.
* Bahwa angka-angka ini adalah prakiraan finansial (simulasi open-loop, data sintetis, penerimaan penawaran 70%, keefektifan hold 50%).
* Bahwa scorecard memenuhi KS >= 35 / Gini >= 0,45.
* Bahwa model sudah berjalan di SAS Viya.

Keterbatasan teknis: model dilatih pada agregat akhir hari (skor di tengah hari konservatif); SQLite hanya cocok untuk satu replika; Docker image dan manifest k8s belum pernah di-build/apply; `kubectl --dry-run=client` pernah menghubungi cluster dari kubeconfig (hanya baca, ditolak, tidak ada perubahan), jadi jangan dipakai untuk validasi manifest.

---

## 9. Runbook demo
```bash
cd <project-root>
.venv/bin/python serving/seed_store.py                                      # isi feature store demo (data DEV)
STORE_PATH=serving/demo.db .venv/bin/python -m uvicorn app:app --app-dir serving --port 8080 &
.venv/bin/python serving/demo.py                                            # tiga jenis fraud ditahan, dua nasabah normal lolos
# uji
.venv/bin/python serving/test_rolling_parity.py && .venv/bin/python serving/test_service.py && .venv/bin/python serving/test_limit.py
.venv/bin/python sas_export/preflight.py && .venv/bin/python sas_export/test_sas_package.py
```
Notebook (di `notebooks/`) dibuka berurutan: 02 -> 02b -> 02c -> 03 (model v1), 07 -> 07b (model v1.1), 04 (scorecard + forecast), 05 (kebijakan + simulasi), 06 (audit), 08 (pemantauan). Jupyter: `.venv/bin/jupyter lab`. Mock UI: `mock_ui/index.html` (atau halaman terbitnya).

---

## 10. Sumber referensi SAS
* [Registrasi model Python di SAS Viya Workbench](https://blogs.sas.com/content/sgf/2025/09/30/register-python-models-in-sas-viya-workbench/)
* [MLOps for Pirates and Snakes: sasctl](https://blogs.sas.com/content/subconsciousmusings/2023/08/11/mlops-for-pirates-and-snakes/)
* [sassoftware/model-management-resources](https://github.com/sassoftware/model-management-resources)
* [sassoftware/onnx-integration](https://github.com/sassoftware/onnx-integration)
* [Impor PMML ke Model Manager (komunitas)](https://communities.sas.com/t5/SAS-Viya/Import-PMML-model-to-Model-Manager/m-p/764262)
