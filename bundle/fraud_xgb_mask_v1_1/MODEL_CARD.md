# Model card - fraud risk score v1.1 (XGB `mask`, tanpa atribut terproteksi)
**Status:** kandidat juara untuk data **sintetis** (SAS Hackathon 2026). Menggantikan v1; evaluasi akhir pada data baru ada di `07b_final_test_v1_1.ipynb`.

## Perubahan dari v1
* Gender, usia, dan wilayah **tidak** menjadi fitur (proposal §6.4); `schema.json` mencatat `protected_attributes_in_model: []`.
* `tenure_months` dan `hist_days` dikeluarkan karena dihitung dari awal jendela data, bukan dari pembukaan rekening.
* 63 fitur (v1: 68). Resep pelatihan dan hyperparameter sama (augmentasi `mask`, XGBoost bawaan).

## Hasil DEV Jul-Sep (rata-rata 3 seed)
PR-AUC bersih 0.670 (std seed 0.008); 20% nilai hilang 0.511 (std 0.002). Model v1: 0.661 dan 0.486.

## Ambang pita (pada `score`)
suspicious >= 0.00247 (presisi >= 5% di DEV-OOT); high_risk >= 0.04664 (presisi >= 50%).

## Keterbatasan
Data sintetis; angka memvalidasi pipeline, bukan perilaku produksi. Fraud stealth tetap lebih sulit. Model dilatih pada agregat akhir hari. Seleksi, ambang, dan klaim performa harus diulang pada data bank sebelum dipakai.
## Hasil TEST2 (data baru, seed 10; dijalankan sekali)
* PR-AUC bersih 0.734 (CI95 0.675-0.787); 20% nilai hilang 0.558; tanpa payee 0.466; aturan gate saja 0.008; v1 pada data yang sama 0.752.
* Kriteria: C1 lulus; C2 lulus; C3 lulus; C4 lulus; C5 lulus; C6 lulus; C7 lulus.
* Detail: `artifacts/test2_results.json`, notebook `07b_final_test_v1_1.ipynb`.
