"""Nhận lệnh từ Telegram để đánh giá rủi ro / ghi nhật ký lệnh.

Chỉ xử lý tin nhắn từ đúng chat đã cấu hình (TELEGRAM_CHAT_ID) — người khác nhắn cho bot bị bỏ qua.
Lấy tin bằng getUpdates (polling ngắn, mỗi vài giây), vị trí đọc lưu ở kv `tg_offset`.

Lệnh:
  /danhgia MUA FPT 58.2 SL 56 TP 62 KL 1000   — chỉ đánh giá, không lưu
  /vaolenh MUA FPT 58.2 SL 56 TP 62 KL 1000   — đánh giá + lưu vào nhật ký (theo dõi, cảnh báo)
  /lenhmo                                      — danh sách lệnh đang mở (lãi/lỗ, rủi ro)
  /dong 12 [giá]                               — đóng lệnh #12 (mặc định theo giá hiện tại)
  /caidat 500000000 2 20                       — vốn, % rủi ro tối đa/lệnh, % tỷ trọng tối đa/mã
  /trogiup                                     — hướng dẫn
Giá vào có thể bỏ trống (lấy giá hiện tại). Giá cổ phiếu VN nhập theo nghìn đồng (58.2 = 58.200 ₫).
"""
import json
from typing import Optional

from . import notifier, risk, storage, trades
from .config import TELEGRAM_CHAT_ID, TELEGRAM_ENABLED

_offset: Optional[int] = None
_SIDES = {"MUA": "buy", "BUY": "buy", "LONG": "buy", "BAN": "sell", "BÁN": "sell", "SELL": "sell", "SHORT": "sell"}
_KEYS = {"SL": "stop", "CL": "stop", "STOP": "stop", "TP": "target", "CT": "target", "TARGET": "target",
         "KL": "qty", "QTY": "qty", "SOLUONG": "qty"}
_HELP = (
    "<b>FinWatch · Đánh giá rủi ro lệnh</b>\n"
    "/danhgia MUA FPT 58.2 SL 56 TP 62 KL 1000 — chỉ đánh giá\n"
    "/vaolenh MUA BTC 81000 SL 78500 TP 86000 KL 0.05 — đánh giá + lưu nhật ký, theo dõi &amp; cảnh báo\n"
    "   (thêm VITHE nếu là vị thế đã mua từ trước, vd /vaolenh MUA FPT 52 SL 50 KL 1000 VITHE)\n"
    "/goiy MUA FPT [giá] [KL 1000] — gợi ý cắt lỗ / chốt lời tham chiếu (ATR, hỗ trợ/kháng cự)\n"
    "/lenhmo — lệnh đang mở (kèm gợi ý dời cắt lỗ)\n"
    "/capnhat 12 SL 57 TP 63 — sửa cắt lỗ / chốt lời của lệnh #12\n"
    "/dong 12 [giá] — đóng lệnh #12\n"
    "/caidat 500000000 2 20 — vốn (₫), % rủi ro tối đa/lệnh, % tỷ trọng tối đa/mã\n"
    "Mã: cổ phiếu VN (FPT, HPG…), SJC, XAU, XAG, WTI, BRENT, NG, HG, KC, BTC, ETH…\n"
    "Giá cổ phiếu VN theo nghìn đồng; bỏ trống giá vào = giá hiện tại.\n"
    "<i>Kết quả là đánh giá định lượng để tham khảo, không phải khuyến nghị.</i>"
)


def parse_order(args: list) -> dict:
    """['MUA','FPT','58.2','SL','56','TP','62','KL','1000'] -> dict yêu cầu cho risk.evaluate."""
    toks = [t for a in args for t in a.split("=") if t]
    req: dict = {"side": "buy"}
    i = 0
    if i < len(toks) and toks[i].upper() in _SIDES:
        req["side"] = _SIDES[toks[i].upper()]
        i += 1
    if i < len(toks):
        req["symbol"] = toks[i]
        i += 1
    if i < len(toks) and risk._f(toks[i]) is not None:
        req["entry"] = risk._f(toks[i])
        i += 1
    if any(t.upper() in ("VITHE", "DANGGIU") for t in toks):  # vị thế đã mở từ trước (giá vào cũ)
        req["position"] = True
    while i < len(toks) - 1:
        field = _KEYS.get(toks[i].upper())
        if field:
            req[field] = risk._f(toks[i + 1])  # '1,000' -> 1000; '0,5' -> 0.5
            i += 2
        else:
            i += 1
    return req


def _e(s) -> str:
    return notifier._esc(s)


