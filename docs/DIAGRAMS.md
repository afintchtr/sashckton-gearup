# Diagram proses end-to-end, versi 3 (Viya sebagai pusat keputusan)

Diperbarui 27 Oktober 2026. Diselaraskan dengan [DECISION_FLOWS.md](DECISION_FLOWS.md). Dasar keputusan:
- **PostgreSQL** (kunci CIF) menyimpan semua data dan dapat diakses dari Viya dan Python. Transaksi masuk dan keluar lengkap; enrichment channel (perangkat, kredensial, lokasi) masuk lewat **Kafka**.
- **SAS Viya** adalah satu-satunya yang diketahui middleware (endpoint gateway). Model ada di Viya dan dipanggil dari **Intelligent Decisioning**. Viya mengirim aksi ke channel dan menerima umpan balik nasabah.
- **Python hanya untuk pengembangan model** (latih ulang, ekspor ke Viya) serta oracle uji dan data sintetis.
- Asumsi default nomor 1–12 berlaku ([STORYBOARD.md](STORYBOARD.md) frame 3.12). Hal yang bergantung pada versi Viya ditandai **[cek lab]**.

**Tiga decision dan satu sub-decision** (penamaan lama F1–F8 dipetakan di bawah):
| Nama | Isi | Padanan penamaan lama |
|---|---|---|
| **D1 Fraud verdict** | keputusan transaksi real-time | F1 (dan F8 bila dipanggil ulang saat sinyal) |
| **D2 Evaluasi limit** | evaluasi malam 02.00, termasuk menerapkan tawaran yang jatuh tempo | F2 |
| **D3 Respons nasabah** | umpan balik hold dan step-up, tawaran, self-cap, banding | F3, F4, F5 |
| **Sub-decision Notifikasi** | isi, kanal, dan waktu pesan | F6 |
| Pemantauan dan retrain | job terjadwal, Model Manager, Visual Analytics (bukan decision) | F7 |

## 0. Arsitektur komponen
```mermaid
flowchart LR
    subgraph CH["Channel dan core"]
        APP["BRImo (aplikasi nasabah)"]
        CORE["Core banking: otorisasi dan tabel limit"]
        MW["Middleware (gateway)"]
    end
    subgraph ST["Aliran data"]
        KF[("Kafka: transaksi, enrichment channel, aksi")]
    end
    subgraph DB["PostgreSQL (kunci CIF)"]
        PG[("events, daily, features, customer, limit_state, tiket, decision_log")]
    end
    subgraph VY["SAS Viya"]
        MAS["MAS: endpoint gateway dan model"]
        ID["Intelligent Decisioning: D1 fraud verdict, D2 evaluasi limit, D3 respons nasabah, sub-decision Notifikasi"]
        MM["Model Manager: registri, persetujuan, monitoring"]
        JB["Job terjadwal: fitur harian, panggil D2 pukul 02.00, batas waktu hold 24 jam"]
        WF["Workflow Manager: banding dan kasus fraud"]
        VA["Visual Analytics: dashboard"]
    end
    subgraph PY["Python (pengembangan saja)"]
        NB["Notebook: latih, validasi, ekspor"]
        OR["Oracle uji dan data sintetis"]
    end
    subgraph PPL["Orang"]
        FO["Tim fraud"]
        RV["Petugas banding"]
        RA["Analis risiko"]
    end
    APP --> MW
    CORE --> MW
    MW -->|"CIF dan transaksi"| MAS
    MAS --- ID
    ID -->|"baca fitur dan state"| PG
    JB --> PG
    JB -->|"panggil D2"| ID
    CORE -->|transaksi| KF
    APP -->|enrichment| KF
    KF -->|sink| PG
    ID -->|aksi| KF
    KF -->|notifikasi| APP
    ID --> WF
    WF --> FO
    WF --> RV
    PG --> VA
    PG --> NB
    NB -->|"paket model"| MM
    MM --> MAS
    OR -.->|uji paritas| ID
    RA --> VA
    CORE -.->|membaca tabel limit| PG
```

