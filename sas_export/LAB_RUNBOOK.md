# Runbook: impor model fraud v1.1 ke SAS Viya (Model Manager) di lab

Paket: `sas_export/fraudXgbMaskV11/` dan `fraudXgbMaskV11.zip`. Status: diuji lokal **dan di Viya lab (27 Okt 2026: impor, uji 25 kasus, publish ke MAS)**; uji REST menunggu token. Runbook ini dibuat supaya kegagalan pertama terlihat sebelum model didaftarkan.

## 0. Tanyakan ke admin lab (sekali, sebelum mulai)
| Pertanyaan | Mengapa penting |
|---|---|
| Versi Viya dan apakah Model Manager serta MAS (Micro Analytic Service) aktif | menentukan perintah dan UI yang tersedia |
| Versi Python di lingkungan MAS dan paket yang terpasang (`xgboost`, `pandas`, `numpy`) | pickle XGBoost sensitif terhadap versi |
| Bolehkah memasang paket tambahan di lingkungan Python MAS | bila versinya berbeda |
| Nama tujuan publikasi MAS (biasanya `maslocal`) dan nama proyek Model Manager | untuk perintah publish |
| Izin: membuat proyek, impor model, publish ke MAS | menghindari gagal di tengah |

## 1. Cek lokal (1 menit)
```bash
python sas_export/preflight.py          # harus berakhir: PREFLIGHT: SIAP DIIMPOR
```

## 2. Cek di lingkungan Python lab SEBELUM mendaftarkan (5 menit)
Salin folder `fraudXgbMaskV11/` dan `verify_validation_cases.py` ke lingkungan Python yang sama dengan MAS (mis. SAS Viya Workbench), lalu:
```bash
python verify_validation_cases.py ./fraudXgbMaskV11
```
Hasil yang diharapkan: `25 dari 25 kasus cocok | PASS`. Bagian atas keluaran membandingkan versi paket dengan `requirements.json`.
* Versi paket BERBEDA tetapi PASS: lanjut, catat versinya.
* Gagal memuat pickle atau skor berbeda: pasang versi persis (`xgboost==3.2.0`, `pandas==2.3.3`, `numpy==2.2.6`) di lingkungan itu, atau minta saya melatih ulang dengan versi lab (bundel dan paket dibangun ulang dengan satu perintah).

## 3. Daftarkan model
**Lewat UI:** Model Manager, buat proyek (mis. *Dynamic Customer Limit*), pilih *Import*, unggah `fraudXgbMaskV11.zip`.

**Lewat sasctl** (pustaka Python resmi SAS):
```python
from sasctl import Session, pzmm
from sasctl.services import model_repository as mr, model_publish as mp
with Session("viya.host", "user", "password", verify_ssl=False):
    mr.create_project("Dynamic Customer Limit", repository="Repository")          # lewati bila proyek sudah ada
    zf = pzmm.ZipModel.zip_files(model_files="sas_export/fraudXgbMaskV11", model_prefix="fraudXgbMaskV11", is_viya4=True)
    mr.import_model_from_zip("fraud_xgb_mask_v1_1", "Dynamic Customer Limit", zf)
```
Pastikan: variabel target `fraud_today` (event = `1`), fungsi *Classification*, 63 variabel input, output `EM_EVENTPROBABILITY`, `EM_CLASSIFICATION`, `band`, `quality`.

## 4. Validasi di dalam Viya (kriteria lulus)
Skor 25 kasus di `lab_validation_cases.csv` lewat Model Manager (*Test*) atau REST MAS, lalu bandingkan dengan kolom `expected_*`:
| Pemeriksaan | Toleransi |
|---|---|
| `EM_EVENTPROBABILITY` | selisih mutlak < 1e-5 (atau relatif 1e-4) |
| `band`, `quality`, `EM_CLASSIFICATION` | harus sama persis |
| Lima kasus terakhir (nilai kosong) | tetap menghasilkan skor, bukan error |
Bila selisih besar hanya pada kasus bernilai kosong: periksa apakah MAS mengubah nilai kosong menjadi 0 (model dilatih agar kosong = tidak tersedia).
**Hasil di lab (27 Okt 2026):** lulus setelah kode skor diperbaiki agar menerima Series (lihat tabel masalah). Impor tabel uji ke CAS: pilih **Replace file** bila tabel sudah ada.

