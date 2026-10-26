# Cobalt listing design note

PASS/FAIL: PASS về UI, SEO và pipeline; bộ ảnh thật chưa được cung cấp trong workspace nên chưa có ảnh được chọn.

## Ảnh

Thư mục chuẩn vẫn là `assets/images/nha-xanh-co-ban-cu-ebur/`. Khi có ảnh thật, `images[]` sẽ render ảnh đầu tiên làm hero/card thumbnail và các ảnh còn lại trong gallery. Hiện không dùng ảnh giả hoặc ảnh chụp màn hình không liên quan.

## UI chính

- Trang generated `/listings/nha-xanh-co-ban-cu-ebur/` có hero/gallery no-photo safe, highlight cards, thông số, phép tính từ giá chào, box pháp lý/giá và CTA mobile.
- Homepage card hiển thị thumbnail nếu có, giá, diện tích, khu vực, nhà mới, sổ riêng và badge dưới 2 tỷ.
- Responsive mobile-first với gallery, CTA sticky và màu cobalt/gỗ nhẹ.

## SEO

Trang chi tiết giữ canonical riêng, title/description, Open Graph và JSON-LD dựa trên dữ liệu thật; sitemap được generator cập nhật tự động.

## Deploy

Deployment: cập nhật sau publish.
URL listing: https://haisala.github.io/sala-bmt-listings/listings/nha-xanh-co-ban-cu-ebur/
URL homepage: https://haisala.github.io/sala-bmt-listings/
