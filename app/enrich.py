"""Làm giàu tin tức: dịch sang tiếng Việt + đánh giá mức độ ảnh hưởng thị trường.

- Dịch: Google Translate (endpoint miễn phí) -> dự phòng MyMemory -> giữ nguyên gốc.
- Đánh giá ảnh hưởng: heuristic theo từ khoá (cục bộ, không gọi mạng), có tính đến
  số nguồn đối chiếu và độ tin cậy hãng tin.
"""
import re
import urllib.parse
from typing import List, Optional

import httpx

from .collectors.http import get_client

# ---------------------------------------------------------------------------
# DỊCH
# ---------------------------------------------------------------------------

def _google(text: str, client: httpx.Client) -> Optional[str]:
    url = ("https://translate.googleapis.com/translate_a/single"
           "?client=gtx&sl=en&tl=vi&dt=t&q=" + urllib.parse.quote(text))
    r = client.get(url)
    r.raise_for_status()
    data = r.json()
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


def _mymemory(text: str, client: httpx.Client) -> Optional[str]:
    url = ("https://api.mymemory.translated.net/get?langpair=en|vi&q="
           + urllib.parse.quote(text))
    r = client.get(url)
    r.raise_for_status()
    return r.json().get("responseData", {}).get("translatedText")


def translate_vi(text: str, client: Optional[httpx.Client] = None) -> str:
    """Dịch sang tiếng Việt; nếu mọi cách thất bại thì trả lại nguyên văn."""
    if not text or not text.strip():
        return text or ""
    own = client is None
    client = client or get_client()
    try:
        for fn in (_google, _mymemory):
            try:
                out = fn(text, client)
                if out and out.strip():
                    return out.strip()
            except Exception:  # noqa: BLE001 — thử cách kế tiếp
                continue
        return text
    finally:
        if own:
            client.close()


# ---------------------------------------------------------------------------
# ĐÁNH GIÁ MỨC ĐỘ ẢNH HƯỞNG THỊ TRƯỜNG
# ---------------------------------------------------------------------------

# Từ khoá tác động MẠNH (điểm 3)
_HIGH = ["crash", "collapse", "plunge", "plummet", "tumble", "rout", "surge", "soar",
         "spike", "record high", "record low", "all-time high", "default", "bankruptcy",
         "sanction", "sanctions", "war", "invasion", "rate hike", "rate cut", "recession",
         "ban", "halt", "emergency", "crisis", "bailout", "shock", "slump", "meltdown",
         "skyrocket", "freefall"]
# Từ khoá tác động VỪA (điểm 2)
_MED = ["jump", "jumps", "rise", "rises", "fall", "falls", "drop", "drops", "gain",
        "gains", "rally", "sell-off", "selloff", "cut", "cuts", "hike", "warn", "warns",
        "warning", "miss", "misses", "beat", "beats", "downgrade", "upgrade", "tariff",
        "inflation", "jobs report", "fed", "opec", "earnings", "layoff", "layoffs",
        "stimulus", "deficit", "outflow", "inflow"]

_POS = ["surge", "soar", "spike", "jump", "jumps", "rise", "rises", "gain", "gains",
        "rally", "record high", "all-time high", "beat", "beats", "upgrade", "boom",
        "recover", "recovery", "climb", "higher", "rebound", "skyrocket", "top", "tops"]
_NEG = ["crash", "collapse", "plunge", "plummet", "tumble", "fall", "falls", "drop",
        "drops", "slump", "sell-off", "selloff", "loss", "losses", "miss", "misses",
        "downgrade", "warn", "warns", "cut", "cuts", "recession", "default", "bankruptcy",
        "sanction", "sanctions", "war", "ban", "crisis", "lower", "sink", "freefall",
        "meltdown", "layoff", "layoffs", "rout"]

# Từ khoá tiếng Việt cho tin CafeF / Vietstock / VnEconomy (tin Việt không dịch nên
# bộ từ khoá tiếng Anh ở trên không khớp được).
_HIGH += ["giảm sàn", "tăng trần", "lao dốc", "bán tháo", "sụp đổ", "phá sản", "vỡ nợ",
          "khởi tố", "bị bắt", "bắt tạm giam", "truy tố", "thao túng", "đình chỉ",
          "huỷ niêm yết", "hủy niêm yết", "cưỡng chế", "giải chấp", "call margin",
          "khủng hoảng", "kỷ lục", "tăng vọt", "giảm mạnh", "tăng mạnh", "bứt phá",
          "thua lỗ nặng", "lỗ nặng"]
