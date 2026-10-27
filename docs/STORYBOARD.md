# Storyboard v2: Dynamic Customer Limit di SAS Viya (SAS Hackathon 2026, Team Gear Up)

Diperbarui 27 Oktober 2026. Menggantikan versi 1. Sumber: proposal (`Dynamic-Customer-Limit-Proposal.docx` v1.0, 30 Sep 2026), laporan progres ([PROGRESS_REPORT.md](PROGRESS_REPORT.md)), hasil kerja di Viya lab, dan keputusan arsitektur terbaru.

**Keputusan arsitektur yang mendasari versi ini**
- **SAS Viya adalah pusat.** Middleware hanya mengenal endpoint gateway Viya. Model ada di Viya dan dipanggil dari SAS Intelligent Decisioning (banyak decision flow). Viya mengirim aksi ke channel dan menerima umpan balik nasabah.
- **PostgreSQL** (kunci CIF) menyimpan semua data dan dapat diakses dari Viya dan Python. Transaksi masuk dan keluar lengkap; enrichment channel (perangkat, kredensial, lokasi) masuk lewat **Kafka**.
- **Python hanya untuk pengembangan model:** latih ulang, validasi, ekspor ke Viya; juga oracle uji dan data sintetis. Python **bukan** runtime.
- Tujuan kompetisi: **memaksimalkan Viya**.
- Asumsi default nomor 1–12 berlaku (frame 3.12). Hal yang bergantung pada versi Viya ditandai **[cek lab]**.

Semua angka dampak berasal dari **data sintetis** dan **simulasi open-loop** (dijalankan dengan logika Python yang kini harus diimplementasikan ulang di Viya, lalu diuji kesetaraannya). Tiap frame memuat *Visual*, *Narasi*, *Bukti*, dan bila perlu *Jujur*. Diagram: [DIAGRAMS.md](DIAGRAMS.md).

