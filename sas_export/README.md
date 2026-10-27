# Paket ekspor untuk SAS Viya (lab)
**Paket yang direkomendasikan: v1.1** (`fraudXgbMaskV11`, 63 fitur, tanpa gender/usia/wilayah). Mulai dari [LAB_RUNBOOK.md](LAB_RUNBOOK.md); jalankan `python sas_export/preflight.py` lebih dulu. Paket v1 (68 fitur) bisa dibangun ulang dengan `build_sas_package.py --version v1` bila perlu.

## Isi
| Path | Fungsi | Status verifikasi |
|---|---|---|
| `fraudXgbMaskV11/fraudXgbMaskV11.pickle` | model fraud (XGBClassifier mentah, bukan pembungkus notebook) | dimuat ulang dan dipakai oleh kode skor di bawah |
| `fraudXgbMaskV11/score_fraudXgbMaskV11.py` | kode skor gaya MAS (`score(...)` dengan 63 input, `settings.pickle_path`) | **diuji lokal** dengan modul `settings` tiruan: selisih skor vs bundel maks 4,38e-08, pita dan kualitas identik 100% (600 baris, 15% nilai sengaja `None`) |
| `fraudXgbMaskV11/inputVar.json`, `outputVar.json`, `ModelProperties.json`, `fileMetadata.json`, `requirements.json` | metadata Model Manager, dihasilkan `sasctl.pzmm` 1.12.0 | dibuat oleh pustaka resmi; **diterima Model Manager saat impor (27 Okt 2026)** |
| `fraudXgbMaskV11.zip` | semua file di atas siap diunggah | - |
| `scorecard/scorecard_score.sas` | scorecard WoE sebagai DATA step (poin per fitur + `risk_points`) | **diuji**: logika DATA step (di-parse ulang) identik dengan scorecard Python, selisih 0,000 poin pada 5.000 baris |
| `scorecard/scorecard.json`, `tier_cuts.json` | spesifikasi bin/poin dan batas tier | - |
| `fraudXgbMaskV11/lab_validation_cases.csv` | 25 kasus input (5 dengan nilai kosong) beserta skor yang harus direproduksi di Viya | skor dihitung dari bundel v1.1 |
| `verify_validation_cases.py` | pemeriksaan mandiri untuk dijalankan di lingkungan Python lab sebelum mendaftarkan model | lulus lokal (25 dari 25) |
| `preflight.py` | kelengkapan paket + kasus validasi di interpreter bersih | SIAP DIIMPOR |
| `build_sas_package.py`, `test_sas_package.py` | membangun ulang dan menguji paket (termasuk jalur batch Series) | - |
| `test_mas_rest.py` | memanggil modul MAS lewat REST untuk 25 kasus dan membandingkan dengan `expected_*` (token dari `VIYA_TOKEN`) | belum dijalankan (menunggu token) |
| `decisioning/` | variabel, rule set (fraud gate, verdict), 43 kasus uji dan hasil yang diharapkan untuk Intelligent Decisioning, implementasi rujukan | setara `serving/gate.py` pada 3.000 baris acak; belum diuji di Viya |
| `build_va_data.py`, `va_data/` | tabel agregat untuk Visual Analytics | dipakai laporan 6 halaman |
| `build_lab_sample.py`, `lab_evidence/` | sampel latih 20 MB (stratified) dan manifest SHA-256 | CSV tidak masuk git; dibuat ulang dengan skrip |

## Cara mendaftarkan di lab (sudah dijalankan lewat UI pada 27 Okt 2026; lihat "Hasil di lab")
```python
from sasctl import Session, pzmm
from sasctl.services import model_repository as mr, model_publish as mp
with Session("viya.host", "user", "password", verify_ssl=False):
    zf = pzmm.ZipModel.zip_files(model_files="sas_export/fraudXgbMaskV11", model_prefix="fraudXgbMaskV11", is_viya4=True)
    mr.import_model_from_zip("fraud_xgb_mask_v1_1", "Dynamic Customer Limit", zf)      # proyek harus ada; buat dengan mr.create_project(...)
    mp.publish_model("fraud_xgb_mask_v1_1", "maslocal")                                 # tujuan MAS bawaan
```
Alternatif tanpa skrip: unggah `fraudXgbMaskV11.zip` lewat UI SAS Model Manager (Import).

