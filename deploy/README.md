# Chạy FinWatch 24/7 trên VPS (không phụ thuộc máy cá nhân)

Khi chạy trên laptop, tắt máy là ngừng thu thập dữ liệu, cảnh báo Telegram và bản tóm tắt 7h sáng.
Giải pháp: đưa server lên một **máy chủ ảo (VPS) luôn bật**, dùng lại nguyên Docker Compose sẵn có.

```
 Điện thoại / laptop ──(Tailscale, mã hoá, riêng tư)──► VPS: Tailscale Serve :443
                                                          └─► 127.0.0.1:8000  Docker "fin-watch"
                                                                ├─ FastAPI + lịch thu thập (APScheduler)
                                                                ├─ Bot Telegram (polling)
                                                                └─ data/finwatch.db (SQLite, sao lưu 03:00)
```

**Cơ chế giữ chạy liên tục**

| Sự cố | Tự xử lý bằng |
|---|---|
| App crash / lỗi Python | `restart: unless-stopped` trong `docker-compose.yml` |
| VPS khởi động lại (bảo trì, cập nhật) | Docker bật cùng hệ thống (`systemctl enable docker`) → container tự lên lại |
| App treo nhưng không chết | Cron mỗi 5 phút gọi `/health`, không trả lời thì `docker compose restart` |
| Đầy ổ đĩa do log | Log Docker giới hạn 3 × 10 MB |
| Mất dữ liệu | `deploy/backup.sh` sao lưu SQLite mỗi ngày, giữ 14 bản |

Dashboard **không có đăng nhập** (chứa lệnh giao dịch của bạn), nên trên VPS cổng 8000 chỉ mở ở
`127.0.0.1`; truy cập từ ngoài đi qua Tailscale — giống cách đang dùng trên máy nhà.

## 1. Chọn VPS

Cấu hình đủ dùng: **1 vCPU, 1 GB RAM, 10 GB ổ**, Ubuntu 22.04/24.04.

| Lựa chọn | Chi phí | Ghi chú |
|---|---|---|
| Oracle Cloud Always Free (ARM Ampere) | Miễn phí | Cần thẻ để xác minh; đôi khi hết máy ở region Singapore/Tokyo |
| Hetzner, Vultr, DigitalOcean (Singapore) | ~4–6 USD/tháng | Ổn định, tạo máy trong vài phút |
| VPS Việt Nam (Viettel IDC, BizFly, …) | ~100–150k/tháng | Gần nguồn dữ liệu VN; Telegram có thể bị chặn → cần `TELEGRAM_PROXY` |

> Không nên dùng gói miễn phí của Render/Railway/Heroku: chúng **ngủ khi không có truy cập**, lịch
> thu thập và bot Telegram sẽ dừng; ổ đĩa không bền nên mất SQLite mỗi lần deploy.

## 2. Cài đặt (khoảng 10 phút)

SSH vào VPS rồi chạy:

```bash
curl -fsSL https://raw.githubusercontent.com/Nhim123/Financial-analyse/main/deploy/setup-vps.sh -o setup-vps.sh
sudo bash setup-vps.sh https://github.com/Nhim123/Financial-analyse.git
```

Repo **private**: tạo Personal Access Token (quyền đọc repo) trên GitHub, rồi dùng URL
`https://<token>@github.com/Nhim123/Financial-analyse.git` (hoặc copy thư mục lên bằng `scp` và chạy
`sudo bash deploy/setup-vps.sh` trong đó).

Script tự: cài Docker + Tailscale, tải mã nguồn về `/opt/finwatch`, tạo `.env`, chạy container và cài cron
(watchdog, sao lưu, dọn image). Chạy lại script bao nhiêu lần cũng được.

## 3. Cấu hình `.env` trên VPS

```bash
sudo nano /opt/finwatch/.env
```

- Copy `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` và các tuỳ chọn từ `.env` trên máy nhà.
- Giữ `BIND_ADDR=127.0.0.1`.
- Đổi `DASHBOARD_URL` thành địa chỉ Tailscale của VPS (bước 4), ví dụ `https://finwatch.<tailnet>.ts.net`.

Áp dụng: `cd /opt/finwatch && sudo docker compose up -d`

## 4. Truy cập dashboard qua Tailscale

```bash
sudo tailscale up                    # mở link hiện ra, đăng nhập CÙNG tài khoản Tailscale đang dùng
sudo tailscale set --hostname=finwatch
sudo tailscale serve --bg --https=443 http://127.0.0.1:8000
tailscale serve status               # in ra địa chỉ https://finwatch.<tailnet>.ts.net
```

Mở địa chỉ đó trên điện thoại/laptop (đã bật Tailscale). Cấu hình Serve được giữ sau khi VPS khởi động lại.

## 5. Chuyển dữ liệu cũ từ máy nhà (tuỳ chọn)

Giữ lại lịch sử giá, tin, lệnh giao dịch. **Dừng app ở máy nhà trước**, rồi từ máy Windows:

```bash
scp data/finwatch.db <user>@<ip-vps>:/tmp/finwatch.db
```

Trên VPS:

```bash
cd /opt/finwatch && sudo docker compose stop
sudo mv /tmp/finwatch.db data/finwatch.db
sudo docker compose up -d
```

## 6. ⚠️ Chỉ chạy MỘT nơi

Bot Telegram dùng `getUpdates`: hai server cùng token sẽ giành tin nhắn của nhau (lỗi 409) và **gửi
cảnh báo trùng lặp**. Khi VPS đã chạy, đừng bật `run-local.bat` / Docker ở máy nhà với cùng `.env`
(muốn thử ở máy nhà thì để trống `TELEGRAM_BOT_TOKEN` trong `.env` của máy nhà).

## Vận hành hằng ngày

| Việc | Lệnh (trên VPS, trong `/opt/finwatch`) |
|---|---|
| Trạng thái | `sudo docker compose ps` (cột STATUS phải là `healthy`) |
| Xem log | `sudo docker compose logs -f --tail 100` |
| Cập nhật code mới (sau khi `git push` từ máy nhà) | `sudo bash deploy/update.sh` |
| Sao lưu ngay | `sudo bash deploy/backup.sh` |
| Khôi phục bản sao lưu | `sudo docker compose stop && sudo cp data/backup/finwatch-<ngày>.db data/finwatch.db && sudo docker compose up -d` |
| Log watchdog / sao lưu | `/var/log/finwatch-watchdog.log`, `/var/log/finwatch-backup.log` |
| Tải bản sao lưu về máy nhà | `scp <user>@<ip-vps>:/opt/finwatch/data/backup/finwatch-<ngày>.db .` |
