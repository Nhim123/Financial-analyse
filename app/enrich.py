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


def _compile(words: List[str]) -> "re.Pattern":
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I)


_RE_HIGH = _compile(_HIGH)
_RE_MED = _compile(_MED)
_RE_POS = _compile(_POS)
_RE_NEG = _compile(_NEG)


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
