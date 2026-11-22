# SALA BĐS — DECISION LOG (TẦNG 2: QUYẾT ĐỊNH CÒN HIỆU LỰC)

**Ngày chuẩn hóa:** 22/11/2026. **Dự án duy nhất:** SALA BĐS, `Haisala/sala-bmt-listings`. Đây là nguồn tra cứu quyết định, **không phải checkpoint thứ hai hay lệnh Codex**. Tình trạng/việc hiện hành chỉ lấy từ [LIVE CHECKPOINT — Issue #39](https://github.com/Haisala/sala-bmt-listings/issues/39); kết quả và bằng chứng ở [Tầng 3 — Evidence Index](./SALA_BDS_EVIDENCE_INDEX.md).

**Cách sử dụng:** Mặc định chỉ đọc mô tả Issue #39; chỉ tra đúng mục tài liệu này khi hỏi quyết định, kiến trúc, phạm vi hoặc xung đột. Không tự đánh đồng **DESIGN / SOURCE_EXISTS / TEST_ONLY / STAGING / PRODUCTION / OWNER_ACCEPTED**. Quyết định mới có phạm vi rõ thay thế quyết định cũ thì ghi `SUPERSEDED` và giữ URL gốc; không xóa lịch sử.

## D01 — Hệ thống SALA BĐS duy nhất, nguồn dữ liệu gốc duy nhất — LOCKED

- Một hệ thống SALA BĐS, một tài khoản/mật khẩu cũ, đích là **một phiên đăng nhập**; không tạo tài khoản, mật khẩu, Keychain mới, không yêu cầu cung cấp mật khẩu qua chat/GitHub. **Cùng mật khẩu ở hai ứng dụng không chứng minh một phiên**.
- Một `Property Master/Property Core canonical`: soạn/sửa tin, giá, ảnh, nguồn và quyền sử dụng, revision, lịch sử duyệt trong hệ thống chung; Website không tạo kho dữ liệu BĐS chuẩn thứ hai.
- Web Admin chỉ là **phân hệ** bên trong SALA BĐS, không phải ứng dụng/Admin độc lập. Customer Web phục vụ public read-only đối với **approved publication projection** (bản hiển thị đã duyệt); không có Owner login/intake tùy ý trên public website.
- **Gate A** (duyệt dữ liệu vào nguồn chuẩn) khác **duyệt xuất bản Web**; **Gate B** theo hợp đồng duyệt Content, không đồng nhất ba cổng. Quyền publish/unpublish được kiểm ở server, có audit, idempotency, quyền media và revision.
- Sự kiện Website (traffic, lượt xem chi tiết, CTA) quay về SALA BĐS, gắn canonical property/publication ID, chống ghi trùng, bảo vệ quyền riêng tư; không suy ra tracking/lead thật đã hoạt động.
- Nguồn: [Owner one-system #7066301660](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066301660), [Owner architecture lock #7066721682](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066721682), [blueprint V1.0](../SALA_BDS_MASTER_SYSTEM_WEB_FIRST_BLUEPRINT_V1_OWNER_APPROVED.md).

## D02 — FIRST WEB ưu tiên trước, UI đã duyệt ráp sau — LOCKED

**Mục tiêu:** Xây lõi First Web + quản trị xuất bản + vận hành/bảo trì + hợp đồng API/data/UI đủ chắc để đạt `FIRST_WEB_CORE_READY_TO_SKIN`; sau đó ráp vỏ ChatSite đã Owner duyệt **không viết lại nghiệp vụ/kho dữ liệu** → kiểm thử qua các cổng → phát hành `https://haisala.io.vn` → Owner nghiệm thu. Chỉ sau đó mới ưu tiên phát triển/tích hợp các phân hệ AI Content, CRM, Social, Market/Growth (theo phép riêng); không xóa khả năng đã có.

- First Web Admin gồm (A) duyệt/cập nhật/gỡ đăng, trạng thái/nhật ký/traffic, và (B) quản trị version, staging, test, bản phát hành SHA, backup/restore, rollback, release receipt.
- `READY_TO_SKIN` đòi kiểm chứng auth/session, canonical dữ liệu, publication và event quay về, ranh giới data/UI, an toàn phát hành; **không có nghĩa LIVE**, và không thay các cổng Phase 2–5.
- Giao diện cuối cùng thuộc thiết kế ChatSite/Visual Master đã duyệt. Không tự redesign hero/footer hoặc tạo Admin thứ hai để làm mẫu.
- Nguồn: [Owner bổ sung vận hành & ready-to-skin #7066721682](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066721682), [Owner one-system #7066301660](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066301660).

## D03 — MASTER 5 PHASE FIRST WEB — LOCKED, KHÔNG ĐỔI TÊN

Nguồn đầy đủ: [MASTER 5 PHASE #7058182954](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7058182954). Đây là **5 giai đoạn FIRST WEB**, khác với **4 giai đoạn Bộ xử lý Content** ở D06.

1. **Phase 1 — LOCAL Core → Web + artifact safety:** dữ liệu nguồn đã duyệt → bản public theo revision → Website, đảm bảo đóng gói allowlist, kiểm tra an toàn TEST_ONLY. PASS_LOCAL từng phần không chứng minh tích hợp hệ thống thống nhất.
2. **Phase 2 — STAGING PREP/CUTOVER:** nguồn SHA và gói deploy rõ; staging cô lập; routing/HTTPS/Auth, backup/restore và rollback thực nghiệm; Owner GO riêng.
3. **Phase 3 — PHYSICAL iPhone + desktop E2E:** nghiệm thu luồng input/duyệt/publish/read/CTA/traffic cùng dữ liệu, trên thiết bị thật và staging phù hợp; Owner xác nhận.
4. **Phase 4 — PRODUCTION READINESS:** mã/artifact/manifest SHA, private file safety, rollback, giám sát, hồ sơ phát hành, Owner duyệt.
5. **Phase 5 — RELEASE + OWNER ACCEPTANCE:** chỉ triển khai exact SHA sau Owner GO; kiểm tra website thật, public data, quyền, media, traffic; Owner nghiệm thu.

**Chuyển bước:** chỉ tiến khi gate của phase trước có bằng chứng/Owner GO phù hợp. Không gọi các checkpoint P0.x/P1.1 là những phase mới. Nguồn quy tắc sơ đồ cuốn chiếu: [#7062626669](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7062626669).

## D04 — Vận hành và bảo toàn dữ liệu khi phát hành — LOCKED DESIGN, NOT IMPLEMENTATION PROOF

- `Code release ≠ Property Master/ảnh gốc/publication/lead/traffic/events`: các dữ liệu sống được duy trì trong lưu trữ bền vững, tách khỏi tệp site/release thay thế.
- Workflow: Development → staging dữ liệu cách ly → thử security, iPhone/desktop, manifest/backup/restore/migration/rollback → Owner duyệt exact SHA → release receipt → monitoring.
- Rollback code **không được** ghi đè một DB snapshot cũ làm mất các cập nhật thật phát sinh sau phát hành; không reseed/xóa Property Master vì đổi phiên bản Website.
- `Publish BĐS` là quyết định nghiệp vụ hiển thị sản phẩm; `Deploy Web` là quyết định phát hành phần mềm, hai quyền phê duyệt riêng.
- Nguồn: [#7066721682](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066721682). **Chưa mặc định backup/restore, production-safe release hoặc READY_TO_SKIN đã nghiệm thu.**

## D05 — Website chính và web nháp cũ — OWNER LOCK, PHẠM VI HẸP

- Owner quyết định thay web nháp “BMT Property Hub” ở đường truy cập khách bằng **First Web mới tại `haisala.io.vn`**, không bắt buộc giữ/bảo lưu **vỏ web nháp**. Quyền này **không đồng nghĩa quyền xóa** dữ liệu Property Master, ảnh gốc, lead, lịch sử, môi trường staging Admin hoặc hệ thống khác.
- Giao diện thật sẽ ráp sau lõi; trang maintenance có bằng chứng triển khai trước đó **không có nghĩa ứng dụng First Web đã LIVE**.
- Nguồn: [#7064335056](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7064335056), [#7065120021](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7065120021), [#7066721682](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066721682).

## D06 — Bộ xử lý Content 4 giai đoạn, một API → năm Content — LOCKED, KHÔNG KÍCH HOẠT TRONG FIRST WEB

**Đây là phân hệ SALA BĐS Admin/Content Studio (Issue #38), không phải năm Phase First Web và không phải kiến trúc SALA Media.**

1. **DATA:** tiếp nhận tin/ảnh, facts, chuẩn hóa, chống trùng, source ID, citation, quyền sử dụng và kiểm duyệt dữ liệu. Provenance được gộp ở lớp dữ liệu/quyền, **không bỏ bằng chứng nguồn/rights**.
2. **CONTENT:** giữ năng lực Audience / Research / Strategy / Content có ích; một hành động tạo Content = **tối đa đúng một yêu cầu sinh OpenAI** → **đúng năm bản nội dung** nếu thành công, có nguồn và kiểm tra nội bộ. Không gọi thêm AI để extraction, đếm token, model preflight, retry ẩn; không tự đặt 5 phong cách biên tập khi chưa duyệt.
3. **REVIEW:** Owner chọn/sửa/duyệt/lưu Content với ID ổn định; review/retrieve/publish **không gọi thêm AI**, không tự đăng.
4. **MEASUREMENT:** tách Performance/Learning khỏi lúc tạo Content; chỉ chạy với dữ liệu tương tác thật sau đăng, không tự sửa model.

**Usage Ledger bắt buộc:** một receipt/attempt phản ánh request và liên kết năm Content; model/request ID, trạng thái, tokens input/output/total và chi tiết nếu provider trả; đơn giá nguồn/phiên bản và chi phí USD **ước tính**; billed USD chỉ khi có đối soát authoritative. Không bịa 0/token/cost khi không có. Cost popup Admin là năng lực giao diện tương lai, không cấp quyền tích hợp API hoặc deploy.

**Giữ mã có ích, không kích hoạt:** `ai9`/Content/provider, route và schema legacy có thể giữ cho tương thích; **không xóa module chỉ vì tên chứa AI9**. Không vận hành chín lớp AI nối tiếp; thực tế chín trách nhiệm nội bộ từng là xử lý deterministic, **không phải chín API calls**. Market Intelligence không còn là bước bắt buộc của mỗi lần sinh Content; **không có quyết định xóa vĩnh viễn** năng lực phân tích thị trường được hỗ trợ bằng nguồn. Giữ Performance/Learning có điều kiện, không chạy vô ích trước Content.

**Ranh giới First Web/PR #44:** mã dormant (không kích hoạt) có thể được giữ khi cần cho dependency/compatibility, **không tự cho phép lộ route, gọi provider, sử dụng secret, tạo thêm pipeline hoặc gọi API trả phí**. Việc giữ mã không có nghĩa PR đã an toàn/được phép merge: vẫn phải review nguồn, import side effects, phụ thuộc, quyền, dữ liệu và bằng chứng tích hợp.

**Nguồn gốc quyết định (sau cùng thắng quyết định cũ):** [đổi 3 → 5 Content, tên sản phẩm & one request #6955558218](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6955558218), [Owner 4 giai đoạn + cost ledger #6956180969](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6956180969), [Owner giữ các năng lực cũ #6955664751](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6955664751); [hot-path simplification receipt #6956411944](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6956411944). Owner tái xác nhận nguyên tắc **giữ mã hữu ích nhưng không kích hoạt trong First Web** ở chat SALA BĐS ngày 22/11/2026; không coi đó là bằng chứng kiểm thử PR #44.

**SUPERSEDED về product target:** ba Content/“AI 9 layers” là tên/đích cũ, không dùng làm nhãn sản phẩm hoặc contract thế hệ nội dung mới. Ký hiệu nội bộ, schema và compatibility cũ có thể tiếp tục tồn tại cho đến khi một migration an toàn được duyệt riêng.

## D07 — Governance 3 tầng chống đầy chat — OWNER APPROVED 22/11/2026

- **Tầng 1**: mô tả [Issue #39](https://github.com/Haisala/sala-bmt-listings/issues/39) — chỉ CURRENT, RESULT, BLOCKER, ONE NEXT và 2 link tầng dưới; checkpoint cũ được **thay thế**, không append toàn bộ lịch sử.
- **Tầng 2**: tệp này — quyết định Owner còn hiệu lực, phạm vi, ràng buộc, điều kiện chuyển bước; không đổi do một lần test.
- **Tầng 3**: [Evidence Index](./SALA_BDS_EVIDENCE_INDEX.md) — kết quả test, PR/repo/commit/receipt, môi trường, UNKNOWN; link báo cáo gốc và [archive mô tả cũ](./SALA_BDS_ISSUE39_PRE_3T_ARCHIVE.md).
- **Khi mở chat mới:** đọc chỉ Tầng 1; hỏi quyết định đọc đúng mục Tầng 2; cần chứng minh tra Tầng 3 và đúng nguồn. Một câu hỏi **không** kéo theo audit toàn repo.
- **Khi Codex trả RESULT:** đối chiếu duy nhất RESULT mới với TASK, yêu cầu, SHA và môi trường; cập nhật Tầng 3 → thay delta tại Tầng 1. Chỉ sửa Tầng 2 khi Owner chốt quyết định mới. Không tự tạo checkpoint/task/PR/phase song song vì đổi chat.
- Owner chốt phạm vi và GO; ChatGPT quản trị/đối chiếu; Codex triển khai trong phạm vi riêng được phép. Không sửa SALA Media/NaviBDS/hệ thống khác.

## Lưu ý về authority

[Blueprint V1.0](../SALA_BDS_MASTER_SYSTEM_WEB_FIRST_BLUEPRINT_V1_OWNER_APPROVED.md) là **design target**; các quyết định Owner sau đó trong [Issue #39](https://github.com/Haisala/sala-bmt-listings/issues/39) và [Issue #38](https://github.com/Haisala/sala-bmt-listings/issues/38) bổ sung/thay thế trong **đúng phạm vi**. Không biến thiết kế cũ AI9 chín khối thành lệnh chạy chín API hoặc ép hoàn thành các phân hệ khác trước First Web. Phần lịch sử mô tả Issue trước chuẩn hóa được giữ nguyên tại [archive](./SALA_BDS_ISSUE39_PRE_3T_ARCHIVE.md).
