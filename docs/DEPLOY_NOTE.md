# Deploy note

PASS/FAIL: PASS

- Local repo path: `/Users/trandinhhieu/sala-bmt-listings`
- Remote repo URL: https://github.com/Haisala/sala-bmt-listings
- Website URL: https://haisala.github.io/sala-bmt-listings/
- Commit hiện tại trên remote main: `82d445614740f497439dffcca2057ba85e8beeca`
- Deployment status: Success — GitHub Actions run #5

## Thêm listing

Thêm một record vào `data/listings.json`, theo `data/schema.json`. Không thêm dữ liệu chưa được xác minh; để `images` rỗng nếu chưa có ảnh thật.

## Đổi giá

Đổi `price` (số nguyên VNĐ) và `priceLabel` nếu cần. Giữ `priceNote` nêu rõ đây là giá chào, không phải giá giao dịch.

## Đổi trạng thái

Đặt `status` thành một trong: `Đang bán`, `Đã cọc`, `Đã bán`.

## Tự động hóa tiếp theo

Có thể thêm script đọc Google Sheet export/API và sinh `data/listings.json`, sau đó chạy schema validation trong GitHub Actions. Chưa lưu credential ở giai đoạn này.
