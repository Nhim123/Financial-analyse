# Handoff: Màn 2A — Trung tâm Tin tức & Cảnh báo (Market Dashboard)

## Overview
Màn hình dashboard desktop cho **nhà phân tích / quản lý quỹ** theo dõi biến động thị trường tài chính Việt Nam & quốc tế (cổ phiếu, chỉ số, crypto, hàng hóa, ngoại hối) theo thời gian thực. Bàn giao này tập trung vào **màn 2A** — bản đã chốt, tổ chức thành **4 tab**. Dữ liệu trong prototype là **mô phỏng** (random walk) cập nhật định kỳ; khi triển khai thật cần thay bằng websocket/API thực.

## About the Design Files
File `Dashboard Thi Truong.dc.html` là **tài liệu thiết kế tham khảo bằng HTML** — prototype thể hiện diện mạo & hành vi mong muốn, KHÔNG phải mã production để sao chép trực tiếp. Nhiệm vụ: **tái hiện thiết kế này trong codebase đích** theo pattern/thư viện sẵn có. Nếu chưa có codebase, khuyến nghị **React + TypeScript** (dashboard dữ liệu dày). File là "Design Component" (`.dc.html`) dùng runtime template riêng — bạn KHÔNG cần runtime đó; đọc như đặc tả cấu trúc + style và dựng lại bằng component thật.

Trong file có cả các phương án tham khảo khác (1A/1B/1C, section `#turn1`). **Chỉ cần dựng màn 2A** (section `#turn2` › article `#2a`); các phương án kia chỉ để tham khảo bố cục.

## Fidelity
**High-fidelity.** Màu sắc, typography, khoảng cách, bo góc, tương tác là giá trị cuối — tái hiện pixel-perfect. Giá trị cụ thể ở mục Design Tokens.

## Khung tổng thể
- Vùng thiết kế cố định **1440 × 920 px**, bo góc ngoài 22px, nền `#f4f6f2`, viền `1px #e0e5dd`, bóng `0 30px 70px -30px rgba(15,27,23,.4)`.
- Font: **IBM Plex Sans** (text/nhãn), **IBM Plex Mono** (mọi con số: giá, %, giờ, khối lượng). Số luôn dùng mono.
- **Quy ước màu thị trường (chuẩn VN, không đảo):** tăng = xanh lá `#12a150`, giảm = đỏ `#e5484d`, đứng giá = vàng `#c9a91a`. Mũi tên ▲ tăng / ▼ giảm.
- **Màu nhấn thương hiệu (accent) = đỏ SSI `#d81e2c`** — CHỈ dùng cho nhãn/branding (tab active, nav, badge "MỚI", chấm lịch hôm nay, chip nhãn danh mục "Cổ phiếu", logo). KHÔNG dùng accent cho chỉ báo tăng/giảm giá.

## Cấu trúc màn 2A
Bố cục dọc (flex-column) trong frame:
1. **Toolbar** (cao 56px, nền trắng, viền dưới `#e6eae4`, padding ngang 26px): logo (ô vuông 32px bo 10px nền accent chữ "Ʌ" trắng + chữ "Thị trường"); **tab bar**; spacer; ô tìm kiếm giả (icon ⌕ + "Tìm mã, chỉ số…"); đồng hồ mono.
2. **Ticker chạy ngang** (cao 38px, nền `#0f1b17`): marquee cuộn vô tận (~38s/vòng, nhân đôi nội dung để loop liền). Mỗi mục: nhãn `#b9c6bf` + giá trị trắng mono + %thay đổi mono (xanh/đỏ), phân cách viền phải `#24332c`.
3. **Vùng nội dung theo tab** (đổi theo tab đang chọn).

### Tab bar — 4 tab
Thứ tự: **Tổng quan · Cổ phiếu Việt Nam · Giá vàng, dầu, crypto · FX**. Tab active nền accent (đỏ SSI) chữ trắng; tab thường chữ `#4a5a53` nền `#f0f3ee`; padding 8×14, bo 9px. Bấm tab đổi nội dung vùng chính (state `activeTab` 0–3).

