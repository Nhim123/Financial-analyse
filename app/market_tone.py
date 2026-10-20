"""Chỉ số Dovish/Hawkish — đo "giọng điệu chính sách tiền tệ" của dòng tin.

Khái niệm tài chính:
- HAWKISH (diều hâu): tín hiệu thắt chặt — tăng lãi suất, lạm phát nóng, QT.
  Thường bất lợi cho vàng/crypto/cổ phiếu, hỗ trợ USD, đẩy lợi suất trái phiếu.
- DOVISH (bồ câu): tín hiệu nới lỏng — hạ lãi suất, kích thích, lạm phát hạ nhiệt.
  Thường hỗ trợ tài sản rủi ro và vàng, gây áp lực lên USD.

Phương pháp:
1. Mỗi tin được quét từ khoá tín hiệu (khớp NGUYÊN TỪ) ở tiêu đề + tóm tắt gốc (tiếng Anh
   hoặc tiếng Việt với tin trong nước).
2. Tin nghiêng về phía có nhiều tín hiệu hơn; bằng nhau (cùng >0) = "mixed".
3. Tổng hợp có TRỌNG SỐ: w = mức ảnh hưởng (1-3) + 1 nếu tin được ≥2 nguồn xác nhận.
4. Kết quả: % Dovish / % Trung lập / % Hawkish trên tổng trọng số, và điểm nghiêng
   ròng net = (H - D) / (H + D) * 100 chỉ tính trên các tin CÓ tín hiệu chính sách.
5. Tác động tới từng nhóm thị trường (market_effects) suy ra từ độ nghiêng theo cơ chế truyền
   dẫn chính sách — là ước tính định tính, không phải dự báo giá.
"""
import re
from typing import Dict, List, Optional

# --- Tín hiệu HAWKISH (thắt chặt) ---
_HAWKISH = [
    "rate hike", "rate hikes", "raise rates", "raises rates", "raising rates",
    "rate increase", "higher rates", "higher for longer", "hawkish",
    "tightening", "tightens", "tighten policy", "quantitative tightening",
    "taper", "tapering", "restrictive", "balance sheet runoff",
    "inflation surges", "inflation jumps", "inflation accelerates",
    "hot inflation", "sticky inflation", "inflation pressures",
    "price pressures", "wage pressures", "overheating", "inflation fears",
    "strong jobs report", "yields surge", "yields jump", "yields rise",
    "bond selloff", "bond sell-off", "cpi beats",
    # Tiếng Việt (tin CafeF / Vietstock / VnEconomy và bản dịch)
    "tăng lãi suất", "nâng lãi suất", "lãi suất tăng", "giữ lãi suất cao", "thắt chặt tiền tệ",
    "thắt chặt chính sách", "diều hâu", "hút ròng", "phát hành tín phiếu",
    "lạm phát tăng", "lạm phát cao", "lạm phát nóng", "lạm phát tăng tốc", "lợi suất tăng",
    "rút thanh khoản",
]

# --- Tín hiệu DOVISH (nới lỏng) ---
_DOVISH = [
    "rate cut", "rate cuts", "cut rates", "cuts rates", "cutting rates",
    "rate reduction", "lower rates", "lowers rates", "dovish",
    "easing", "eases policy", "loosening", "quantitative easing",
    "stimulus", "liquidity injection", "accommodative", "bond buying",
    "inflation cools", "inflation eases", "inflation slows",
    "cooling inflation", "disinflation", "deflation", "cpi misses",
    "weak jobs", "jobs miss", "unemployment rises", "recession fears",
    "economic slowdown", "growth slows", "soft landing",
    "fed pause", "rate pause", "pauses rates", "pivot",
    # Tiếng Việt
    "giảm lãi suất", "hạ lãi suất", "cắt giảm lãi suất", "lãi suất giảm", "nới lỏng tiền tệ",
    "nới lỏng chính sách", "bồ câu", "bơm tiền", "bơm ròng", "bơm thanh khoản", "hỗ trợ thanh khoản",
    "kích thích kinh tế", "gói kích thích", "lạm phát giảm", "lạm phát hạ nhiệt", "lợi suất giảm",
]


def _compile(words: List[str]) -> "re.Pattern":
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


_RE_H = _compile(_HAWKISH)
_RE_D = _compile(_DOVISH)
# Đuôi phủ định ngay sau tín hiệu (tối đa 1 từ đệm): odds/bets/hopes ... fall/fade/ease…
_RE_NEGATE = re.compile(
    r"\s*(?:-\s*)?(?:(?:odds|bets|expectations|hopes|fears|chances|bets)\s+)?"
    r"(?:fall|falls|fell|fade|fades|faded|ease|eases|eased|recede|recedes|dim|dims|wane|wanes|cool|cools|"
    r"decrease|decreases|decreased|decline|declines|declined|drop|drops|dropped|slip|slips|slipped|"
    r"diminish|diminishes|subside|subsides|retreat|retreats|shrink|shrinks|"
    r"giảm|hạ nhiệt|suy yếu|lùi xa)\b", re.I)

