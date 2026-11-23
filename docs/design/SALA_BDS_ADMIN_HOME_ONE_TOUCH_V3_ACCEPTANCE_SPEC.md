# SALA – BĐS / ADMIN HOME ONE-TOUCH V3 — OWNER DESIGN & ENGINEERING CONTRACT

**Owner quyết định:** 23/11/2026 — “Sửa đúng luồng; làm mọi thứ thông; đúng giao diện Hà Lan đã thiết kế; giao Codex chi tiết, đừng đi sai đường”.

**Scope:** \`Haisala/sala-bmt-listings\` — Issue #39 / Draft PR #44 — \`https://admin-next.haisala.io.vn\` + \`https://web-staging.haisala.io.vn\` **STAGING ONLY**. Chỉ phục vụ SALA BĐS; không can thiệp SALA Media, NaviBDS, legacy protected listeners, DNS/apex/Production, main/merge. Dùng **một SALA Admin/login, một Property Core canonical**, một Admin Website subroute \`/website\`. Không tạo hệ thống quản trị thứ hai.

## 0. Quy tắc ưu tiên / bằng chứng

1. Quyết định D15 one-touch đã khóa trong \`docs/governance/SALA_BDS_DECISION_LOG.md\` và bản GO mở rộng [#7100265737](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7100265737); RESULT gần nhất [#7101480651](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7101480651). Phiên bản triển khai gần nhất được báo cáo Admin \`b7e78305ee2f9b4ff9a5cb9cf8da1dcf8daa64c2\`, Customer \`c47509d07bc1c77e1157d89f339a4d11a3f81f45\`; **Codex phải xác minh runtime hiện tại** trước thay đổi.
2. Tài liệu Website Master V2 đã có trong PR: \`docs/design/SALA_BDS_ADMIN_WEBSITE_MASTER_DESIGN_V2.html\`, \`...MASTER_SPEC_V2.md\`. Master V2 *vẫn là nguồn hợp đồng chức năng* (8 khu vực); ba ảnh UI Home/thiếu trường/Quản lý Website mới nhất từ phiên thiết kế 23/11 là *nguồn giao diện V3*. File \`SALA_BDS_ADMIN_HOME_ONE_TOUCH_V3_REFERENCE.html\` trong cùng thư mục biểu diễn cấu trúc và style của 3 ảnh để Codex có thể đọc được. **Đây là prototype thiết kế với số liệu DEMO; không tái sử dụng số trong Staging.**
3. Khi thiết kế mâu thuẫn với ranh giới nghiệp vụ/hệ thống bảo mật, **giữ kiểm soát nghiệp vụ**, nêu khoảng lệch cụ thể và đề xuất UI tương đương; không tự bỏ gate, không tự nói “pixel-perfect”.

## 1. ĐÍCH SẢN PHẨM — KHÔNG ĐỔI

Owner trên **Safari iPhone và desktop**: nhập note tự do + ảnh/tệp + nguồn/quyền → một nút **TẠO CONTENT** → kiểm tra dữ liệu và quyền trong nền → nếu thiếu: **popup/bottom sheet** chỉ các trường còn thiếu → bổ sung vào **đúng intake hiện tại**, không upload lại/không mất ảnh → tiếp tục **một AI generation request cho đúng 5 bản riêng tư** → xem/sửa/chọn/lưu → xác nhận facts & Gate A bằng hành động Owner có chủ ý trong context (không menu Gate A ở Home) → Gate B / **DUYỆT CHO WEBSITE** → canonical preview đầy đủ → **XÁC NHẬN ĐĂNG** → Customer Feed/Search/Detail/Media thực, sửa revision/re-review/revoke, Reload/restart không mất trạng thái. Không auto-public. Website Staging có địa chỉ truy cập công khai: Owner phải nhận biết trước xuất bản dữ liệu thật.

**Chỉ một nút bắt đầu xử lý, không phải bỏ các quyết định cuối**. Không thêm “XỬ LÝ NGUỒN”, “CHẠY AI9”, “GATE A”, “TẠO CANDIDATE” vào Home như tác vụ bắt buộc. Với Gate A: xử lý kiểm tra nội bộ/chuẩn bị review được ẩn; **phê duyệt vào Core vẫn cần thao tác Owner**, có thể lồng trong bước duyệt dữ kiện/contextual confirmation, lưu audit trace. Không được auto-approve.

## 2. ĐÚNG LUỒNG — LOẠI BỎ BUG HIỆN CÓ

**Bug đã xác minh trong \`admin/admin_server.py\` của \`b7e7830\`:** Form \`#intake\` có \`TẠO CONTENT\` (lưu intake rồi tạo Content); \`Approval Queue\` cũng render \`TẠO CONTENT\` với \`onclick=createContent(x.id, action)\`. Hàm \`createContent(id,button)\` lại gọi \`new FormData(intakeForm)\` để lấy \`evidence_source_id / evidence_claim / evidence_rights\` từ form **đang mở**, bất kể intake ID được chọn. Đây là rủi ro *cross-intake evidence mixing*. **Ưu tiên chữa tận gốc**: \`createContent(intakeId)\` lấy evidence/revision từ chính server-side intake tương ứng hoặc popup owner xác nhận đúng intake; không truyền evidence của nguồn B vào nguồn A. UI mới đổi nút trong Queue thành **TIẾP TỤC XỬ LÝ**, mở view hồ sơ đã lưu, ảnh, tiến độ, bằng chứng/rights tương ứng và một CTA đúng state. Không tạo intake thứ hai khi retry. Idempotency per intake + request, giữ key outcome-unknown và chống double-click/multi-tab. “Lưu nháp” khác “Tạo Content”.

**State contract** (tên UI, không phải thay state machine backend): 
- **Nguồn mới / Đã lưu nháp / Chưa đủ dữ kiện / Chờ tạo Content / Có 5 Content / Chờ Owner xác nhận facts / Chờ duyệt Website / Chờ xác nhận đăng / Đang hiển thị / Đã thu hồi / Lỗi cần xử lý.**
- Mỗi hồ sơ chỉ có **một hành động tiếp theo khả dụng**; action disabled khi blocked kèm lý do, không tự retry API.
- Home \`Việc cần làm ngay\` ưu tiên hồ sơ cần bổ sung, Content cần duyệt, tin chờ đăng. Không hiện các thao tác kỹ thuật như nút Gate A.

## 3. VALIDATION BẮT BUỘC VÀ POPUP

Tham chiếu duy nhất bộ quy tắc \`property_memory/review_center.py::_validate...\` của PR đang chạy, thay đổi chỉ khi có Owner duyệt. Bộ nền cho SALE thường là:
1. \`title\` (tên BĐS),
2. \`address\` (vị trí/địa chỉ),
3. \`area\` (diện tích),
4. \`property_type\` (loại BĐS),
5. \`transaction\` (bán/thuê),
6. \`price_vnd\` nếu bán hoặc \`price_vnd_per_month\` nếu thuê.

Bổ sung theo **loại hình/giao dịch**: HOUSE yêu cầu \`house_type\`, \`bedrooms\`, \`bathrooms\` theo mã hiện tại; RENT phải kiểm \`price_cycle\`/MONTH phù hợp. Giá >0 và có đơn vị chuẩn, vùng giá từ text tự do phải được xác nhận trước ghi Core. Mỗi trường “chưa rõ” khác “đã xác minh”. **Legal_status rỗng/UNKNOWN không là blocker mặc định**, theo quyết định Owner [#7046847898](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7046847898); mâu thuẫn pháp lý công bố thì phải chặn/Owner xử lý. \`citations\` và \`rights\` phải xác minh trước AI/public theo hợp đồng an toàn; **không giả định mọi ảnh đều có quyền**.

Popup **chỉ xuất hiện khi xác định thiếu/mâu thuẫn**, trên iPhone là bottom sheet/full-page modal dễ đọc:
- Header “**Bổ sung thông tin còn thiếu**”, số trường thiếu *tính thật theo intake và loại BĐS*, tên hồ sơ, ảnh thumbnail nếu có.
- Chỉ render **các trường thiếu**, cùng nhãn thân thiện (ví dụ \`Diện tích\`, \`Giá bán\`); trường đã có tự liệt kê tóm tắt, **không hỏi lại**. Nếu mâu thuẫn thì hiển thị hai nguồn + yêu cầu Owner chọn/xác minh, không tự đoán.
- \`Lưu nháp\` lưu draft và quyền mà Owner đã xác nhận; \`BỔ SUNG & TIẾP TỤC TẠO CONTENT\` chỉ enabled khi server revalidate PASS; lỗi inline và focus vào lỗi; **không chạy OpenAI nếu thiếu**.
- Người dùng đóng popup, reload hoặc đổi tab vẫn giữ đúng hồ sơ đang xử lý và ảnh/tệp đã lưu, không đòi nhập lại từ đầu. Phải chứng minh persistence cả tình huống API fail/timeout/connection drop.
- Khi đủ facts/rights, chỉ 1 explicit generation request; không gọi AI riêng để điền missing facts, không hidden retries.

**Không hard-code ví dụ của ảnh mock:** Biệt thự EcoCity thật đã ghi 250m² và 16,5 tỷ trong note của Owner; hình mock "thiếu diện tích và giá" là ví dụ minh họa UI, **không được tự gán thiếu/ghi sai dữ liệu thật**. Nếu parser chưa trích được facts từ note, hiển thị “cần Owner xác nhận diện tích/giá đã nhập” thay vì xóa/khẳng định thiếu thông tin; giữ note nguyên vẹn.

## 4. BA MÀN HÌNH OWNER ĐÃ DUYỆT — KHÔNG REDESIGN

**Tổng thể:** SALA – BĐS navy / white / very light blue; logo **SALA - BĐS custom S Master 01E** nếu nguồn Brand Kit thật có sẵn (không dùng logo tòa nhà của ảnh minh họa làm logo chính thức), bo thẻ ~12–20 px, viền rất nhẹ, shadow tinh tế, font hệ thống dễ đọc. iPhone 15 Pro Max portrait **full-page, không popup nhỏ**, bottom navigation sticky, safe-area; desktop responsive chuyển layout, vẫn cùng chức năng. Header STAGING rõ ràng.

**Screen A — HOME điều phối:** 
- Header brand \`SALA BĐS Admin\`, \`Home điều phối\`, badge \`STAGING · KHÔNG PHẢI PRODUCTION\`.
- Trạng thái nhỏ \`AI Content\` \`Website\` \`Queue\` phản ánh trạng thái thật; khi AI chưa lấy được key phải hiển thị **Chưa sẵn sàng** chứ không “Đang chờ” lành mạnh.
- \`Việc cần làm ngay\` là 4 thẻ 2×2: Nguồn mới / Content chờ duyệt / Tin chờ đăng / Tin đang hiển thị, số liệu thật.
- \`Hành động nhanh\` 4 nút: Nhập nguồn mới / Tiếp tục xử lý / Duyệt nội dung / Quản lý Website. Không có nút Gate A.
- \`Luồng một chạm\` 5 nhãn: Nhập nguồn → Tạo Content → Xem 5 Content → Xác nhận đăng → Theo dõi Website. Phụ chú Gate A được xử lý/đối chiếu nội bộ, Owner xác nhận facts khi cần.
- \`Danh sách công việc gần nhất\` hàng theo sản phẩm, status + CTA hành động theo state. Chỉ hiển thị thumbnail BĐS của hồ sơ đã lưu và có quyền xem, không mượn ảnh demo/nhà khác.
- \`Tóm tắt hệ thống\` ở cuối: Nguồn nhận, Review nội bộ, Tin public, Cảnh báo.
- Bottom nav: Home / Nguồn / Content / Website / Khác; **mỗi tab có đích thật**, không liên kết giả.

**Screen B — MISSING FIELDS:** 
- In-context form như ảnh thiết kế: ảnh/nickname hồ sơ, cảnh báo số trường bắt buộc, các input chỉ trường thiếu, \`Thông tin đã có\` được giữ, thông báo dữ liệu và ảnh không mất, \`Lưu nháp\` + \`Tiếp tục tạo Content\`.
- Trên iPhone là modal/bottom sheet không vỡ keyboard/focus, không tràn ngang; role dialog/aria, close/return, focus restoration, invalid feedback, no duplicate API.
- Trên desktop popup vừa đủ rộng/scroll được; không làm người dùng mất context của Intake.

**Screen C — QUẢN LÝ WEBSITE:** 
- Giữ **8 nhóm chính đã khóa**: Tổng quan; Tin BĐS & xuất bản; Traffic & hành vi; Nhu cầu tìm kiếm; CRM & chăm sóc; Hiệu quả kinh doanh; Giám sát/cảnh báo; Tích hợp & dữ liệu.
- Header, số liệu cards 2×2, listing theo BĐS với thumbnail/status/preview/publish/revoke, báo cáo traffic đúng semantics, CRM báo chưa kết nối nếu chưa có, bảng integrations, bottom nav Tổng quan/Sản phẩm/Traffic/CRM/Thêm. Bảng không tràn màn.
- Nút \`Đăng Web\` trong bản mock **KHÔNG thể bỏ qua** Web Approval + canonical final preview + **XÁC NHẬN ĐĂNG** từ Owner; nếu thiếu gate thì chỉ mở bước cần duyệt. \`Thu hồi\` phải có owner confirmation và revision-aware audit.
- Không mock số 12 / 4 / 82 / 146 / 126 / 7 / 54% là live data; chưa có nguồn -> \`Chưa có dữ liệu\`, chưa tích hợp -> \`Chưa kết nối\`. Không biến click CTA thành lead; từ khóa thô không tự thu thập.

**Hình tham chiếu gốc:** 3 ảnh từ cuộc chat Owner ngày 23/11/2026 (Home điều phối, Bổ sung thông tin, Quản lý Website) được Owner duyệt. Nếu Codex chưa truy cập được 3 PNG gốc từ môi trường làm việc, dùng file reference HTML + spec này để triển khai *khớp layout/style/interaction*; **không được tự nhận pixel-perfect** trước khi Owner so ảnh và xác nhận. Không tự đổi brand/hero/chân trang Customer đã khóa. 

## 5. GATE A/B/PUBLISH — KHÔNG THAY HỢP ĐỒNG SERVER

- Input parse/dedup/validate và tạo Review sau one-click là background orchestration, **Owner approval cho Core vẫn explicit**. Tương tác Owner có thể gộp vào màn “Xác nhận dữ kiện” phù hợp UX; tuyệt đối không gọi server mutation Gate A khi Owner chưa chủ ý xác nhận.
- 5 Content riêng tư từ **một** OpenAI generation request cho một lần user click, receipt usage/tokens/cost. Sau đó Owner chọn/sửa/lưu + Gate B và duyệt riêng Website; canonical facts luôn là nguồn thật không phải lời quảng cáo AI.
- Preview cuối phải chứa property ID/revision, địa chỉ, loại, diện tích, giá, tình trạng pháp lý khi xác minh, ảnh/rights và mô tả; Owner bấm XÁC NHẬN ĐĂNG, không preview-only là publish.
- Revoked/stale/invalid rights phải ẩn listing/detail/media fail-closed trên Website staging, không rollback ảnh/DB của Owner.

## 6. BLOCKER API KEYCHAIN TRONG STAGING (CHƯA XONG)

RESULT #7101480651: Terminal Keychain đọc được và có một lần AI thật tạo 5 variants; **Admin launchd trả OSStatus 44 (\`KEYCHAIN_CREDENTIAL_MISSING\`)**, AI trên Staging vẫn OFF. Đầu tiên audit ngữ cảnh user session/keychain/search list/access control/process bootstrap và exact named service/account **chỉ metadata và result code**, không in/export secret. Giữ credential cũ; **không hardcode key, không \`.env\` plaintext, không mở ACL rộng, không tạo key mới, không bỏ auth**. Có thể áp dụng phương án quyền tối thiểu cho riêng launchd Admin Staging sau xác minh an toàn; cần Owner tương tác OS nếu hệ điều hành yêu cầu, báo một thao tác rõ. Stop nếu không có cách cấp quyền đúng. Chỉ bật Content-live trên Staging khi đã lấy được credential qua tiến trình thực, guard token/request/cost PASS; test một request có kiểm soát tại route thực. Không gọi liên tục để “thử”.

## 7. TRIỂN KHAI & NGHIỆM THU BẮT BUỘC

**Thứ tự Codex:**
A. Preflight source, PR HEAD, active SHA/runtime, quyền, tài liệu, diff và bảo toàn dữ liệu; lập ma trận gap \`EXISTS/PARTIAL/MISSING/NOT_VERIFIED\`; không viết lại Core.
B. Sửa bug **cross-intake evidence mixing** + đổi tên Queue CTA; chuyển Home thành command center mà không xóa route có ích; implement missing-fields validation/modal; state transitions/Owner confirmations thực.
C. Nối một chạm đầy đủ 5 Content, handle Keychain blocker bằng quyền tối thiểu; đúng một provider generation call/explicit click, guard/accounting.
D. Tích hợp UI Home + popup + Website theo V3; dùng dữ liệu thật, không mock; Desktop + iPhone 15 Pro Max; accessibility.
E. Backup & restore PASS, release exact SHA **Staging only**, authenticated HTTPS E2E with synthetic isolated fixtures + UI tests, negative tests, cleanup; không post dữ liệu thật Owner. Owner tự nhập và bấm xác nhận với EcoCity nếu muốn.

**PASS matrix ít nhất:**
1. Note/ảnh đầy đủ → click một lần → facts/evidence đúng → 5 biến thể thật/receipt → review/sửa/chọn → xác nhận facts/Gate A → duyệt Website → preview → xác nhận đăng → Customer detail/image/feed/search → sửa/re-review → thu hồi, trên **Safari iPhone** và desktop.
2. Note thiếu 1,2, hoặc nhiều fields → popup chỉ missing; bổ sung -> tiếp tục đúng intake; không mất ảnh; save draft/reload; không gọi AI trước validation.
3. Loại HOUSE có \`house_type/bedrooms/bathrooms\`; RENT price cycle; \`legal_status\` unknown optional; mâu thuẫn rights, stale revision, duplicate sources; wrong/negative price; missing required metadata.
4. 2 intake A/B khác nguồn/rights → click “Tiếp tục xử lý” A trong khi form B đang điền → **không trộn** evidence, media, revision; multiple tabs/reload/retry no duplicate/no wrong AI charge.
5. Staging runtime AI from service context **authenticated**; Keychain guard/disable switch, refusal before actual paid API on error; timeout/unknown outcome preserves idempotency; request-count=1, media \`NOT_SENT\` nếu hợp đồng chưa cho.
6. Không giả dữ liệu UI, không auto Gate A/B/public, not-authorized HTTP401, CSRF/session, stale/rights, analytics semantics; Visual comparison screens sizes including keyboard, long text and smallest viewport.
7. Snapshot/isolated restore/integrity; data hashes Owner intake EcoCity 5 ảnh unchanged unless Owner explicitly modifies; no changes to protected listeners/main/Production/DNS/Social.

**RESULT** vào Issue #39 với mã nguồn exact SHA, runtime hashes và URLs, demo video/screenshots side by side của 3 UI, test receipts, provider request id/token/cost (không secret), statuses \`HOME/P0_ONE_TOUCH/MISSING_POPUP/AI_LIVE/WEB_ADMIN/CONNECTED_E2E/OWNER_ACCEPTANCE\` PASS/PARTIAL/BLOCKED. Chỉ nói \`STAGING_OWNER_READY\` khi tất cả runtime receipts + Owner actual Safari review được chứng minh; Owner acceptance vẫn pending tới khi Hải xác nhận. Không merge PR #44; không dùng Production.

**STAGING MUST BE REAL; DESIGN MOCK MUST STAY DOCUMENTATION.**
