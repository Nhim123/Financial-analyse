# FinWatch — Thu thập & Kiểm chứng thông tin tài chính

Hệ thống chạy bằng **Docker**, tự động thu thập số liệu tài chính thế giới từ nhiều
nguồn, **đối chiếu chéo và kiểm chứng**, rồi hiển thị trên một dashboard có **ghi rõ
từng nguồn thông tin**.

## Lĩnh vực theo dõi
- **Chứng khoán thế giới**: S&P 500, Dow Jones, Nasdaq, FTSE 100, Nikkei 225, DAX
- **Vàng**: XAU/USD
- **Dầu**: WTI, Brent
- **Crypto**: BTC, ETH, BNB, SOL, XRP
- **FX**: USD↔EUR/GBP/JPY/CNY/VND

## Nguồn dữ liệu (đều miễn phí, không cần API key)
| Nguồn | Dùng cho | Vai trò |
|-------|----------|---------|
| Frankfurter (ECB) | FX | Tham chiếu chính thức (ECB) |
| ExchangeRate-API | FX | Đối chiếu chéo |
| CoinGecko | Crypto | Tổng hợp |
| Binance | Crypto | Sàn giao dịch (đối chiếu) |
| CNBC | Chỉ số, hàng hoá | Dữ liệu thị trường |
| Yahoo Finance | Chỉ số, hàng hoá | Đối chiếu chéo |

## Thu thập tin tức (theo lĩnh vực)
Tự động lấy tin qua RSS của các hãng uy tín, phân loại vào đúng lĩnh vực, dịch tiếng Việt và kiểm chứng:
- **Nguồn:** Financial Times, MarketWatch, Seeking Alpha (thị trường); CoinDesk, Cointelegraph (crypto); OilPrice (dầu); Investing News (vàng).
- **Dịch & tóm tắt tiếng Việt:** tiêu đề + tóm tắt được dịch qua Google Translate (miễn phí, dự phòng MyMemory), có **cache** để không dịch lại.
- **Đánh giá ảnh hưởng thị trường:** mỗi tin được chấm **mức độ** (🔴 Cao / 🟠 Trung bình / ⚪ Thấp) và **chiều tác động** (🟢 Tích cực / 🔴 Tiêu cực / Trung lập) bằng heuristic từ khoá (khớp nguyên từ), có tính số nguồn đối chiếu; hiển thị kèm từ khoá tín hiệu để minh bạch.
- **Kiểm chứng tin:** ① gộp trùng theo độ tương đồng tiêu đề · ② **đối chiếu chéo** — tin có ≥2 hãng độc lập cùng đưa được gắn nhãn *"✓ Đã xác nhận"* · ③ độ mới (đánh dấu tin cũ) · ④ lưu vết link + thời gian.
- **Chỉ số Dovish/Hawkish:** đo giọng điệu chính sách tiền tệ của dòng tin — biểu đồ % (🕊️ Dovish / Trung lập / 🦅 Hawkish), điểm nghiêng ròng −100…+100 kèm kim chỉ, độ tin cậy theo số tin có tín hiệu. Trọng số = mức ảnh hưởng + xác nhận đa nguồn. Chi tiết trong `app/market_tone.py`.
- Tin FX được lọc theo từ khoá từ các feed thị trường chung.

## 4 phương thức kiểm chứng (số liệu)
1. **Đối chiếu đa nguồn** — cùng một chỉ số lấy từ ≥2 nguồn, cảnh báo nếu chênh lệch vượt ngưỡng.
2. **Độ tin cậy nguồn** — gắn mức tin cậy + cờ "chính thức", kiểm tra HTTPS.
3. **Phát hiện bất thường** — cảnh báo khi biến động vượt ngưỡng so với lần thu thập trước.
4. **Lưu vết (audit trail)** — mỗi số liệu lưu kèm URL, thời gian, dữ liệu thô và mã hash.

## Chạy