_TONE_LABELS = {
    "hawkish": ("Hawkish", "🦅"),
    "dovish": ("Dovish", "🕊️"),
    "mixed": ("Trái chiều", "⚖️"),
    "neutral": ("Trung lập", "⚪"),
}


# ---------------------------------------------------------------------------
# TÁC ĐỘNG TỚI TỪNG NHÓM THỊ TRƯỜNG — theo cơ chế truyền dẫn chính sách tiền tệ:
# hướng ảnh hưởng khi tín hiệu HAWKISH (+1 = có lợi/tăng, -1 = bất lợi/giảm); DOVISH đảo chiều.
# (key, tên, hướng khi hawkish, giải thích khi hawkish, giải thích khi dovish)
# ---------------------------------------------------------------------------
MARKETS = [
    ("ck_tg", "CK thế giới", -1, "Lãi suất cao làm giảm định giá cổ phiếu",
     "Chi phí vốn giảm, hỗ trợ định giá cổ phiếu"),
    ("ck_vn", "CK Việt Nam", -1, "Áp lực tỷ giá, khối ngoại dễ rút vốn",
     "Dư địa nới lỏng, dòng vốn có thể quay lại"),
    ("vang", "Vàng", -1, "Lợi suất thực tăng, vàng kém hấp dẫn",
     "Lợi suất thực giảm, hỗ trợ giá vàng"),
    ("dau", "Dầu", -1, "Kỳ vọng nhu cầu năng lượng chậm lại",
     "Kỳ vọng nhu cầu năng lượng phục hồi"),
    ("crypto", "Crypto", -1, "Thanh khoản thắt lại, tài sản rủi ro chịu áp lực",
     "Thanh khoản dồi dào, khẩu vị rủi ro tăng"),
    ("usd", "USD / tỷ giá", +1, "USD mạnh lên, USD/VND chịu áp lực tăng",
     "USD suy yếu, tỷ giá hạ nhiệt"),
]


def market_effects(lean: Optional[float]) -> List[Dict]:
    """Tác động ước tính tới từng nhóm thị trường từ độ nghiêng chính sách.

    lean: điểm nghiêng ròng -100 (dovish) .. +100 (hawkish); None = không có tín hiệu.
    |lean| < 15 -> trung tính; ≥ 50 -> tác động mạnh.
    """
    out = []
    for key, name, hawk_dir, why_h, why_d in MARKETS:
        if lean is None or abs(lean) < 15:
            d, why = 0, "Chưa có tín hiệu chính sách rõ ràng"
        else:
            d = hawk_dir if lean > 0 else -hawk_dir
            why = why_h if lean > 0 else why_d
        strong = lean is not None and abs(lean) >= 50
        if d > 0:
            label = "Tăng mạnh" if key == "usd" and strong else ("Tăng" if key == "usd" else
                                                                ("Tích cực mạnh" if strong else "Tích cực"))
        elif d < 0:
            label = "Giảm mạnh" if key == "usd" and strong else ("Giảm" if key == "usd" else
                                                                ("Tiêu cực mạnh" if strong else "Tiêu cực"))
        else:
            label = "Trung tính"
        out.append({"key": key, "name": name, "dir": d, "label": label, "why": why,
                    "arrow": "▲" if d > 0 else ("▼" if d < 0 else "■")})
    return out


def item_effects(tone: Dict) -> List[Dict]:
    """Tác động của MỘT tin: tin hawkish/dovish -> hướng tác động tới từng nhóm thị trường."""
    t = tone.get("tone")
    if t not in ("hawkish", "dovish"):
        return []
    n = abs(len(tone.get("hawkish_signals", [])) - len(tone.get("dovish_signals", [])))
    return market_effects((60 if n >= 2 else 40) * (1 if t == "hawkish" else -1))


