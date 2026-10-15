"""Cấu hình hệ thống: nguồn dữ liệu, danh mục theo dõi, ngưỡng kiểm chứng."""
import os


def _load_dotenv(path: str) -> None:
    """Nạp file .env (KEY=VALUE) vào biến môi trường; biến đã đặt sẵn được ưu tiên."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# ---------------------------------------------------------------------------
# Tham số chung
# ---------------------------------------------------------------------------
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "finwatch.db"))
COLLECT_INTERVAL_SECONDS = int(os.getenv("COLLECT_INTERVAL_SECONDS", "600"))
# Chu kỳ lấy GIÁ TRỰC TIẾP (giây) đẩy lên giao diện — tách khỏi chu kỳ kiểm chứng ở trên.
LIVE_INTERVAL_SECONDS = int(os.getenv("LIVE_INTERVAL_SECONDS", "15"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))

# Ngưỡng cảnh báo khi đối chiếu đa nguồn (chênh lệch % giữa các nguồn).
DIFF_THRESHOLD_PCT = {
    "crypto": 1.0,
    "fx": 0.5,
    "stock": 1.5,
    "commodity": 2.0,
}

# Ngưỡng phát hiện bất thường: thay đổi % so với lần thu thập trước.
ANOMALY_THRESHOLD_PCT = {
    "crypto": 15.0,
    "fx": 5.0,
    "stock": 8.0,
    "commodity": 10.0,
}

# ---------------------------------------------------------------------------
# DANH BẠ NGUỒN (Source registry) — phục vụ hiển thị & đánh giá độ tin cậy
# trust: 1=cao nhất (cơ quan chính thức), 2=sàn/đơn vị uy tín, 3=tổng hợp
# ---------------------------------------------------------------------------
SOURCES = {
    "coingecko": {
        "label": "CoinGecko",
        "homepage": "https://www.coingecko.com",
        "category": "Tổng hợp dữ liệu crypto",
        "trust": 2,
        "official": False,
    },
    "binance": {
        "label": "Binance",
        "homepage": "https://www.binance.com",
        "category": "Sàn giao dịch crypto",
        "trust": 2,
        "official": True,
    },
    "frankfurter": {
        "label": "Frankfurter (ECB)",
        "homepage": "https://www.frankfurter.app",
        "category": "Tỷ giá tham chiếu — nguồn gốc Ngân hàng Trung ương châu Âu (ECB)",
        "trust": 1,
        "official": True,
    },
    "erapi": {
        "label": "ExchangeRate-API (open)",
        "homepage": "https://www.exchangerate-api.com",
        "category": "Tổng hợp tỷ giá",
        "trust": 3,
        "official": False,
    },
    "cnbc": {
        "label": "CNBC",
        "homepage": "https://www.cnbc.com/markets",
        "category": "Dữ liệu thị trường (chỉ số, hàng hoá)",
        "trust": 2,
        "official": False,
    },
    "yahoo": {
        "label": "Yahoo Finance",
        "homepage": "https://finance.yahoo.com",
        "category": "Tổng hợp dữ liệu thị trường",
        "trust": 2,
        "official": False,
    },
}

# ---------------------------------------------------------------------------
# DANH MỤC THEO DÕI
# Mỗi mục có "key" duy nhất + ánh xạ mã sang từng nguồn để đối chiếu.
# ---------------------------------------------------------------------------

# Crypto — đối chiếu CoinGecko vs Binance, quy ra USD
CRYPTO = [
    {"key": "BTC", "name": "Bitcoin",  "coingecko": "bitcoin",      "binance": "BTCUSDT"},
    {"key": "ETH", "name": "Ethereum", "coingecko": "ethereum",     "binance": "ETHUSDT"},
    {"key": "BNB", "name": "BNB",      "coingecko": "binancecoin",  "binance": "BNBUSDT"},
    {"key": "SOL", "name": "Solana",   "coingecko": "solana",       "binance": "SOLUSDT"},
    {"key": "XRP", "name": "XRP",      "coingecko": "ripple",       "binance": "XRPUSDT"},
]

# FX — quy ước thống nhất: giá trị = số đơn vị ngoại tệ cho 1 USD (1 USD = X CCY)
# Đối chiếu Frankfurter (ECB) vs ExchangeRate-API.
FX = [
    {"key": "USDEUR", "name": "1 USD = ? EUR", "ccy": "EUR"},
    {"key": "USDGBP", "name": "1 USD = ? GBP", "ccy": "GBP"},
    {"key": "USDJPY", "name": "1 USD = ? JPY", "ccy": "JPY"},
    {"key": "USDCNY", "name": "1 USD = ? CNY", "ccy": "CNY"},
    {"key": "USDVND", "name": "1 USD = ? VND", "ccy": "VND"},
]

# Chứng khoán — chỉ số lớn thế giới. Đối chiếu CNBC vs Yahoo.
STOCKS = [
    {"key": "SPX",  "name": "S&P 500 (Mỹ)",         "cnbc": ".SPX",    "yahoo": "^GSPC"},
    {"key": "DJI",  "name": "Dow Jones (Mỹ)",        "cnbc": ".DJI",    "yahoo": "^DJI"},
    {"key": "NDQ",  "name": "Nasdaq Composite (Mỹ)", "cnbc": ".IXIC",   "yahoo": "^IXIC"},
    {"key": "FTSE", "name": "FTSE 100 (Anh)",        "cnbc": ".FTSE",   "yahoo": "^FTSE"},
    {"key": "N225", "name": "Nikkei 225 (Nhật)",     "cnbc": ".N225",   "yahoo": "^N225"},
    {"key": "DAX",  "name": "DAX (Đức)",             "cnbc": ".GDAXI",  "yahoo": "^GDAXI"},
]

# Hàng hoá — vàng & dầu. Đối chiếu CNBC vs Yahoo.
COMMODITIES = [
    {"key": "XAU",   "name": "Vàng (XAU/USD)",   "cnbc": "@GC.1",  "yahoo": "GC=F"},
    {"key": "WTI",   "name": "Dầu thô WTI",       "cnbc": "@CL.1",  "yahoo": "CL=F"},
    {"key": "BRENT", "name": "Dầu thô Brent",     "cnbc": "@LCO.1", "yahoo": "BZ=F"},
]

ASSET_LABELS = {
    "crypto": "Tiền mã hoá (Crypto)",
    "fx": "Tỷ giá (FX)",
    "stock": "Chứng khoán thế giới",
    "commodity": "Hàng hoá (Vàng / Dầu)",
}

# ---------------------------------------------------------------------------
# TIN TỨC (News)
# ---------------------------------------------------------------------------
NEWS_MAX_PER_CLASS = int(os.getenv("NEWS_MAX_PER_CLASS", "8"))
NEWS_STALE_HOURS = int(os.getenv("NEWS_STALE_HOURS", "48"))
# Ngưỡng tương đồng tiêu đề để coi là "cùng một tin" (đối chiếu chéo / gộp trùng).
NEWS_SIM_THRESHOLD = float(os.getenv("NEWS_SIM_THRESHOLD", "0.5"))

# Danh bạ hãng tin (trust: 1 uy tín hàng đầu, 2 chuyên ngành uy tín, 3 tổng hợp)
NEWS_SOURCES = {
    "ft":            {"label": "Financial Times", "homepage": "https://www.ft.com/markets",   "trust": 1},
    "marketwatch":   {"label": "MarketWatch",     "homepage": "https://www.marketwatch.com",  "trust": 2},
    "coindesk":      {"label": "CoinDesk",        "homepage": "https://www.coindesk.com",     "trust": 2},
    "oilprice":      {"label": "OilPrice",        "homepage": "https://oilprice.com",         "trust": 2},
    "investingnews": {"label": "Investing News",  "homepage": "https://investingnews.com",    "trust": 3},
    "cointelegraph": {"label": "Cointelegraph",   "homepage": "https://cointelegraph.com",    "trust": 3},
    "seekingalpha":  {"label": "Seeking Alpha",   "homepage": "https://seekingalpha.com",     "trust": 3},
    # Hãng tin Việt Nam — lang "vi": không dịch, không phân loại theo từ khoá tiếng Anh.
    "cafef":         {"label": "CafeF",           "homepage": "https://cafef.vn",             "trust": 2, "lang": "vi"},
    "vietstock":     {"label": "Vietstock",       "homepage": "https://vietstock.vn",         "trust": 2, "lang": "vi"},
    "vneconomy":     {"label": "VnEconomy",       "homepage": "https://vneconomy.vn",         "trust": 2, "lang": "vi"},
}

# Nhóm tin hiển thị (khác nhóm số liệu ASSET_LABELS: tin tỷ giá đã thay bằng tin Việt Nam).
NEWS_CLASS_LABELS = {
    "stock": "Chứng khoán thế giới",
    "commodity": "Hàng hoá (Vàng / Dầu)",
    "crypto": "Tiền mã hoá (Crypto)",
    "vn": "Chứng khoán & Doanh nghiệp Việt Nam",
}

# Feed RSS đã kiểm tra chạy được. "default" = nhóm gán mặc định cho feed chuyên đề;
# feed tổng hợp (default rỗng) chỉ hiển thị tin khớp từ khoá.
NEWS_FEEDS = [
    {"source": "coindesk",      "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "default": ["crypto"]},
    {"source": "cointelegraph", "url": "https://cointelegraph.com/rss",                   "default": ["crypto"]},
    {"source": "oilprice",      "url": "https://oilprice.com/rss/main",                   "default": ["commodity"]},
    {"source": "investingnews", "url": "https://investingnews.com/category/daily/resource-investing/precious-metals-investing/gold-investing/feed/", "default": ["commodity"]},
    {"source": "marketwatch",   "url": "http://feeds.marketwatch.com/marketwatch/topstories/",  "default": []},
    {"source": "marketwatch",   "url": "http://feeds.marketwatch.com/marketwatch/marketpulse/", "default": []},
    {"source": "ft",            "url": "https://www.ft.com/markets?format=rss",           "default": []},
    {"source": "seekingalpha",  "url": "https://seekingalpha.com/market_currents.xml",    "default": []},
    {"source": "cafef",         "url": "https://cafef.vn/thi-truong-chung-khoan.rss",     "default": ["vn"]},
    {"source": "cafef",         "url": "https://cafef.vn/doanh-nghiep.rss",               "default": ["vn"]},
    {"source": "vietstock",     "url": "https://vietstock.vn/830/chung-khoan/co-phieu.rss", "default": ["vn"]},
    {"source": "vietstock",     "url": "https://vietstock.vn/737/doanh-nghiep/hoat-dong-kinh-doanh.rss", "default": ["vn"]},
    {"source": "vneconomy",     "url": "https://vneconomy.vn/chung-khoan.rss",            "default": ["vn"]},
]

# Tin Việt Nam: loại bài PHÂN TÍCH / NHẬN ĐỊNH thị trường, chỉ giữ tin sự kiện.
# So khớp trên tiêu đề (chữ thường) và đường dẫn bài (slug không dấu).
NEWS_VN_EXCLUDE_TITLE = [
    "phân tích", "nhận định", "khuyến nghị", "góc nhìn", "dự báo", "chuyên gia",
    "blog chứng khoán", "vietstock daily", "kịch bản", "chiến lược đầu tư", "xu hướng",
    "theo dấu dòng tiền", "thấy gì", "có nên", "cổ phiếu cần quan tâm", "cơ hội đầu tư",
    "định giá", "bình luận", "tuần tới", "phiên tới", "điểm nhấn",
]
NEWS_VN_EXCLUDE_LINK = [
    "phan-tich", "nhan-dinh", "khuyen-nghi", "goc-nhin", "du-bao", "chuyen-gia",
    "blog-chung-khoan", "vietstock-daily", "kich-ban", "chien-luoc-dau-tu", "xu-huong",
]

# Từ khoá phân loại tin vào từng lĩnh vực. Khớp NGUYÊN TỪ (word boundary) để tránh
# bắt nhầm chuỗi con — vd "euro" không được khớp trong "Europe".
NEWS_KEYWORDS = {
    "crypto": ["bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency",
               "blockchain", "stablecoin", "solana", "xrp", "ripple", "binance",
               "coinbase", "defi", "altcoin", "token", "tokens"],
    "commodity": ["gold", "silver", "xau", "bullion", "oil", "crude", "brent", "wti",
                  "opec", "commodity", "commodities", "metal", "metals", "energy",
                  "gas", "gasoline"],
    "stock": ["stock", "stocks", "shares", "s&p", "nasdaq", "dow", "wall street",
              "equities", "equity", "ipo", "earnings", "ftse", "nikkei", "dax",
              "index", "indices", "stock market"],
}

# ---------------------------------------------------------------------------
# THÔNG BÁO TELEGRAM — để trống token/chat_id thì tính năng tự TẮT.
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
# Proxy khi mạng chặn Telegram, vd: http://host:port hoặc socks5://host:port
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "").strip() or None
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)
# Giờ gửi bản tin định kỳ (giờ Việt Nam), phân tách bằng dấu phẩy. Để trống = không gửi.
TELEGRAM_DIGEST_HOURS = os.getenv("TELEGRAM_DIGEST_HOURS", "8,20").strip()
# 1 = chỉ đẩy ngay tin ảnh hưởng Cao ĐÃ được ≥2 nguồn xác nhận; 0 = mọi tin ảnh hưởng Cao.
TELEGRAM_NEWS_REQUIRE_VERIFIED = os.getenv("TELEGRAM_NEWS_REQUIRE_VERIFIED", "0") == "1"
# Chỉ đẩy ngay tin đăng trong N giờ gần nhất (tin cũ hơn chỉ xuất hiện trong bản tin định kỳ).
TELEGRAM_NEWS_MAX_AGE_HOURS = float(os.getenv("TELEGRAM_NEWS_MAX_AGE_HOURS", "6"))

# Ngưỡng "BIẾN ĐỘNG MẠNH" theo % thay đổi trong phiên (crypto: 24h). Vượt ngưỡng -> báo;
# vượt gấp đôi, gấp ba... -> báo tiếp (mỗi bậc một lần/ngày).
MOVE_ALERT_PCT = {"stock": 1.5, "commodity": 2.5, "crypto": 5.0, "fx": 0.8}
# Ngưỡng "ĐỘT BIẾN" trong khoảng ~1 giờ (so với lịch sử thu thập của hệ thống).
SPIKE_ALERT_PCT = {"stock": 1.0, "commodity": 1.5, "crypto": 3.0, "fx": 0.5}
# Số tin tối đa trong một tin nhắn cảnh báo (chống spam).
TELEGRAM_MAX_NEWS_PER_ALERT = int(os.getenv("TELEGRAM_MAX_NEWS_PER_ALERT", "5"))
# Địa chỉ dashboard chèn vào tin nhắn (đổi thành IP/tên miền nếu xem từ điện thoại).
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:8000").rstrip("/")
