# Deploy note

PASS/FAIL: PASS khi workflow GitHub Pages hoàn tất.

- Local repo path: `/Users/trandinhhieu/sala-bmt-listings`
- Remote repo URL: https://github.com/Haisala/sala-bmt-listings
- Website URL: https://haisala.github.io/sala-bmt-listings/
- Commit hiện tại: cập nhật sau khi push
- Deployment status: theo dõi tại GitHub Actions

## Thêm listing

Thêm một record vào `data/listings.json`, theo `data/schema.json`. Không thêm dữ liệu chưa được xác minh; để `images` rỗng nếu chưa có ảnh thật.

## Đổi giá

Đổi `price` (số nguyên VNĐ) và `priceLabel` nếu cần. Giữ `priceNote` nêu rõ đây là giá chào, không phải giá giao dịch.

## Đổi trạng thái

Đặt `status` thành một trong: `Đang bán`, `Đã cọc`, `Đã bán`.

## Tự động hóa tiếp theo

Có thể thêm script đọc Google Sheet export/API và sinh `data/listings.json`, sau đó chạy schema validation trong GitHub Actions. Chưa lưu credential ở giai đoạn này.
