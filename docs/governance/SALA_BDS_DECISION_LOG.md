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

## D08 — Owner GO cho tích hợp nguồn đọc First Web (increment cô lập) — APPROVED 22/11/2026

**Căn cứ:** [RESULT đối chiếu Admin/Customer Web #7071851933](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071851933) và [Owner GO thực hiện #7071923255](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071923255).

- **Đã chứng minh ở mức source/runtime:** năm nhóm trách nhiệm trùng/đè lên nhau (Owner login, intake/edit/media, Property identity/Core, review, visibility/publication), hai dịch vụ localhost trả health, public apex vẫn maintenance. **Chưa chứng minh có hai bản ghi thật bị trùng**, không được xóa DB dựa vào nghi vấn.
- **Quyết định Owner:** Admin SALA BĐS/Property Master là nguồn dữ liệu chuẩn và nơi quản trị nghiệp vụ; Customer Web chỉ đọc **approved revision-bound projection** đã qua Gate A và Web publication riêng biệt. Giữ nguyên mã UI/public route hữu ích, không làm web-only canonical Core nữa.
- **Được phép ngay:** Codex sửa mã tích hợp **nhỏ, cô lập trên nhánh review PR #44**, test với fixture/DB tổng hợp để nối public Feed/Search/Detail/Media của Customer Web tới Admin-approved projection; fail closed khi chưa duyệt, dữ liệu stale/revoke/invalid; lập và lưu kế hoạch bảo toàn dữ liệu Customer Web.
- **Chưa được phép:** đọc/ghi/chuyển đổi/xóa/backup DB thật, ngừng Owner panel/intake/review routes trong runtime thực, gỡ mã legacy, hợp nhất login thật, merge/main, staging/production deploy/DNS/tunnel, API trả phí, mở Phase 2. Các bước thay thế chức năng hoặc data migration phải có bằng chứng test và Owner GO riêng.
- **Không đổi mục tiêu cấp trên:** một phiên Owner login bằng mật khẩu hiện tại, một Property Master, Web Admin là phân hệ xuất bản/bảo trì. Code release khác data; rollback mã không ghi đè dữ liệu thật. Giữ AI9/Content/provider có ích nhưng dormant theo D06.
- **Gate bước này:** chỉ công nhận `LOCAL_SYNTHETIC_PROJECTION_INTEGRATION` khi file/commit và test positive/negative có receipt. Đây **không phải** `FIRST_WEB_CORE_READY_TO_SKIN`, `Phase1_UNIFIED_PASS`, `staging` hoặc `production` acceptance.

## D09 — Owner GO kiểm thử HTTP E2E giữa hai tiến trình cô lập — APPROVED 22/11/2026

**Căn cứ:** [Codex RESULT source TEST_ONLY #7072170795](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072170795) trên [PR #44](https://github.com/Haisala/sala-bmt-listings/pull/44), **[Owner GO kiểm thử sandbox HTTP #7072250230](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072250230)**.

- **Owner duyệt công đoạn thực thi giới hạn:** Codex dựng **Admin synthetic + Customer synthetic từ đúng PR #44 HEAD** trong sandbox/private temporary root, hai tiến trình loopback/cổng trống riêng, không dùng credential hay dữ liệu thật. Chạy **E2E HTTP thực** từ Gate A + quyền media + Owner publication → staging artifact approved → Customer feed/search/detail/media; kiểm revoked/stale/invalid-source/no-legacy-fallback, quyền âm tính và dấu vết request/response. Có quyền thêm harness/test hoặc sửa lỗi nhỏ an toàn **trên nhánh review PR #44**, không tạo hệ thống dữ liệu/đăng nhập mới.
- **Ranh giới tuyệt đối:** KHÔNG restart hoặc thay runtime Admin `:18876`, Customer `:18877`, không động property/customer/media/SQLite thật, không động launch agent, Pages, DNS, tunnel, apex, `staging.haisala.io.vn`, `main`, không merge, không bật API AI, không chuyển/xóa legacy B hoặc thay auth thật. Quyết định giữ AI9/Content/provider hữu ích nhưng dormant tiếp tục hiệu lực.
- **Safety gate:** kiểm chứng cô lập trước khi bind và trước mọi test; thiếu quyền, cổng hoặc config an toàn thì `BLOCKED`, không chạy thử vào runtime thật. Không suy từ kiểm thử in-process hoặc HTTP GET health riêng thành E2E thành công.
- **Đây là `ISOLATED_HTTP_E2E` trên loopback, KHÔNG phải GO official Phase 2 staging/deploy/HTTPS, không chứng minh single-session, traffic-event chiều về hoặc `FIRST_WEB_CORE_READY_TO_SKIN`.** Phase 1 unified `NOT_VERIFIED` và Phase 2 `NO_GO` giữ nguyên cho tới khi có receipt đáp ứng toàn gate Master 5 Phase và phép Owner riêng.
- **Giao nhận:** một RESULT Issue #39 có fingerprint nguồn/tiến trình, HTTP status từng cạnh, test negative, SHA, no real-data IO, handoff PR branch và một next action. Chỉ cập nhật trạng thái sau khi đối chiếu báo cáo mới.

## D10 — Owner cho phép xin quyền loopback cô lập để gỡ HTTP_BIND_BLOCKED — APPROVED 22/11/2026

**Bằng chứng:** [RESULT BLOCKED #7072294543](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072294543) / [Owner GO xử lý môi trường #7072339267](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072339267).

- Owner phê duyệt cho **Codex đề nghị quyền thực thi giới hạn mở hai cổng `127.0.0.1` riêng bằng cơ chế approval chính thức của môi trường**, chỉ phục vụ một lần kiểm thử TEST_ONLY isolated HTTP E2E từ PR #44 theo task #7072250230. Nếu phiên đó không hỗ trợ quyền phù hợp, xác định môi trường được phê duyệt khác hoặc STOP và nêu chính xác bước Owner phải thao tác.
- **Không đồng nghĩa quyền đã được cấp**, không khắc phục `PermissionError` bằng comment GitHub hoặc đổi lệnh chạy trong sandbox cũ. Không tự bật full network/full disk, tắt sandbox, chiếm cổng `:8765/:18876/:18877`, sửa main, động dữ liệu/credential thật, dịch vụ đang chạy hoặc hệ thống khác.
- Quyết định này **chỉ gỡ blocker môi trường**; không cho phép merge, release, production/staging public hay chuyển Phase 2. `PHASE_1_UNIFIED=NOT_VERIFIED`, `PHASE_2=NO_GO`, `PR_MERGE_GO=NO`.
- Khi có quyền thực và test, Codex phải lập RESULT gồm proof bind/socket/process SHA, test HTTP từng cạnh, dữ liệu synthetic và clean teardown; không dùng test in-process trước đó thay E2E.

## Lưu ý về authority

[Blueprint V1.0](../SALA_BDS_MASTER_SYSTEM_WEB_FIRST_BLUEPRINT_V1_OWNER_APPROVED.md) là **design target**; các quyết định Owner sau đó trong [Issue #39](https://github.com/Haisala/sala-bmt-listings/issues/39) và [Issue #38](https://github.com/Haisala/sala-bmt-listings/issues/38) bổ sung/thay thế trong **đúng phạm vi**. Không biến thiết kế cũ AI9 chín khối thành lệnh chạy chín API hoặc ép hoàn thành các phân hệ khác trước First Web. Phần lịch sử mô tả Issue trước chuẩn hóa được giữ nguyên tại [archive](./SALA_BDS_ISSUE39_PRE_3T_ARCHIVE.md).
