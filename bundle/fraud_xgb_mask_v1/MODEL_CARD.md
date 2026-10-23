# Model card - fraud risk score v1 (XGB `mask`)
**Status:** kandidat juara, dilatih dan divalidasi pada data **sintetis** (SAS Hackathon 2026). Belum diuji pada data TEST dan belum divalidasi pada data bank.

## Tujuan dan penggunaan
Memberi skor risiko fraud (0-1, terkoreksi prior) untuk satu nasabah-hari dan pita `genuine / suspicious / high_risk`.
Dimaksudkan sebagai **salah satu input** fraud gate di decision flow (proposal §6.3), bukan keputusan tunggal. Skor model saja tidak boleh menurunkan limit nasabah; hanya gate real-time dengan bukti langsung.

## Data dan fitur
* Training: 359,008 nasabah-hari (DEV Okt 2025-Jun 2026, negatif di-down-sample 3%); 68 fitur (profil, perilaku 1/7/30/90 hari, device, payee, merchant, geo, pass-through).
* Dikeluarkan sengaja: `fraud_alerts_90`, `disputes_90` (bocor sirkular), `near_limit_days_90`, `declined_cnt_90`, `baseline_tier` (bergantung pada limit saat ini).
* Resep pelatihan `mask`: data asli + satu salinan yang 0-40% selnya dikosongkan acak, agar model tahan terhadap fitur tidak lengkap.

## Hasil (PR-AUC berbobot, prevalensi asli ~0,035%)
| Kondisi | CV 5-fold | Uji maju | DEV-OOT Jul-Sep |
|---|---|---|---|
| Bersih | 0,762 | 0,726 | 0,654 (CI95 0,557-0,777) |
| 20% nilai hilang | 0,589 | 0,568 | 0,506 (CI95 0,415-0,602) |
| Tanpa fitur payee | 0,498 | 0,529 | 0,399 |
Stabilitas antar seed (DEV-OOT): bersih 0,669 ± 0,015; 20% hilang 0,532 ± 0,017. Baseline rule gate §6.3: ~0,009. Logistic Regression: 0,209.

## Ambang pita (pada `score`)
suspicious >= 0.00254 (presisi >= 5% di DEV-OOT); high_risk >= 0.05366 (presisi >= 50%).

## Keterbatasan
* Data sintetis: angka hanya memvalidasi pipeline. Seleksi, ambang, dan klaim performa harus diulang pada data bank (atau sampel anonim) sebelum dipakai.
* Fraud *stealth* (sinyal lemah) tetap lebih sulit; tanpa fitur payee performa turun sekitar 40%.
* Biaya augmentasi: PR-AUC bersih DEV-OOT lebih rendah ~0,04 dibanding model tanpa augmentasi (0,699), sebagai harga ketahanan terhadap data tidak lengkap.
* Model dilatih pada pool tanpa DEV-OOT; belum dilatih ulang pada seluruh data.

## Pemantauan yang disarankan
Tingkat kekosongan fitur per grup, distribusi skor/pita (PSI > 0,25 -> retrain), KS/PR-AUC pada label yang matang, laju alert per kelompok usia dan gender, proporsi `quality = low`.
## Hasil TEST (data baru, seed 8; dijalankan sekali)
* PR-AUC bersih 0.716 (CI95 0.639-0.781); 20% nilai hilang 0.535; tanpa payee 0.459; rule gate §6.3 0.015.
* Kriteria: C1 lulus; C2 lulus; C3 lulus; C4 lulus; C5 lulus.
* Detail: `artifacts/test_results.json`, notebook `03_final_test.ipynb`.