## 1. Sequence: keputusan transaksi real-time (D1) dengan step-up dan hold
```mermaid
sequenceDiagram
    autonumber
    actor N as Nasabah
    participant B as BRImo
    participant C as Core banking
    participant M as Middleware (gateway)
    participant V as Viya D1 Fraud verdict
    participant P as PostgreSQL
    participant K as Kafka
    N->>B: Mulai transfer
    B->>C: Minta otorisasi
    C->>M: Cek fraud
    M->>V: POST D1 (CIF, transaksi yang diputuskan)
    V->>P: Ambil fitur harian, agregat hari ini, profil, limit
    P-->>V: Satu baris per CIF
    Note over V: Gabungkan transaksi, model fraud, aturan gate, verdict
    V->>K: Publikasikan log keputusan
    V-->>M: verdict, band, kode aturan
    M-->>C: verdict
    alt ALLOW
        C-->>B: Setujui
        B-->>N: Transfer berhasil
    else STEP_UP_AUTH
        V->>K: Aksi: minta verifikasi
        K->>B: Perintah verifikasi
        B->>N: OTP atau biometrik
        N-->>B: Hasil verifikasi
        B->>M: Umpan balik
        M->>V: POST D3 (hasil verifikasi)
        V-->>M: Lepas atau tahan
        M-->>C: Keputusan akhir
    else HOLD
        V->>K: Aksi: tahan dan notifikasi
        K->>B: Pesan transaksi ditahan
        B-->>N: Ditahan demi keamanan
    end
```

## 2. Sequence: hold dan umpan balik nasabah (D3)
```mermaid
sequenceDiagram
    actor N as Nasabah
    participant B as BRImo
    participant M as Middleware
    participant V as Viya D3 Respons nasabah
    participant P as PostgreSQL
    participant J as Job terjadwal
    participant K as Kafka
    participant W as Workflow Manager
    actor F as Tim fraud
    Note over N,F: Transaksi ditahan, batas waktu 24 jam
    alt Nasabah: ini saya
        N->>B: Konfirmasi di aplikasi
        B->>M: Umpan balik
        M->>V: POST D3 (konfirmasi)
        V->>V: Skor ulang (model fraud dan gate)
        alt Tidak lagi high_risk
            V->>P: Catat hasil
            V-->>M: Lepaskan transaksi
        else Masih high_risk
            V->>W: Kirim ke tim fraud, tetap ditahan
            W->>F: Tugas tinjauan
            V-->>M: Tetap ditahan
        end
    else Nasabah: bukan saya
        N->>B: Laporkan bukan saya
        B->>M: Umpan balik
        M->>V: POST D3 (penyangkalan)
        V->>P: Catat label awal
        V->>W: Buat kasus (blokir, eskalasi)
        W->>F: Tugas penanganan
        V-->>M: Blokir
    else Tidak ada respons
        J->>V: Panggil D3 untuk hold yang melewati 24 jam
        V->>P: Tandai kedaluwarsa
        V->>K: Aksi: batalkan transaksi ditahan
    end
```

## 3. Sequence: data masuk dan fitur harian
```mermaid
sequenceDiagram
    participant C as Core banking
    participant B as BRImo (log channel)
    participant R as Sistem fraud dan CRM
    participant K as Kafka
    participant A as Agregator (SQL atau ESP)
    participant P as PostgreSQL
    participant J as Viya job terjadwal
    actor E as Data engineer
    E->>P: Inject master nasabah (manual)
    loop Setiap kejadian
        C->>K: Transaksi masuk dan keluar
        B->>K: Enrichment: perangkat, kredensial, lokasi
        R->>K: Alert dan sengketa
        K->>P: Sink ke tabel events
        K->>A: Kejadian untuk agregat
        A->>P: Perbarui agregat hari ini
    end
    J->>P: 01.30 Tutup hari kemarin, isi hari tanpa transaksi
    J->>P: Hitung 63 fitur per CIF ke tabel features
    Note over J,P: Rumus fitur harus identik dengan fitur latih (uji paritas dengan oracle Python)
```

## 4. Sequence: evaluasi limit harian 02.00 (D2)
```mermaid
sequenceDiagram
    autonumber
    participant J as Viya job terjadwal (02.00)
    participant V as Viya D2 Evaluasi limit
    participant P as PostgreSQL
    participant K as Kafka
    participant B as BRImo
    actor N as Nasabah
    J->>P: Ambil nasabah layak dan fitur
    loop Setiap CIF
        J->>V: Panggil D2 (fitur, state limit)
        Note over V: Model fraud, gate, scorecard, prakiraan, Tier targets, Lifecycle, Rekomendasi
        V-->>J: aksi, alasan, state baru
        J->>P: Simpan state dan tabel limit
    end
    J->>K: Aksi notifikasi (tawaran dan perubahan dikirim setelah 07.00)
    K->>B: Pesan tawaran atau perubahan
    B-->>N: Kartu tawaran atau alasan
    Note over P: Core banking membaca tabel limit dari sini. D2 juga menerapkan tawaran yang sudah diterima dan jatuh tempo (24 jam atau lebih)
```