_MED += ["lợi nhuận", "doanh thu", "cổ tức", "phát hành", "thoái vốn", "thâu tóm",
         "sáp nhập", "niêm yết", "khối ngoại", "mua ròng", "bán ròng", "lãi suất",
         "tỷ giá", "trái phiếu", "nâng hạng", "xử phạt", "vi phạm", "cảnh báo",
         "kiểm soát", "lãi", "lỗ", "tăng", "giảm"]
_POS += ["tăng trần", "tăng vọt", "tăng mạnh", "bứt phá", "kỷ lục", "lãi", "lợi nhuận tăng",
         "mua ròng", "nâng hạng", "hồi phục", "tăng", "cổ tức"]
_NEG += ["giảm sàn", "lao dốc", "bán tháo", "sụp đổ", "phá sản", "vỡ nợ", "khởi tố", "bị bắt",
         "bắt tạm giam", "truy tố", "thao túng", "đình chỉ", "huỷ niêm yết", "hủy niêm yết",
         "cưỡng chế", "giải chấp", "khủng hoảng", "giảm mạnh", "lỗ", "thua lỗ", "bán ròng",
         "xử phạt", "vi phạm", "cảnh báo", "giảm"]

_LEVELS = {3: ("Cao", "🔴"), 2: ("Trung bình", "🟠"), 1: ("Thấp", "⚪")}

# ---------------------------------------------------------------------------
# MỨC QUAN TRỌNG VỚI THỊ TRƯỜNG (dùng cho báo cáo định kỳ: chỉ giữ tin có thể làm thị trường biến động)
# Từ khoá tác động mạnh ở trên bắt cả tin doanh nghiệp nhỏ / tin "mềm" (vd "lập kỷ lục doanh thu",
# "công bố giải thưởng"). Điểm dưới đây cộng thêm chủ đề vĩ mô – chính sách, phạm vi toàn thị trường,
# tín hiệu Dovish/Hawkish, số nguồn xác nhận; trừ điểm tin mềm.
# ---------------------------------------------------------------------------
_MACRO = ["fed", "federal reserve", "fomc", "powell", "ecb", "boj", "pboc", "central bank", "interest rate",
          "interest rates", "rate hike", "rate cut", "inflation", "cpi", "ppi", "gdp", "recession", "payrolls",
          "jobs report", "unemployment", "treasury yields", "treasury secretary", "treasuries", "yields",
          "bond market", "tariff", "tariffs",
          "trade war", "sanction", "sanctions", "war", "invasion", "ceasefire", "opec", "debt ceiling",
          "government shutdown", "stimulus", "bailout", "default", "dollar", "yuan", "currency", "oil supply",
          "energy crisis", "gas supply", "gas storage", "hormuz", "khủng hoảng năng lượng", "nguồn cung dầu",
          "ngân hàng nhà nước", "nhnn", "lãi suất", "lạm phát", "tỷ giá", "chính sách tiền tệ", "điều hành",
          "thuế quan", "thuế đối ứng", "chiến tranh", "trừng phạt", "nâng hạng", "tín dụng", "room tín dụng",
          "quốc hội", "chính phủ", "thủ tướng", "bộ tài chính", "ủy ban chứng khoán", "ubcknn", "gói hỗ trợ",
          "nghị định", "nghị quyết", "fdi", "xuất khẩu", "giải ngân"]
_MARKET = ["stock market", "wall street", "s&p 500", "s&p", "nasdaq", "dow", "dow jones", "stocks", "equities",
           "global markets", "all-time high", "record high", "bitcoin", "btc", "ether", "crypto market",
           "gold price", "gold prices", "gold", "oil price", "oil prices", "crude", "brent", "wti", "msci",
           "ftse russell", "nikkei", "vn-index", "vnindex", "vn30", "hnx", "upcom", "chứng khoán",
           "thị trường chứng khoán", "toàn thị trường", "thị trường chung",
           "khối ngoại", "bán ròng", "mua ròng", "thanh khoản", "giá vàng", "vàng miếng", "giá dầu",
           "cổ phiếu ngân hàng", "nhóm ngân hàng", "bluechip", "vốn hoá"]
_SOFT = ["award", "awards", "podcast", "quiz", "sponsored", "giveaway", "how to", "giải thưởng", "vinh danh",
         "khai trương", "tuyển dụng", "hội thảo", "tọa đàm", "toạ đàm", "kỷ niệm", "từ thiện", "tài trợ",
         "cuộc thi", "khánh thành", "trao tặng"]


