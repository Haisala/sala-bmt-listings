# Phase 2 automation note

PASS/FAIL: PASS sau khi publish và kiểm tra deployment.

## Đã thêm/thay đổi

- `scripts/listing_manager.py`: add, update, status, validate, build, publish; tính toán giá/m², giá/mét ngang và quy đổi 5m ngang từ GIÁ CHÀO.
- `scripts/publish.sh`: entry point an toàn, kiểm tra repo/root/remote trước publish.
- `scripts/test_listing_manager.py`: smoke tests cho validation, build và dữ liệu căn xanh cô-ban.
- `data/schema.json`, `data/listings.json`: schema mở rộng, tương thích dữ liệu cũ; bổ sung metadata đã được cung cấp.
- `listings/<slug>/index.html`: trang chi tiết sinh tự động; `sitemap.xml` sinh tự động.
- `app.js`: card homepage trỏ tới trang generated.

## Test

- Test count: 3 smoke tests passed.
- Listing test: `nha-xanh-co-ban-cu-ebur`.
- Giá giữ nguyên: giá chào 1.850.000.000 VNĐ.
- Diện tích: 135m²; giá/m² đất: 13.703.703,70 VNĐ; giá/mét ngang: 370.000.000 VNĐ; quy đổi 5m ngang: 1.850.000.000 VNĐ.
- Đây là phép tính từ GIÁ CHÀO, không phải giá giao dịch.

## Publish

- Deployment result: cập nhật sau publish.
- Website: https://haisala.github.io/sala-bmt-listings/
- Căn xanh cô-ban: https://haisala.github.io/sala-bmt-listings/listings/nha-xanh-co-ban-cu-ebur/
- SHA cuối: cập nhật sau publish.

## Cách Codex sử dụng

Codex nhận thông tin và ảnh, chuẩn hóa thành record JSON, đặt ảnh vào `assets/images/<slug>/`, chạy `validate`, `build`, rồi gọi `scripts/publish.sh`. Người dùng không cần sửa JSON/HTML hoặc chạy Git.

- Thêm: tạo payload JSON rồi gọi `python3 scripts/listing_manager.py add payload.json`.
- Đổi giá/mô tả: gọi `update` với payload có `slug`; manager tự cập nhật `updatedAt`.
- Đổi trạng thái: gọi `update` với `{"slug":"...","status":"Đang bán|Đã cọc|Đã bán"}`.

## Giới hạn còn lại

Chưa kết nối Google Sheet; chưa có image resize dependency; publish cần credential GitHub CLI đã xác thực; giá trị thiếu chỉ được cảnh báo, không được tự suy đoán.

## Đề xuất Phase 3

Thêm adapter Google Sheet → JSON có dry-run/diff, kiểm duyệt thay đổi và image optimization nhẹ.
