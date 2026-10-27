# Checkpoint
Terakhir diperbarui: 27 Oktober 2026. Laporan lengkap: [PROGRESS_REPORT.md](PROGRESS_REPORT.md). Peta folder: [../README.md](../README.md). Cerita dan diagram: [STORYBOARD.md](STORYBOARD.md), [DIAGRAMS.md](DIAGRAMS.md).

## Tujuan
1. Finalisasi model fraud / suspicious / genuine yang terdokumentasi di notebook. **Selesai** (v1.1).
2. Solusi yang berjalan di **SAS Viya sebagai pusat keputusan**: middleware → endpoint Viya → decision flow (model + aturan bisnis) → aksi ke channel dan umpan balik nasabah. Python hanya untuk pengembangan model.
3. Skor sebagai masukan decision rule (fraud gate §6.3) dalam kerangka *Dynamic Customer Limit*, dengan siklus limit (tawaran, hold, banding) sebagai decision flow tambahan.

## Arsitektur target (keputusan 27 Oktober 2026)
* **Viya** adalah satu-satunya yang diketahui middleware (endpoint gateway MAS). Model ada di Viya dan dipanggil dari Intelligent Decisioning. Viya mengirim aksi ke channel dan menerima umpan balik.
* **PostgreSQL** (kunci CIF) menyimpan semua data dan dapat diakses dari Viya dan Python. Transaksi masuk dan keluar lengkap; enrichment channel (perangkat, kredensial, lokasi) lewat **Kafka**. Master nasabah di-inject manual.
* **Python hanya untuk pengembangan:** latih ulang dan validasi, ekspor ke Viya (paket `pzmm`), oracle uji paritas, data sintetis. Bukan runtime.
* **Delapan decision flow** (F1 fraud verdict, F2 limit harian, F3 umpan balik hold, F4 tawaran dan self-cap, F5 banding, F6 notifikasi, F7 pemantauan dan retrain, F8 evaluasi ulang karena sinyal, opsional): lihat STORYBOARD frame 2.6.
* **Asumsi default nomor 1–12** berlaku sampai tim memutuskan lain (STORYBOARD frame 3.12). Yang paling perlu persetujuan: siklus hold (2), perilaku bila Viya gagal (1), alur rilis model (6).

## Keadaan sekarang
* **Model aktif: v1.1** (`bundle/fraud_xgb_mask_v1_1`, 63 fitur, tanpa gender/usia/wilayah/tenure cacat). TEST2: PR-AUC 0,734 (CI 0,675-0,787), 7 dari 7 kriteria lulus. v1 tetap sebagai pembanding.
* **Data terkunci:** `data/test` (dipakai v1) dan `data/test2` (dipakai v1.1) read-only dengan manifest SHA-256; `splits.load_test()` dan `load_test2()` memverifikasi hash. Jangan dipakai untuk tuning atau seleksi.
* **Di Viya lab (27 Okt 2026):** paket `fraudXgbMaskV11.zip` diimpor ke SAS Model Manager (proyek `sashckton-gearup`); uji tabel 25 kasus cocok dengan skor yang diharapkan; **dipublish ke MAS (`maslocal`) sebagai modul `fraudxgbmaskv11`**, langkah `score`. Satu bug ditemukan dan diperbaiki: Model Manager memanggil `score()` dengan kolom utuh (Series), sehingga kode skor kini menerima nilai tunggal maupun Series. **Belum:** panggilan REST ke MAS (menunggu token OAuth) dan versi persis Python/xgboost di MAS.
* **Decision flow F1** dilaporkan sudah dibangun di Intelligent Decisioning; **belum diverifikasi** dengan berkas uji kita (`sas_export/decisioning/id_test_input.csv`, 43 kasus). Cetak biru, variabel, rule set, dan implementasi rujukan: [INTELLIGENT_DECISIONING_BLUEPRINT.md](INTELLIGENT_DECISIONING_BLUEPRINT.md). Tabel aturan terbukti setara dengan `serving/gate.py` pada 3.000 baris acak.
* **Visual Analytics:** laporan 6 halaman (performa model, band risiko, ketahanan, fairness, pemantauan, model limit); tabel pendukung di `sas_export/va_data/` (dibangun `sas_export/build_va_data.py`).
* **Python backend (`serving/`):** prototipe dan **spesifikasi/oracle**; logikanya (fitur bergulir, gate, mesin limit) harus diimplementasikan ulang di Viya dan diuji setara. `deploy/` (Dockerfile, k8s) tidak lagi bagian dari arsitektur target dan tidak pernah di-build.
* **Paket bukti data latih:** `sas_export/lab_evidence/training_sample_20mb.csv` (42.395 baris, stratified, manifest dengan SHA-256); dibuat ulang dengan `sas_export/build_lab_sample.py`. Bukan representatif terhadap base rate.
* **Dokumen rujukan baru:** [FEATURES_AND_EXPLAINABILITY.md](FEATURES_AND_EXPLAINABILITY.md) (kamus 63 fitur, pengaruh fitur, penjelasan), [STORYBOARD.md](STORYBOARD.md), [DIAGRAMS.md](DIAGRAMS.md).
* **Mock UI** (`mock_ui/index.html`) dan halaman ringkasan proyek diterbitkan sebagai halaman privat.

