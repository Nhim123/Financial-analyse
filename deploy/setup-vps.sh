#!/usr/bin/env bash
# ============================================================
#  FinWatch — cài đặt chạy 24/7 trên VPS Ubuntu/Debian (chạy được nhiều lần).
#  Dùng:  sudo bash setup-vps.sh <URL-repo-git> [thư-mục-cài]
#  Ví dụ: sudo bash setup-vps.sh https://github.com/Nhim123/Financial-analyse.git
#  Xem hướng dẫn đầy đủ: deploy/README.md
# ============================================================
set -euo pipefail

REPO_URL="${1:-}"
APP_DIR="${2:-/opt/finwatch}"

if [ "$(id -u)" -ne 0 ]; then
    echo "Cần chạy bằng sudo." >&2
    exit 1
fi

# 1) Docker
if ! command -v docker >/dev/null 2>&1; then
    echo "[setup] Cài Docker ..."
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

# 2) Tailscale (truy cập dashboard riêng tư, không mở cổng ra Internet)
if ! command -v tailscale >/dev/null 2>&1; then
    echo "[setup] Cài Tailscale ..."
    curl -fsSL https://tailscale.com/install.sh | sh
fi
systemctl enable --now tailscaled

# 3) Mã nguồn
if [ -d "$APP_DIR/.git" ]; then
    echo "[setup] Đã có mã nguồn tại $APP_DIR — cập nhật ..."
    git -C "$APP_DIR" pull --ff-only
else
    if [ -z "$REPO_URL" ]; then
        echo "Thiếu URL repo. Dùng: sudo bash setup-vps.sh <URL-repo-git>" >&2
        exit 1
    fi
    apt-get update -qq && apt-get install -y -qq git
    git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
mkdir -p data

# 4) File .env: tạo từ mẫu nếu chưa có, buộc dashboard chỉ nghe trên 127.0.0.1
if [ ! -f .env ]; then
    cp .env.example .env
    echo "[setup] Đã tạo .env từ .env.example — nhớ điền TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID."
fi
if grep -q '^BIND_ADDR=' .env; then
    sed -i 's/^BIND_ADDR=.*/BIND_ADDR=127.0.0.1/' .env
else
    printf '\n# VPS: chỉ nghe nội bộ, truy cập qua Tailscale\nBIND_ADDR=127.0.0.1\n' >> .env
fi
chmod 600 .env

# 5) Chạy app
docker compose up -d --build

# 6) Cron: kiểm tra sức khoẻ mỗi 5 phút, sao lưu DB 03:00, dọn image cũ hằng tuần
cat > /etc/cron.d/finwatch <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root cd $APP_DIR && (curl -fsS --max-time 15 http://127.0.0.1:8000/health >/dev/null || docker compose restart app) >> /var/log/finwatch-watchdog.log 2>&1
0 3 * * * root bash $APP_DIR/deploy/backup.sh >> /var/log/finwatch-backup.log 2>&1
30 4 * * 0 root docker image prune -f >/dev/null 2>&1
EOF
chmod 644 /etc/cron.d/finwatch

echo
echo "[ok] FinWatch đang chạy. Kiểm tra: docker compose -f $APP_DIR/docker-compose.yml ps"
if ! tailscale status >/dev/null 2>&1; then
    echo "[tiếp] Đăng nhập Tailscale:   sudo tailscale up"
fi
echo "[tiếp] Mở dashboard qua Tailscale: sudo tailscale serve --bg --https=443 http://127.0.0.1:8000"
echo "       rồi xem địa chỉ bằng:      tailscale serve status"