Empat bab: [1 Masalah](#bab-1-masalah), [2 Apa yang dilakukan](#bab-2-apa-yang-dilakukan), [3 Mekanisme kerja](#bab-3-mekanisme-kerja), [4 Dampak](#bab-4-dampak).

---

## Bab 1. Masalah

### Frame 1.1 Dua nasabah, satu batas yang sama
**Visual:** dua kartu. Kiri: Budi, pemilik usaha kecil, transfer ke pemasok ditolak "melebihi limit harian". Kanan: Sari, korban penipuan, memindahkan seluruh saldo ke rekening penipu dalam satu sesi, sampai tepat di batas limit.
**Narasi:** limit yang sama membuat nasabah sah gagal membayar dan, di saat yang sama, memberi ruang bagi penipu.
**Bukti:** proposal §1: limit ditetapkan menurut kelas kartu saat onboarding dan berubah hanya lewat cabang atau telepon.

### Frame 1.2 Dua kegagalan sekaligus
| Nyeri nasabah | Nyeri bank |
|---|---|
| Kebutuhan tumbuh, ditolak di saat butuh | Transaksi ditolak karena limit bocor ke kompetitor dan e-wallet |
| Satu-satunya jalan: permohonan manual, makan hari | Limit tinggi yang statis adalah ruang yang dipakai penipu |
| Limit besar yang tak pernah dipakai = eksposur fraud tanpa pendapatan | Tiap perubahan manual = biaya operasional dan temuan audit tanpa dasar analitik |
**Narasi:** satu angka statis tidak mungkin tepat untuk semua nasabah. **Bukti:** proposal §2.1–2.2.

### Frame 1.3 Bukti dari luar
**Bukti (menurut proposal §2.3):** Rp9,1 triliun kerugian scam dilaporkan, 432.637 laporan (Nov 2024 – Jan 2026), 94.344 rekening diblokir (OJK / Indonesia Anti-Scam Centre). Brasil mewajibkan jeda 24 jam sebelum kenaikan limit PIX berlaku; Singapura menahan 24 jam arus keluar berisiko; Malaysia 12 jam (Maybank, Jul 2024). Kenaikan limit kredit otomatis menaikkan utang konsumen ±30% dari kenaikannya dalam enam bulan (Federal Reserve, Jan 2026), karena itu kartu kredit dikeluarkan dari lingkup.
**Jujur:** angka internal BRI belum ada (transaksi ditolak per bulan, permintaan perubahan limit, waktu penanganan, kerugian fraud di atas tier 3). **Business case belum terisi.**

### Frame 1.4 Ketegangan inti
**Narasi:** menaikkan limit tanpa pengaman memperbesar fraud; menahan limit tanpa data merugikan nasabah baik. Solusinya **kenaikan yang lambat dan diblokir gate, penurunan yang cepat**. **Bukti:** proposal §4.1.

### Frame 1.5 Tujuan dan lingkup
1. Setiap nasabah layak mendapat limit sesuai kebutuhan dan risiko yang terbukti, tanpa permohonan manual.
2. Setiap kenaikan lolos **fraud gate**, sehingga limit dinamis mengurangi, bukan menambah, eksposur fraud.
3. Setiap keputusan **dapat dijelaskan dan diajukan banding** (POJK 22/2023, UU PDP 27/2022).
4. Dibangun di atas stack **SAS Viya**, tanpa perubahan core banking untuk pilot.
**Lingkup:** limit transfer harian BRImo (BI-FAST, RTOL, QRIS), individu dengan tenure ≥ 12 bulan dan inflow bulanan teratur. *Di luar lingkup:* kartu kredit, rekening korporasi, optimasi reinforcement learning.

---

## Bab 2. Apa yang dilakukan

### Frame 2.1 Solusi dalam satu kalimat
**Narasi:** *Dynamic Customer Limit* mengganti tier statis dengan **mesin limit berbasis perilaku, dijaga gate fraud, dan dapat dijelaskan**, dijalankan sepenuhnya di SAS Viya.
| Dimensi | Hari ini | Dengan Dynamic Customer Limit |
|---|---|---|
| Penetapan limit | kelas kartu saat onboarding | tier perilaku, dievaluasi harian |
| Jalur naik | cabang atau call center, berhari-hari | tawaran di aplikasi, diterima nasabah, berlaku 24 jam |
| Kontrol fraud pada kenaikan | tidak ada yang khusus | gate real-time (skor fraud, perangkat, payee, kredensial, aktivitas malam) |
| Jalur turun | jarang, manual | hold atau step-down seketika, dengan notifikasi dan banding |
| Penjelasan | tidak ada | alasan pada setiap keputusan |
| Tata kelola | dokumen kebijakan | champion/challenger, pemantauan, log keputusan |

### Frame 2.2 Prinsip: naik pelan, turun cepat
**Aturan kebijakan (proposal §4.3):** evaluasi harian 02.00; satu tier per siklus; jeda 7 hari antar perubahan; berlaku 24 jam setelah diterima; histeresis; mulai dari tier dasar; re-KYC di T4 dan T5; nasabah boleh menurunkan limit sendiri seketika, menaikkan hanya lewat tawaran.
**Lantai yang tidak boleh ditawar (§4.4):** *nasabah tidak pernah lebih buruk daripada limit statis hari ini, kecuali gate fraud menyala berdasarkan bukti langsung.* (Proposal minta kalimat ini disebut apa adanya saat pitch.)
**Jujur:** histeresis di proposal 3 evaluasi, kita pakai **14**, karena evaluasi harian membuat 3 hanya berarti 3 hari (churn 1,02 vs 0,75 per nasabah-tahun).

### Frame 2.3 Lima tier
| Tier | Batas harian | Profil | Syarat masuk (ringkas) |
|---|---|---|---|
| T1 | Rp10 juta | baru / aktivitas rendah | tenure < 12 bulan atau tanpa inflow teratur |
| T2 | Rp25 juta | bergaji, stabil | skor ≥ 550, 3 bulan inflow teratur, tanpa sengketa |
| T3 | Rp50 juta | mapan, pemakaian tumbuh | skor ≥ 620, prakiraan > 60% T2, 6 bulan di T2 |
| T4 | Rp100 juta | aktivitas tinggi, mirip UMKM | skor ≥ 680, 6 bulan di T3, re-KYC ≤ 24 bulan |
| T5 | Rp200 juta | bernilai tinggi, terverifikasi | skor ≥ 740, tinjauan manual pada masuk pertama |
**Jujur:** nilai tier adalah placeholder di bawah plafon BI-FAST Rp250 juta; Produk dan Risk yang memutuskan angka akhir.

### Frame 2.4 Fraud gate (delapan pemeriksaan)
| Pemeriksaan | Ambang |
|---|---|
| Skor fraud model | band high_risk (H: hold) |
| Skor rekening mule | ada (H), rujuk AML (**belum dibangun**: tidak ada model mule khusus) |
| Perangkat baru | pertama terlihat < 7 hari |
| Ganti kredensial / telepon / email | < 72 jam |
| Payee pertama dengan nominal besar | > 50% batas harian, step-up |
| Kecepatan malam | > 30% batas harian pukul 22.00–05.00 |
| Arus keluar terhadap saldo | > 50% saldo dan saldo > Rp50 juta |
| Alert atau sengketa terbuka | ada |
**Perubahan yang sudah kita putuskan:** dua aturan *hold* (malam dan arus-keluar) terlalu bising (presisi 2,8% dan 0,3%), dijadikan **step-up**. Hold salah turun dari 795 ke sekitar 81 per 1.000 nasabah-tahun; hold hanya dari skor model.

### Frame 2.5 Alur keputusan harian
**Visual:** Gambar 1 proposal (*Daily scoring run → Fraud gate hit? → Enough history? → Scorecard + capacity forecast → bandingkan tier → Offer +1 / Step down / Keep → Log*). Pseudo-logika proposal §7: gate kena → tidak naik (hold bila hit H); riwayat < 6 bulan → KEEP; selain itu `rec = MIN(batas skor, tier dari prakiraan × 1,2, tier sekarang + 1)`.

### Frame 2.6 Delapan decision flow di Viya
**Visual:** peta delapan kotak, tiap kotak dengan pemicunya.
| Flow | Pemicu | Masukan utama | Keluaran |
|---|---|---|---|
| **F1 Fraud verdict** | middleware, per transaksi | CIF dan transaksi yang diputuskan; fitur dari Postgres | ALLOW / STEP_UP_AUTH / HOLD, band, kode aturan |
| **F2 Limit harian** | job terjadwal 02.00 | fitur, skor scorecard, prakiraan, state limit | KEEP / OFFER / STEP_DOWN / HOLD, alasan, state baru |
| **F3 Umpan balik step-up dan hold** | umpan balik dari channel | hasil verifikasi atau konfirmasi | lepas / blokir + kasus fraud / kedaluwarsa |
| **F4 Tawaran dan self-cap** | umpan balik nasabah | terima tawaran atau batas baru | berlaku +24 jam / berlaku seketika |
| **F5 Banding** | permintaan nasabah | alasan keberatan | tiket, hasil tinjauan, kunci tier 6 bulan |
| **F6 Notifikasi** | keluaran flow lain | aksi dan alasan | isi pesan, kanal, waktu kirim (setelah 07.00 untuk tawaran) |
| **F7 Pemantauan dan retrain** | job terjadwal | log keputusan, hasil matang | status, pemicu, peringatan |
| F8 Evaluasi ulang karena sinyal (opsional) | kejadian enrichment | perangkat baru, ganti kredensial, alert | skor ulang |
**Narasi:** Intelligent Decisioning adalah mesin keputusan; ia tidak menyimpan state atau menjalankan timer. Timer dan state dipegang job terjadwal Viya dan Postgres **[cek lab]**.

### Frame 2.7 Pembagian peran
| Pihak | Peran | Bukan perannya |
|---|---|---|
| **SAS Viya** | gateway (MAS), model, semua decision flow, job terjadwal, tata kelola (Model Manager), dashboard (Visual Analytics), alur kerja manusia (Workflow Manager) | menyimpan data operasional |
| **PostgreSQL** (CIF) | sumber kebenaran: event, agregat harian, fitur, master nasabah, state limit, tiket, log keputusan | memutuskan |
| **Kafka** | pengangkut transaksi, enrichment, dan aksi ke channel | menyimpan permanen |
| **Core banking** | otorisasi; membaca tabel limit | menghitung limit |
| **Python** | latih ulang, validasi, ekspor; oracle uji; data sintetis | runtime produksi |

### Frame 2.8 Yang sudah dan belum
| Komponen | Status |
|---|---|
| Data sintetis (scam, mule, ATO, varian stealth, hard negative) | **Ada** (DEV 50.000, TEST 20.000, TEST2 20.000 terkunci, SIM 10.000 nasabah) |
| Model fraud v1.1 (63 fitur, tanpa gender/usia/wilayah) | **Ada**: PR-AUC 0,734, 7 dari 7 kriteria lulus |
| Model di Viya: impor, uji 25 kasus, publish ke MAS (`fraudxgbmaskv11`) | **Ada**; REST belum diuji (token) |
| Decision flow F1 di Intelligent Decisioning | **Dibangun di lab** (laporan pengguna); belum diuji dengan berkas uji kita |
| Laporan Visual Analytics 6 halaman | **Ada** |
| Scorecard (DATA step) dan prakiraan kebutuhan (model Python) | Scorecard **kuning**: lemah (KS 14,8 vs target 35); prakiraan coverage 94%; belum di Viya |
| Logika gate, mesin limit, pemantauan | **Ada di Python** (spesifikasi dan oracle); harus diimplementasikan di Viya |
| PostgreSQL, Kafka, job fitur harian, caslib ke Postgres | **Belum** |
| Decision flow F2–F8, Workflow Manager, timer | **Belum** |
| Model mule, business case, integrasi produksi | **Belum** |

### Frame 2.9 Temuan yang mengubah cerita proposal
1. Prakiraan MVP (P95 × 1,2) hanya menutup **76%** kebutuhan puncak; regresi kuantil menutup **94%**.
2. Dua aturan *hold* lebih bising daripada model: dijadikan step-up.
3. **Keamanan datang dari gate, kenyamanan dari limit dinamis.**
4. Step-down oleh gate berguna: tanpa itu eksposur naik **+6,1%**.
5. Histeresis 3 evaluasi harian terlalu pendek.
6. Atribut terproteksi tidak dibutuhkan: v1.1 hanya −0,017 PR-AUC dari v1 (tidak signifikan).
7. Pemicu retrain proposal (PSI skor, KS) tidak cukup untuk kejadian langka: ditambah penurunan relatif PR-AUC, alarm nilai kosong, jumlah fitur drift.

---

## Bab 3. Mekanisme kerja

### Frame 3.1 Arsitektur target
**Visual:** diagram 0 di [DIAGRAMS.md](DIAGRAMS.md). Arus utama: BRImo dan core banking → **middleware** → **endpoint MAS di Viya** → decision flow → membaca **PostgreSQL**; kejadian transaksi dan enrichment → **Kafka** → Postgres; aksi dari decision → **Kafka** → BRImo; Python → paket model → **Model Manager** → MAS.
**Narasi:** middleware hanya perlu tahu satu hal: alamat endpoint Viya. Semua pengetahuan keputusan ada di Viya.

### Frame 3.2 Lapisan data
**Postgres (kunci CIF):**
| Tabel | Isi | Ditulis oleh |
|---|---|---|
| `customer` | master nasabah (lima kolom model: penghasilan deklarasi, pekerjaan, segmen, shared device, tanggal KYC; ditambah tanggal buka, tier dasar; usia, gender, wilayah hanya untuk audit) | **inject manual** (data engineer) |
| `events` | transaksi masuk dan keluar, sinyal perangkat dan kredensial, alert, sengketa | sink dari Kafka |
| `daily` | satu baris per CIF per hari (agregat) | agregator (SQL atau ESP) dan job penutupan hari |
| `features` | 63 fitur per CIF | job Viya 01.30 |
| `limit_state` | tier, tier sejak, jeda, tawaran, batas pilihan nasabah, kunci tier, jadwal berlaku | decision lewat job |
| `tickets` | banding dan kasus fraud | Workflow Manager / F5 |
| `decision_log` | masukan, skor, verdict, versi model, versi decision | decision (lewat Kafka) |
**Kafka:** topik transaksi, enrichment channel, aksi (Viya → channel). Umpan balik nasabah masuk lewat middleware ke endpoint Viya.
**Data yang dibutuhkan model dari sumber lain (sudah dianggap lengkap):** uang masuk dan saldo (core banking), perangkat dan kredensial (log channel), alert dan sengketa (sistem fraud dan CRM), batas harian (tabel limit). Kalau salah satu hilang, kemampuan model turun (PR-AUC DEV dibanding baseline: tanpa id payee 0,61×; tanpa arus masuk 0,78×; tanpa saldo 0,80×; tanpa perangkat 0,90×; hanya transfer keluar 0,51×).
**Jujur:** Postgres, Kafka, dan agregator belum dibangun; kemampuan membaca Postgres dari dalam decision **[cek lab]**.

### Frame 3.3 Fitur: harian dan hari berjalan
**Visual:** dua garis: "fitur harian (sampai kemarin)" dan "fitur hari berjalan".
**Narasi:** 63 fitur dihitung dari 90 hari catatan harian ditambah catatan hari ini. Fitur harian dihitung job Viya 01.30 ke tabel `features`; fitur hari berjalan (17 fitur, sekitar 41% pengaruh model) harus segar, sehingga dibaca dari tabel agregat hari ini dan digabung dengan transaksi yang sedang diputuskan.
**Bukti:** di Python, satu fungsi fitur yang sama dipakai untuk pelatihan dan serving, dan terbukti identik (68 dari 68 fitur pada 6.000 nasabah-hari). Bila fitur dihitung ulang di Viya, **uji paritas dengan oracle Python wajib diulang**.
**Definisi bisnis yang dikunci (asumsi):** hari = WIB; per CIF; id payee kanonik (kode bank + nomor rekening); transaksi gagal dikeluarkan; reversal mengurangi; hari tanpa transaksi diisi nol dengan saldo dibawa maju.

### Frame 3.4 Model: satu angka, tiga band
**Visual:** garis skala logaritmik: genuine < 0,25% ≤ suspicious < 4,7% ≤ high_risk; rata-rata fraud 0,03%.
**Narasi:** model menjawab satu pertanyaan, *seberapa mirip aktivitas hari ini dengan pola fraud*. Keluarannya `EM_EVENTPROBABILITY`, `EM_CLASSIFICATION`, `band`, `quality`. Skor kecil karena fraud hanya ±0,03% nasabah-hari; ambang berarti ±8× dan ±150× rata-rata.
**Bukti:** high_risk = 0,046% trafik, presisi 54%, menangkap 78% fraud; suspicious = 0,42% trafik, presisi 1,2%, +16% fraud; 6,3% fraud lolos sebagai genuine.
**Jujur:** suspicious cocok untuk step-up atau peninjauan, bukan blokir. Pengaruh fitur dan penjelasan model: [FEATURES_AND_EXPLAINABILITY.md](FEATURES_AND_EXPLAINABILITY.md).

### Frame 3.5 F1 Fraud verdict (transaksi real-time)
**Visual:** diagram 1 dan alur node: Start → baca fitur dan state → gabungkan transaksi → **node model** → **rule set Fraud gate** (7 aturan) → **rule set Verdict** → End.
**Langkah:** middleware memanggil F1 dengan CIF dan transaksi yang diputuskan → F1 membaca satu baris fitur dari Postgres → model → gate → verdict dikembalikan ke middleware, log dipublikasikan ke Kafka. STEP_UP_AUTH dan HOLD memicu aksi ke channel.
**Bukti:** tabel aturan setara dengan gate Python pada 3.000 baris acak; 43 kasus uji dengan hasil yang diharapkan tersedia ([INTELLIGENT_DECISIONING_BLUEPRINT.md](INTELLIGENT_DECISIONING_BLUEPRINT.md)).
**Jujur:** (1) transaksi yang sedang diputuskan harus ikut dihitung dalam fitur hari berjalan; defaultnya F1 menerima transaksi itu dan menggabungkannya (uji paritas wajib). (2) `quality = low` sekarang menghasilkan ALLOW; usulan aturan tambahan: `quality = "low" AND verdict = "ALLOW"` → STEP_UP_AUTH (keputusan tim). (3) Alasan SHAP model **tidak** keluar dari modul MAS, sehingga pesan ke nasabah memakai kode aturan gate dan band.

### Frame 3.6 F3 Siklus hold dan step-up
**Visual:** diagram 2 dan diagram 7.
**Narasi:** setelah HOLD atau STEP_UP, nasabah memberi umpan balik lewat BRImo → middleware → F3:
- **"Ini saya" atau verifikasi lolos:** skor ulang tanpa menaikkan limit, lalu transaksi dilepas.
- **"Bukan saya":** label awal dicatat, kasus dibuat di Workflow Manager untuk tim fraud (blokir kartu, eskalasi), transaksi diblokir.
- **Tanpa respons:** setelah **24 jam** (mengikuti aturan hold 24 jam di proposal) transaksi dibatalkan; job timer memeriksa kedaluwarsa.
**Jujur:** proposal hanya menyebut "konfirmasi di aplikasi atau telepon"; siklus ini adalah asumsi default dan harus disetujui tim fraud.

### Frame 3.7 F2 Limit harian (02.00)
**Visual:** diagram 4 dan diagram 8.
**Narasi:** job terjadwal memilih nasabah layak lalu memanggil F2 per CIF. F2 menggabungkan **scorecard** (poin dan alasan), **prakiraan kebutuhan** 30 hari, **model fraud**, dan **gate**, lalu menerapkan kebijakan tier (jeda 7 hari, histeresis 14 evaluasi, tidak pernah di bawah tier dasar). Hasilnya aksi dan state baru yang ditulis ke Postgres; tabel limit dibaca core banking.
**Aksi:** `KEEP`, `OFFER_INCREASE`, `APPLY_INCREASE`, `STEP_DOWN`, `HOLD`, `CANCEL_OFFER`, `EXPIRE_OFFER`, `REVIEW_QUEUE`, `GATE_STEP_DOWN`.
**Jujur:** scorecard sudah ada sebagai DATA step; **prakiraan kebutuhan baru berupa model Python** dan harus diekspor sebagai model kedua di MAS. Kegagalan batch: tier terakhir tetap berlaku, tidak ada perubahan otomatis tanpa skor yang lebih muda dari 48 jam (proposal §4.4).

### Frame 3.8 F4 dan F5: tawaran, self-cap, banding
**Visual:** diagram 5.
- **Tawaran:** nasabah menerima → F4 memeriksa ulang gate → tawaran berlaku **+24 jam**; job timer menerapkannya ke tabel limit dan memicu notifikasi. Tawaran ditolak atau kedaluwarsa (14 hari) → ditawarkan lagi setelah 30 hari.
- **Self-cap:** berlaku seketika; kenaikan kembali hanya lewat tawaran.
- **Banding:** F5 membuat tugas di Workflow Manager (tinjauan manusia ≤ 2 hari kerja); petugas dapat mengunci tier 6 bulan atau menolak; hasil dikirim ke nasabah.
**Pesan tawaran (contoh):** "Limit transfer harian Anda bisa naik ke Rp50 juta karena pendapatan stabil dan tidak ada sengketa. Terima? Berlaku 24 jam setelah diterima."

### Frame 3.9 F6 Notifikasi dan F7 pemantauan
- **F6:** memilih isi, kanal, dan waktu. Tawaran dikirim setelah 07.00; hold dan step-up segera. Pesan dikirim sebagai aksi ke Kafka lalu BRImo.
- **F7:** job terjadwal membaca `decision_log` dan hasil yang matang, menghitung PSI skor dan fitur, KS, PR-AUC, migrasi tier, fairness, lalu menampilkannya di Visual Analytics. **Pemicu retrain:** PSI skor > 0,25, KS turun > 5 poin, ditambah (usulan kita) PR-AUC turun relatif > 20%, kenaikan nilai kosong > 5 poin, dan ≥ 5 fitur drift.

### Frame 3.10 Siklus hidup model: Python hanya untuk pengembangan
**Visual:** diagram 6.
**Alur:** Python membaca data latih dan label dari Postgres → latih dan validasi pada test set terkunci → paket model (zip pzmm) → impor ke **Model Manager** sebagai versi baru → uji kasus validasi dan sampel, bandingkan dengan champion → alur persetujuan → publish ke MAS → decision memakai versi baru → monitoring.
**Bukti (sudah dilakukan untuk v1.1):** impor, uji 25 kasus (skor cocok, band dan quality identik, lima kasus bernilai kosong tetap menghasilkan skor), publish ke MAS. Satu bug ditemukan dan diperbaiki: Model Manager memanggil `score()` dengan kolom utuh (pandas Series), sehingga kode skor kini menerima nilai tunggal maupun Series.
**Jujur:** label fraud terkonfirmasi datang dengan jeda (hingga beberapa minggu), sehingga performa terbaru tertunda.

### Frame 3.11 Komponen Viya yang dimaksimalkan
| Komponen Viya | Dipakai untuk | Status |
|---|---|---|
| **Intelligent Decisioning** | F1–F8 | F1 dibangun; sisanya rencana |
| **MAS** | endpoint gateway dan model real-time | model fraud ter-publish; endpoint belum diuji (token) |
| **Model Manager** | registri, uji, persetujuan, champion/challenger, performa | impor, uji, publish selesai; persetujuan dan monitoring rencana |
| **Job terjadwal** | fitur harian, F2 02.00, timer, F7 | rencana |
| **Workflow Manager** | banding dan kasus fraud | rencana **[cek lab]** |
| **Visual Analytics** | dashboard performa, band, ketahanan, fairness, pemantauan, model limit | 6 halaman selesai |
| **CAS dan caslib ke Postgres** | batch dan analitik | rencana **[cek lab]** |
| ESP, Model Studio, Visual Forecasting | tidak dipakai pada default (scorecard dan prakiraan diekspor dari Python) | opsional |

### Frame 3.12 Asumsi default (belum diverifikasi tim)
| # | Hal | Asumsi default |
|---|---|---|
| 1 | Pemanggilan | sinkron di jalur otorisasi, < 150 ms; bila Viya gagal: lolos untuk nominal kecil, step-up untuk nominal besar (ambang ditentukan Risk) |
| 2 | Siklus hold | ini saya → lepas dan skor ulang; bukan saya → blokir dan eskalasi ke tim fraud; tanpa respons → batal setelah 24 jam |
| 3 | Fitur | harian dari job Viya ke Postgres; hari berjalan dari tabel agregat yang diperbarui dari Kafka; definisi bisnis di frame 3.3 |
| 4 | Timer dan state | state di Postgres, timer lewat job terjadwal Viya |
| 5 | Limit ke core | Viya menulis tabel limit di Postgres yang dibaca core banking |
| 6 | Label dan rilis | label dari hasil investigasi (jeda beberapa minggu); rilis lewat alur persetujuan di Model Manager |
| 7 | Penjelasan | pesan dari kode gate dan band; alasan scorecard untuk limit |
| 8 | Komponen Viya | Intelligent Decisioning, MAS, Model Manager, Visual Analytics, job terjadwal |
| 9 | Scorecard dan prakiraan | diekspor dari Python (scorecard sebagai DATA step, prakiraan sebagai model kedua di MAS) |
| 10 | Tinjauan banding | Workflow Manager |
| 11 | KPI | angka simulasi, ditandai sintetis, sampai angka internal tersedia |
| 12 | Privasi | akses menurut peran, retensi sesuai kebijakan bank |

---

## Bab 4. Dampak

*Catatan: semua angka dari simulasi sintetis pada logika Python. Logika yang sama harus dibuktikan setara setelah diimplementasikan di Viya.*

### Frame 4.1 Nasabah sah: lebih sedikit ditolak (10.000 nasabah × 364 hari)
| | Hari ini | Dynamic Limit |
|---|---|---|
| Transaksi sah ditolak limit (per 1.000 nasabah-tahun) | 2.327 | **1.574** (−32%) |
| Nasabah sah yang pernah terdampak | 32,0% | **25,2%** |
| Volume sah terselesaikan | Rp3.798 miliar | Rp4.008 miliar (**+5,5%**) |
**Tambahan:** 31,9% nasabah pernah naik tier, median 25 hari sampai kenaikan pertama; rata-rata hanya 0,75 perubahan tier per nasabah-tahun.

### Frame 4.2 Nasabah: harga yang dibayar
- **Step-up pada hari sah:** 1.903 per 1.000 nasabah-tahun (suspicious 1.370, malam 287, arus-keluar 319). Ini gesekan nyata.
- **Hold salah:** sekitar 81 per 1.000 nasabah-tahun (dari 795 bila night velocity dan arus-keluar dijadikan hold).
- **Perlindungan:** **tidak ada nasabah di bawah tier awalnya**; setiap penurunan atau hold disertai alasan, tautan banding, dan tinjauan manusia ≤ 2 hari kerja.

### Frame 4.3 Nasabah yang menjadi korban
**Bukti (replay demo kecil, 50 hari fraud, 19 nasabah):** ATO dan mule tertangkap seluruhnya, scam 74%, scam *stealth* 44%. Pada test set: ATO stealth hanya 28% masuk high_risk dan 29% lolos sebagai genuine.
**Jujur:** sampel kecil; jangan dikutip sebagai kemampuan umum. Fraud stealth tetap titik lemah.

### Frame 4.4 Bank: fraud berkurang, volume naik
| | Hari ini | Statis + gate | Dinamis + gate |
|---|---|---|---|
| Fraud lolos (Rp juta, simulasi) | 7.239 | 4.106 (**−43%**) | 4.173 (+1,6% vs statis+gate; total **−42%**) |
| Volume sah terselesaikan | Rp3.798 miliar | Rp3.798 miliar | Rp4.008 miliar (+5,5%) |
**Narasi:** penurunan fraud datang dari **gate**; limit dinamis menambah volume sambil menjaga eksposur hampir sama (+1,6%). Tanpa step-down oleh gate, eksposur naik +6,1%. Efek pada biaya operasional **belum terukur** (angka internal belum ada).

### Frame 4.5 Risiko dan batasan
- Scorecard 90 hari lemah: KS 14,8 vs target 35.
- v1.1 sedikit di bawah v1 (−0,017 PR-AUC, tidak signifikan): harga kepatuhan atribut terproteksi.
- Keadilan hasil tier: rasio kenaikan antar usia **0,60** dan wilayah **0,76**, di bawah ambang 0,8; perlu ditelusuri sebelum pilot (recall model sendiri seimbang: gender 1,01, usia 1,03, wilayah 1,18).
- Batas rata-rata pada hari fraud pertama naik 18% (Rp29,2 → 34,4 juta): perlu dipantau.
- Tidak ada model mule khusus; aturan buatan tangan ditolak (menandai 46% hari normal).
- Fitur `occupation` termasuk lima besar pengaruh dan kemungkinan artefak simulasi; uji tanpa fitur itu sebelum pakai data bank.
- Pengimplementasian ulang fitur dan gate di Viya membawa risiko ketidaksesuaian; **uji paritas dengan oracle Python wajib**.
- Semua angka dari data sintetis dan simulasi open-loop; ambang harus dikalibrasi ulang dengan data bank.

### Frame 4.6 Dari demo ke pilot
1. **Segera:** uji F1 di Viya dengan `id_test_input.csv`; uji REST ke MAS (token); cek Postgres/caslib, Workflow Manager, dan akses query dari decision; bangun F2–F7 bertahap; business case dengan angka internal BRI.
2. **Sebelum pilot:** latih ulang dengan data bank (seleksi dan ambang diulang), uji tanpa `occupation`, telusuri ketimpangan usia dan wilayah, model mule, definisi fitur dikunci bersama tim data.
3. **Pilot:** mode shadow, kemudian terbatas, dengan pemantauan di Visual Analytics dan pemicu retrain yang sudah diperluas.
**Penutup (kalimat kunci proposal §10):** *limit statis gagal bagi nasabah dan tidak melindungi siapa pun. Dynamic Customer Limit memberi tiap nasabah ruang yang telah ia peroleh, memblokir kenaikan yang dibutuhkan penipu, dan menjelaskan setiap keputusan.*

---

## Lampiran: peta frame ke sumber
| Frame | Sumber utama |
|---|---|
| 1.1–1.5 | proposal §1, §2, §3 |
| 2.1–2.5 | proposal §1, §4, §6.3, §7 |
| 2.6–2.8 | keputusan arsitektur 27 Okt 2026; PROGRESS_REPORT §1–2 |
| 2.9 | PROGRESS_REPORT §4 |
| 3.1–3.3 | [DIAGRAMS.md](DIAGRAMS.md); `serving/store.py`, `serving/rolling.py` (spesifikasi dan oracle) |
| 3.4 | `bundle/fraud_xgb_mask_v1_1`, [FEATURES_AND_EXPLAINABILITY.md](FEATURES_AND_EXPLAINABILITY.md) |
| 3.5 | [INTELLIGENT_DECISIONING_BLUEPRINT.md](INTELLIGENT_DECISIONING_BLUEPRINT.md), `sas_export/decisioning/` |
| 3.6–3.9 | `serving/gate.py`, `serving/limit_service.py`, `limit/policy.py`, `monitoring/monitor.py` (spesifikasi) |
| 3.10 | `sas_export/LAB_RUNBOOK.md` dan hasil di Viya lab |
| 4.1–4.5 | PROGRESS_REPORT §3.1, §3.3, §3.4 |
