"""Nhật ký lệnh + theo dõi lệnh đang mở.

- Lưu mỗi lệnh kèm điểm rủi ro lúc vào (bảng `trades`).
- Mỗi chu kỳ (mặc định 2 phút) đánh giá lại rủi ro các lệnh đang mở theo dữ liệu mới nhất, tính
  lãi/lỗ theo giá trực tiếp và phát cảnh báo khi:
    • giá chạm cắt lỗ / chốt lời, hoặc tiến sát cắt lỗ (còn ≤ 25% khoảng cắt lỗ);
    • điểm rủi ro thị trường vào vùng Cao (≥ 60) hoặc tăng ≥ 15 điểm so với lần báo trước;
    • có tin ảnh hưởng Cao, đi ngược chiều lệnh, đăng trong 3 giờ gần nhất.
- Cảnh báo hiện trên giao diện (nhật ký lệnh + ô Cảnh báo quan trọng) và gửi Telegram nếu đã cấu hình
  (chống trùng qua bảng `notifications`).
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from . import live, notifier, risk, storage
from .config import DASHBOARD_URL

VN_TZ = timezone(timedelta(hours=7))
_MAX_ALERTS = 20


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt(v: Optional[float], dec: int) -> str:
    return "—" if v is None else f"{v:,.{dec}f}"


def _vn(ts: Optional[str]) -> str:
    if not ts:
        return "—"
    return datetime.fromisoformat(ts).astimezone(VN_TZ).strftime("%H:%M %d/%m")


# ---------------------------------------------------------------------------
# Lưu / đọc
# ---------------------------------------------------------------------------
def create(ev: dict, note: str = "", source: str = "web") -> int:
    """Lưu lệnh vừa đánh giá (ev = kết quả risk.evaluate đã ok)."""
    return storage.db_exec(
        """INSERT INTO trades (created_at, source, key, symbol, name, cls, side, entry, stop, target, qty, note,
                               status, risk_entry, risk_now, level_now, eval_json, alerts, alert_score)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'open', ?,?,?,?, '[]', ?)""",
        (_now(), source, ev["key"], ev["symbol"], ev["name"], ev["cls"], ev["side"], ev["entry"], ev["stop"],
         ev["target"], ev["qty"], (note or "")[:300], ev["score"], ev["score"], ev["level"],
         json.dumps(ev, ensure_ascii=False), ev["market_score"]))


def get(tid: int) -> Optional[dict]:
    rows = storage.db_query("SELECT * FROM trades WHERE id=?", (tid,))
    return rows[0] if rows else None


def list_trades(status: str, limit: int = 50) -> List[dict]:
    order = "created_at DESC" if status == "open" else "closed_at DESC"
    return storage.db_query(f"SELECT * FROM trades WHERE status=? ORDER BY {order} LIMIT ?", (status, limit))


def open_count(key: str) -> int:
    return storage.db_query("SELECT COUNT(*) AS n FROM trades WHERE status='open' AND key=?", (key,))[0]["n"]


def close(tid: int, price: Optional[float] = None) -> dict:
    t = get(tid)
    if not t:
        return {"ok": False, "error": f"Không có lệnh #{tid}"}
    if t["status"] != "open":
        return {"ok": False, "error": f"Lệnh #{tid} đã đóng"}
    if price is None:
        q = live.quotes()
        a = risk.asset_info(t["key"], q)
        p = (q.get(t["key"]) or {}).get("price")
        if not p or not a:
            return {"ok": False, "error": "Chưa có giá hiện tại — hãy nhập giá đóng lệnh"}
        price = p * a["scale"]
    if price <= 0:
        return {"ok": False, "error": "Giá đóng lệnh phải lớn hơn 0"}
    storage.db_exec("UPDATE trades SET status='closed', close_price=?, closed_at=? WHERE id=?", (price, _now(), tid))
    return {"ok": True, "id": tid, "price": price}


# ---------------------------------------------------------------------------
# Lãi / lỗ
# ---------------------------------------------------------------------------
def _to_vnd(t: dict, q: dict) -> float:
    a = risk.asset_info(t["key"], q) or {"mult": 1, "ccy": "VND"}
    return a["mult"] * (risk.usdvnd(q) if a["ccy"] == "USD" else 1.0)


def _pnl(t: dict, price: Optional[float], to_vnd: float) -> dict:
    if price is None or not t["entry"]:
        return {"pct": None, "vnd": None}
    s = 1 if t["side"] == "buy" else -1
    return {"pct": s * (price - t["entry"]) / t["entry"] * 100,
            "vnd": s * (price - t["entry"]) * to_vnd * t["qty"] if t["qty"] else None}


def journal_view() -> dict:
    """Dữ liệu nhật ký cho giao diện: lệnh mở (lãi/lỗ theo giá hiện tại) + lệnh đã đóng gần đây."""
    q = live.quotes()
    out_open, out_closed = [], []
    for t in list_trades("open"):
        a = risk.asset_info(t["key"], q)
        scale, dec = (a["scale"], a["dec"]) if a else (1, 2)
        p = (q.get(t["key"]) or {}).get("price")
        price = p * scale if p else None
        to_vnd = _to_vnd(t, q)
        out_open.append({**t, "dec": dec, "scale": scale, "to_vnd": to_vnd, "price": price,
                         "pnl": _pnl(t, price, to_vnd), "unit": a["unit"] if a else "",
                         "now_class": risk.level_of(t["risk_now"] or 0)[1],
                         "entry_class": risk.level_of(t["risk_entry"] or 0)[1],
                         "alerts": json.loads(t["alerts"] or "[]")[-3:][::-1], "created_vn": _vn(t["created_at"])})
    for t in list_trades("closed", 20):
        a = risk.asset_info(t["key"], q)
        out_closed.append({**t, "dec": a["dec"] if a else 2, "pnl": _pnl(t, t["close_price"], _to_vnd(t, q)),
                           "created_vn": _vn(t["created_at"]), "closed_vn": _vn(t["closed_at"]),
                           "entry_class": risk.level_of(t["risk_entry"] or 0)[1]})
    return {"open": out_open, "closed": out_closed,
            "realized": sum(t["pnl"]["vnd"] or 0 for t in out_closed),
            "floating": sum(t["pnl"]["vnd"] or 0 for t in out_open),
            "settings": risk.get_settings()}


def recent_alerts(hours: float = 24) -> List[dict]:
    """Cảnh báo của các lệnh đang mở trong N giờ gần nhất (cho ô Cảnh báo quan trọng ở Tổng quan)."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    out = []
    for t in list_trades("open"):
        for al in json.loads(t["alerts"] or "[]"):
            if datetime.fromisoformat(al["ts"]) >= since:
                out.append({**al, "trade_id": t["id"], "symbol": t["symbol"], "side": t["side"]})
    return sorted(out, key=lambda x: x["ts"], reverse=True)


