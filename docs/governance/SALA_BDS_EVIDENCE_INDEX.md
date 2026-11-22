# SALA BĐS — EVIDENCE INDEX (TẦNG 3: BẰNG CHỨNG & HANDOFF)

**Chuẩn hóa:** 22/11/2026. **Đây là mục lục kiểm chứng, không phải MASTER tiến độ/không phải lệnh chạy.** Trạng thái đang làm lấy từ [Tầng 1 — Issue #39](https://github.com/Haisala/sala-bmt-listings/issues/39); luật và quyết định từ [Tầng 2 — Decision Log](./SALA_BDS_DECISION_LOG.md). Không lấy mục lịch sử bên dưới thay checkpoint mới nhất.

## 0. Đọc đúng lượng cần thiết

- **Câu hỏi thông thường:** chỉ đọc mô tả Issue #39.
- **Hỏi “Owner đã chốt gì?”:** đọc đúng ID quyết định ở Tầng 2, sau đó liên kết gốc nếu có xung đột.
- **Hỏi “đã PASS chưa?”:** tìm đúng dòng bên dưới, mở một result/TASK gốc, xác nhận môi trường, SHA, dữ liệu thật/giả và giới hạn; không quét hàng trăm comment.
- **Codex báo kết quả mới:** đối chiếu TASK → RESULT → file/test/commit; ghi delta ở đây, thay checkpoint ở Issue #39, không append mọi vòng chat.
- **Tình trạng không tìm thấy chứng cứ:** `NOT_VERIFIED` hoặc `UNKNOWN`, không tự coi là không tồn tại hoặc đã hoàn thành.

## 1. Bộ nguồn điều phối hiện hành — First Web

| Vai trò | Link/đầu mối | Giới hạn |
|---|---|---|
| MASTER 5 PHASE | [#7058182954](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7058182954) | Hợp đồng các gate, không phải báo cáo đã hoàn thành |
| Một active checkpoint / sơ đồ cuốn chiếu | [#7062626669](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7062626669) | Không tăng quyền hay tự mở job mới |
| Owner một hệ thống, một phiên, một nguồn canonical | [#7066301660](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066301660) | Kiến trúc yêu cầu, chưa có E2E thống nhất |
| Owner bổ sung Web Admin + maintenance + ready-to-skin | [#7066721682](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066721682) | DESIGN LOCK, không phải UI, runtime hay deployment PASS |
| GO gần nhất cho review/handoff PR #44 | [#7071362357](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071362357) | Không có quyền merge, Phase 2 hoặc vận hành dữ liệu thật |
| Codex RESULT gần nhất | [#7071465070](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071465070) | Review `PARTIAL`, targeted test cục bộ; chưa có unified integration PASS |
| PR source review | [PR #44](https://github.com/Haisala/sala-bmt-listings/pull/44) | DRAFT/UNMERGED; metadata Git mergeability có thể thay đổi |
| Hồ sơ bàn giao Codex đa phiên | [Handoff trên PR #44 @ 0aeaa923](https://github.com/Haisala/sala-bmt-listings/blob/0aeaa92325b7b4c703574047e9b4c9f4f5087502/docs/ISSUE39_LAST_72H_HANDOFF.md) | Có `CONTEXT_GAP` cho lịch sử chat Codex 19–22/11; không bịa thông tin không truy xuất được |
| Kiến trúc đã Owner duyệt | [Blueprint V1.0](../SALA_BDS_MASTER_SYSTEM_WEB_FIRST_BLUEPRINT_V1_OWNER_APPROVED.md) | Design target, các quyết định sau có thể bổ sung/thay thế |

## 2. FIRST WEB — bằng chứng đã đạt, chính xác phạm vi

| Hạng mục | Đã chứng minh | Chưa chứng minh |
|---|---|---|
| Phase 1 Code B local | [#7058638110](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7058638110): `PASS_LOCAL` trong phạm vi TEST_ONLY cũ, Core → projection → Web/Analytics | Hệ thống thống nhất trên staging/live |
| Web nháp cũ và public domain | [#7065120021](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7065120021): Pages chuyển sang trang maintenance, thay public route của web nháp; workflow phát allowlist maintenance | First Web app mới đã live ở apex; production end-to-end |
| Dùng mật khẩu cũ | [#7066142822](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066142822): Customer Web login HTTP cục bộ 200, session/logout, kiểm thử local và restart trong phạm vi ghi nhận | Một phiên/cùng thẩm quyền session với SALA BĐS Admin; public auth |
| Tái đối chiếu kiến trúc một hệ thống | [#7066438757](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066438757): xác định Admin và Customer Web đang có session authority riêng, tích hợp cần tiếp tục | Single login, canonical live read, web publishing trong cùng Admin |
| Port Code B sang PR #44 | [#7066860840](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066860840): 33 file ban đầu, 7 bộ kiểm thử synthetic/cô lập PASS; Code A/B dirty worktree giữ nguyên. [PR #44 source/manifest](https://github.com/Haisala/sala-bmt-listings/pull/44) | Trên runtime thật, production, đúng login, publication/projection/analytics chiều về |
| Review/handoff bổ sung | [#7071465070](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071465070): review `PARTIAL`; phát hiện import `UsageLedger` khởi tạo private root/DB; Codex sửa nhỏ bằng lazy-init trên nhánh PR, targeted subprocess temp-root PASS và diff check PASS | Không phải full review PASS; không thay chứng cứ 7 suites bằng rerun mới; chưa giải quyết hết scope và unified integration |
| PR #44 HEAD mới | [GitHub PR #44](https://github.com/Haisala/sala-bmt-listings/pull/44): đã quan sát head `0aeaa92325b7b4c703574047e9b4c9f4f5087502`, base ghi nhận `83394a901dcc2c1944f083b65fc01c22e5a6602f`; draft, open, unmerged | Không được dùng trạng thái `mergeable=true/false` làm phê duyệt code. Report #7071465070 ghi false, truy vấn PR sau đó trả true: trạng thái Git biến động, cần đọc lại trước quyết định merge |
| 72h Codex handoff | [Handoff PR #44](https://github.com/Haisala/sala-bmt-listings/blob/0aeaa92325b7b4c703574047e9b4c9f4f5087502/docs/ISSUE39_LAST_72H_HANDOFF.md): tái dựng quyết định, nguồn, tests và điều chưa chứng minh từ GitHub | Không khôi phục được toàn bộ transcript cũ; `CONTEXT_GAP` được nêu minh bạch |
| Artifact builder | [#7071465070](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071465070): báo không port bài test vì `assets/brand/b-mark.png` không có trên main baseline được xét | Không kết luận artifact-builder mới PASS; không tự tạo ảnh thay |

**Phân loại hiện hành:** Phase 1 tích hợp unified = `NOT_VERIFIED`; Phase 2 = `NO_GO/NOT_STARTED`; Phase 3–5 = `NOT_STARTED` theo checkpoint và RESULT gần nhất. `haisala.io.vn` vẫn được mô tả là maintenance ở bằng chứng cuối; chưa có receipt chứng minh First Web mới đã thay thế live.

**Các điều kiện còn thiếu:** một phiên đăng nhập SALA BĐS thực, duy nhất Property Master canonical, chủ quyền publication Gate A khác duyệt Web, read model đúng revision, traffic/view/CTA có receipt chiều về, bảo toàn release + backup/restore/migration/rollback thực nghiệm, staging HTTPS/identity/SHA và physical iPhone/desktop. Mỗi điều cần evidence ở đúng phase, không nhồi tất cả vào một audit.

## 3. Issue #38 — Content Processor: quyết định khác với triển khai thực tế

| Nguồn | Đã chứng minh/quyết định | Không được suy ra |
|---|---|---|
| [Owner chuyển 3 → 5 Content #6955558218](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6955558218) | Đích sản phẩm `Bộ xử lý Content`, một generation request, năm Content, một bộ ghi usage/cost, Owner duyệt, không tự đăng | Có API real thành công |
| [Owner 4 giai đoạn + ledger #6956180969](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6956180969) | DATA / CONTENT / REVIEW / MEASUREMENT, giữ năng lực hữu ích của AI9 và thu thập actual provider usage, USD estimate tách billed | Phải chạy cả chín lớp hoặc chạy thêm API cho research/cost |
| [Audit chức năng 9 nội bộ #6955699692](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6955699692) | Chín trách nhiệm `ai9_layers.py` là deterministic; extraction path riêng có thể gọi API tùy cấu hình | Mỗi lớp = một cuộc gọi AI |
| [Refactor hot-path #6956411944](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6956411944) | `PASS/PARTIAL` local: Performance/Learning không chạy vô ích khi preview mặc định, chức năng/schema cũ còn | Đã xóa vĩnh viễn Market Intelligence hay Learning |
| [5 Content offline contract #6956557291](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6956557291) | Fake provider một lời gọi → 5 variants, stable IDs, một receipt, invalid response fail-closed | Model thật/ảnh thực/api provider thành công |
| [Admin E2E offline #6956830243](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6956830243) | Admin `generate` nối fake provider → 5 `DRAFT` → UsageLedger; no publish, zero live calls | API trả phí, production hoặc QA người dùng |
| [Live smoke preflight #6957119529](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6957119529) | `BLOCKED` trước billable call, `LIVE_API_CALLS=0` theo report đó | Cấu hình Keychain/model/API đã thông |
| [Legacy AI path forensic #6957261687](https://github.com/Haisala/sala-bmt-listings/issues/38#issuecomment-6957261687) | Reuse provider/Responses path ở mức source + offline; `PARTIAL/BLOCKED FOR LIVE`, không có actual billed call | Đã nghiệm thu OpenAI thật hoặc ảnh Vision |
| [Owner update trong chat 22/11/2026, đối chiếu các quyết định Issue #38](https://github.com/Haisala/sala-bmt-listings/issues/38) | Giữ module `ai9`/Content/provider có giá trị cho compatibility nếu dormant; không kích hoạt content generation hay làm chậm First Web | Cho phép merge PR #44, deploy, gọi provider hoặc mở Phase khác |

**Quy tắc PR #44:** đánh giá bằng dependency/safety/activation của mã, không đòi xóa chỉ vì tên module. Kết quả Codex review #7071465070 nêu scope chưa phân xử; quyết định Owner bổ sung nói giữ mã hữu ích không kích hoạt, vì vậy điểm tiếp theo là **đối chiếu lại review với phạm vi đó**, không mở chức năng AI trước nghiệm thu First Web.

## 4. Hạ tầng, an toàn và lịch sử

- [Pages deployment/public route #7065120021](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7065120021) — maintenance public, Pages allowlist maintenance; đây không phải Customer Web full launch.
- [Artifact security risk read-only #7001240379](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7001240379) — kết quả lịch sử UNKNOWN về nội dung artifact cũ; không được tự coi mọi exposure concern đã đóng chỉ vì workflow đổi, kiểm đúng bản phát hành khi đến Phase 4.
- [Owner source GO #7066590041](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066590041) — chỉ khôi phục nguồn Code B → nhánh review có kiểm soát; không được ghi đè worktrees.
- [Owner PR44 review GO #7071362357](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071362357) — scope review/handoff, không merge, không tự mở staging.
- [Archive nguyên văn Issue #39 trước chuẩn hóa](./SALA_BDS_ISSUE39_PRE_3T_ARCHIVE.md) — bao gồm CURRENT MASTER MAP dài và AUDIT gốc, chỉ tham chiếu lịch sử. Các comment/MASTER quyết định trên Issue #39 tiếp tục giữ nguyên.
- [Blueprint gốc](../SALA_BDS_MASTER_SYSTEM_WEB_FIRST_BLUEPRINT_V1_OWNER_APPROVED.md) — bản thiết kế, không phải code/runtime receipt.

## 6. Admin ↔ Customer Web — audit thực tế và Owner GO mới (22/11/2026)

- **TASK đối chiếu trước:** [Owner #7071752485](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071752485).
- **RESULT:** [Codex #7071851933](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071851933); [ma trận, runtime và phân quyền chi tiết — handoff @ `baee5e4`](https://github.com/Haisala/sala-bmt-listings/blob/baee5e4ee0611c2760985df9b5641b5765b74c01/docs/ISSUE39_LAST_72H_HANDOFF.md).
- **Quan sát thực từ báo cáo Codex:** Admin A tại `127.0.0.1:18876` GET health 200, Customer B tại `127.0.0.1:18877` GET health 200; apex `https://haisala.io.vn` trả trang “Website đang được xây dựng”. Hai runtime khác nhau; fingerprint process CWD được ghi trong handoff, chưa đồng nhất source PR với runtime.
- **Năm nhóm code authority overlap:** đăng nhập/session, intake/edit/media, identity/Property Core, review/Gate A, công khai/Website publication gate. Customer B có SQLite riêng `properties/listings/revisions/media/provenance`, login/session riêng; Admin A có Core/review/public-release. Đây là **trùng trách nhiệm triển khai**, KHÔNG có bằng chứng bản ghi/data thật bị trùng (không đọc DB).
- **Chưa có:** B ↔ Admin canonical-approved projection read path, true single session, analytics event receipt; Customer B có public feed dựa store riêng, chưa qua Web publication Admin; PR #44 chưa merge. Audit chỉ có HTTP GET và source review, **không thực thi E2E test mới**.
- **Trạng thái PR tại report:** PR #44 head `baee5e4ee0611c2760985df9b5641b5765b74c01` thêm handoff, DRAFT/UNMERGED; GitHub `mergeable` dao động, không phải review PASS.
- **Owner GO mới — TASK:** [#7071923255](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071923255) phê duyệt **increment đầu tiên**: B public đọc Admin-approved projection bằng adapter nhỏ + synthetic-only tests trên review branch; bắt buộc preservation plan, **không** đọc/đụng dữ liệu thật, retire routes, merge hoặc deploy. **TASK_POSTED, CODEX_RESULT_NOT_YET_VERIFIED**.
- **Khi kết quả tới:** đối chiếu route/contract/file SHA, negative tests unapproved/stale/revoked/private media/failed source/no fallback B store, ngăn ghi production; không nâng Phase 1 unified hay Phase 2 khi chỉ local test.

## 6.1 RESULT Owner GO #7071923255 — Admin projection → Customer public read (22/11/2026)

**TASK:** [Owner GO #7071923255](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071923255). **RESULT:** [Codex #7072170795](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072170795), **commit/PR HEAD** [`7aaa5a84579e8995329081cff254baf7f2fad438`](https://github.com/Haisala/sala-bmt-listings/commit/7aaa5a84579e8995329081cff254baf7f2fad438), [handoff tại đúng SHA](https://github.com/Haisala/sala-bmt-listings/blob/7aaa5a84579e8995329081cff254baf7f2fad438/docs/ISSUE39_LAST_72H_HANDOFF.md). [PR #44](https://github.com/Haisala/sala-bmt-listings/pull/44) OPEN/DRAFT/UNMERGED. Trạng thái `mergeable` từ GitHub là kỹ thuật và biến động, không phải Owner GO merge.

**Đã kiểm tra mã trên PR head:** `customer_web_v2/projection_reader.py` đọc chỉ artifact `SALA_PUBLIC_PROJECTION_PATH`, kiểm DTO công khai/quyền ảnh, ràng buộc identity/revision, từ chối nguồn không hợp lệ; `customer_web_v2/sala_web.py` chuyển GET `/api/listings`, detail, `/media` sang reader, trả HTTP 503 nếu thiếu artifact và **không fallback Customer SQLite**. `customer_web_v2/public_contract.py` làm validator dùng lại cho `property_memory/public_contract_adapter.py`; `admin/admin_server.py` bổ sung dọn preview stale khi Gate A thay đổi. **Đây là thay đổi source trên nhánh PR, không phải runtime deployed.**

**TEST_ONLY theo RESULT Codex:** synthetic integration 4/4 PASS, Customer Web 13/13 PASS, Admin public-release suite PASS; py_compile, ResourceWarning và diff check PASS. Các kiểm thử đã mô tả: chưa Gate A; chưa Web-publish; approved revision đúng ID/rights; stale revision; revoke/unpublish; private fields/media; replay; malformed/unavailable projection 503; import không ghi private root. Đối chiếu test và source ở commit trên; đây **không phải chứng nhận thử trên Mac runtime đang chạy**.

**Giữ nguyên:** B SQLite và Owner/login/intake/edit/review routes còn tồn tại để tránh mất dữ liệu, nhưng không là nguồn public feed ở *source mới*; chưa đọc/di chuyển/xóa dữ liệu thật, không merge/deploy/restart. Bản sản xuất/localhost cũ chưa áp cấu hình `SALA_PUBLIC_PROJECTION_PATH`. **Chưa chứng minh** một phiên đăng nhập, E2E runtime kết nối từ Admin tới Customer, traffic-event chiều về, bảo toàn DB/backup/restore thực nghiệm, staging HTTPS, READY_TO_SKIN, Phase 1 unified hoặc Phase 2.

**KẾT LUẬN:** `ACTUAL_CHANGE=IMPLEMENTED_TEST_ONLY`, `PR44_REVIEW=PARTIAL`, `PR_MERGE_GO=NO`, `PHASE_1_UNIFIED=NOT_VERIFIED`, `PHASE_2=NO_GO`.

**MỘT NEXT ACTION:** Owner xem xét kết quả kiểm thử cục bộ và quyết định có cấp quyền riêng cho cấu hình staging cô lập + E2E thật giữa Admin-approved projection và Customer Web hay chưa. Không tự mở gate, merge hoặc loại bỏ đường dữ liệu cũ.

## 6.2 Owner GO isolated HTTP E2E — TASK giao, CHƯA CÓ RESULT

- **TASK:** [Owner #7072250230](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072250230), kế thừa [RESULT source TEST_ONLY #7072170795](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072170795). Source baseline khi giao: PR #44 HEAD `7aaa5a84579e8995329081cff254baf7f2fad438`, DRAFT/UNMERGED, synthetic 4/4 + Customer 13/13 theo Codex. Codex bắt buộc xác minh HEAD lúc bắt đầu.
- **Được giao:** cấu hình **hai tiến trình synthetic HTTP** từ source review branch trong temp/private root tách riêng, chỉ 127.0.0.1 và cổng mới; kiểm Gate A → Web-publication → approved projection → Customer feed/search/detail/media và fail-closed, revoked/stale/rights/no fallback, cleanup, không đụng services thực :18876/:18877 và domain.
- **Tình trạng tại lúc cập nhật:** `TASK_POSTED`; **chưa có RESULT mới, chưa có biên bản chạy HTTP E2E, chưa chứng minh staging thật hoặc official Phase 2.** Không coi GO là PASS.
- **Lúc Codex báo:** đối chiếu TASK URL, commit HEAD/patch, process/port fingerprint, test matrix và HTTP status, temp-only data proof, không chạm runtime hoặc DB thật; lưu receipt tại đây rồi thay checkpoint Tầng 1. Nếu chỉ có in-process test thì không công nhận E2E HTTP; nếu sandbox bind bị chặn thì `BLOCKED` minh bạch.
- **Gate giữ nguyên:** `PHASE_1_UNIFIED=NOT_VERIFIED`; `PHASE_2=NO_GO`; `PR_MERGE_GO=NO`; chưa single-session, traffic chiều về, HTTPS/physical hoặc READY_TO_SKIN.

## 6.3 RESULT isolated HTTP E2E — `HTTP_BIND_BLOCKED` (22/11/2026)

- **TASK:** [Owner GO #7072250230](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072250230). **RESULT:** [Codex #7072294543](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072294543) — `STATUS=ISOLATED_HTTP_E2E_BLOCKED`. **PR #44 HEAD:** [`bf7ebca0353ad2bf74b169b3c11a65f1bd2e05c0`](https://github.com/Haisala/sala-bmt-listings/commit/bf7ebca0353ad2bf74b169b3c11a65f1bd2e05c0); [handoff theo SHA](https://github.com/Haisala/sala-bmt-listings/blob/bf7ebca0353ad2bf74b169b3c11a65f1bd2e05c0/docs/ISSUE39_LAST_72H_HANDOFF.md), PR OPEN/DRAFT/UNMERGED.
- **Bằng chứng quyền môi trường:** `lsof` read-only thấy PID 877 trên `:8765`, PID 31952 trên `:18876`, PID 89406 trên `:18877` ở trước/sau lượt kiểm. **Lần bind loopback ephemeral đầu tiên bị `PermissionError: [Errno 1] Operation not permitted`**; không có lần bind thứ hai. Đây là `HTTP_BIND_BLOCKED` do quyền, **không phải cổng bị chiếm** hoặc lỗi nghiệp vụ.
- **KHÔNG ĐƯỢC NHẬN PASS E2E:** chưa khởi động Admin/Customer sandbox, **không có HTTP request E2E**, mọi cạnh Gate A/publication/projection/feed/detail/media/stale/revoke `NOT_RUN`. `TEST_ONLY_RECORD_COUNTS=0`, `ARTIFACT_HASH=NOT_CREATED`; in-process synthetic tests cũ #7072170795 vẫn riêng biệt, không thể dùng thay E2E HTTP.
- **Safety theo báo cáo:** không app imports/creds, không dữ liệu thật, không writes/restarts/process changes, không DNS/tunnel/deploy/API calls, chỉ handoff doc commit và `git diff --check PASS`. **Không xác nhận tự kiểm runtime** ngoài receipt Codex.
- **Trạng thái hiện hành:** `PR_MERGE_GO=NO`, `PHASE_1_UNIFIED=NOT_VERIFIED`, `PHASE_2=NO_GO`; không chứng minh one-session, traffic/CTA return, staging HTTPS, READY_TO_SKIN.
- **Một việc ưu tiên tiếp theo:** Owner cấp/duyệt môi trường chạy Codex có quyền bind loopback TCP để **chạy lại đúng nhiệm vụ TEST_ONLY E2E cô lập**; tuyệt đối không sử dụng cổng đang chạy làm đường vòng hoặc nới quyền hệ thống khác. Không tự triển khai.

## 6.4 Owner scoped-loopback permission GO — TASK posted, no permission proof yet

- **TASK:** [Owner approval/request #7072339267](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072339267) to continue isolated HTTP E2E [#7072250230](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072250230); latest verified blocker [RESULT #7072294543](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072294543).
- **Known code baseline:** PR #44 head `bf7ebca0353ad2bf74b169b3c11a65f1bd2e05c0`, DRAFT/UNMERGED; real HTTP E2E `NOT_RUN`, bind permission `HTTP_BIND_BLOCKED`.
- **Owner decision only:** Codex may request **single-run, scoped `127.0.0.1` TCP bind permission** through approved execution context. GitHub issue post does **not** change Codex/macOS permissions; no runtime started, no test result after task post. If host approval unavailable, Codex must report exact user-action blocker, not silently work around sandbox.
- **Verification required for next RESULT:** actual granted permission, two separate sandbox server PIDs/ports bound, HTTP request/response matrix and negative tests, temp-only sources/media, existing processes untouched; `PR_MERGE_GO=NO`, `PHASE_2=NO_GO` until distinct Owner gate.

## 6.5 RESULT permission-granted loopback HTTP PARTIAL (22/11/2026)

- **TASK:** [Owner GO scoped-loopback #7072339267](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072339267) continuation of [HTTP E2E #7072250230](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072250230). **RESULT:** [Codex #7072568397](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072568397), `STATUS=ISOLATED_HTTP_E2E_PARTIAL`. **Run source HEAD:** `471f4f53cc3a76297c56fa47c2671416aafc7391`; **PR #44 current HEAD:** [`b798aa7374c7712ac90a1bcf23b9b3f50e1ba952`](https://github.com/Haisala/sala-bmt-listings/commit/b798aa7374c7712ac90a1bcf23b9b3f50e1ba952), [handoff at SHA](https://github.com/Haisala/sala-bmt-listings/blob/b798aa7374c7712ac90a1bcf23b9b3f50e1ba952/docs/ISSUE39_LAST_72H_HANDOFF.md); DRAFT/UNMERGED.
- **Quyền thực đã có một lượt:** scoped `require_escalated` accepted for *one invocation* of harness `scripts/test_issue39_isolated_http_e2e.py`; two actual loopback servers bound: Admin `127.0.0.1:56537` PID 20834 `GET /api/health=200`; Customer `127.0.0.1:56538` PID 20835 `GET /health=200`. **Đây là HTTP socket thật nhưng mới health/auth, không phải end-to-end nghiệp vụ.**
- **Customer synthetic login:** HTTP 200 nhưng harness kỳ vọng JSON key `authenticated`, trong khi source `customer_web_v2/sala_web.py` trả `{"ok": true}` (đã đối chiếu mã). Do vậy harness assertion **FAIL**, không phải server auth fail. Codex đã sửa đúng assertion trên commit `b798aa7` **NHƯNG CHƯA CHẠY LẠI**. Tránh coi mã vừa sửa là test PASS.
- **Các cạnh CHƯA CHẠY:** Admin intake/review/Gate A, Web-publication riêng, artifact handoff, Customer feed/search/detail/media, stale/revoke, negative malformed source/no SQLite fallback. `TEST_ONLY_RECORD_COUNTS=0` (ngoại trừ bootstrap owner synthetic), artifact public chưa được tạo; không tuyên bố isolated HTTP E2E PASS.
- **Isolation/safety theo report:** sandbox root `/private/tmp` mode 0700, no proxy/outbound/model calls; child PIDs đã dừng, temp files đã dọn; PID hiện hữu `:8765=877`, `:18876=31952`, `:18877=89406` không đổi. `py_compile` và `git diff --check` PASS. Không đọc/ghi customer/property/media thật, không merge/deploy/DNS/tunnel. `config SHA256=930a7061324affd96544b2197f004fa5269c17c86b88bd305f966bb9bcc0cb49`, source manifest SHA256 `c37f28a0a4eba745914309d266f780f1634cc153db652575fe48589020b82530`.
- **Gate:** `PR44_MERGE_GO=NO`, `PHASE_1_UNIFIED=NOT_VERIFIED`, `PHASE_2=NO_GO`; one-session, events/CTA back, release safety and READY_TO_SKIN chưa chứng minh.
- **ONE NEXT:** Owner chấp thuận **thêm một lượt quyền giới hạn** chạy harness đã sửa ở exact PR HEAD `b798aa7` và báo đầy đủ HTTP matrix. Quyền lượt trước đã dùng hết; trước GO riêng không tự chạy lại.

## 6.6 Owner GO repeated isolated HTTP E2E — TASK POSTED, no new test RESULT

- **Owner decision / TASK:** [#7072619842](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072619842) authorizes **repeated attempts without a new Owner GO per attempt** for *this same* isolated loopback TEST_ONLY HTTP E2E task; continuation of [PARTIAL RESULT #7072568397](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072568397).
- **Baseline:** [PR #44](https://github.com/Haisala/sala-bmt-listings/pull/44) `b798aa7374c7712ac90a1bcf23b9b3f50e1ba952` DRAFT/UNMERGED. Harness login-key assertion corrected after last attempt, **not rerun** at time of posting. Prior real socket proof two synthetic listeners and health 200; remaining Gate A → publication → feed/detail/media → revoke `NOT_RUN`.
- **Authorization vs permission:** Owner authorizes repeated scope-bound trials; **no evidence sandbox permission is automatically granted or persistent**. Codex must obtain allowed runtime permission via its execution mechanism, and must not disable sandbox or access real property DB/media/credentials/ports/services.
- **Acceptance:** actual HTTP E2E matrix+negative cases with process/source SHA, temp-only data, repeat run receipts, teardown proof and no real data; one Codex RESULT on Issue #39 when settled. **Status now `TASK_POSTED`, not `HTTP_E2E_PASS`.**
- **Gates:** `PR44_MERGE_GO=NO`, `PHASE_1_UNIFIED=NOT_VERIFIED`, `PHASE_2=NO_GO` until separate proof and Owner decision.

## 6.7 RESULT retry authorization blocked — cần Owner cấp quyền trong giao diện Codex (22/11/2026)

- **TASK:** [Owner GO chạy nhiều lượt #7072619842](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072619842). **RESULT:** [Codex #7072642067](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072642067): `STATUS=BLOCKED_PERMISSION_PENDING_USER_ACTION`.
- **Code/PR:** PR #44 `codex/issue39-source-reconciliation` HEAD `b798aa7374c7712ac90a1bcf23b9b3f50e1ba952`, DRAFT/UNMERGED. Harness đã sửa login assertion, `py_compile` và `git diff --check` PASS theo Codex, nhưng **không được rerun**.
- **Blocker:** Codex yêu cầu scoped `require_escalated` cho đúng lệnh `python3 scripts/test_issue39_isolated_http_e2e.py` (hai cổng loopback ephemeral, synthetic-only). Automatic approval review **từ chối** vì comment Owner trên GitHub không được chấp nhận là quyền elevated retry và grant trước đã dùng hết. `PERMISSION_NOT_GRANTED`: không có bind, HTTP request, child process, temp data mới. Đây không phải code E2E FAIL.
- **Giữ bằng chứng trước:** [PARTIAL #7072568397](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7072568397) là run duy nhất có loopback HTTP health 200 + Customer login HTTP 200, nhưng Gate A/Web publication/Customer projection/revoke vẫn `NOT_RUN`. Không gán `HTTP_E2E_PASS`.
- **Safety:** Codex nói không lách sandbox, không dùng listener thật `:8765/:18876/:18877`, không động dữ liệu thật, DNS/production hay dự án khác. Không có commit/mã thay đổi ở lần retry này.
- **Gates:** `PR_MERGE_GO=NO`, `PHASE_1_UNIFIED=NOT_VERIFIED`, `PHASE_2=NO_GO`.
- **ONE_NEXT_ACTION:** Owner phải thực hiện thao tác **phê duyệt elevated loopback riêng cho harness trong giao diện Codex**. Chỉ khi được cấp quyền thực mới tiếp tục chạy lại; không đăng thêm comment GO tương đương để kỳ vọng tự mở sandbox.

## 5. Quy tắc chứng minh/đổi chat

Mỗi kết quả mới cần ghi: `TASK_URL`, `RESULT_URL`, repo/branch/commit HEAD, file path/diff/test, loại dữ liệu (synthetic/real), môi trường (local/staging/production), trạng thái PASS/FAIL/UNKNOWN và giới hạn; chỉ bổ sung **delta** mới. Khi một mục đã có receipt thì **không tự test lại** trừ xung đột/hệ quả mới. Không coi 404 từ phạm vi kiểm tra hẹp là chức năng không tồn tại toàn hệ thống. Tài liệu này là mục lục, không thay link RESULT gốc.

**VỊ TRÍ:** SALA BĐS / FIRST WEB / Phase 1; PR #44 source integration đã đạt TEST_ONLY nhưng review PARTIAL. **BƯỚC TIẾP THEO DUY NHẤT:** Owner cấp quyền elevated loopback cho harness trong giao diện Codex theo RESULT #7072642067; comment GitHub chưa đủ mở quyền thực thi. Phase 2 NO_GO.
