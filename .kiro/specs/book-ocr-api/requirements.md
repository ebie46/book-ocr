# Requirements Document

## Introduction

Book OCR API adalah sebuah layanan HTTP yang mengekstrak teks dari halaman buku berbentuk gambar (foto atau scan) dengan memanfaatkan model vision MiniCPM yang dijalankan melalui backend Ollama. Layanan ini menyediakan API yang dapat dikonsumsi oleh aplikasi klien (misalnya aplikasi mobile, web, atau pipeline batch) untuk mengubah halaman buku menjadi teks yang dapat dibaca mesin. Layanan ini berjalan pada port 8003 dan berkomunikasi dengan Ollama untuk inferensi model.

## Glossary

- **Book_OCR_Service**: Layanan HTTP yang menerima gambar halaman buku dan mengembalikan teks hasil OCR.
- **Ollama_Backend**: Server inferensi model lokal yang menjalankan model vision MiniCPM dan diakses Book_OCR_Service melalui HTTP.
- **MiniCPM_Model**: Model vision-language (varian MiniCPM-V) yang digunakan untuk melakukan OCR pada gambar halaman buku.
- **OCR_Request**: Permintaan HTTP yang berisi satu atau lebih gambar halaman buku beserta parameter opsional.
- **OCR_Response**: Respons HTTP yang berisi teks hasil ekstraksi dan metadata terkait.
- **Page_Image**: Gambar dari satu halaman buku dalam format JPEG atau PNG.
- **Health_Endpoint**: Endpoint HTTP yang melaporkan status operasional Book_OCR_Service dan Ollama_Backend.
- **Configuration**: Kumpulan parameter runtime (URL Ollama, nama model, port, batas ukuran file, timeout) yang mengatur perilaku Book_OCR_Service.
- **Listening_Port**: Port TCP tempat Book_OCR_Service menerima koneksi HTTP, secara default 8003.
- **Max_File_Size**: Batas ukuran maksimum satu Page_Image yang diterima oleh Book_OCR_Service, dinyatakan dalam megabyte.
- **Inference_Timeout**: Batas waktu maksimum Book_OCR_Service menunggu respons dari Ollama_Backend untuk satu Page_Image.

## Requirements

### Requirement 1: Layanan HTTP dan Konfigurasi Port

**User Story:** Sebagai operator layanan, saya ingin Book_OCR_Service berjalan pada port 8003 dan dapat dikonfigurasi, sehingga saya dapat menyesuaikan deployment dengan lingkungan saya.

#### Acceptance Criteria

1. THE Book_OCR_Service SHALL menerima koneksi HTTP pada Listening_Port 8003 secara default.
2. WHERE variabel konfigurasi `PORT` disediakan, THE Book_OCR_Service SHALL menggunakan nilai variabel tersebut sebagai Listening_Port.
3. WHERE variabel konfigurasi `OLLAMA_BASE_URL` disediakan, THE Book_OCR_Service SHALL menggunakan nilai variabel tersebut sebagai alamat Ollama_Backend.
4. WHERE variabel konfigurasi `OLLAMA_MODEL` disediakan, THE Book_OCR_Service SHALL menggunakan nilai variabel tersebut sebagai nama MiniCPM_Model yang dipanggil pada Ollama_Backend.
5. WHEN Book_OCR_Service dimulai, THE Book_OCR_Service SHALL mencatat (log) Listening_Port, alamat Ollama_Backend, dan nama MiniCPM_Model yang aktif.

### Requirement 2: Endpoint OCR Halaman Tunggal

**User Story:** Sebagai pengembang klien, saya ingin mengirim satu gambar halaman buku ke API dan menerima teksnya, sehingga saya dapat mengintegrasikan OCR ke dalam aplikasi saya.

#### Acceptance Criteria

1. THE Book_OCR_Service SHALL menyediakan endpoint `POST /v1/ocr` yang menerima satu Page_Image dalam request multipart/form-data dengan field `image`.
2. THE Book_OCR_Service SHALL menyediakan endpoint `POST /v1/ocr` yang menerima satu Page_Image dalam request `application/json` dengan field `image_base64` berisi data gambar terenkode Base64.
3. WHEN OCR_Request yang valid diterima pada `POST /v1/ocr`, THE Book_OCR_Service SHALL mengirim Page_Image ke Ollama_Backend menggunakan MiniCPM_Model dan mengembalikan OCR_Response dengan status HTTP 200 berisi field `text` (teks hasil OCR), `model` (nama model), dan `processing_time_ms` (durasi pemrosesan dalam milidetik).
4. WHERE field `prompt` disertakan dalam OCR_Request, THE Book_OCR_Service SHALL meneruskan nilai prompt tersebut ke Ollama_Backend sebagai instruksi tambahan untuk MiniCPM_Model.
5. WHERE field `language` disertakan dalam OCR_Request dengan nilai kode bahasa (misalnya `id`, `en`), THE Book_OCR_Service SHALL menambahkan instruksi bahasa tersebut ke prompt yang dikirim ke MiniCPM_Model.

### Requirement 3: Endpoint OCR Multi-Halaman (Batch)

