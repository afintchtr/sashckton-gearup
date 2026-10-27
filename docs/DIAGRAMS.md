# Diagram proses end-to-end, versi 2 (Viya sebagai pusat keputusan)

Diperbarui 27 Oktober 2026. Menggantikan versi 1 (yang memakai backend Python di runtime). Dasar keputusan:
- **PostgreSQL** (kunci CIF) menyimpan semua data dan dapat diakses dari Viya dan Python. Transaksi masuk dan keluar lengkap; enrichment channel (perangkat, kredensial, lokasi) masuk lewat **Kafka**.
- **SAS Viya** adalah satu-satunya yang diketahui middleware (endpoint gateway). Model ada di Viya dan dipanggil dari **Intelligent Decisioning** (banyak decision flow). Viya mengirim aksi ke channel dan menerima umpan balik nasabah.
- **Python hanya untuk pengembangan model** (latih ulang, ekspor ke Viya) serta sebagai oracle uji dan data sintetis.
- Asumsi default nomor 1–12 diberlakukan (lihat [STORYBOARD.md](STORYBOARD.md) frame 3.12). Hal yang bergantung pada versi Viya ditandai **[cek lab]**.

Singkatan decision flow: F1 fraud verdict, F2 limit harian, F3 umpan balik step-up dan hold, F4 tawaran dan self-cap, F5 banding, F6 notifikasi, F7 pemantauan dan retrain, F8 (opsional) evaluasi ulang karena sinyal.

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
        ID["Intelligent Decisioning: decision flow F1 sampai F8"]
        MM["Model Manager: registri, persetujuan, monitoring"]
        JB["Job terjadwal: fitur harian, limit 02.00, timer"]
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

## 1. Sequence: keputusan transaksi real-time (F1) dengan step-up dan hold
```mermaid
sequenceDiagram
    autonumber
    actor N as Nasabah
    participant B as BRImo
    participant C as Core banking
    participant M as Middleware (gateway)
    participant V as Viya F1 Fraud verdict
    participant P as PostgreSQL
    participant K as Kafka
    N->>B: Mulai transfer
    B->>C: Minta otorisasi
    C->>M: Cek fraud
    M->>V: POST F1 (CIF, transaksi yang diputuskan)
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
        M->>V: POST F3 (hasil verifikasi)
        V-->>M: Lepas atau tahan
        M-->>C: Keputusan akhir
    else HOLD
        V->>K: Aksi: tahan dan notifikasi
        K->>B: Pesan transaksi ditahan
        B-->>N: Ditahan demi keamanan
    end
```

## 2. Sequence: hold dan umpan balik nasabah (F3)
```mermaid
sequenceDiagram
    actor N as Nasabah
    participant B as BRImo
    participant M as Middleware
    participant V as Viya F3 Umpan balik hold
    participant P as PostgreSQL
    participant W as Workflow Manager
    actor F as Tim fraud
    Note over N,F: Transaksi ditahan, batas waktu 24 jam
    alt Nasabah: ini saya
        N->>B: Konfirmasi di aplikasi
        B->>M: Umpan balik
        M->>V: POST F3 (konfirmasi)
        V->>V: Skor ulang tanpa menaikkan limit
        V->>P: Catat hasil
        V-->>M: Lepaskan transaksi
    else Nasabah: bukan saya
        N->>B: Laporkan bukan saya
        B->>M: Umpan balik
        M->>V: POST F3 (penyangkalan)
        V->>P: Catat label awal
        V->>W: Buat kasus (blokir, eskalasi)
        W->>F: Tugas penanganan
        V-->>M: Blokir
    else Tidak ada respons
        V->>V: Job timer memeriksa kedaluwarsa
        V->>P: Tandai kedaluwarsa
        V-->>M: Batalkan transaksi
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

## 4. Sequence: evaluasi limit harian 02.00 (F2)
```mermaid
sequenceDiagram
    autonumber
    participant J as Viya job terjadwal (02.00)
    participant V as Viya F2 Limit harian
    participant P as PostgreSQL
    participant K as Kafka
    participant B as BRImo
    actor N as Nasabah
    J->>P: Ambil nasabah layak dan fitur
    loop Setiap CIF
        J->>V: Panggil F2 (fitur, state limit)
        Note over V: Scorecard, prakiraan kebutuhan, model fraud, gate, kebijakan tier
        V-->>J: aksi, alasan, state baru
        J->>P: Simpan state dan tabel limit
    end
    J->>K: Aksi notifikasi (dijadwalkan setelah 07.00)
    K->>B: Pesan tawaran atau perubahan
    B-->>N: Kartu tawaran atau alasan
    Note over P: Core banking membaca tabel limit dari sini