### TAB 0 — Tổng quan
- **Header:** tiêu đề "Trung tâm tin tức & cảnh báo" (700/22) + dòng ngày tiếng Việt đầy đủ (`#6b7770`/13). Bên phải: chip "Đang giao dịch" (nền `#e7f6ec`, chấm xanh) + chip "Chế độ cảnh báo · BẬT" (nền `#fdeaef`, chữ `#c0303a`, **chấm đỏ nhấp nháy** — pulse 1.4s).
- **Hàng 6 thẻ chỉ số** (grid 6 cột, gap 12): mỗi thẻ nền trắng viền `#e6eae4` bo 14px; tên + %thay đổi cùng hàng, giá trị lớn mono 18px bên dưới. Gồm VN-Index, VN30, HNX-Index, S&P 500, BTC/USD, Vàng SJC.
- **Vùng chính** (flex-column, gap 14):
  - **Grid 3 cột** `1fr 268px 420px`, gap 16:
    - **Cột trái — Dòng tin thị trường** (nền trắng bo 18px): header "Dòng tin thị trường" + badge "TRỰC TIẾP" (chấm xanh nhấp nháy). Danh sách tin, mỗi tin là card 2 cột [thời gian | nội dung], viền trái 3px accent + nền nhấn nhẹ `rgba(accent,.06)` nếu tin mới (age<3); cụm trái có giờ mono + badge "MỚI" (nền accent); nội dung: [chip danh mục + nguồn] → tiêu đề (600/14) → **tóm tắt ngắn 1–2 dòng** (400/12 `#6b7770`).
    - **Cột giữa — Top movers** (2 card dọc): **Tăng mạnh nhất** (xanh) và **Giảm mạnh nhất** (đỏ); mỗi dòng: mã mono + tên (ellipsis) | sparkline 40×16 | giá mono | % mono.
    - **Cột phải:** **Cảnh báo quan trọng** (flex 1.5) — header icon ⚠ (đỏ) + tiêu đề + badge đếm số (nền `#fdeaef` chữ `#c0303a`); mỗi cảnh báo: ô icon 34px (nền theo mức độ) + [chip mức độ KHẨN/CHÚ Ý/TIN + giờ] → tiêu đề → chi tiết. Bên dưới là **Heatmap ngành** (flex 1, grid 4 cột, nền ô xanh/đỏ alpha theo độ lớn %).
  - **Lịch sự kiện dự kiến trong tuần** (grid 5 cột T2–T6): header có icon lịch (SVG stroke accent) + chú giải 3 mức tác động (Cao `#e5484d` / Trung bình `#b45309` / Thấp `#7d8f86`); mỗi cột có ô header ngày (nền accent + chữ trắng nếu là hôm nay) + các event card (chấm mức độ + giờ + tiêu đề).

### TAB 1 — Cổ phiếu Việt Nam
- **Header:** "Cổ phiếu Việt Nam" + "Sàn HOSE · <ngày>"; bên phải chip độ rộng (số tăng↑ / đứng— / giảm↓) + chip "Đang giao dịch".
- **Grid `1fr 372px`:**
  - **Trái — Bảng giá cổ phiếu HOSE:** cột [Mã | Tên | sparkline | Giá | +/- | KL], các dòng chia đều chiều cao.
  - **Phải (cột card dọc, gap 14):**
    1. **Độ rộng thị trường** — thanh 3 màu (tăng xanh / đứng vàng / giảm đỏ) + số đếm.
    2. **Thanh khoản phiên** *(ô này)* — tiêu đề + %so-với-TB20 (màu xanh/đỏ); tổng giá trị khớp (mono 22px) + dòng phụ "<khối lượng> · TB20: <giá trị>"; **mini bar-chart** (16 cột, màu accent opacity .55); hàng dưới chia 3 ô **HOSE / HNX / UPCOM** kèm % tỉ trọng + giá trị.
    3. **Dòng tiền chủ động** — thanh mua/bán + khối ngoại ròng.
    4. **Heatmap ngành** (grid 3 cột, flex-1).
