"""Lớp kiểm chứng / kiểm nghiệm số liệu.

Bốn phương thức:
  1. Đối chiếu đa nguồn  — so chênh lệch % giữa các nguồn cho cùng một chỉ số.
  2. Độ tin cậy nguồn    — chỉ chấp nhận HTTPS + gắn trust theo danh bạ SOURCES.
  3. Phát hiện bất thường — so với giá đồng thuận lần thu thập trước.
  4. Lưu vết (audit)     — mọi reading lưu kèm URL, thời gian, hash (ở storage.py).
"""
import statistics
from datetime import datetime, timedelta
from typing import Dict, List

from .config import (ANOMALY_THRESHOLD_PCT, DIFF_THRESHOLD_PCT, SOURCES)
from .models import Reading
from . import storage


def _pct(a: float, b: float) -> float:
    base = (abs(a) + abs(b)) / 2 or 1.0
    return abs(a - b) / base * 100.0


def verify_group(key: str, readings: List[Reading]) -> dict:
    """Kiểm chứng một chỉ số từ nhiều nguồn."""
    asset_class = readings[0].asset_class
    name = readings[0].name
    quote = readings[0].quote
    ts = max(r.ts for r in readings)

    valid = [r for r in readings if r.ok and r.value is not None]
    notes: List[str] = []

    # --- (2) Độ tin cậy nguồn ---
    detail = []
    for r in readings:
        src = SOURCES.get(r.source, {})
        https_ok = r.source_url.startswith("https://")
        detail.append({
            "source": r.source,
            "label": src.get("label", r.source),
            "url": r.source_url,
            "value": r.value,
            "ok": r.ok,
            "error": r.error,
            "trust": src.get("trust"),
            "official": src.get("official", False),
            "https": https_ok,
        })

    if not valid:
        return {
            "key": key, "asset_class": asset_class, "name": name, "quote": quote,
            "ts": ts, "consensus": None, "num_sources": 0, "max_diff_pct": None,
            "change_pct": None, "day_chg_pct": None, "status": "ERROR",
            "notes": "Không nguồn nào trả về dữ liệu hợp lệ.", "detail": detail,
        }

    values = [r.value for r in valid]
    consensus = statistics.median(values)

    # --- (1) Đối chiếu đa nguồn ---
    max_diff = 0.0
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            max_diff = max(max_diff, _pct(values[i], values[j]))

    diff_thr = DIFF_THRESHOLD_PCT.get(asset_class, 1.0)
    status = "OK"
    if len(valid) < 2:
        status = "WARN"
        notes.append("Chỉ có 1 nguồn — chưa thể đối chiếu chéo.")
    elif max_diff > diff_thr:
        status = "WARN"
        notes.append(f"Chênh lệch giữa các nguồn {max_diff:.2f}% > ngưỡng {diff_thr}%.")

    # --- (3) Phát hiện bất thường ---
    prev = storage.last_consensus(key, ts)
    change_pct = None
    if prev:
        change_pct = (consensus - prev) / prev * 100.0
        anom_thr = ANOMALY_THRESHOLD_PCT.get(asset_class, 10.0)
        if abs(change_pct) > anom_thr:
            status = "WARN"
            notes.append(
                f"Biến động {change_pct:+.2f}% so với lần trước > ngưỡng {anom_thr}%."
            )

    if status == "OK":
        notes.append("Đạt: nhiều nguồn khớp, không bất thường.")

    # --- % thay đổi trong phiên (crypto: 24h) ---
    day_vals = [r.day_chg for r in valid if r.day_chg is not None]
    if day_vals:
        day_chg_pct = statistics.median(day_vals)
    else:
        # Nguồn không công bố (vd tỷ giá) -> tự tính so với lần thu thập cách ~24 giờ.
        t = datetime.fromisoformat(ts)
        ref = storage.consensus_between(
            key, (t - timedelta(hours=26)).isoformat(timespec="seconds"),
            (t - timedelta(hours=22)).isoformat(timespec="seconds"))
        day_chg_pct = (consensus - ref) / ref * 100.0 if ref else None

    return {
        "key": key, "asset_class": asset_class, "name": name, "quote": quote,
        "ts": ts, "consensus": round(consensus, 6), "num_sources": len(valid),
        "max_diff_pct": round(max_diff, 4), "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "day_chg_pct": round(day_chg_pct, 4) if day_chg_pct is not None else None,
        "status": status, "notes": " ".join(notes), "detail": detail,
    }


def verify_all(readings: List[Reading]) -> List[dict]:
    groups: Dict[str, List[Reading]] = {}
    for r in readings:
        groups.setdefault(r.key, []).append(r)
    return [verify_group(k, rs) for k, rs in groups.items()]
