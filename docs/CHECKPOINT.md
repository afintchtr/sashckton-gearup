# Checkpoint
Terakhir diperbarui: 23 Oktober 2026. Laporan lengkap: [PROGRESS_REPORT.md](PROGRESS_REPORT.md). Peta folder: [../README.md](../README.md).

## Tujuan
1. Finalisasi model fraud / suspicious / genuine yang terdokumentasi di notebook.
2. Layanan REST (k8s) yang mengeluarkan skor risiko transaksi.
3. Skor sebagai input decision rule di SAS Viya (fraud gate §6.3) dalam kerangka *Dynamic Customer Limit*.

## Keadaan sekarang
* **Model aktif: v1.1** (`bundle/fraud_xgb_mask_v1_1`, 63 fitur, tanpa gender/usia/wilayah/tenure cacat). TEST2: PR-AUC 0,734 (CI 0,675-0,787), 7 dari 7 kriteria lulus. v1 (`bundle/fraud_xgb_mask_v1`) tetap sebagai pembanding.
* **Data terkunci:** `data/test` (dipakai v1) dan `data/test2` (dipakai v1.1) read-only dengan manifest SHA-256; `splits.load_test()` dan `load_test2()` memverifikasi hash. Jangan dipakai untuk tuning atau seleksi.
* **Layanan** (`serving/`) memakai v1.1 secara bawaan; semua uji lolos. `deploy/Dockerfile` dan `deploy/k8s/` ditulis tetapi belum di-build/apply.
* **Paket SAS** (`sas_export/fraudXgbMaskV11`) lulus preflight dan 25 kasus validasi lokal. **Di Viya lab (23 Okt 2026):** diimpor ke proyek `sashckton-gearup`, uji tabel Model Manager 25 baris cocok dengan expected, dan sudah dipublish ke MAS (`maslocal`) sebagai modul `fraudxgbmaskv11` (step `score`). Kode skor diperbaiki agar menerima `Series` (jalur batch Model Manager). **Belum dicoba:** panggilan REST ke MAS (lihat Pending).
* **Mock UI** (`mock_ui/index.html`) dan halaman ringkasan proyek diterbitkan sebagai halaman privat.

## Langkah berikutnya (urutan yang disarankan)
1. **Business case** (G16): format dan angka internal masih perlu dikonfirmasi.
2. **Impor ke SAS Viya lab** (G4): jalankan `verify_validation_cases.py` di lingkungan Python lab, daftarkan lewat Model Manager, validasi 25 kasus, publish ke MAS.
3. Telusuri rasio kenaikan tier antar usia (0,60) dan wilayah (0,76) (G15).
4. Opsional: model mule-account khusus (G5).
5. Untuk pilot nyata: ulangi seleksi dan ambang pada data bank; feature store produksi (Redis/Postgres); mode shadow; ESP dan Intelligent Decisioning.

## Pending (menunggu admin lab SAS Viya)
* **Token OAuth / cara autentikasi REST ke MAS** (host `hack-w6i3ob4oj4.engage.sas.com`): diperlukan untuk menjalankan `sas_export/test_mas_rest.py` (25 kasus, harus `25 dari 25 kasus cocok | PASS`). Token dibaca dari `VIYA_TOKEN`; jangan ditulis ke repo.
* Tanyakan juga: versi `xgboost`/Python di lingkungan MAS (log Viya memberi peringatan pickle versi XGBoost; skor tetap cocok), dan apakah endpoint boleh diakses dari jaringan luar lab.
* Setelah REST lulus: dashboard dokumentasi di Visual Analytics (data agregat saja, bukan data nasabah mentah).

## Hal yang perlu diingat
* Semua data sintetis; angka memvalidasi pipeline, bukan perilaku produksi.
* Scorecard 90 hari lemah (KS 14,8 vs target 35); ini sifat data, bukan bug.
* Hysteresis default 14 evaluasi (proposal: 3) dan aturan night velocity/outflow-balance menjadi step-up: dua penyimpangan sadar dari proposal, beralasan di laporan.
* `kubectl --dry-run=client` pernah menghubungi cluster dari kubeconfig (hanya baca, ditolak); jangan dipakai untuk validasi manifest.
* Jupyter berjalan dari `.venv`; jangan memindahkan `.venv`.
