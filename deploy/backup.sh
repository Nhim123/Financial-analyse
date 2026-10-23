#!/usr/bin/env bash
# Sao lưu SQLite khi app đang chạy (backup API, không khoá/hỏng file), giữ 14 bản gần nhất.
# Bản sao nằm ở data/backup/ trên máy chủ.
set -euo pipefail
cd "$(dirname "$0")/.."

STAMP="$(date +%Y%m%d-%H%M)"
docker compose exec -T app python -c "
import os, sqlite3
os.makedirs('/code/data/backup', exist_ok=True)
src = sqlite3.connect('/code/data/finwatch.db')
dst = sqlite3.connect('/code/data/backup/finwatch-$STAMP.db')
src.backup(dst)
dst.close(); src.close()
"
ls -1t data/backup/finwatch-*.db | tail -n +15 | xargs -r rm -f
echo "[$(date '+%F %T')] backup ok: data/backup/finwatch-$STAMP.db"
