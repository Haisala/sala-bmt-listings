# SALA BĐS — lưu trữ mô tả Issue #39 trước khi chuẩn hóa 3 tầng

**Snapshot:** 22/11/2026 (Asia/Ho_Chi_Minh). **Nguồn:** https://github.com/Haisala/sala-bmt-listings/issues/39. Bản sao giữ nguyên nguyên văn nội dung mô tả Issue trước lần thay đổi này; đây là LỊCH SỬ, không phải nguồn tình trạng mới nhất. Mỗi quyết định/receipt có link gốc của nó.

---

<!-- SALA_BDS_CURRENT_MASTER_MAP_START -->
# SƠ ĐỒ TỔNG HIỆN HÀNH — SALA BĐS / FIRST WEB / ISSUE #39

**BẮT BUỘC GIỮ:** [MASTER 5 PHASE #7058182954](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7058182954) + [một active checkpoint / sơ đồ cuốn chiếu #7062626669](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7062626669). Không dùng tên P0.9/P1.1 thay cho Phase 1–5. **Thiết kế ≠ đã code ≠ local PASS ≠ staging PASS ≠ production PASS.**

**OWNER LOCK MỤC TIÊU:** **Xây xong First Web lõi + quản trị + bảo trì trước → sẵn sàng ráp vỏ ChatSite đã duyệt mà không xây lại backend/kho dữ liệu → kiểm thử/phát hành Website chính → Owner nghiệm thu → sau đó mới phát triển các chức năng SALA BĐS khác.** Không ép xây toàn AI9/CRM/Social/Marketing trước khi First Web live. Vỏ hiện tại **để sau**, nhưng đã có data/UI contract để ráp nhanh và sạch. [Owner bổ sung kiến trúc #7066721682](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066721682).

```text
                     SALA BĐS — HỆ THỐNG DUY NHẤT
             Một tài khoản · Mật khẩu cũ · MỘT PHIÊN ĐĂNG NHẬP
                                 │
                                 ▼
                    PROPERTY MASTER CHUNG
     Soạn/sửa tin · Ảnh · Giá · Lịch sử · AI/Review · Gate A
                                 │
                                 ▼
               FIRST WEB ADMIN (PHÂN HỆ TRONG SALA BĐS)
           ┌─────────────────────┴────────────────────┐
           │                                          │
    A. QUẢN TRỊ XUẤT BẢN                      B. VẬN HÀNH WEBSITE
    Chọn BĐS nguồn chung                      Dev → Staging → Test
    Duyệt đăng riêng Gate A                   Version/SHA/manifest
    Cập nhật / Gỡ đăng                        Backup/restore/data safety
    Trạng thái + audit                        Owner release approval
           │                                  Deploy/rollback/receipts
           │                                          │
           └──────────────┬───────────────────────────┘
                          ▼
                    CUSTOMER WEB / haisala.io.vn
           Public read-only · Approved projection only
             Home · Feed · Search · Detail · Media · CTA
                          │
                          ▼
             TRAFFIC / DETAIL VIEW / CTA → SALA BĐS
             (Events + Owner analytics, không sửa facts)

             RELEASE BẢO TOÀN DỮ LIỆU:
     Code release versioned ≠ Property Master/ảnh/events
     Staging data riêng, immutable approved release,
     migration + backup/restore có thử, rollback ứng dụng
     KHÔNG ghi đè dữ liệu phát sinh trong sản xuất

              FIRST_WEB_CORE_READY_TO_SKIN (MỐC MỤC TIÊU)
   ← Chỉ đạt khi chung login/core/publish/traffic/contracts +
     cơ chế phát hành & bảo toàn dữ liệu có kiểm thử phù hợp
                          │
           RÁP VỎ CHATSite OWNER ĐÃ DUYỆT (LÀM SAU CORE)
                          │
           QUA ĐÚNG PHASE/GATES → LIVE → OWNER ACCEPTANCE
                          │
             MỚI MỞ CÁC PHÂN HỆ SALA BĐS KHÁC
         (AI Content / CRM / Social / Market / Growth ...)
```

## MASTER 5 PHASE — TRẠNG THÁI THỰC TẾ (KHÔNG NÂNG TỪ THIẾT KẾ)

```text
PHASE 1 — LOCAL CORE → WEB + ARTIFACT SAFETY
  Code B Core→projection→Web + Analytics        PASS_LOCAL (scope cũ, TEST_ONLY)
  GitHub PR #40/#42/#43                         MERGED (scope riêng)
  Architecture one-login/one-canonical/admin   CHƯA PASS tích hợp
  Release/maintenance data-safety contract     OWNER LOCK DESIGN, CHƯA TEST ĐỦ
  ACTIVE CHECKPOINT DUY NHẤT:
    NEW_CUSTOMER_WEB_BUILD_AND_APEX_REPLACE
    └─ PR44_REVIEW_AND_LAST72H_HANDOFF       OWNER GO #7071362357
       Code B → nhánh sạch, 33 file, 7 suites    DRAFT PR READY
       PR #44 head 7527037 · mergeable=true     UNMERGED
       Review nguồn/bảo mật/contract             CHỜ CODEX
       Khôi phục bối cảnh 19–22/11 từ GitHub     CHỜ CODEX
       Lưu handoff docs/ISSUE39_LAST_72H_HANDOFF.md trên PR #44
       không merge, không runtime/deploy
  GATE PHASE 2: nguồn tích hợp/allowlist rõ,
     staging độc lập, exact SHA + Owner GO

PHASE 2 — STAGING PREP / CUTOVER                NO_GO / NOT_STARTED
  Staging cô lập; Auth/HTTPS, version SHA, backup/restore,
  migration/rollback; không đọc/ghi production data

PHASE 3 — PHYSICAL IPHONE + DESKTOP E2E         NOT_STARTED
  One login, publish/revoke, page/CTA/traffic,
  giao diện đã ráp, đồng bộ trạng thái/data & counters

PHASE 4 — PRODUCTION READINESS                 NOT_STARTED
  Artifact/release SHA, an toàn dữ liệu, rollback có test,
  bảo mật/monitoring, dossier cho Owner

PHASE 5 — RELEASE + OWNER ACCEPTANCE            NOT_STARTED
  Deploy đúng SHA sau Owner GO; verify apex HTTPS;
  dữ liệu không mất, public correct, Owner nghiệm thu
```

**PHÂN BIỆT 2 LOẠI PUBLISH:** `Publish BĐS` là Owner cho phép một sản phẩm được hiển thị (Gate nghiệp vụ). `Deploy Web` là Owner duyệt phiên bản phần mềm mới (Gate phát hành), tuyệt đối không đồng nhất.

**BẰNG CHỨNG ĐANG CÓ:** [Mới nhất: Codex source reconciliation #7066860840](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066860840) → [Draft PR #44](https://github.com/Haisala/sala-bmt-listings/pull/44): 33 file, source Admin/Property Memory từ Code B được đưa vào nhánh riêng trên main \`83394a9\`; 7 bộ synthetic test PASS, Code A/B giữ nguyên, **chưa merge**; hiện GitHub PR API báo \`mergeable=true\` (đã thay đổi so với \`false\` trong báo cáo Codex, không coi là code-review PASS). **Còn thiếu one-session, canonical live read, traffic end-to-end, release safety nghiệm thu và HTTPS.** [Phase 1 Code B PASS_LOCAL #7058638110](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7058638110); [local host + password cũ 19/19 #7066142822](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066142822), **chỉ cùng mật khẩu chứ chưa chứng minh dùng chung phiên đăng nhập**; [P1.1 source mismatch #7066438757](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066438757); [nhiệm vụ hiện hành #7066590041](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066590041); [khóa vận hành & ready-to-skin #7066721682](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066721682). **Hiện `haisala.io.vn` vẫn maintenance theo chứng cứ gần nhất, không phải Website mới đã live.**

**QUY TẮC TRIỂN KHAI:** code Website có thể thay khi deploy, **không được xóa/ghi đè Property Master, ảnh gốc, sản phẩm/phiên bản duyệt, event/traffic đang chạy**. Backup/restore và rollback phải thực nghiệm; rollback code không được tự khôi phục DB cũ làm mất giao dịch mới. UI ChatSite không viết lại core. Giữ workflow Pages allowlist, không upload repo root.

**LỆNH HIỆN HÀNH:** [Owner GO PR #44 review + last-72h durable handoff #7071362357](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7071362357). Đây là một lượt review tiếp nối checkpoint, **không phải mở Phase mới**. Codex phải lưu bối cảnh ba ngày từ nguồn GitHub + Code B vào file trên PR #44 và một comment Issue, tránh mất tiến độ khi phiên Codex kết thúc. Comment đã đăng **không tự chứng minh Codex session đã chạy/nhận lệnh**.

**QUYỀN & ONE NEXT ACTION:** Quyết định mới về **First Web Admin gồm Quản trị xuất bản + Vận hành/Bảo trì Website, chuẩn bị READY_TO_SKIN, không làm mất dữ liệu khi deploy** đã khóa trong [#7066721682](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066721682) và sơ đồ này; đây là **DESIGN LOCK, CHƯA PHẢI ĐÃ TRIỂN KHAI**. Kết quả Codex [#7066860840](https://github.com/Haisala/sala-bmt-listings/issues/39#issuecomment-7066860840) đã đáp ứng phần tạo nhánh review của #7066590041. **MỘT VIỆC ƯU TIÊN**: Codex review [PR #44](https://github.com/Haisala/sala-bmt-listings/pull/44) **và khôi phục handoff ba ngày** trên chính PR, theo provenance, file ownership, dependency, source–canonical authority và bảo mật trước mọi merge; \`mergeable=true\` chỉ là điều kiện Git kỹ thuật. Không tự merge/cutover, không giao thêm task Codex song song hay thay DNS/tunnel/DB thật. Sau review mới quyết định phần tích hợp còn thiếu và gate Phase 2. Không chạm SALA Media, NaviBDS, Admin staging hay dữ liệu thật ngoài quyền.

<!-- SALA_BDS_CURRENT_MASTER_MAP_END -->

---

## HỒ SƠ AUDIT GỐC (LƯU LÀM THAM CHIẾU — KHÔNG PHẢI CHECKPOINT HIỆN HÀNH)

# SALA BĐS — Web First Readiness Audit (READ-ONLY)

## Quyết định Owner / mục tiêu
Owner đã duyệt kiến trúc tổng thể SALA BĐS và lộ trình Web First, yêu cầu Codex **kiểm kê hiện trạng thực tế trước khi phát triển thêm**. Đây là audit (kiểm tra đối chiếu), **không phải lệnh triển khai**. Phân biệt kiến trúc đã duyệt với code, tests, staging và production thực sự được chứng minh.

## Nguồn phải đọc trước
- Repository hiện tại `Haisala/sala-bmt-listings`: tree, docs, code, tests, scripts, cấu hình triển khai và lịch sử thay đổi liên quan.
- Issue #37 (staging tunnel/Gate A), Issue #38 (OpenAI Integration/AI Cost Governance), các issue/PR khác thực sự liên quan; đọc checkpoint/comment mới nhất và dẫn URL cụ thể.
- Bản thiết kế Owner chốt: SALA BĐS Master System & Web First Blueprint V1.0. Nếu file PDF chưa có trong repo/workspace, **ghi DESIGN_SOURCE_NOT_ACCESSIBLE và xin đường dẫn**, không tự khẳng định đã đối chiếu chính xác bản PDF. Có thể dùng các mục tiêu đã tóm tắt dưới đây làm khung tạm.

## Phạm vi audit
1. Customer Web: Home, Feed, Search, Filter, Detail, CTA, Footer; giao diện mobile-first; tình trạng hero/footer và visual handoff đã chốt; kết nối dữ liệu thật.
2. Admin hiện hữu và Admin Lite: Dashboard, danh sách tin, chi tiết & media, review/approval/publication; phân quyền, auth, trạng thái staging.
3. Luồng Intake → AI9 (phân biệt lớp nghiệp vụ với API calls) → dedupe → media rights/citations → Gate A → Property Core canonical → public Website; cập nhật giá/trạng thái/ảnh; bảo vệ dữ liệu gốc.
4. Hạ tầng và kiểm thử: APIs, tests, health, staging tunnel, DNS/TLS, backup, logging, monitoring, rollback; nêu rõ môi trường từng bằng chứng.
5. Các khối ngoài Web First (Content Studio, marketing, CRM, market analytics, AI Operator) chỉ ghi nhận ranh giới/điểm nối đã có, **không mở rộng phạm vi triển khai**.
6. Đối chiếu trùng lặp: tái sử dụng thành phần hiện có, phát hiện hai nguồn dữ liệu hoặc API tương đương, dependency thừa, code chết; không tự xóa hoặc tái cấu trúc.

## Đầu ra bắt buộc — comment ngay tại issue này
- Bảng từng chức năng: `DESIGNED / CODE_EXISTS / TESTED / STAGING_VERIFIED / PRODUCTION_VERIFIED / BLOCKED / UNKNOWN` (có thể nhiều cột độc lập), dẫn **file path + line/commit + test output + issue/comment URL** tương ứng; không gộp test local thành production PASS.
- Sơ đồ luồng hiện trạng (as-is) và khoảng cách so với kiến trúc mục tiêu (to-be); đánh dấu phần chưa kiểm chứng.
- Danh sách thiếu sót theo mức độ P0/P1/P2; phân biệt lỗi, thiếu bằng chứng, và tính năng chưa triển khai.
- Tình trạng 8 gate Web First: 0 Audit, 1 Visual Master, 2 Website, 3 Admin Lite, 4 Property Core integration, 5 E2E, 6 production readiness, 7 Owner acceptance; PASS chỉ khi đủ bằng chứng.
- Rủi ro phá vỡ các thành phần đã duyệt và khuyến nghị **một việc ưu tiên tiếp theo**, kèm tiêu chí nghiệm thu và phạm vi thay đổi tối thiểu.
- Kết luận rõ `AUDIT_COMPLETE / PARTIAL / BLOCKED`, commit SHA đã kiểm, thời điểm và môi trường; mọi mục thiếu bằng chứng phải ghi UNKNOWN.

## Ranh giới quyền hạn — nghiêm ngặt
READ-ONLY trên code và runtime: không sửa file, không commit/PR, không cài package, không chạy migration, không tạo/sửa intake/review/property, không approve/publish, không gọi API AI trả phí, không thay DNS/tunnel, không chạm production, không động SALA Media/NaviBDS hoặc hệ thống khác. Chỉ chạy kiểm tra không phá hủy nếu an toàn; không thử nghiệm ghi dữ liệu. **Chỉ được ghi báo cáo audit vào issue này**. Không tự giao tiếp nhiệm vụ triển khai tiếp theo.

## Khung thiết kế đã chốt (tóm tắt, chưa thay thế PDF)
Một Property Core chuẩn; Website + Admin Lite là Phase 1; Owner duyệt giao diện; ChatGPT Sites tạo mẫu, Codex tích hợp đúng thiết kế; GitHub là nguồn bằng chứng; Gate A và quyền xuất bản phải được bảo vệ; ưu tiên reuse thay vì xây mới. Logo/brand SALA - BĐS navy/trắng; mobile-first iPhone 15 Pro Max.