**User Story:** Sebagai pengembang klien, saya ingin mengirim beberapa halaman buku sekaligus dan menerima teks per halaman, sehingga saya dapat memproses bab atau buku secara efisien.

#### Acceptance Criteria

1. THE Book_OCR_Service SHALL menyediakan endpoint `POST /v1/ocr/batch` yang menerima daftar Page_Image dalam request multipart/form-data dengan field `images` (multi-file) atau dalam request `application/json` dengan field `pages` berisi array objek `{page_number, image_base64}`.
2. WHEN OCR_Request batch yang valid diterima, THE Book_OCR_Service SHALL memproses setiap Page_Image dan mengembalikan OCR_Response dengan status HTTP 200 berisi field `pages` berupa array objek `{page_number, text, status, error}` dalam urutan halaman yang sama dengan request.
3. IF salah satu Page_Image dalam batch gagal diproses, THEN THE Book_OCR_Service SHALL menetapkan field `status` halaman tersebut menjadi `failed`, mengisi field `error` dengan deskripsi kesalahan, dan tetap memproses halaman berikutnya.
4. THE Book_OCR_Service SHALL membatasi jumlah Page_Image per OCR_Request batch hingga maksimum 20 halaman.
5. IF OCR_Request batch berisi lebih dari 20 Page_Image, THEN THE Book_OCR_Service SHALL menolak request dengan status HTTP 413 dan body JSON berisi field `error` dengan nilai `batch_too_large`.

### Requirement 4: Validasi Format dan Ukuran Gambar

**User Story:** Sebagai operator layanan, saya ingin permintaan dengan gambar tidak valid ditolak dengan jelas, sehingga sumber daya server tidak terbuang dan klien menerima umpan balik yang berguna.

#### Acceptance Criteria

1. THE Book_OCR_Service SHALL menerima Page_Image dalam format JPEG dan PNG.
2. IF OCR_Request berisi file dengan tipe MIME selain `image/jpeg` atau `image/png`, THEN THE Book_OCR_Service SHALL menolak request dengan status HTTP 415 dan body JSON berisi field `error` dengan nilai `unsupported_media_type`.
3. THE Book_OCR_Service SHALL menetapkan Max_File_Size default sebesar 10 MB per Page_Image.
4. WHERE variabel konfigurasi `MAX_FILE_SIZE_MB` disediakan, THE Book_OCR_Service SHALL menggunakan nilai variabel tersebut (dalam megabyte) sebagai Max_File_Size.
5. IF Page_Image dalam OCR_Request melebihi Max_File_Size, THEN THE Book_OCR_Service SHALL menolak request dengan status HTTP 413 dan body JSON berisi field `error` dengan nilai `file_too_large`.
6. IF OCR_Request tidak berisi field gambar yang dipersyaratkan, THEN THE Book_OCR_Service SHALL menolak request dengan status HTTP 400 dan body JSON berisi field `error` dengan nilai `missing_image`.
7. IF data gambar dalam OCR_Request tidak dapat didekode sebagai JPEG atau PNG yang valid, THEN THE Book_OCR_Service SHALL menolak request dengan status HTTP 400 dan body JSON berisi field `error` dengan nilai `invalid_image`.

### Requirement 5: Penanganan Error dari Ollama_Backend

**User Story:** Sebagai pengembang klien, saya ingin menerima error yang jelas ketika backend Ollama bermasalah, sehingga saya dapat membedakan masalah klien dari masalah server.

#### Acceptance Criteria

1. IF Ollama_Backend tidak dapat dijangkau saat memproses OCR_Request, THEN THE Book_OCR_Service SHALL mengembalikan status HTTP 503 dan body JSON berisi field `error` dengan nilai `ollama_unavailable`.
2. IF Ollama_Backend mengembalikan error bahwa MiniCPM_Model tidak tersedia atau belum diunduh, THEN THE Book_OCR_Service SHALL mengembalikan status HTTP 503 dan body JSON berisi field `error` dengan nilai `model_not_available` serta field `model` berisi nama model yang dikonfigurasi.
3. THE Book_OCR_Service SHALL menetapkan Inference_Timeout default sebesar 120 detik per Page_Image.
4. WHERE variabel konfigurasi `INFERENCE_TIMEOUT_SECONDS` disediakan, THE Book_OCR_Service SHALL menggunakan nilai variabel tersebut sebagai Inference_Timeout.
5. IF pemrosesan satu Page_Image oleh Ollama_Backend melebihi Inference_Timeout, THEN THE Book_OCR_Service SHALL membatalkan permintaan ke Ollama_Backend dan mengembalikan status HTTP 504 dan body JSON berisi field `error` dengan nilai `inference_timeout`.
6. IF Ollama_Backend mengembalikan error lain di luar yang disebutkan di atas, THEN THE Book_OCR_Service SHALL mengembalikan status HTTP 502 dan body JSON berisi field `error` dengan nilai `ollama_error` serta field `detail` berisi pesan kesalahan dari Ollama_Backend.

### Requirement 6: Endpoint Health dan Readiness