# ---------------------------------------------------------------------------
# Theo dõi lệnh đang mở
# ---------------------------------------------------------------------------
def _alert_key(tid: int, kind: str, extra: str = "") -> str:
    return f"trade:{tid}:{kind}:{hashlib.sha1(extra.encode()).hexdigest()[:10] if extra else ''}"


def monitor() -> dict:
    opened = list_trades("open", 200)
    if not opened:
        return {"checked": 0, "alerts": 0}
    ctx = risk.context()
    q = ctx["q"]
    new_alerts = 0
    for t in opened:
        ev = risk.evaluate({"key": t["key"], "side": t["side"], "entry": t["entry"], "stop": t["stop"],
                            "target": t["target"], "qty": t["qty"]}, ctx)
        if not ev.get("ok"):
            continue
        a = risk.asset_info(t["key"], q)
        price = ev["price"]
        pnl = _pnl(t, price, _to_vnd(t, q))
        s = 1 if t["side"] == "buy" else -1
        found = []  # (khoá chống trùng, mức, nội dung)
        if t["stop"]:
            if s * (price - t["stop"]) <= 0:
                found.append((_alert_key(t["id"], "stop"), "KHẨN",
                              f"Giá {_fmt(price, a['dec'])} đã chạm cắt lỗ {_fmt(t['stop'], a['dec'])}"))
            elif abs(price - t["stop"]) <= 0.25 * abs(t["entry"] - t["stop"]):
                day = datetime.now(VN_TZ).strftime("%Y%m%d")
                found.append((_alert_key(t["id"], "near", day), "CHÚ Ý",
                              f"Giá {_fmt(price, a['dec'])} tiến sát cắt lỗ {_fmt(t['stop'], a['dec'])}"))
        if t["target"] and s * (price - t["target"]) >= 0:
            found.append((_alert_key(t["id"], "target"), "TIN",
                          f"Giá {_fmt(price, a['dec'])} đã đạt chốt lời {_fmt(t['target'], a['dec'])}"))
        base = t["alert_score"] if t["alert_score"] is not None else (t["risk_entry"] or 0)
        new_base = base
        if (ev["market_score"] >= 60 > base) or ev["market_score"] - base >= 15:
            found.append((_alert_key(t["id"], "risk", str(ev["market_score"])), "CHÚ Ý",
                          f"Rủi ro thị trường tăng {base} → {ev['market_score']} ({ev['market_level']}): "
                          + ", ".join(ev["top_risks"] or ["nhiều yếu tố"])))
            new_base = ev["market_score"]
        elif ev["market_score"] < base - 15:
            new_base = ev["market_score"]  # rủi ro đã giảm hẳn -> hạ mốc để lần tăng sau vẫn được báo
        for n in ev["news"]:
            against = {"Tích cực": -1, "Tiêu cực": 1}.get(n["direction"], 0) * s > 0
            if against and n["impact"] == "Cao" and n["age"] is not None and n["age"] <= 3:
                found.append((_alert_key(t["id"], "news", n["link"]), "CHÚ Ý", f"Tin bất lợi: {n['title']}"))

        alerts = json.loads(t["alerts"] or "[]")
        have = {al["key"] for al in alerts}
        fresh = [f for f in found if f[0] not in have]
        for key, level, text in fresh:
            alerts.append({"key": key, "ts": _now(), "level": level, "text": text})
        storage.db_exec("UPDATE trades SET risk_now=?, level_now=?, eval_json=?, alerts=?, alert_score=? WHERE id=?",
                        (ev["score"], ev["level"], json.dumps(ev, ensure_ascii=False),
                         json.dumps(alerts[-_MAX_ALERTS:], ensure_ascii=False), new_base, t["id"]))
        if fresh:
            new_alerts += len(fresh)
            _notify(t, a, price, pnl, ev, fresh)
    return {"checked": len(opened), "alerts": new_alerts}