- **Dải tin cuối:** "Tin cổ phiếu Việt Nam" — 4 thẻ tin (chip danh mục, nguồn, giờ, tiêu đề, tóm tắt).

### TAB 2 — Giá vàng, dầu, crypto
- **Header:** "Giá vàng, dầu & tiền mã hóa" + chip "Cập nhật 24/7".
- **Grid `1.3fr 1fr`:**
  - **Trái:** **thẻ hero Bitcoin** tối màu (nền `#0f1b17`, giá lớn mono 34px, area chart, vốn hóa + KL24h) + **bảng tiền mã hóa** (Đồng | sparkline | Giá | 24h).
  - **Phải — Vàng, dầu & hàng hóa:** grid 2 cột các thẻ (tên + %; đơn vị; giá mono 17px + sparkline). Gồm vàng SJC, vàng thế giới, dầu WTI/Brent, bạc, đồng, cà phê…
- **Dải tin cuối:** "Tin vàng, dầu & crypto" — 4 thẻ tin.

### TAB 3 — FX (ngoại hối)
- **Header:** "Thị trường ngoại hối" + chip "Liên ngân hàng".
- **Grid 4 cột** các thẻ cặp tiền: cặp (mono 15px) + tên + %thay đổi; tỷ giá lớn mono 21px + sparkline; hàng dưới "Mua vào" (xanh) / "Bán ra" (đỏ). Gồm USD/VND, EUR/USD, USD/JPY, GBP/USD, USD/CNY, EUR/VND, USD/CHF…
- **Dải tin cuối:** "Tin ngoại hối" — 4 thẻ tin.

## Interactions & Behavior
- **Mô phỏng real-time:** một `setInterval` (mặc định 1500ms) mỗi nhịp điều chỉnh %thay đổi từng chỉ số/cổ phiếu/ngành/crypto/FX bằng bước ngẫu nhiên nhỏ (kẹp biên), cập nhật giá = base × (1+%/100), đẩy điểm mới vào lịch sử sparkline (cửa sổ trượt). Đồng hồ tiến theo tốc độ mô phỏng.
- **Chuyển tab:** click tab → `setTab(i)` đổi `activeTab`; mỗi tab render qua cờ `showOverview/showStocks/showCommodity/showFx`.
- **Dòng tin tự đẩy (tab Tổng quan):** cứ mỗi 3 nhịp, 1 tin mới từ pool chèn lên đầu feed (age=0 → badge "MỚI" + viền trái accent); feed giữ tối đa 8, tin cũ tăng age, mất "mới" khi age ≥ 3.
- **Tin theo nhóm (tab 1/2/3):** danh sách tĩnh riêng cho từng nhóm (`catNews.stocks/commodity/fx`).
- **Cảnh báo tự sinh** từ dữ liệu mỗi nhịp: cổ phiếu |%|≥4.5 → KHẨN, ≥3.2 → CHÚ Ý; khối ngoại ròng |x|>2000 tỷ → KHẨN else >500 tỷ → TIN; ngành |%|≥2.2 → CHÚ Ý. Sắp theo mức độ, hiển thị tối đa 6, badge đếm tổng.
- **Ticker** cuộn CSS vô tận (translateX 0→-50%, nội dung nhân đôi). **Chấm live/cảnh báo** nhấp nháy (pulse 1.4s).
- **Tabs đã hoạt động; nav/khung thời gian/tìm kiếm hiện tĩnh** — cần nối logic thật khi triển khai.

## State Management
- `raw`: dữ liệu gốc — `idx` (chỉ số), `stocks`, `sectors`, `ticker`, `news`, `commod`, `crypto`, `fx`, `chartHist`. Mỗi phần tử giữ `base`, `value`, `chg`, `hist[]`.
- `feed`: tin đang hiển thị ở tab Tổng quan (kèm `age`, `time`).
- `activeTab`: 0–3.
- `now`: đồng hồ mô phỏng.
- Dẫn xuất trong render (không lưu state): `cards, ticker, chart, sectors, gainers, losers, movers, flow, liquidity, feed, alerts, alertCount, week, commod, crypto, fx, cryHero, stockNews, commodNews, fxNews, breadthBar…`.
- Bộ đếm nội bộ: `tickCount, newsId, poolIdx`.
- **Data thật:** thay lớp mô phỏng bằng websocket/polling, giữ nguyên cấu trúc view-model dẫn xuất để component không phải đổi.

