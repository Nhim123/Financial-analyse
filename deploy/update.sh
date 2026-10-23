#!/usr/bin/env bash
# Cập nhật bản mới nhất từ git và khởi động lại (dữ liệu trong data/ được giữ nguyên).
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only
docker compose up -d --build
docker compose ps