def assess_tone(title: str, summary: str = "") -> Dict:
    """Chấm giọng điệu chính sách cho một tin (văn bản gốc — tiếng Anh hoặc tiếng Việt)."""
    text = f" {title} {summary} "
    h_set, d_set = set(), set()
    # Tín hiệu bị phủ định ngay sau đó ("rate hike odds fall", "kỳ vọng tăng lãi suất giảm")
    # mang nghĩa ngược lại -> tính cho phía đối diện.
    for rx, same, other in ((_RE_H, h_set, d_set), (_RE_D, d_set, h_set)):
        for m in rx.finditer(text):
            sig = m.group(0).lower()
            (other if _RE_NEGATE.match(text, m.end()) else same).add(sig)
    h, d = sorted(h_set), sorted(d_set)
    if len(h) > len(d):
        tone = "hawkish"
    elif len(d) > len(h):
        tone = "dovish"
    elif h and d:
        tone = "mixed"
    else:
        tone = "neutral"
    label, sym = _TONE_LABELS[tone]
    return {"tone": tone, "label": label, "sym": sym,
            "hawkish_signals": h, "dovish_signals": d}


def aggregate(items: List[dict]) -> Optional[Dict]:
    """Tổng hợp chỉ số thị trường từ các tin đã hiển thị (đã gắn tone + impact)."""
    # Loại trùng theo link (một tin có thể xuất hiện ở nhiều lĩnh vực).
    unique = {}
    for it in items:
        unique.setdefault(it["link"], it)
    if not unique:
        return None

    dw = hw = nw = 0.0
    h_top: List[str] = []
    d_top: List[str] = []
    for it in unique.values():
        w = float(it["impact"]["level"]) + (1.0 if it["verified"] else 0.0)
        tone = it["tone"]["tone"]
        if tone == "hawkish":
            hw += w
            h_top.extend(it["tone"]["hawkish_signals"])
        elif tone == "dovish":
            dw += w
            d_top.extend(it["tone"]["dovish_signals"])
        else:  # neutral + mixed
            nw += w

    total = dw + hw + nw
    pct_d = round(dw / total * 100, 1)
    pct_h = round(hw / total * 100, 1)
    pct_n = round(100 - pct_d - pct_h, 1)

    # Điểm nghiêng ròng: chỉ tính trên tin có tín hiệu chính sách.
    if (dw + hw) > 0:
        net = round((hw - dw) / (hw + dw) * 100)
        if net >= 50:
            verdict, vsym, vclass = "Nghiêng mạnh HAWKISH — kỳ vọng thắt chặt tiền tệ", "🦅", "hawk"
        elif net >= 15:
            verdict, vsym, vclass = "Thiên HAWKISH — nghiêng về thắt chặt", "🦅", "hawk"
        elif net <= -50:
            verdict, vsym, vclass = "Nghiêng mạnh DOVISH — kỳ vọng nới lỏng tiền tệ", "🕊️", "dove"
        elif net <= -15:
            verdict, vsym, vclass = "Thiên DOVISH — nghiêng về nới lỏng", "🕊️", "dove"
        else:
            verdict, vsym, vclass = "CÂN BẰNG — tín hiệu hai chiều tương đương", "⚖️", "bal"
    else:
        net = None
        verdict, vsym, vclass = "Chưa đủ tín hiệu chính sách trong dòng tin hiện tại", "⚪", "bal"

    def _top(sig_list: List[str], k: int = 5) -> List[str]:
        freq: Dict[str, int] = {}
        for s in sig_list:
            freq[s] = freq.get(s, 0) + 1
        return [s for s, _ in sorted(freq.items(), key=lambda x: -x[1])[:k]]

    # Độ tin cậy của chỉ số: phụ thuộc SỐ LƯỢNG tin có tín hiệu chính sách.
    # Ít tín hiệu -> kết luận dễ bị nhiễu bởi 1-2 tiêu đề đơn lẻ.
    n_sig = sum(1 for it in unique.values() if it["tone"]["tone"] in ("dovish", "hawkish"))
    if n_sig >= 6:
        confidence, conf_label = "high", "Độ tin cậy: Cao"
    elif n_sig >= 3:
        confidence, conf_label = "medium", "Độ tin cậy: Trung bình"
    elif n_sig >= 1:
        confidence, conf_label = "low", "Độ tin cậy: Thấp (ít tín hiệu)"
    else:
        confidence, conf_label = "none", "Không có tín hiệu"

    return {
        "confidence": confidence,
        "confidence_label": conf_label,
        "n_signal_items": n_sig,
        "pct_dovish": pct_d,
        "pct_neutral": pct_n,
        "pct_hawkish": pct_h,
        "net": net,                       # -100 (dovish tuyệt đối) .. +100 (hawkish tuyệt đối)
        "verdict": verdict,
        "verdict_sym": vsym,
        "verdict_class": vclass,          # dove | bal | hawk (để tô màu)
        "n_items": len(unique),
        "n_dovish": sum(1 for it in unique.values() if it["tone"]["tone"] == "dovish"),
        "n_hawkish": sum(1 for it in unique.values() if it["tone"]["tone"] == "hawkish"),
        "top_hawkish_signals": _top(h_top),
        "top_dovish_signals": _top(d_top),
    }