## Design Tokens

**Nền / bề mặt**
- Nền ngoài frame: `#d3dacf` + chấm lưới `rgba(15,27,23,.06)`
- Nền frame: `#f4f6f2`; nền phụ nhạt: `#eef1ec`, `#f0f3ee`, `#f8faf7`
- Card trắng `#ffffff`; viền card `#e6eae4`; phân cách nhạt `#f4f6f2`, `#eef1ec`
- Tối (ticker/tile hero): `#0f1b17`; viền tối `#24332c`

**Chữ:** chính `#0f1b17` / `#1c2a24` / `#243029`; phụ `#6b7770`; mờ `#93a099`; trên nền tối `#b9c6bf`, `#8fa89d`.

**Ngữ nghĩa thị trường (không đảo):**
- Tăng `#12a150` (sáng trên nền tối `#5ee39a`)
- Giảm `#e5484d` (sáng trên nền tối `#ff8a8f`)
- Đứng giá / mức TB `#c9a91a` / `#b45309`
- Chip xanh (open/live) nền `#e7f6ec` chữ `#0e7a3c`; chip đỏ (cảnh báo) nền `#fdeaef` chữ `#c0303a`

**Accent thương hiệu (branding, tweakable):** mặc định **đỏ SSI `#d81e2c`**; lựa chọn `#d81e2c`, `#b3121f`, `#4338ca`, `#0e7490`. Dùng cho tab active, nav, badge MỚI, chấm lịch hôm nay, chip "Cổ phiếu", link, mini-bar thanh khoản.

**tagMap (chip danh mục tin)** — [chữ, nền]:
- Vĩ mô `#7c3aed` / `#f1ebfd` · Cổ phiếu accent / `#e6f2f0` · Toàn cầu `#0e7490` / `#e2f4f7` · Crypto `#b45309` / `#fbf0df` · Hàng hóa `#be123c` / `#fdeaef`

**Typography:** IBM Plex Sans (text) + IBM Plex Mono (số); weight 400/500/600/700. Cỡ: 8–10 (nhãn/chip), 11–13 (nội dung/bảng), 14–16 (tiêu đề card), 18–22 (giá lớn), 34 (giá hero BTC). Số căn cột dùng mono.

**Bo góc:** 5 (chip nhỏ) · 9–12 (chip/ô/event) · 13–16 (card nhỏ) · 18 (card chính) · 20–22 (tile hero/frame).
**Bóng frame:** `0 30px 70px -30px rgba(15,27,23,.4)`.
**Spacing:** gap chính 12–16; padding card 14–20; padding vùng 28.

## Tweaks (props có thể chỉnh)
- `live` (boolean, mặc định true) — bật/tắt cập nhật mô phỏng.
- `speed` (600–3000ms, mặc định 1500) — tốc độ cập nhật.
- `accent` (color, mặc định `#d81e2c`) — màu nhấn thương hiệu.

## Assets
- **Fonts:** IBM Plex Sans + IBM Plex Mono (Google Fonts, weight 400/500/600/700) — nên self-host hoặc dùng gói sẵn của dự án.
- **Icons:** phần lớn là ký tự Unicode (▲ ▼ ⚠ ⚡ ◉ ⌕ ↑ ↓ Ʌ) + 1 inline SVG (icon lịch). Khi triển khai nên thay bằng bộ icon của dự án (Lucide/Heroicons) cho nhất quán.
- **Hình ảnh:** không có bitmap; mọi sparkline/chart là SVG polyline/path sinh từ dữ liệu.
- Không dùng tài sản thương hiệu Anthropic.

## Files
- `Dashboard Thi Truong.dc.html` — prototype đầy đủ. **Màn cần dựng: 2A** = section `#turn2` › article `#2a`. Logic mô phỏng + view-model ở lớp `Component` cuối file (`seed()`, `tick()`, `renderVals()`).
