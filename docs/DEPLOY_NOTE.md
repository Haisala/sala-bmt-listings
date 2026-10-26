# Deploy note

PASS/FAIL: PASS khi GitHub Pages workflow hoàn tất.

- local repo path: /Users/trandinhhieu/sala-bmt-listings
- remote repo URL: https://github.com/Haisala/sala-bmt-listings
- website URL: https://haisala.github.io/sala-bmt-listings/
- commit hiện tại: theo GitHub main
- deployment status: theo dõi tại Actions

## Cách thêm listing
Thêm record đã xác minh vào data/listings.json theo data/schema.json. Chưa có ảnh thì giữ images là mảng rỗng.

## Đổi giá
Đổi price và priceLabel; luôn giữ ghi chú đây là giá chào, không phải giá giao dịch.

## Đổi trạng thái
Dùng một trong: Đang bán, Đã cọc, Đã bán.

## Tự động hóa tiếp theo
Thêm script Google Sheet export/API → data/listings.json và chạy validation trong Actions; chưa dùng credential.