def _notify(t: dict, a: dict, price: float, pnl: dict, ev: dict, fresh: List[tuple]) -> None:
    if not notifier.status().get("enabled"):
        return
    keys = set(storage.filter_unsent([k for k, _, _ in fresh]))
    items = [f for f in fresh if f[0] in keys]
    if not items:
        return
    icon = {"KHẨN": "🚨", "CHÚ Ý": "⚠️", "TIN": "🎯"}
    lines = [f"<b>Lệnh #{t['id']} · {'MUA' if t['side'] == 'buy' else 'BÁN'} {notifier._esc(t['symbol'])}</b>"]
    lines += [f"{icon.get(lv, '•')} {notifier._esc(text)}" for _, lv, text in items]
    pl = ""
    if pnl["pct"] is not None:
        pl = f" · lãi/lỗ {pnl['pct']:+.2f}%" + (f" ({pnl['vnd']:+,.0f} ₫)" if pnl["vnd"] is not None else "")
    lines.append(f"Giá {_fmt(price, a['dec'])} {notifier._esc(a['unit'])}{pl}")
    lines.append(f"Rủi ro hiện tại: <b>{ev['score']}/100 ({ev['level']})</b>")
    lines.append(f"{DASHBOARD_URL}/#lenh")
    if notifier.send_message("\n".join(lines)).get("ok"):
        storage.mark_sent([k for k, _, _ in items], _now())