## Langkah berikutnya (urutan yang disarankan)
1. **Uji F1 di Viya** dengan `id_test_input.csv` dan bandingkan dengan `id_test_expected.csv`. Sesuaikan bila nama variabel berbeda.
2. **Cek kemampuan lab:** membaca PostgreSQL dari dalam decision (node query/lookup atau code file), caslib ke Postgres (Manage Data → Sources → Add), Workflow Manager, job terjadwal, jaringan keluar dari cluster ke database.
3. **REST ke MAS:** token OAuth, lalu `sas_export/test_mas_rest.py` (modul `fraudxgbmaskv11`; untuk decision cek nama modul dan langkahnya di daftar modul).
4. **Putuskan aturan `quality = low`** (usulan: STEP_UP_AUTH bila verdict ALLOW) dan alur rilis model.
5. **Bangun F2** (limit harian): scorecard (`sas_export/scorecard/scorecard_score.sas`), prakiraan kebutuhan (model Python → ekspor sebagai model kedua di MAS), kebijakan tier (`limit/policy.py` sebagai spesifikasi). Lalu F3–F7.
6. **Lapisan data:** Postgres (skema di STORYBOARD frame 3.2), sink Kafka, agregator, job fitur harian 01.30; **uji paritas fitur** dengan oracle Python (`serving/rolling.py`, `serving/test_rolling_parity.py`).
7. **Business case** (G16): format dan angka internal masih perlu dikonfirmasi.
8. Telusuri rasio kenaikan tier antar usia (0,60) dan wilayah (0,76) (G15).
9. Opsional: model mule-account khusus (G5); latih ulang tanpa `occupation` (peringkat 5 pengaruh, kemungkinan artefak simulasi); tambahkan alasan (`reasons`) sebagai keluaran kode skor MAS.
10. Untuk pilot nyata: ulangi seleksi dan ambang pada data bank; mode shadow.

## Pending (menunggu admin lab SAS Viya)
* **Token OAuth / cara autentikasi REST ke MAS** (host `hack-w6i3ob4oj4.engage.sas.com`): diperlukan untuk menjalankan `sas_export/test_mas_rest.py` (25 kasus, harus `25 dari 25 kasus cocok | PASS`). Token dibaca dari `VIYA_TOKEN`; jangan ditulis ke repo.
* Versi `xgboost`/Python di lingkungan MAS (Viya memberi peringatan pickle versi XGBoost saat memuat model; skor tetap cocok), dan apakah endpoint boleh diakses dari jaringan luar lab.
* Ketersediaan: SAS Data Connector dan SAS/ACCESS untuk PostgreSQL, Workflow Manager, ESP, Visual Forecasting.
* Setelah REST lulus: uji F1 end-to-end dan dashboard pemantauan dari log keputusan.

## Hal yang perlu diingat
* Semua data sintetis; angka memvalidasi pipeline, bukan perilaku produksi.
* Scorecard 90 hari lemah (KS 14,8 vs target 35); ini sifat data, bukan bug.
* Hysteresis default 14 evaluasi (proposal: 3) dan aturan night velocity/outflow-balance menjadi step-up: dua penyimpangan sadar dari proposal, beralasan di laporan.
* **Fitur hari berjalan (17 fitur, ±41% pengaruh model) harus segar.** Bila dihitung ulang di Viya, transaksi yang sedang diputuskan harus ikut dihitung, dan hasilnya harus identik dengan fitur latih.
* Modul MAS hanya mengeluarkan `EM_EVENTPROBABILITY`, `EM_CLASSIFICATION`, `band`, `quality`. Alasan SHAP dan kode gate tidak ikut kecuali kode skor diperluas.
* Intelligent Decisioning adalah mesin keputusan tanpa state dan timer (perlu dicek di lab); timer (24 jam, jeda 7 hari, kedaluwarsa tawaran) dan state limit dipegang job terjadwal dan Postgres.
* Saat mengimpor CSV ke CAS lewat Manage Data, pilih **Replace file** (bukan Cancel import), dan pastikan yang dipilih adalah CSV yang benar.
* `kubectl --dry-run=client` pernah menghubungi cluster dari kubeconfig (hanya baca, ditolak); jangan dipakai untuk validasi manifest.
* Jupyter berjalan dari `.venv`; jangan memindahkan `.venv`.