### Cách 1 — Docker (khuyến nghị)
> ⚠️ Docker Desktop trên Windows cần **WSL2**. Nếu `docker compose` báo lỗi
> *"cannot find pipe dockerDesktopLinuxEngine"* nghĩa là WSL2 chưa bật — xem mục
> [Bật WSL2](#bật-wsl2-cho-docker-windows) bên dưới.
```bash
docker compose up --build
```

### Cách 2 — Python local (không cần Docker/WSL2)
Nhấp đúp **`run-local.bat`** (hoặc chạy trong terminal). Script tự tạo `.venv`,
cài thư viện và khởi động server.

Sau khi chạy (cách nào cũng được), mở trình duyệt: **http://localhost:8000**

- **Giao diện duy nhất: trang chủ `/`** — "Trung tâm tin tức & cảnh báo" theo thiết kế màn 2A
  (`docs/design/`), trải kín màn hình, **4 tab** (nhớ tab đang xem; đường dẫn `#tong-quan`,
  `#co-phieu`, `#hang-hoa`, `#fx`):
  - **Tổng quan** — 6 thẻ (VN-Index, VN30, HNX-Index, S&P 500, BTC, Vàng SJC), **tin tức theo
    4 lĩnh vực** (Chứng khoán thế giới · Vàng & Dầu · Tiền mã hoá · CK Việt Nam — mỗi nhóm 3 tin mới
    nhất kèm nhãn Dovish/Hawkish của nhóm, nút **"Xem thêm"** mở trang tin riêng của lĩnh vực),
    Tăng/Giảm mạnh nhất (Thế giới · VN30 · HNX30, nút xem bảng xếp hạng đầy đủ), cảnh báo quan
    trọng, heatmap 12 chỉ số ngành HOSE, lịch sự kiện kinh tế trong tuần.
  - **Trang tin theo lĩnh vực** (`#tin-the-gioi`, `#tin-hang-hoa`, `#tin-crypto`, `#tin-viet-nam`) —
    toàn bộ tin của lĩnh vực (tối đa `NEWS_MAX_PER_CLASS` = 20), mỗi tin có mức ảnh hưởng, số nguồn
    xác nhận, giọng điệu Dovish/Hawkish (kèm từ khoá tín hiệu, có xử lý phủ định như "rate hike
    odds fall") và tác động ước tính tới 6 nhóm thị trường (CK thế giới, CK Việt Nam, Vàng, Dầu,
    Crypto, USD/tỷ giá). Cột phải: chỉ số Dovish/Hawkish của lĩnh vực + bảng tác động tới từng
    nhóm thị trường (điểm nghiêng co lại khi ít tin mang tín hiệu). Nút Quay lại của trình duyệt
    đưa về Tổng quan.
  - **Cổ phiếu Việt Nam** — bảng giá 14 mã HOSE thanh khoản lớn nhất ("Xem tất cả" = toàn sàn),
    độ rộng thị trường, thanh khoản phiên so với TB20, dòng tiền chủ động, khối ngoại ròng, tin VN.
  - **Giá vàng, dầu, crypto** — Bitcoin (biểu đồ 24h, vốn hoá), 8 đồng tiền mã hoá, vàng SJC /
    thế giới, dầu WTI / Brent, bạc, đồng, khí tự nhiên, cà phê.
  - **FX** — 8 cặp tiền kèm giá mua/bán Vietcombank, chỉ số Dovish/Hawkish, tin ngoại hối.
  - **Đánh giá lệnh** (`#lenh`) — nhập lệnh (cổ phiếu VN, vàng SJC/thế giới, bạc, dầu, khí, đồng,
    cà phê, crypto; Mua/Bán, giá vào, cắt lỗ, chốt lời, khối lượng) → **điểm rủi ro 0–100** từ 8 yếu tố
    cơ bản: xu hướng thị trường chung, chính sách tiền tệ (Dovish/Hawkish), tin tức liên quan tới mã,
    sự kiện kinh tế 48 giờ tới, biến động & đuổi giá/sát trần sàn, thanh khoản, khối ngoại trên mã,
    độ tin cậy dữ liệu; cộng **kiểm tra quản lý vốn** (có cắt lỗ, R:R, % vốn rủi ro, tỷ trọng, lô chẵn,
    khối lượng tối đa theo cài đặt). **Nhật ký lệnh** lưu điểm rủi ro lúc vào, lãi/lỗ theo giá trực
    tiếp; cứ 2 phút đánh giá lại lệnh đang mở và **cảnh báo** (giao diện + Telegram) khi chạm/tiến sát
    cắt lỗ, đạt chốt lời, rủi ro tăng mạnh hoặc có tin bất lợi mới. Vốn & giới hạn rủi ro lưu trong
    database cục bộ. Cùng bộ chấm điểm dùng cho bot Telegram:
    `/danhgia MUA FPT 58.2 SL 56 TP 62 KL 1000` (chỉ đánh giá), `/vaolenh …` (đánh giá + lưu),
    `/lenhmo`, `/dong 12 [giá]`, `/caidat 500000000 2 20`, `/trogiup` — bot chỉ nhận lệnh từ đúng
    `TELEGRAM_CHAT_ID`. Kết quả là đánh giá định lượng để tham khảo, không phải khuyến nghị mua/bán.
  - Ô tìm kiếm trên thanh công cụ lọc nhanh mã/tên. Nút **⛶** vào chế độ toàn màn hình;
    trên điện thoại các vùng tự xếp 1–2 cột.
- **Thời gian thực** (không cần tải lại trang, không có nút cập nhật thủ công):
  - **Giá trực tiếp** lấy mỗi 15 giây (`LIVE_INTERVAL_SECONDS`) từ Binance (crypto), CNBC
    (chỉ số, hàng hoá, tỷ giá) và SSI iBoard (chỉ số sàn, chỉ số ngành, cổ phiếu VN) rồi **đẩy ngay**
    lên giao diện qua luồng `/stream` (Server-Sent Events — lần đầu gửi toàn bộ, sau đó chỉ các mã
    vừa đổi giá). Ô số nháy xanh khi tăng, đỏ khi giảm; % hiển thị là **% trong phiên**
    (crypto: 24h). Đèn trên thẻ chỉ số: xanh nhấp nháy = đang giao dịch, xám = thị trường đóng cửa.
  - Dữ liệu chu kỳ chậm mỗi 5 phút (`MARKET_INTERVAL_SECONDS`): vàng SJC (PNJ), tỷ giá
    Vietcombank, vốn hoá crypto (CoinGecko), sparkline, lịch sự kiện (ForexFactory, mỗi giờ).
  - **Tin tức & kiểm chứng đa nguồn** chạy tự động mỗi 10 phút (`COLLECT_INTERVAL_SECONDS`);
    xong chu kỳ, vùng tin / cảnh báo / Tăng-Giảm / Dovish-Hawkish tự thay nội dung tại chỗ.
  - Thanh công cụ: "● TRỰC TIẾP · giá HH:MM:SS · tin HH:MM". Mất kết nối sẽ chuyển đỏ và
    tự kết nối lại.
  - Lưu ý: giá trực tiếp lấy từ một nguồn mỗi mã để đủ nhanh; đối chiếu chéo nhiều nguồn
    vẫn thực hiện ở chu kỳ 10 phút.
- Dữ liệu lưu tại `./data/finwatch.db` (SQLite).

### Xem trên điện thoại
- **Cùng Wi-Fi nhà:** `http://<IP-LAN-của-máy>:8000` — xem IP bằng lệnh `ipconfig` (dòng IPv4,
  dạng `192.168.x.x`); IP có thể đổi khi router khởi động lại.
- **Ở bất cứ đâu (4G) qua Tailscale** — riêng tư, chỉ thiết bị đăng nhập tài khoản Tailscale của
  chủ máy mới vào được: bật app Tailscale trên điện thoại rồi mở
  **`http://<tên-máy>.<tailnet>.ts.net:8080`** (xem đúng địa chỉ bằng `tailscale serve status`).
  Cài đặt: `tailscale serve --bg --http=8080 http://127.0.0.1:8000`.
  - Cơ chế: *Tailscale Serve* chuyển cổng 8080 trong mạng Tailscale vào `127.0.0.1:8000`, nên không
    cần mở thêm cổng tường lửa Windows. (Không dùng cổng 80 vì Windows/HTTP.sys đã chiếm sẵn.)
  - Xem cấu hình: `tailscale serve status` · Tắt: `tailscale serve --http=8080 off`.
  - Tailscale tự chạy cùng Windows; cấu hình Serve được giữ lại sau khi khởi động lại máy.
  - `DASHBOARD_URL` trong `.env` đã trỏ tới địa chỉ này để link trong tin Telegram mở được từ điện thoại.

### Bật WSL2 cho Docker (Windows)
Mở **PowerShell với quyền Administrator** rồi chạy:
```powershell
wsl --install
```
Khởi động lại máy, mở Docker Desktop, đợi báo *Engine running*, sau đó
`docker compose up --build`.

## Thông báo Telegram
Luồng hoàn chỉnh: **thu thập → kiểm chứng → hiển thị giao diện → gửi Telegram**.

**Hệ thống gửi gì?**
- **🚨 Biến động & cảnh báo** — sau mỗi chu kỳ thu thập, chỉ gửi khi có điều MỚI:
  - 🚀📉 **Biến động mạnh trong phiên** (crypto, tỷ giá: 24h) vượt ngưỡng — chứng khoán ±1.5%,
    hàng hoá ±2.5%, crypto ±5%, tỷ giá ±0.8%. Nếu biến động tăng lên gấp 2, gấp 3 ngưỡng sẽ báo
    thêm (mỗi bậc 1 lần/ngày). % trong phiên lấy từ nguồn (CNBC, Yahoo, CoinGecko, Binance).
  - ⚡ **Đột biến trong ~1 giờ** — chứng khoán ±1%, hàng hoá ±1.5%, crypto ±3%, tỷ giá ±0.5%.
  - 🦅🕊️ Giọng điệu chính sách tiền tệ đổi chiều (Dovish ↔ Hawkish).
  - 🛡️ Sự cố dữ liệu: chỉ số mất toàn bộ nguồn, các nguồn lệch nhau vượt ngưỡng.
- **🗞 Tóm tắt tin ngày hôm qua** — mỗi sáng lúc `TELEGRAM_RECAP_HOUR` (mặc định 7:00 giờ VN): mọi tin có
  giờ đăng trong ngày hôm qua (gom từ các lần thu thập, kể cả tin sáng nay mới lấy được), số tin / nguồn /
  tin ảnh hưởng cao, giọng điệu Dovish/Hawkish cả ngày + tác động ước tính tới CK, vàng, dầu, crypto, USD,
  giá chốt các chỉ số chính, rồi theo từng lĩnh vực tối đa 5 tin quan trọng nhất (link bài gốc), cuối cùng
  là lịch sự kiện kinh tế hôm nay. **Chỉ gồm tin quan trọng, ảnh hưởng lớn tới thị trường**: mỗi tin được chấm
  điểm `market_importance` (vĩ mô – chính sách trong tiêu đề +3, toàn thị trường +2, mức ảnh hưởng Cao +3,
  tín hiệu Dovish/Hawkish +2, ≥2 nguồn xác nhận +2…, tin mềm như giải thưởng/khai trương −4); chỉ tin đạt
  `REPORT_MIN_IMPORTANCE` (mặc định 5) được đưa vào — áp dụng cho cả mục tin của bản tin 8h/20h. Phần tác
  động thị trường chỉ hiện khi có ≥2 tin mang tín hiệu. Mỗi ngày gửi một lần; server tắt lúc 7:00 thì tự gửi bù khi bật lại
  (trước 23:00). Báo rõ nếu hôm qua server chỉ chạy một phần ngày. Nút **"🗞 Tóm tắt hôm qua"** ở tab Tổng
  quan để xem trước và gửi ngay.
- **📰 Tin tức mới** — tin nhắn RIÊNG, chia theo 4 nhóm như màn hình chính (📈 Chứng khoán thế giới ·
  🛢️ Vàng & Dầu · 🪙 Tiền mã hoá · 🇻🇳 CK & Doanh nghiệp VN). Mỗi tin: mức ảnh hưởng (🔴/🟠/⚪), tiêu đề
  (link bài gốc), giờ đăng (giờ VN), nguồn, ✓ số nguồn xác nhận, chiều tác động, tóm tắt.
  - `TELEGRAM_NEWS_MODE`: `important` (mặc định — chỉ tin ảnh hưởng Cao) hoặc `all` (mọi tin mới).
    Đo thực tế: ~1,2 tin mới mỗi 10 phút (~170 tin/ngày); chế độ `important` khoảng một nửa.
  - `TELEGRAM_NEWS_GROUPS`: chỉ nhận một số nhóm, vd `vn,crypto` (để trống = tất cả).
  - Chỉ tin đăng trong `TELEGRAM_NEWS_MAX_AGE_HOURS` giờ (mặc định 6); tối đa
    `TELEGRAM_MAX_NEWS_PER_ALERT` tin/tin nhắn (mặc định 8), tin dư sang chu kỳ sau.
  - **Lần đầu bật** chỉ gửi các tin ưu tiên nhất, phần tồn đọng được bỏ qua (không gửi dồn).
  - **Giờ yên lặng** `TELEGRAM_SILENT_HOURS=23-6`: tin vẫn tới nhưng không reo chuông.
  - Tin Việt Nam được chấm mức ảnh hưởng bằng từ khoá tiếng Việt (giảm sàn, lao dốc, khởi tố,
    huỷ niêm yết, lãi kỷ lục…) trong `app/enrich.py`.
- **Bản tin định kỳ** (mặc định 8h & 20h giờ VN): biến động đáng chú ý nhất, giá + % trong phiên
  của mọi chỉ số, kết quả kiểm chứng, chỉ số Dovish/Hawkish, 5 tin nổi bật.

Ngưỡng biến động chỉnh trong `app/config.py` (`MOVE_ALERT_PCT`, `SPIKE_ALERT_PCT`). Nếu thấy
quá nhiều tin, đặt `TELEGRAM_NEWS_REQUIRE_VERIFIED=1` trong `.env` để chỉ nhận tin đã được ≥2 hãng xác nhận.

Mỗi cảnh báo chỉ gửi **một lần** (lưu ở bảng `notifications`). Nếu gửi lỗi, chu kỳ sau tự thử lại.

**Cài đặt (5 phút):**
1. Trên Telegram, chat với **@BotFather** → gõ `/newbot` → đặt tên → nhận **token**.
2. Sao chép `.env.example` thành `.env`, dán token vào `TELEGRAM_BOT_TOKEN=`.
3. Khởi động lại server, mở Telegram và nhắn **`/start`** cho bot của bạn
   (muốn gửi vào nhóm: thêm bot vào nhóm rồi nhắn một tin trong nhóm).
4. Mở **http://localhost:8000/api/notify/chat-id** → copy số `chat_id` vào `TELEGRAM_CHAT_ID=` trong `.env`.
5. Khởi động lại server. Trang chủ hiện **✈ Telegram · BẬT**; bấm **✈ Gửi bản tin** để thử.

Tuỳ chọn khác trong `.env`: giờ gửi bản tin, chỉ gửi tin đã xác nhận hay mọi tin ảnh hưởng cao,
proxy (khi mạng chặn Telegram), `DASHBOARD_URL` (đổi thành IP máy để bấm link từ điện thoại).

## Đường dẫn hệ thống
Không có API dữ liệu công khai; chỉ giữ các đường dẫn phục vụ giao diện và cài đặt:
- `GET /stream` — luồng giá trực tiếp cho trang chủ (Server-Sent Events)
- `POST /notify/digest` — nút "✈ Gửi bản tin" · `POST /notify/test` — gửi tin thử
- `GET /api/notify/chat-id` — liệt kê chat đã nhắn cho bot (bước 4 cài Telegram)
- `GET /health` — kiểm tra server còn chạy

## Cấu trúc
```
app/
  config.py          # nguồn, danh mục, ngưỡng kiểm chứng, từ khoá tin
  collectors/        # crypto / fx / market (cnbc, yahoo) / news (RSS)
  verify.py          # 4 phương thức kiểm chứng số liệu
  news_service.py    # gộp trùng + đối chiếu chéo + đánh giá ảnh hưởng
  enrich.py          # dịch tiếng Việt + chấm mức ảnh hưởng thị trường
  market_tone.py     # chỉ số Dovish/Hawkish
  live.py            # giá trực tiếp 15 giây (Binance, CNBC, SSI) + luồng /stream
  marketdata.py      # dữ liệu chu kỳ chậm: vàng SJC, tỷ giá VCB, lịch sự kiện, sparkline
  homepage.py        # view-model trang chủ (các tab)
  risk.py            # chấm điểm rủi ro lệnh theo fundamental + kiểm tra quản lý vốn
  trades.py          # nhật ký lệnh, lãi/lỗ, theo dõi & cảnh báo lệnh đang mở
  telegram_bot.py    # nhận lệnh /danhgia, /vaolenh, /lenhmo, /dong qua Telegram
  recap.py           # tóm tắt tin ngày hôm qua, gửi Telegram 7:00 mỗi sáng
  storage.py         # SQLite + audit trail (readings / verifications / news)
  notifier.py        # gửi Telegram: cảnh báo tức thời + bản tin định kỳ
  pipeline.py        # thu thập -> kiểm chứng -> lưu -> dịch -> thông báo
  main.py            # FastAPI + scheduler + routes
  templates/         # home.html (trang chủ — giao diện duy nhất)
```

> Số liệu chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.