## Risiko yang dicek di lab (hasilnya di bagian berikutnya)
1. **Versi paket di lingkungan Python MAS** harus kompatibel dengan model: `xgboost==3.2.0`, `pandas==2.3.3`, `numpy==2.2.6` (lihat `requirements.json`). Versi XGBoost yang berbeda dapat gagal memuat pickle atau mengubah penanganan kategori.
2. **Fitur kategorikal.** Model dilatih dengan `enable_categorical`. Kode skor pzmm otomatis akan mengirim string biasa dan gagal, sehingga kode skor di sini membangun `pd.Categorical` dengan daftar kategori tetap. Kategori tak dikenal dianggap tidak tersedia.
3. **Konvensi MAS** (`import settings`, `settings.pickle_path`, docstring `Output:`) mengikuti pola kode skor yang dihasilkan pzmm; konvensi persisnya perlu dikonfirmasi pada versi Viya di lab.
4. **Nilai kosong:** MAS harus meneruskan `None`/missing untuk fitur yang tidak ada; model dilatih untuk itu, tetapi cek bahwa MAS tidak memaksa 0.
5. **Format lain:** PMML tidak disarankan (laporan komunitas menyebut masalah pada model ensemble pohon, dan dukungan versi PMML bergantung pada rilis Viya); ONNX masuk lewat jalur ASTORE (bukan impor langsung Model Manager) dan XGBoost dengan fitur kategorikal tidak tertangani konverter ONNX standar.
6. **Bagian lain dari proposal tidak berupa model:** forecast kuantil dapat dikemas dengan cara yang sama (pickle + kode skor); **mesin kebijakan** (`limit/policy.py`) perlu diimplementasikan ulang sebagai rule set di SAS Intelligent Decisioning memakai tabel aturan di notebook 05 bagian 1.

## Hasil di lab Viya (27 Oktober 2026)
* **Impor** `fraudXgbMaskV11.zip` lewat Model Manager berhasil (proyek `sashckton-gearup`): Python, XGBoost, Classification, 63 variabel input. Konvensi `import settings` dan `settings.pickle_path` berfungsi di versi lab.
* **Uji tabel 25 kasus:** skor cocok dengan `expected_*` (probabilitas, `band`, `quality`, `EM_CLASSIFICATION`); lima kasus bernilai kosong tetap menghasilkan skor.
* **Bug yang ditemukan dan diperbaiki:** pada percobaan pertama hasil hanya 1 baris. Log menunjukkan Model Manager memanggil `score()` dalam **mode batch dengan kolom utuh (pandas Series)**, kode lama memanggil `float(Series)` lalu gagal dan jatuh ke mode baris-per-baris yang hanya mempertahankan satu baris. Kode skor kini menerima nilai tunggal (mengembalikan tuple) maupun Series (mengembalikan DataFrame 4 kolom); `test_sas_package.py` menguji kedua jalur (selisih 0).
* **Peringatan XGBoost** saat memuat pickle ("configuration generated by an older version"): hanya peringatan, skor tetap cocok. Catat versi `xgboost` di lingkungan MAS.
* **Publish ke MAS** (`maslocal`): modul `fraudxgbmaskv11`, langkah `score`. Daftar modul: `/microanalyticScore/modules`.
* **Belum:** panggilan REST (token OAuth), versi persis Python/xgboost di MAS, dan uji decision flow dengan berkas di `decisioning/`.
* **Pelajaran impor data ke CAS:** pilih **Replace file** bila tabel sudah ada (default "Cancel import" membatalkan diam-diam, sehingga tabel lama tetap dipakai), dan pastikan file yang dipilih adalah CSV yang benar (memilih `.py` menghasilkan galat "too many columns").

## Sumber
* [Registrasi model Python di SAS Viya Workbench (sasctl/pzmm)](https://blogs.sas.com/content/sgf/2025/09/30/register-python-models-in-sas-viya-workbench/)
* [MLOps for Pirates and Snakes: paket sasctl](https://blogs.sas.com/content/subconsciousmusings/2023/08/11/mlops-for-pirates-and-snakes/)
* [sassoftware/model-management-resources](https://github.com/sassoftware/model-management-resources)
* [sassoftware/onnx-integration (ONNX lewat ASTORE)](https://github.com/sassoftware/onnx-integration)
* [Diskusi komunitas: impor PMML ke Model Manager](https://communities.sas.com/t5/SAS-Viya/Import-PMML-model-to-Model-Manager/m-p/764262)