def market_importance(n: dict) -> dict:
    """Điểm quan trọng với thị trường của một tin đã gộp (news_service): score, important, lý do.

    +3 mức ảnh hưởng Cao (+1 nếu Trung bình) · +3 chủ đề vĩ mô/chính sách · +2 phạm vi toàn thị trường
    (hai mục này chỉ +1 nếu từ khoá chỉ có trong tóm tắt, không có trong tiêu đề)
    · +2 có tín hiệu Dovish/Hawkish · +2 khi ≥2 nguồn xác nhận (+1 mỗi nguồn thêm, tối đa +4)
    · +1 hãng tin uy tín hàng đầu · −4 tin mềm (giải thưởng, khai trương, hội thảo…).
    Tin đạt REPORT_MIN_IMPORTANCE điểm (mặc định 5) mới được đưa vào báo cáo định kỳ.
    """
    from .config import REPORT_MIN_IMPORTANCE  # import muộn: config không phụ thuộc module này
    title = f" {n.get('title', '')} {n.get('vi_title', '')} "
    text = f"{title} {n.get('summary', '')} "
    score, why = 0, []
    level = (n.get("impact") or {}).get("level", 1)
    if level == 3:
        score += 3
        why.append("ảnh hưởng cao")
    elif level == 2:
        score += 1
    # Chủ đề nằm trong TIÊU ĐỀ mới tính đủ điểm; chỉ nhắc trong tóm tắt (vd bài về một doanh nghiệp
    # nhỏ có câu "thị trường chứng khoán…") thì cộng ít.
    if _RE_MACRO.search(title):
        score += 3
        why.append("vĩ mô – chính sách")
    elif _RE_MACRO.search(text):
        score += 1
    if _RE_MARKET.search(title):
        score += 2
        why.append("toàn thị trường")
    elif _RE_MARKET.search(text):
        score += 1
    if (n.get("tone") or {}).get("tone") in ("hawkish", "dovish"):
        score += 2
        why.append(n["tone"]["label"])
    src = n.get("corroboration_count", 1)
    if src >= 2:
        score += min(4, src)
        why.append(f"{src} nguồn xác nhận")
    if n.get("trust") == 1:
        score += 1
    if _RE_SOFT.search(text):
        score -= 4
        why.append("tin mềm")
    return {"score": score, "important": score >= REPORT_MIN_IMPORTANCE, "reasons": why}


def _compile(words: List[str]) -> "re.Pattern":
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


_RE_HIGH = _compile(_HIGH)
_RE_MED = _compile(_MED)
_RE_POS = _compile(_POS)
_RE_NEG = _compile(_NEG)
_RE_MACRO = _compile(_MACRO)
_RE_MARKET = _compile(_MARKET)
_RE_SOFT = _compile(_SOFT)


def _hits(text: str, rx: "re.Pattern") -> List[str]:
    # Khớp NGUYÊN TỪ để tránh bắt nhầm chuỗi con (vd 'ban' trong 'bank').
    return sorted({m.lower() for m in rx.findall(text)})


def assess_impact(title: str, summary: str, corroboration_count: int = 1,
                  source_trust: Optional[int] = None) -> dict:
    text = f" {title} {summary} "

    high = _hits(text, _RE_HIGH)
    med = _hits(text, _RE_MED)
    base = 3 if high else (2 if med else 1)

    # Nhiều nguồn độc lập cùng đưa tin -> đáng chú ý hơn (chỉ nâng Thấp -> Trung bình).
    if corroboration_count >= 2 and base == 1:
        base = 2
    score = min(base, 3)

    pos = _hits(text, _RE_POS)
    neg = _hits(text, _RE_NEG)
    if len(pos) > len(neg):
        direction, dsym = "Tích cực", "🟢"
    elif len(neg) > len(pos):
        direction, dsym = "Tiêu cực", "🔴"
    else:
        direction, dsym = "Trung lập", "⚪"

    signals = sorted(set(high + med))[:5]
    level_label, level_sym = _LEVELS[score]
    return {
        "level": score,                 # 1..3
        "level_label": level_label,     # Thấp / Trung bình / Cao
        "level_sym": level_sym,
        "direction": direction,         # Tích cực / Tiêu cực / Trung lập
        "direction_sym": dsym,
        "signals": signals,             # từ khoá tín hiệu (tiếng Anh) để minh bạch
    }
