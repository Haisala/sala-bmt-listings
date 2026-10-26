# Cobalt listing design note

PASS/FAIL: PASS — đã tích hợp bộ ảnh thật từ manifest và giữ nguyên dữ liệu giá/pháp lý của listing.

## Ảnh

Hero + thumbnail: `01-san-truoc.webp` (sân trước / chỗ đậu xe bán tải).

Gallery theo manifest: `02-phong-khach.webp`, `03-phong-khach-toan-canh.webp`, `04-bep-an.webp`, `05-bep-an-goc-rong.webp`, `06-hanh-lang.webp`, `07-phong-ngu.webp`, `08-san-truoc-goc-rong.webp`, `09-san-truoc-goc-ben.webp`, `10-bep-an-phu.webp`.

Tất cả ảnh nằm trong `assets/images/nha-xanh-co-ban-cu-ebur/`, không dùng ảnh giả.

## UI chính

- Trang generated `/listings/nha-xanh-co-ban-cu-ebur/` có hero/gallery no-photo safe, highlight cards, thông số, phép tính từ giá chào, box pháp lý/giá và CTA mobile.
- Homepage card hiển thị thumbnail nếu có, giá, diện tích, khu vực, nhà mới, sổ riêng và badge dưới 2 tỷ.
- Responsive mobile-first với gallery, CTA sticky và màu cobalt/gỗ nhẹ.

## SEO

Trang chi tiết giữ canonical riêng, title/description, Open Graph và JSON-LD dựa trên dữ liệu thật; sitemap được generator cập nhật tự động.

## Deploy

Deployment: cập nhật sau publish, kiểm tra homepage/listing/sitemap HTTP 200.
URL listing: https://haisala.github.io/sala-bmt-listings/listings/nha-xanh-co-ban-cu-ebur/
URL homepage: https://haisala.github.io/sala-bmt-listings/
