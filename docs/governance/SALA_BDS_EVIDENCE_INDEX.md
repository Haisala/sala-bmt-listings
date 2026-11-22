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

## 5. Quy tắc chứng minh/đổi chat

Mỗi kết quả mới cần ghi: `TASK_URL`, `RESULT_URL`, repo/branch/commit HEAD, file path/diff/test, loại dữ liệu (synthetic/real), môi trường (local/staging/production), trạng thái PASS/FAIL/UNKNOWN và giới hạn; chỉ bổ sung **delta** mới. Khi một mục đã có receipt thì **không tự test lại** trừ xung đột/hệ quả mới. Không coi 404 từ phạm vi kiểm tra hẹp là chức năng không tồn tại toàn hệ thống. Tài liệu này là mục lục, không thay link RESULT gốc.

**VỊ TRÍ:** SALA BĐS / FIRST WEB / Phase 1 / PR #44 review PARTIAL; dữ liệu quản trị GitHub only. **BƯỚC TIẾP THEO DUY NHẤT:** áp dụng khóa Owner “giữ mã hữu ích, không kích hoạt” trong đánh giá scope của PR #44, rồi xác minh các blocker integration trước khi Owner quyết định merge/Phase 2.