## 5. Publish ke MAS dan uji REST
```python
    mp.publish_model("fraud_xgb_mask_v1_1", "maslocal")
```
Contoh panggilan (format REST MAS; sesuaikan nama modul dan host dengan versi Viya di lab):
```
POST https://<viya>/microanalyticScore/modules/<modulId>/steps/score
{"inputs": [{"name": "out_7", "value": 1200000}, {"name": "occupation", "value": "Guru"}, ...semua 63 variabel...]}
```
Variabel yang tidak tersedia boleh dikirim kosong/`null`.

## 6. Hubungkan ke decision flow (SAS Intelligent Decisioning)
* Gunakan modul MAS di atas sebagai simpul *Scoring*; keluaran `band` dan `quality` menjadi masukan aturan.
* Aturan deterministik fraud gate dan kebijakan tier: lihat tabel aturan di `05_limit_policy_simulation.ipynb` bagian 1 dan `serving/gate.py` (keputusan akhir: hold hanya dari skor model; night velocity dan outflow/balance menjadi step-up).
* `quality = low` berarti banyak fitur tidak tersedia: jatuh ke aturan deterministik, jangan memutuskan hanya dari skor.
* Scorecard risiko: `sas_export/scorecard/scorecard_score.sas` (DATA step); batas tier di `tier_cuts.json`.

## 7. Masalah yang mungkin muncul
| Gejala | Kemungkinan penyebab | Tindakan |
|---|---|---|
| Gagal memuat pickle / `ModuleNotFoundError: xgboost` | paket tidak ada atau versi berbeda di lingkungan MAS | langkah 2; minta admin memasang versi persis |
| `ValueError` tentang kategori / dtype | masukan kategorikal terkirim sebagai tipe lain | kode skor sudah mengubah ke kategori tetap; pastikan nilai `occupation` dan `segment` berupa teks |
| Semua skor sama / mendekati 0 | nilai kosong dipaksa 0 oleh pemanggil | kirim `null`, bukan 0 |
| `settings` tidak ditemukan | konvensi MAS pada versi Viya berbeda | kirim pesan galatnya ke saya; kode skor disesuaikan |
| Nama variabel ditolak | karakter atau panjang nama | semua nama sudah huruf kecil dan garis bawah; laporkan nama yang ditolak |
| Hasil tes hanya 1 baris; log: `float() argument must be ... not 'Series'` | Model Manager memanggil `score()` dalam mode batch dengan kolom utuh | sudah diperbaiki: `score()` menerima nilai tunggal maupun Series (ulangi build paket bila memakai versi lama) |
| Impor CSV: "At least one row of input data is invalid: too many columns" | file yang dipilih bukan CSV yang dimaksud (mis. skrip `.py`) | pilih `lab_validation_cases.csv` |
| Tabel di CAS masih versi lama setelah impor ulang | opsi "If table name exists" bernilai Cancel import | pilih **Replace file** lalu segarkan sumber data di laporan |
| Peringatan XGBoost "configuration generated by an older version" saat memuat pickle | versi `xgboost` di lingkungan MAS berbeda | hanya peringatan bila skor cocok; catat versinya dan laporkan ke admin |

## 8. Laporkan balik
Versi Viya dan Python MAS, hasil langkah 2 dan 4 (jumlah kasus cocok), dan pesan galat lengkap bila ada. Dengan itu paket bisa disesuaikan tanpa menebak.

## 9. Langkah berikutnya setelah publish
1. Panggil modul lewat REST dengan `sas_export/test_mas_rest.py` (token dari admin lab; host dan nama modul ada di skrip). Hasil yang diharapkan: `25 dari 25 kasus cocok | PASS`.
2. Uji decision flow dengan `sas_export/decisioning/id_test_input.csv` dan bandingkan dengan `id_test_expected.csv` ([cetak biru](../docs/INTELLIGENT_DECISIONING_BLUEPRINT.md)).
3. Arsitektur target dan decision flow lain: [STORYBOARD.md](../docs/STORYBOARD.md), [DIAGRAMS.md](../docs/DIAGRAMS.md).