## 5. Sequence: tawaran, self-cap, banding (D3)
```mermaid
sequenceDiagram
    autonumber
    actor N as Nasabah
    participant B as BRImo
    participant M as Middleware
    participant V as Viya D3 Respons nasabah
    participant P as PostgreSQL
    participant D as Viya D2 (evaluasi malam)
    participant K as Kafka
    participant W as Workflow Manager
    actor R as Petugas banding
    alt Terima tawaran
        N->>B: Terima kenaikan
        B->>M: Umpan balik
        M->>V: POST D3 (terima tawaran)
        V->>P: Periksa gate, simpan tanggal penerimaan
        V-->>M: Diterima, berlaku pada evaluasi malam berikutnya
        D->>P: Malam berikutnya: terapkan tier baru ke tabel limit
        D->>K: Aksi: notifikasi limit baru
        K->>B: Pesan limit baru
    else Turunkan limit sendiri
        N->>B: Atur batas lebih rendah
        B->>M: Permintaan
        M->>V: POST D3 (self-cap)
        V->>P: Terapkan langsung ke tabel limit
        V-->>M: Selesai
    else Ajukan banding
        N->>B: Keberatan atas penurunan atau hold
        B->>M: Permintaan
        M->>V: POST D3 (banding)
        V->>W: Buat tugas tinjauan (2 hari kerja)
        V-->>M: Tiket
        W->>R: Tugas
        R->>W: Keputusan (kunci tier 6 bulan atau tolak)
        W->>V: Hasil tinjauan
        V->>P: Simpan kunci tier
        V->>K: Aksi: notifikasi hasil
        K->>B: Pesan hasil
    end
```

## 6. Sequence: siklus hidup model dan pemantauan
```mermaid
sequenceDiagram
    participant P as PostgreSQL
    actor A as Analis risiko
    participant NB as Python (notebook latih)
    participant MM as Model Manager
    participant V as Viya MAS dan Decision
    participant VA as Visual Analytics
    P->>NB: Data latih dan label (query SQL)
    A->>NB: Latih ulang dan validasi di test set terkunci
    NB->>MM: Impor paket model (zip pzmm), versi baru
    A->>MM: Uji kasus validasi dan sampel, bandingkan dengan champion
    MM->>MM: Alur persetujuan
    MM->>V: Publish ke MAS, decision memakai versi baru
    loop Terjadwal
        V->>P: Log keputusan
        MM->>P: Baca log dan hasil yang matang
        MM->>VA: Performa, PSI, KS, fairness
    end
    VA-->>A: Peringatan bila pemicu menyala
```

## 7. Activity: transaksi real-time dengan siklus hold (D1 dan D3)
```mermaid
flowchart TB
    subgraph NAS["Nasabah dan BRImo"]
        n1(["Mulai transfer"])
        n2["Verifikasi atau konfirmasi di aplikasi"]
        n3(["Transfer berhasil"])
        n4(["Transaksi dibatalkan atau ditahan"])
    end
    subgraph MC["Core banking dan middleware"]
        c1["Minta otorisasi dan cek fraud"]
        c2{"Terapkan verdict"}
    end
    subgraph VY["SAS Viya"]
        v1["D1: ambil fitur, gabungkan transaksi"]
        v2["Node model, rule set gate, rule set verdict"]
        v3{"D3: skor ulang dan keputusan"}
    end
    subgraph DT["PostgreSQL dan Kafka"]
        d1["Baca fitur dan state"]
        d2["Catat log keputusan"]
        d3["Kirim aksi ke channel"]
    end
    subgraph OPS["Tim fraud"]
        o1["Tangani kasus: tinjau, blokir, investigasi"]
    end
    n1 --> c1 --> v1 --> d1 --> v2 --> d2 --> c2
    c2 -->|ALLOW| n3
    c2 -->|STEP_UP_AUTH| d3
    c2 -->|HOLD| d3
    d3 --> n2 --> v3
    v3 -->|"lolos, atau ini saya dan tidak lagi high_risk"| n3
    v3 -->|"ini saya tetapi masih high_risk, atau bukan saya"| o1 --> n4
    v3 -->|tanpa respons 24 jam| n4
```

