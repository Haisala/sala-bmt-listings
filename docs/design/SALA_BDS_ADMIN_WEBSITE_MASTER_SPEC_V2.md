# SALA - BĐS — MASTER SPEC V2: QUẢN LÝ WEBSITE + TRAFFIC + CRM + KINH DOANH

**Ngày:** 23/11/2026  
**Owner:** Hải Sala  
**Nguồn quản trị:** [`Haisala/sala-bmt-listings` — Issue #39](https://github.com/Haisala/sala-bmt-listings/issues/39), [PR #44](https://github.com/Haisala/sala-bmt-listings/pull/44) (DRAFT).  
**Trạng thái:** `PRODUCT_SCOPE_OWNER_LOCKED / UI_PROTOTYPE_READY / SOURCE_AUDIT_REQUIRED / P1_NOT_IMPLEMENTED`.  
**Artifact giao diện:** `SALA_BDS_ADMIN_WEBSITE_MASTER_DESIGN_V2.html` (HTML độc lập, responsive; mọi số liệu và hồ sơ là **DEMO**).  
**Quyền thực hiện:** Owner yêu cầu **thiết kế và audit trước khi làm**; chưa cho phép sửa mã triển khai hoặc chuyển Staging/Production trong tác vụ audit này.

## 1. Kết luận kiến trúc đã chốt

- `SALA Admin` là cổng quản trị chung; có một trang riêng **QUẢN LÝ WEBSITE**, cùng hệ thống xác thực/quyền Owner và Property Core. iPhone xem trang toàn màn hình; desktop có thanh menu riêng hoặc tab theo thao tác người dùng.
- **Traffic của Website** được xem trong Quản lý Website, nhưng dữ liệu sự kiện có cấu trúc để hợp nhất với **đo lường đa kênh** về sau. Không gộp tùy tiện các chỉ số khác đơn vị.
- **CRM Website** là góc nhìn khách từ Website, dùng chung **CRM trung tâm SALA BĐS** tương lai; không tạo database/hồ sơ khách hàng độc lập không thể hợp nhất.
- **Một BĐS chuẩn → nhiều kênh**, dùng `property_id`, revision và quyền ảnh; lượt xem/tìm kiếm phải được phân biệt với khách đã được xác minh và giao dịch.
- **AI phân tích/nhắc việc tự động** chỉ sử dụng dữ liệu có nguồn và được cấp quyền; không tự nhắn khách, không tự phát hành, không gọi API trả phí ở giai đoạn audit.
- **P0 trước P1:** P0 connected Admin HTTPS → Customer HTTPS E2E chưa được chứng minh theo [Result #7096718498](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7096718498), Owner chưa nghiệm thu iPhone. Audit P1 chỉ là kiểm tra nguồn/read-only, không được làm gián đoạn P0.

## 2. Sơ đồ menu và điều hướng

| Màn hình | Mục tiêu chính | Thiết kế V2 |
|---|---|---|
| 1. Tổng quan kinh doanh | Một màn hình cho Hải biết sản phẩm, khách, cảnh báo, cơ hội | 4 KPI + biểu đồ + top BĐS + việc cần làm |
| 2. Tin BĐS & xuất bản | Tình trạng tin, ảnh, quyền, duyệt/đăng/gỡ, xem từng BĐS | Tìm/lọc/xếp hạng + chi tiết theo tin + quy trình 2 bước |
| 3. Traffic & hành vi | Lượt xem, nguồn, CTA, yêu thích, hành vi ảnh | Biểu đồ + phân tách nguồn + giải thích chỉ số |
| 4. Nhu cầu tìm kiếm | Từ khóa, bộ lọc, số lần không có kết quả, thiếu nguồn hàng | Top truy vấn, phân khúc, bảng zero-results |
| 5. CRM & chăm sóc | Khách hợp lệ, phân công, lịch chăm sóc, lịch xem, giao dịch | Bảng tiến độ + lịch sử + cảnh báo chậm phản hồi |
| 6. Hiệu quả kinh doanh | Phễu xem → liên hệ → lịch xem → giao dịch, so sánh tin/kênh | Phễu minh bạch đơn vị + so sánh nguồn + chi phí nếu có |
| 7. Giám sát & cảnh báo | BĐS sai/hết hạn, media rights, lỗi Website, SEO, tồn việc | Trung tâm cảnh báo và trạng thái dựa trên evidence |
| 8. Tích hợp & dữ liệu | Phân biệt thành phần hiện có, cần bổ sung, làm sau | Bản đồ dữ liệu, ranh giới privacy và kết nối |

**iPhone:** thanh tác vụ nhanh cho Tổng quan / Sản phẩm / Traffic / CRM / Thêm, không dùng popup nhỏ để quản trị toàn màn hình. **Máy tính:** thanh điều hướng trái, bảng đầy đủ. Màu navy–trắng SALA - BĐS. Chữ `S` trong HTML chỉ là **placeholder**, không phải logo Brand Kit chuẩn; Codex không được dùng mẫu để thay thế logo Owner đã khóa.

## 3. Khung dữ liệu, định nghĩa sự kiện và chống suy diễn

### 3.1 Lượt xem sản phẩm và hành vi

- `property_detail_view`: **số sự kiện xem trang chi tiết**, có `property_id` + `publication_id`, theo ngày và tổng thời gian lựa chọn; **không gọi là số người**.
- `property_card_view`: lượt hiển thị/xem thẻ nếu phát sinh đúng sự kiện đã quy định; cần chống đo trùng do render/scroll.
- `page_view`: xem trang; nguồn, referrer, UTM chỉ theo dữ liệu đã được phép thu thập.
- `contact_cta_click`: lượt bấm call/Zalo/other; **không coi là liên hệ thành công, lead hợp lệ hay giao dịch**.
- `favorite_add`, `favorite_remove` và trạng thái **đã lưu** là **tính năng đề xuất mới**, cần thiết kế riêng với quyền riêng tư, idempotency, chống click lặp; nếu chỉ là lưu cục bộ thì báo rõ không có tổng hợp hoặc liên thiết bị.
- `gallery_interaction`: chỉ đo thao tác thực sự có ích (ví dụ mở thư viện/xem ảnh), giới hạn số sự kiện và tránh theo dõi quá mức.
- Báo cáo theo sản phẩm gồm: lượt xem, lượt lưu, lượt liên hệ, lead hợp lệ, lịch xem, giao dịch, thay đổi giá, thời điểm chỉnh sửa và trạng thái publication; có phân biệt **BĐS có nhiều revision/publication**.

### 3.2 Tìm kiếm và nhu cầu

- Hiện tại `customer_web_v2/frontend/app.js` có `search_filter(query_used=yes)` nếu gõ từ khóa, **chưa truyền raw search text lên analytics**. Điều này phải được giữ trong đánh giá hiện trạng.
- Đề xuất trước: báo cáo theo **khu vực / phân khúc giá / loại BĐS / giao dịch / search-result count (zero result)** được chuẩn hóa, không gắn định danh cá nhân.
- Lưu **nguyên văn từ khóa** chỉ là lựa chọn có điều kiện, yêu cầu review PII, nội dung nhạy cảm, retention, cơ sở thu thập, UI minh bạch và chính sách lọc/ẩn; không mặc định ghi từ khóa thô vào event store.
- Các bộ lọc hoặc nhu cầu phổ biến có thể cùng xuất hiện trong một truy vấn; tỷ trọng giữa các bộ lọc không nhất thiết cộng 100%.

### 3.3 CRM và lịch sử chăm sóc

- Chỉ tạo **lead (khách có yêu cầu/đầu mối liên hệ)** khi nhận thông tin hợp lệ từ form, kênh kết nối được xác thực hoặc nhập liệu được phép. Không tạo định danh khách từ hành vi xem tin ẩn danh.
- Một `customer_id` có thể gắn nhiều `property_id` và nhiều yêu cầu, nhưng phải có nguyên tắc chống trùng minh bạch (không ghép hai khách chỉ vì cùng mạng IP).
- Trạng thái: khách mới → đã phân công → đang chăm sóc → lịch xem → thương lượng / giao dịch → sau bán; có trách nhiệm, thời gian, quyền đọc/sửa, audit log và nhắc nội bộ.
- Hồ sơ có thể chứa PII phải được lưu riêng với thống kê ẩn danh, có giới hạn quyền nhân viên, retention/xóa và cơ sở xử lý thông tin phù hợp. Tính năng nhắn khách tự động cần phê duyệt riêng, không làm trong P1 cơ bản.

### 3.4 Hiệu quả và kiểm soát rủi ro

- KPI: view events ≠ unique visitors ≠ CTA clicks ≠ verified leads ≠ appointments ≠ transactions.
- `conversion_rate` phải công bố mẫu số cụ thể (ví dụ verified leads / CTA clicks), thời gian quan sát và số lượng hồ sơ được phép thống kê; không tạo tỷ lệ giả khi thiếu dữ liệu.
- Đối chiếu BĐS “xem nhiều, ít liên hệ”, sản phẩm được lưu nhiều, xu hướng theo khu vực, sản phẩm quá hạn, giá giảm, media rights và trạng thái đã bán.
- SEO/AI Search: chỉ hiển thị điều kiện đo được (metadata, index/noindex, lỗi đường dẫn); không tự khẳng định thứ hạng khi chưa có nguồn thích hợp.
- Chi phí quảng cáo, giá trị giao dịch và hoa hồng chỉ hiển thị nếu có nguồn chi phí/chứng từ đáng tin cậy; còn thiếu thì ghi **Chưa có dữ liệu**.
- Các cảnh báo tự động và AI gợi ý phải nêu dữ liệu nguồn, ngưỡng và người có quyền xử lý; không tự sửa giá, ảnh, pháp lý, trạng thái hoặc Content.

## 4. Kiểm tra mã đã xác định trước audit Codex

Các quan sát sau dựa trên source PR #44 HEAD tham chiếu `1d8348ad2b1d504a2d54fbb4aa3569b01bb2a34a`; **không thay thế audit hiện trạng mới**:

| Hạng mục | Bằng chứng đã thấy | Kết luận tạm |
|---|---|---|
| Event store | `property_memory/analytics_store.py` hỗ trợ `page_view`, `property_card_view`, `property_detail_view`, `search_filter`, `contact_cta_click`; `summary` theo ngày/property/event/channel/source | **Có nền tảng mã; chưa chứng minh metrics live** |
| Frontend telemetry | `customer_web_v2/frontend/app.js` phát `page_view`, `property_detail_view` và filter event; CTA trong source đang disabled nếu thiếu Owner config | **Một phần** |
| Từ khóa gõ | Event contract ghi `query_used=yes` thay raw text | **Chưa thu thập từ khóa cụ thể** |
| Yêu thích | Chưa có evidence module/contract hoàn chỉnh được xác minh ở phạm vi review | **Cần audit** |
| CRM chăm sóc trung tâm | Chưa có evidence end-to-end trong First Web được xác minh ở phạm vi review | **Cần audit** |
| Social adapter | 7 kênh draft-only trong RESULT Issue #39 | **Không tương đương đã kết nối** |
| Xuất bản Web | Facts-only Web publish/confirm/revoke đã kiểm thử cô lập | **P0 connected HTTPS và iPhone chưa nghiệm thu** |

## 5. Lộ trình triển khai điều kiện sau audit

**P0 — đang hoạt động, ưu tiên duy nhất:** Hoàn tất connected Admin-authenticated HTTPS → Customer HTTPS isolated E2E; bằng chứng exact SHA, quyền, cleanup/no leak, publish/revoke, Safari Owner. Không cắt ngang bằng P1.

**P1-A — giao diện vận hành Web sau P0 và duyệt UI:** Route riêng trong cùng Admin; danh sách tin, duyệt/xác nhận đăng/gỡ, trạng thái thực, chi tiết BĐS, traffic nền tảng đã có, thông báo lỗi, preview Website; không thiết kế lại Brand Kit.

**P1-B — mở rộng khi audit xác định nguồn:** Thống kê theo BĐS, nhu cầu lọc/zero-result được tối thiểu hóa, favorites nếu được phê duyệt, bước tiếp nhận lead hợp lệ. Không xây CRM/analytics độc lập trước khi map dữ liệu trung tâm.

**Các pha tiếp theo, GO riêng:** CRM đa nhân viên đầy đủ, đa kênh, SEO/AI Search nâng cao, tích hợp social thật, marketing automation và AI tự gửi; chỉ khi có hợp đồng dữ liệu, quyền/chi phí và Owner cho phép.

> Owner đã chốt **phạm vi sản phẩm dài hạn**, không đồng nghĩa cấp GO viết toàn bộ tính năng ngay. Audit phải tách `EXISTS / PARTIAL / MISSING / NOT_VERIFIED`, ưu tiên tận dụng mô-đun có sẵn và tránh 2 hệ thống dữ liệu chuẩn.

## 6. Yêu cầu audit Codex trước khi thực thi

1. **Chỉ đọc:** xác nhận HEAD/branch/PR state, các thay đổi chưa commit, P0 gate và môi trường; không commit, merge, deploy, chỉnh DB hoặc gọi AI.
2. **Inventory có bằng chứng:** routes và Admin UI hiện hữu; Property Core/Review/Gate A/Web approval/Gate B; media rights; Customer read routes; event store/relay/report; CTA; CRM/lead/contact; SEO/settings; channel adapters; roles/auth; background jobs nếu có.
3. **Mapping** cho 8 màn hình: `UI block → current route/source → source-of-truth → owner/security → exists/partial/missing → missing contract → tests needed → stage`.
4. **Phân loại rõ loại dữ liệu:** anonymous event / account or customer PII / property canonical / publication projection / marketing channel metric / CRM case; xác định nơi tồn tại và nguyên tắc retention, idempotency, dedup, quyền.
5. **Báo cáo lỗ hổng:** có thể đo số lượt xem theo property/publication chưa; có lượt lưu/yêu thích chưa; có raw searches chưa (không tự mở thu thập); CTA enabled và lead valid chưa; CRM central source/permissions chưa; các chỉ số live có receipts chưa.
6. **Khóa P0:** không thực hiện P1 trước `P0_CUSTOMER_STAGING_BUSINESS_E2E=PASS` và Owner Safari acceptance. Không can thiệp lệnh P0 đang chạy.
7. **Trả kết quả tại Issue #39**: audit có bảng bằng chứng và 1 đề xuất ưu tiên theo phụ thuộc; **STOP**. Chỉ Owner mới quyết định GO code, scope, ngân sách, Staging/Production.

## 7. Nghiệm thu thiết kế

- Không có số liệu demo nào được hiểu là kết quả của SALA BĐS thật.
- Mobile iPhone 15 Pro Max không có tràn màn hình ngang; điều hướng nhanh bằng một tay; các bảng có cuộn ngang riêng nếu cần.
- Desktop có bảng và biểu đồ lớn; phần quản lý tin luôn có thông tin trạng thái/revision và đường quay lại Admin.
- Bản HTML là **prototype**, không phải phần mềm sản xuất, không có back-end và không thay Visual Master đã duyệt.
- Owner chốt **các chức năng trong spec**; màn hình cụ thể sau khi audit có thể điều chỉnh theo contracts hiện có nhưng không tự thay quyết định sản phẩm Owner.