**User Story:** Sebagai operator layanan, saya ingin memeriksa status Book_OCR_Service dan ketersediaan Ollama_Backend, sehingga saya dapat memantau kesehatan sistem dan mengintegrasikan dengan orchestrator.

#### Acceptance Criteria

1. THE Book_OCR_Service SHALL menyediakan endpoint `GET /health` yang mengembalikan status HTTP 200 dengan body JSON berisi field `status` bernilai `ok` ketika proses Book_OCR_Service berjalan.
2. THE Book_OCR_Service SHALL menyediakan endpoint `GET /ready` yang melakukan permintaan probe ke Ollama_Backend untuk memverifikasi bahwa Ollama_Backend dapat dijangkau dan MiniCPM_Model tersedia.
3. WHEN endpoint `GET /ready` dipanggil dan Ollama_Backend dapat dijangkau serta MiniCPM_Model tersedia, THE Book_OCR_Service SHALL mengembalikan status HTTP 200 dengan body JSON berisi field `status` bernilai `ready`, `ollama_url`, dan `model`.
4. IF Ollama_Backend tidak dapat dijangkau atau MiniCPM_Model tidak tersedia saat endpoint `GET /ready` dipanggil, THEN THE Book_OCR_Service SHALL mengembalikan status HTTP 503 dengan body JSON berisi field `status` bernilai `not_ready` dan field `reason` berisi deskripsi penyebab.

### Requirement 7: Format Respons dan Konsistensi

**User Story:** Sebagai pengembang klien, saya ingin format respons API yang konsisten, sehingga saya dapat menulis parser klien dengan andal.

#### Acceptance Criteria

1. THE Book_OCR_Service SHALL mengembalikan seluruh respons sukses maupun error dalam format `application/json` dengan charset UTF-8.
2. THE Book_OCR_Service SHALL menyertakan header `Content-Type: application/json; charset=utf-8` pada setiap respons.
3. WHEN OCR_Response sukses dikembalikan, THE Book_OCR_Service SHALL menyertakan field `request_id` berisi pengidentifikasi unik untuk OCR_Request tersebut.
4. WHEN OCR_Response error dikembalikan, THE Book_OCR_Service SHALL menyertakan field `request_id`, `error` (kode error), dan `message` (deskripsi human-readable).

### Requirement 8: Logging dan Observabilitas

**User Story:** Sebagai operator layanan, saya ingin melihat log permintaan dan kesalahan, sehingga saya dapat memecahkan masalah dan memantau penggunaan.

#### Acceptance Criteria

1. WHEN OCR_Request diterima, THE Book_OCR_Service SHALL mencatat log berisi `request_id`, endpoint yang dipanggil, dan ukuran payload (dalam byte).
2. WHEN OCR_Request selesai diproses, THE Book_OCR_Service SHALL mencatat log berisi `request_id`, status HTTP, dan durasi pemrosesan (dalam milidetik).
3. IF terjadi error saat memproses OCR_Request, THEN THE Book_OCR_Service SHALL mencatat log dengan level `error` berisi `request_id`, kode error, dan stack trace atau pesan error dari Ollama_Backend.
4. THE Book_OCR_Service SHALL TIDAK mencatat isi Page_Image atau teks hasil OCR pada level log default untuk menghindari pencatatan data sensitif.

### Requirement 9: Batas Konkurensi

**User Story:** Sebagai operator layanan, saya ingin layanan tidak kewalahan saat menerima banyak permintaan bersamaan, sehingga server tetap stabil.

#### Acceptance Criteria

1. THE Book_OCR_Service SHALL membatasi jumlah permintaan inferensi yang dikirim ke Ollama_Backend secara bersamaan hingga maksimum 2 permintaan secara default.
2. WHERE variabel konfigurasi `MAX_CONCURRENT_INFERENCES` disediakan, THE Book_OCR_Service SHALL menggunakan nilai variabel tersebut sebagai batas konkurensi inferensi.
3. WHILE batas konkurensi inferensi telah tercapai, THE Book_OCR_Service SHALL mengantrekan OCR_Request berikutnya dan memprosesnya sesuai urutan kedatangan.
4. IF OCR_Request berada dalam antrean lebih dari Inference_Timeout, THEN THE Book_OCR_Service SHALL menolak request tersebut dengan status HTTP 503 dan body JSON berisi field `error` dengan nilai `queue_timeout`.

### Requirement 10: Dokumentasi API

**User Story:** Sebagai pengembang klien, saya ingin dokumentasi API yang dapat dijelajahi, sehingga saya dapat memahami dan menguji endpoint dengan cepat.

#### Acceptance Criteria

1. THE Book_OCR_Service SHALL menyediakan endpoint `GET /docs` yang menampilkan dokumentasi API interaktif (misalnya Swagger UI atau setara).
2. THE Book_OCR_Service SHALL menyediakan endpoint `GET /openapi.json` yang mengembalikan spesifikasi OpenAPI 3.x dari seluruh endpoint publik dalam format JSON.
3. THE spesifikasi OpenAPI yang dikembalikan SHALL mendokumentasikan setiap endpoint OCR beserta skema request, skema response sukses, dan skema response error.