## 8. Activity: evaluasi limit harian dan siklus tawaran (D2 dan D3)
```mermaid
flowchart TB
    subgraph JD["Viya job terjadwal"]
        j1(["02.00: mulai batch harian"])
    end
    subgraph VY["SAS Viya: D2 Evaluasi limit"]
        v0{"Tawaran diterima dan jatuh tempo?"}
        v2a["CANCEL_OFFER (gate kena), tawar ulang setelah 30 hari"]
        v10["APPLY_INCREASE: terapkan tier tawaran"]
        v1{"Gate hold kena?"}
        v2["HOLD atau GATE_STEP_DOWN"]
        v3{"Riwayat 6 bulan dan fitur terisi?"}
        v4["KEEP"]
        v5["Rekomendasi = MIN(batas skor, kebutuhan, tier + 1)"]
        v6{"Bandingkan dengan tier sekarang"}
        v7["OFFER_INCREASE (jeda 7 hari)"]
        v8["STEP_DOWN (14 evaluasi lebih rendah)"]
        v9["KEEP"]
    end
    subgraph DT["PostgreSQL dan Kafka"]
        p1["Ambil fitur dan state limit"]
        p2["Simpan state, tabel limit, log"]
        p3["Antrekan notifikasi setelah 07.00"]
        p4["Tawaran kedaluwarsa setelah 14 hari, tawarkan lagi setelah 30 hari"]
    end
    subgraph NAS["Nasabah dan BRImo"]
        n1["Terima notifikasi"]
        n2{"Terima tawaran?"}
        n3["Ajukan banding"]
    end
    subgraph D3["SAS Viya: D3 Respons nasabah"]
        a1["Periksa gate, simpan tanggal penerimaan"]
        a2["Hapus tawaran, tawar ulang setelah 30 hari"]
    end
    subgraph OPS["Petugas banding"]
        o1["Tinjau 2 hari kerja: kunci tier atau tolak"]
    end
    j1 --> p1 --> v0
    v0 -->|"ya, gate kena"| v2a
    v0 -->|"ya, gate bersih"| v10
    v0 -->|tidak| v1
    v1 -->|ya| v2
    v1 -->|tidak| v3
    v3 -->|tidak| v4
    v3 -->|ya| v5 --> v6
    v6 -->|lebih tinggi| v7
    v6 -->|lebih rendah| v8
    v6 -->|sama| v9
    v2a --> p2
    v10 --> p2
    v2 --> p2
    v4 --> p2
    v7 --> p2
    v8 --> p2
    v9 --> p2
    p2 --> p3 --> n1
    n1 --> n2
    n2 -->|ya| a1
    a1 -.->|evaluasi malam berikutnya| j1
    n2 -->|tidak| a2 --> p4
    n1 -->|keberatan| n3 --> o1 --> p2
```

## Status per diagram
| Diagram | Sudah ada | Masih rencana |
|---|---|---|
| 0. Arsitektur | model di Model Manager dan MAS; laporan Visual Analytics; Python (latih, oracle) | Postgres, Kafka, job terjadwal, Workflow Manager, caslib ke Postgres |
| 1. D1 | cetak biru ([INTELLIGENT_DECISIONING_BLUEPRINT.md](INTELLIGENT_DECISIONING_BLUEPRINT.md), [DECISION_FLOWS.md](DECISION_FLOWS.md) bagian 3); D1 dilaporkan sudah dibangun di lab, belum diuji dengan berkas uji kita | membaca fitur dari Postgres di dalam decision **[cek lab]**, menggabungkan transaksi yang diputuskan, aksi ke Kafka |
| 2. D3 hold | logika gate dan verdict | seluruh siklus hold dan kasus fraud; aturan "ini saya tetapi masih high_risk tetap ditahan" adalah **[asumsi]** |
| 3. Data dan fitur | rumus fitur dan oracle paritas di Python | sink Kafka, agregator, job fitur di Viya |
| 4. D2 | logika kebijakan di Python (`limit/policy.py`), rule set terinci di DECISION_FLOWS bagian 4 | decision D2 di Viya; **scorecard dan prakiraan harus dibungkus sebagai model Python (MAS tidak menjalankan DATA step)** |
| 5. D3 tawaran, self-cap, banding | logika di Python | semua di Viya, Workflow Manager |
| 6. Model | impor, uji 25 kasus, publish MAS | persetujuan, monitoring Model Manager, pemicu |
| 7 dan 8. Activity | menggambarkan target | idem |