def format_eval(ev: dict) -> str:
    if not ev.get("ok"):
        return "❌ " + _e("; ".join(ev.get("errors", ["Không đánh giá được"])))
    d = ev["dec"]

    def f(v):
        return "—" if v is None else f"{v:,.{d}f}"

    lvl_icon = {"lo": "🟢", "mid": "🟠", "hi": "🔴"}[ev["level_class"]]
    money_txt = {"ok": "đạt", "warn": "cần chú ý", "bad": "vi phạm"}[ev["money_status"]]
    lines = [
        f"<b>Đánh giá lệnh {ev['side_label']} {_e(ev['symbol'])}</b> · {_e(ev['name'])}",
        f"Giá hiện tại {f(ev['price'])} {_e(ev['unit'])}{'' if ev['price_live'] else ' (thị trường nghỉ)'}",
        f"Vào {f(ev['entry'])} · CL {f(ev['stop'])} · CT {f(ev['target'])}"
        + (f" · KL {ev['qty']:,g}" if ev["qty"] else ""),
        f"{lvl_icon} Rủi ro: <b>{ev['score']}/100 — {ev['level']}</b> "
        f"(thị trường {ev['market_score']}, quản lý vốn: {money_txt})",
        "", "<b>Yếu tố thị trường</b>",
    ]
    for fa in ev["factors"]:
        if fa["kind"] == "na":
            continue
        icon = {"risk": "🔴", "ok": "🟢", "neutral": "⚪"}[fa["kind"]]
        detail = fa["detail"] if len(fa["detail"]) <= 140 else fa["detail"][:139] + "…"
        lines.append(f"{icon} {_e(fa['name'])} <b>{fa['score']}</b> — {_e(detail)}")
    lines += ["", "<b>Quản lý vốn</b>"]
    for c in ev["money"]["checks"]:
        icon = {"ok": "✅", "warn": "⚠️", "bad": "❌"}[c["status"]]
        lines.append(f"{icon} {_e(c['label'])} — {_e(c['detail'])}")
    if ev["money"]["risk_amt"]:
        lines.append(f"Số tiền rủi ro nếu chạm cắt lỗ: {ev['money']['risk_amt']:,.0f} ₫")
    lines += ["", f"<i>{_e(ev['disclaimer'])}</i>"]
    return "\n".join(lines)


def format_levels(ev: dict) -> str:
    """Tin nhắn gợi ý cắt lỗ / chốt lời tham chiếu cho một lệnh (từ risk.evaluate -> levels)."""
    if not ev.get("ok"):
        return "❌ " + _e("; ".join(ev.get("errors", ["Không tính được"])))
    lv = ev.get("levels")
    if not lv:
        return "❌ Chưa lấy được dữ liệu giá để tính gợi ý — thử lại sau."
    d = ev["dec"]

    def f(v):
        return "—" if v is None else f"{v:,.{d}f}"

    lines = [f"<b>Gợi ý cắt lỗ / chốt lời · {ev['side_label']} {_e(ev['symbol'])}</b>",
             f"Giá vào {f(ev['entry'])} {_e(ev['unit'])} · ATR(14) {f(lv['atr'])} ({lv['atr_pct']:.2f}%/phiên)",
             "", f"🛑 <b>Cắt lỗ {f(lv['stop']['value'])}</b> (−{lv['stop']['dist_pct']:.2f}%) — {_e(lv['stop']['method'])}",
             "   Khác: " + " · ".join(f"{_e(x['label'])} {f(x['value'])}" for x in lv["stop"]["alt"]),
             f"🎯 <b>Chốt lời {f(lv['target']['value'])}</b>"
             + (f" (R:R 1:{lv['target']['rr']:.2f})" if lv["target"]["rr"] else "") + f" — {_e(lv['target']['method'])}",
             "   Mốc: " + " · ".join(f"{x['label']} {f(x['value'])}" for x in lv["targets"])]
    if lv["supports"] or lv["resistances"]:
        lines.append(f"Hỗ trợ: {', '.join(f(x) for x in lv['supports']) or '—'} · "
                     f"Kháng cự: {', '.join(f(x) for x in lv['resistances']) or '—'}")
    if lv.get("budget"):
        lines.append(f"Theo cài đặt vốn: lỗ tối đa {lv['budget']['max_loss']:,.0f} ₫ → KL tối đa "
                     f"~{lv['budget']['qty_max']:,.0f} {_e(ev['qty_unit'])}")
    lines += [f"• {_e(n)}" for n in lv["notes"]]
    lines += ["", f"<i>{_e(lv['disclaimer'])}</i>", "Lưu lệnh: /vaolenh … SL &lt;cắt lỗ&gt; TP &lt;chốt lời&gt;"]
    return "\n".join(lines)