```

## 5. Sequence: tawaran, self-cap, banding (F4, F5)
```mermaid
sequenceDiagram
    autonumber
    actor N as Nasabah
    participant B as BRImo
    participant M as Middleware
    participant V as Viya F4 dan F5
    participant P as PostgreSQL
    participant J as Viya job timer
    participant K as Kafka
    participant W as Workflow Manager
    actor R as Petugas banding
    alt Terima tawaran
        N->>B: Terima kenaikan
        B->>M: Umpan balik
        M->>V: POST F4 (terima)
        V->>P: Periksa gate, simpan tawaran berlaku +24 jam
        V-->>M: Diterima, berlaku 24 jam
        J->>P: Cek berkala tawaran jatuh tempo
        J->>P: Terapkan tier baru ke tabel limit
        J->>K: Aksi: notifikasi limit baru
        K->>B: Pesan limit baru
    else Turunkan limit sendiri
        N->>B: Atur batas lebih rendah
        B->>M: Permintaan
        M->>V: POST F4 (self-cap)
        V->>P: Terapkan langsung ke tabel limit
        V-->>M: Selesai
    else Ajukan banding
        N->>B: Keberatan atas penurunan atau hold
        B->>M: Permintaan
        M->>V: POST F5 (banding)
        V->>W: Buat tugas tinjauan (2 hari kerja)
        V-->>M: Tiket
        W->>R: Tugas
        R->>W: Keputusan (kunci tier 6 bulan atau tolak)
        W->>V: Hasil
        V->>P: Simpan kunci tier
        V->>K: Aksi: notifikasi hasil
        K->>B: Pesan hasil
    end
```

## 6. Sequence: siklus hidup model dan pemantauan (F7)
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

## 7. Activity: transaksi real-time dengan siklus hold
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
        v1["F1: ambil fitur, gabungkan transaksi"]
        v2["Node model, rule set gate, rule set verdict"]
        v3{"F3: hasil verifikasi atau konfirmasi"}
    end
    subgraph DT["PostgreSQL dan Kafka"]
        d1["Baca fitur dan state"]
        d2["Catat log keputusan"]
        d3["Kirim aksi ke channel"]
    end
    subgraph OPS["Tim fraud"]
        o1["Tangani kasus: blokir dan investigasi"]
    end
    n1 --> c1 --> v1 --> d1 --> v2 --> d2 --> c2
    c2 -->|ALLOW| n3
    c2 -->|STEP_UP_AUTH| d3
    c2 -->|HOLD| d3
    d3 --> n2 --> v3
    v3 -->|lolos atau ini saya| n3
    v3 -->|bukan saya| o1 --> n4
    v3 -->|tanpa respons 24 jam| n4
```

## 8. Activity: limit harian dan siklus tawaran
```mermaid
flowchart TB
    subgraph JD["Viya job terjadwal"]
        j1(["02.00: mulai batch harian"])
        j2["Timer: terapkan tawaran jatuh tempo +24 jam"]
    end
    subgraph VY["SAS Viya: F2 Limit harian"]
        v1{"Gate fraud kena?"}
        v2["Tidak naik, hold bila hit H"]
        v3{"Riwayat 6 bulan dan fitur terisi?"}
        v4["KEEP: tier dasar"]
        v5["Rekomendasi = MIN(batas skor, kebutuhan, tier + 1)"]
        v6{"Bandingkan dengan tier sekarang"}
        v7["OFFER_INCREASE (jeda 7 hari)"]
        v8["STEP_DOWN (beberapa evaluasi lebih rendah)"]
        v9["KEEP"]
    end
    subgraph DT["PostgreSQL dan Kafka"]
        p1["Ambil fitur dan state limit"]
        p2["Simpan state, tabel limit, log"]
        p3["Antrekan notifikasi setelah 07.00"]
        p4["Tawaran kedaluwarsa, tawarkan lagi setelah 30 hari"]
    end
    subgraph NAS["Nasabah dan BRImo"]
        n1["Terima notifikasi"]
        n2{"Terima tawaran?"}
        n3["Ajukan banding"]
    end
    subgraph OPS["Petugas banding"]
        o1["Tinjau 2 hari kerja: kunci tier atau tolak"]
    end
    j1 --> p1 --> v1
    v1 -->|ya| v2
    v1 -->|tidak| v3
    v3 -->|tidak| v4
    v3 -->|ya| v5 --> v6
    v6 -->|lebih tinggi| v7
    v6 -->|lebih rendah| v8
    v6 -->|sama| v9
    v2 --> p2
    v4 --> p2
    v7 --> p2
    v8 --> p2
    v9 --> p2
    p2 --> p3 --> n1
    n1 --> n2
    n2 -->|ya| j2 --> p2
    n2 -->|tidak| p4
    n1 -->|keberatan| n3 --> o1 --> p2
```

## Status per diagram
| Diagram | Sudah ada | Masih rencana |
|---|---|---|
| 0. Arsitektur | model di Model Manager dan MAS; laporan Visual Analytics; Python (latih, oracle) | Postgres, Kafka, job terjadwal, Workflow Manager, caslib ke Postgres |
| 1. F1 | cetak biru F1 ([INTELLIGENT_DECISIONING_BLUEPRINT.md](INTELLIGENT_DECISIONING_BLUEPRINT.md)); F1 dilaporkan sudah dibangun di lab, belum diuji dengan berkas uji kita | membaca fitur dari Postgres di dalam decision **[cek lab]**, menggabungkan transaksi yang diputuskan, panggilan aksi ke Kafka |
| 2. F3 | logika gate dan verdict | seluruh siklus hold dan kasus fraud |
| 3. Data dan fitur | rumus fitur dan oracle paritas di Python | sink Kafka, agregator, job fitur di Viya |
| 4. F2 | logika kebijakan, scorecard (DATA step), prakiraan (model Python) di Python | decision F2 di Viya, model prakiraan di MAS |
| 5. F4 dan F5 | logika di Python | semua di Viya, timer, Workflow Manager |
| 6. Model | impor, uji 25 kasus, publish MAS | persetujuan, monitoring Model Manager, pemicu |
| 7 dan 8. Activity | menggambarkan target | idem |