def _open_list() -> str:
    j = trades.journal_view()
    if not j["open"]:
        return "Không có lệnh nào đang mở."
    lines = ["<b>Lệnh đang mở</b>"]
    for t in j["open"]:
        d = t["dec"]
        pl = "—" if t["pnl"]["pct"] is None else f"{t['pnl']['pct']:+.2f}%"
        if t["pnl"]["vnd"] is not None:
            pl += f" ({t['pnl']['vnd']:+,.0f} ₫)"
        price = "—" if t["price"] is None else f"{t['price']:,.{d}f}"
        lines.append(f"#{t['id']} {'MUA' if t['side'] == 'buy' else 'BÁN'} {_e(t['symbol'])} vào {t['entry']:,.{d}f} → "
                     f"{price} · {pl} · rủi ro {t['risk_entry']}→{t['risk_now']}")
        sg = t.get("sugg")
        if sg and sg.get("changed"):
            lines.append(f"   💡 {_e(sg['stop_action'])}: {sg['stop']:,.{d}f} — /capnhat {t['id']} SL {sg['stop']:g}")
    lines.append(f"Lãi/lỗ tạm tính: {j['floating']:+,.0f} ₫")
    return "\n".join(lines)


def handle(text: str) -> str:
    parts = text.strip().split()
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]
    if cmd in ("/start", "/help", "/trogiup"):
        return _HELP
    if cmd in ("/danhgia", "/vaolenh"):
        if not args:
            return f"Cú pháp: {cmd} MUA FPT 58.2 SL 56 TP 62 KL 1000"
        req = parse_order(args)
        key = risk.resolve(req.get("symbol", ""))
        ev = risk.evaluate(req, open_same=trades.open_count(key) if key else 0)
        msg = format_eval(ev)
        if cmd == "/vaolenh" and ev.get("ok"):
            tid = trades.create(ev, note="", source="telegram")
            msg += f"\n\n📒 Đã lưu lệnh <b>#{tid}</b> vào nhật ký — hệ thống sẽ theo dõi và cảnh báo."
        return msg
    if cmd == "/goiy":
        if not args:
            return "Cú pháp: /goiy MUA FPT [giá vào] [KL 1000]"
        req = parse_order(args)
        ev = risk.evaluate({k: v for k, v in req.items() if k not in ("stop", "target")})
        return format_levels(ev)
    if cmd == "/capnhat":
        if not args or not args[0].lstrip("#").isdigit():
            return "Cú pháp: /capnhat 12 SL 57 TP 63"
        req = parse_order(["X", "X"] + args[1:])  # tái dùng bộ đọc SL/TP
        r = trades.update(int(args[0].lstrip("#")), req.get("stop"), req.get("target"))
        return (f"✅ Lệnh #{r['id']}: cắt lỗ {r['stop'] or 0:,g} · chốt lời {r['target'] or 0:,g}" if r["ok"]
                else "❌ " + _e(r["error"]))
    if cmd == "/lenhmo":
        return _open_list()
    if cmd == "/dong":
        if not args or not args[0].lstrip("#").isdigit():
            return "Cú pháp: /dong 12 [giá đóng]"
        r = trades.close(int(args[0].lstrip("#")), risk._f(args[1]) if len(args) > 1 else None)
        return f"✅ Đã đóng lệnh #{r['id']} tại {r['price']:,g}" if r["ok"] else "❌ " + _e(r["error"])
    if cmd == "/caidat":
        if not args:
            s = risk.get_settings()
            return (f"Vốn {s['capital']:,.0f} ₫ · rủi ro tối đa {s['max_risk_pct']:g}%/lệnh · "
                    f"tỷ trọng tối đa {s['max_pos_pct']:g}%/mã\nĐổi: /caidat 500000000 2 20")
        s = risk.save_settings(dict(zip(["capital", "max_risk_pct", "max_pos_pct"], args)))
        return (f"✅ Đã lưu: vốn {s['capital']:,.0f} ₫ · rủi ro tối đa {s['max_risk_pct']:g}%/lệnh · "
                f"tỷ trọng tối đa {s['max_pos_pct']:g}%/mã")
    return "Không hiểu lệnh. Gõ /trogiup để xem hướng dẫn."


def poll() -> None:
    """Đọc tin nhắn mới (chạy định kỳ vài giây/lần khi Telegram đã cấu hình)."""
    global _offset
    if not TELEGRAM_ENABLED:
        return
    if _offset is None:
        _offset = int(storage.kv_get("tg_offset") or 0)
    with notifier._client() as c:
        r = c.get(notifier._api("getUpdates"),
                  params={"offset": _offset, "timeout": 0, "allowed_updates": json.dumps(["message"])})
        data = r.json()
    if not data.get("ok"):
        return
    for u in data.get("result", []):
        _offset = u["update_id"] + 1
        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(TELEGRAM_CHAT_ID):
            continue  # chỉ nhận lệnh từ chat của chủ tài khoản
        text = (msg.get("text") or "").strip()
        if text.startswith("/"):
            try:
                reply = handle(text)
            except Exception as e:  # noqa: BLE001 — lỗi một lệnh không được làm dừng bot
                reply = f"❌ Lỗi xử lý lệnh: {_e(type(e).__name__)}"
            notifier.send_message(reply)
    storage.kv_set("tg_offset", str(_offset))
