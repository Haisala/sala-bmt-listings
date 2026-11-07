# SALA BĐS / NaviBDS — MASTER HANDOVER & CHECKPOINT

**Version:** 1.0  
**Updated:** 08/11/2026  
**Scope:** Sala BĐS/NaviBDS only. Không áp dụng cho Sala Media, Bright/Bridge, Code A, Quick Tunnel 8765, production hoặc Web Admin legacy.

## 1. Mục tiêu dự án

NaviBDS là app iPhone cho hệ thống Sala BĐS, kết nối staging backend để Owner đăng nhập và vận hành các chức năng quản lý BĐS. Mục tiêu hiện tại là xác minh chuỗi kết nối hoàn chỉnh trước khi sửa hoặc đổi mật khẩu.

## 2. Chuỗi hệ thống cần kiểm chứng

`NaviApp → build/install artifact → API config → HTTPS/Cloudflare → named tunnel → Code B backend → account/password source → auth response → cookie/session → protected API`

## 3. Nguồn chuẩn

- **Code B:** `codex/system-backend`; nguồn chuẩn hiện có cho NaviBDS/backend BĐS.
- **Code A:** Customer Web/frontend; không dùng làm nguồn NaviBDS.
- Worktrees có dirty/untracked content; không reset, merge, stash hoặc xoá khi chưa có bằng chứng và phê duyệt.
- Staging backend: local origin `127.0.0.1:18876`; public `https://staging.haisala.io.vn`; identity `staging/n02-staging`.
- App bundle: `vn.io.haisala.navi-bds`; static version/build trước đó: `0.1 / 1`.

## 4. Checkpoint hiện tại

| Gate | Trạng thái | Kết luận |
|---|---|---|
| Backend health | PASS | Local HTTP 200, đúng staging/n02-staging |
| Cloudflare/named tunnel | PASS | Connector active, public HTTPS health 200 |
| TLS/public route | PASS | System TLS/no-proxy, không có 530 hiện tại |
| Static auth contract | PASS | POST `/api/auth/login`, JSON `password`, 401/200 contract |
| Password source metadata | PASS một phần | File hiện diện, owner/permission phù hợp; chưa đọc giá trị |
| Owner password đúng/sai | UNKNOWN | Chưa được phép dùng/hiển thị password |
| NaviApp đang cài | UNKNOWN | iPhone Hải Sala Home unavailable; không có artifact hiện tại trong Code B |
| Installed bundle/executable/codesign | UNVERIFIED | Chưa nhìn thấy app vật lý trên iPhone |
| Session/protected API | UNVERIFIED | Static cookie contract có, E2E chưa chạy |
| UI error mapping | PARTIAL | 401 bị gom thành thông báo chung |

## 5. Bằng chứng NaviApp quan trọng

- Issue #16 từng ghi nhận lỗi cài đặt `MissingBundleExecutable` trên một artifact NaviBDS.
- Artifact lỗi thiếu `CFBundleExecutable` dù Mach-O executable `NaviBDS` vẫn tồn tại.
- Source static có `CFBundleExecutable=$(EXECUTABLE_NAME)`, target dự kiến executable `NaviBDS`.
- Có khả năng artifact stale/inconsistent, nhưng **chưa được xác nhận là bản đang cài trên iPhone**.
- Vì thiết bị unavailable và không có `.app/.ipa/.xcarchive` hiện tại, chưa được phép kết luận app lỗi hay password lỗi.

## 6. Backend/auth hiện tại

- Backend nhận `POST /api/auth/login` với JSON field `password`.
- Synthetic invalid-auth đã trả HTTP `401`; chứng minh handler auth đang nhận request.
- Static contract: thành công trả authenticated/owner_id/expires_at và `Set-Cookie`; thất bại trả `401 AUTH_FAILED`.
- NaviApp hiện map HTTP 401 thành câu chung: “Mật khẩu Owner không đúng hoặc tài khoản staging chưa sẵn sàng”. Vì vậy UI không phân biệt password, account hoặc allowlist.
- `403` được map thành remote-access-denied; lỗi khác map thành server error.
- Không có bằng chứng hiện tại để kết luận `OWNER_PASSWORD_INVALID`, `ACCOUNT_UNAVAILABLE` hoặc `BACKEND_UNAVAILABLE`.

## 7. Diagnostic/event history — không phải trọng tâm chính

- Auth diagnostic đã nhiều lần arm/rollback an toàn.
- Activation/marker/private-root presence từng PASS; path static khớp.
- Writer synchronous được gọi trong `/api/auth/login`, nhưng trước đây trả `None`, nuốt lỗi.
- Contract thử nghiệm cho thấy `WRITE_FAILED`; `_consume()` fail trước khi tạo event. Patch đã rollback.
- Không có consumer/poll loop; sink là synchronous-only.
- Diagnostic chỉ là công cụ phụ, không được dùng để thay thế việc xác minh NaviApp.
- Chưa được login Owner lại, chưa đổi/reset password.

## 8. Quy tắc làm việc bắt buộc

1. Luôn kiểm tra NaviApp/artifact trước khi kết luận backend hoặc password.
2. Audit theo gate; gate đầu tiên FAIL/UNKNOWN phải được xử lý trước.
3. Không đổi password, reset account hoặc rebuild app khi chưa có bằng chứng root cause.
4. Không đọc/hiển thị password, hash, token, cookie, request body hoặc full environment.
5. Không dùng `--insecure`; public kiểm tra bằng system TLS/no-proxy.
6. Không bật cloudflared debug vì có thể lộ URL/header.
7. Mọi thay đổi staging phải có backup, controlled reload, kiểm tra health/listener và rollback.
8. Giữ nguyên dirty worktree và artifact cũ.
9. Mỗi nhiệm vụ Codex phải cập nhật đầy đủ Issue #16 và trả chat ngắn theo mẫu STATUS/KEY_RESULT/NEXT.
10. Sau mỗi checkpoint quan trọng, cập nhật file handover này và bản sao GitHub.

## 9. Việc đang chờ — chỉ một bước

Khôi phục **read-only visibility của iPhone Hải Sala Home** và xác minh app đang cài:

- Bundle ID, version/build, provenance.
- `CFBundleExecutable`.
- Mach-O executable/architecture.
- Codesign/provisioning.
- API endpoint/environment staging.

Không build, install, login, đổi password, reset pairing hoặc sửa backend trước khi gate này PASS.

## 10. Kết luận hiện tại

`NO_CONFIRMED_ROOT_CAUSE`

Backend, tunnel, HTTPS và static auth contract hiện PASS. Điểm mù đầu tiên là NaviApp artifact/bundle đang cài trên iPhone. Chưa có bằng chứng đủ để nói password sai. Sau khi app PASS mới kiểm tra lại login và session.

## 11. Bức tranh toàn bộ dự án Sala BĐS

### 11.1 Mục tiêu kinh doanh

Xây một hệ thống BĐS Buôn Ma Thuột do Owner duyệt thủ công ở các điểm quan trọng:

`Nguồn tin Zalo/Facebook/cá nhân → AI chuẩn hóa/chống trùng → Owner duyệt → Property Master → Web/App → phân phối nội dung → khách hàng tiềm năng`

Nguyên tắc: AI hỗ trợ thu thập, chuẩn hóa và phân phối; Owner giữ quyền duyệt tin, giá, trạng thái và nội dung công khai.

### 11.2 Khu vực/sản phẩm ưu tiên

- Thôn 8 Cư Êbur.
- Tân An, đặc biệt dữ liệu theo mét dài.
- EcoCity.
- Trung tâm Buôn Ma Thuột.
- Sản phẩm đã có dữ liệu: Nhà xanh cô-banh Cư Êbur, Nhà TDP 8, đất TDP4, shophouse EcoCity.

### 11.3 Các bề mặt sản phẩm

- **NaviBDS iPhone:** quản lý, duyệt và theo dõi vận hành BĐS.
- **Customer Web:** Home/Feed/Search/Filter/Detail/CTA, mobile-first.
- **Backend Code B:** auth, staging API, dữ liệu và các endpoint quản trị.
- **Property Master:** nguồn dữ liệu chuẩn sau khi Owner duyệt.
- **Kênh phân phối:** Facebook, TikTok, Zalo, Google và AI Search; không tự động gửi khi chưa có quyền/điều kiện phù hợp.

### 11.4 Tiêu chuẩn giao diện

- Mobile-first, ưu tiên iPhone 15 Pro Max.
- Ảnh lớn, rõ; CTA Gọi/Zalo nổi bật.
- Filter nhanh, chip “Vuốt →” dễ hiểu.
- Ẩn map/video nếu chưa có dữ liệu thật.
- Việt hóa đầy đủ.
- Không redesign giao diện đã duyệt nếu không có yêu cầu mới.

### 11.5 Quy tắc nội dung tin BĐS

Thứ tự caption chuẩn:

`Vị trí – Giá bán – Nhà/Đất → Ưu điểm → Thông tin chi tiết → CTA`

Không tự thêm giá, pháp lý, diện tích hoặc tiện ích chưa có bằng chứng.

## 12. Kiến trúc vận hành

```text
Nguồn tin
  → Intake/chuẩn hóa
  → chống trùng
  → Owner review
  → Property Master
  → Backend/API
  → NaviBDS + Customer Web
  → xuất bản/phân phối
```

Các lớp phải kiểm tra độc lập: dữ liệu, API, auth, session, app artifact, public route và kênh phân phối.

## 13. Hạ tầng và môi trường

- MacBook là host phát triển/vận hành staging.
- Code B là nguồn NaviBDS/backend chuẩn hiện tại.
- Staging origin: `127.0.0.1:18876`.
- Public staging: `https://staging.haisala.io.vn`.
- Named tunnel staging: connector riêng, không dùng Quick Tunnel 8765 cho kết luận staging.
- Public health phải kiểm bằng system TLS/no-proxy.
- Local/public health cần đúng `staging/n02-staging`.
- Production và dữ liệu thật nằm ngoài phạm vi audit hiện tại.

## 14. Auth và tài khoản staging

- Auth endpoint hiện tại: `POST /api/auth/login`.
- Request chính: JSON field `password`.
- Thành công theo static contract: authenticated, owner_id, expires_at và Set-Cookie.
- Thất bại hiện trả 401 AUTH_FAILED; UI đang gom nhiều nguyên nhân thành một thông báo.
- Password source là file plaintext staging với permission hạn chế; giá trị không được đọc/hiển thị trong ChatGPT/GitHub.
- Chưa xác minh giá trị password Owner.
- Chưa thực hiện reset/rotate password.
- Khi cần reset phải có quy trình staging-only, backup, nhập ẩn cục bộ, verify rollback và không ghi secret vào log.

## 15. NaviApp build/install runbook

Trước mỗi kết luận login phải xác minh:

1. iPhone Hải Sala Home được Xcode/CoreDevice nhận diện.
2. Bundle ID là `vn.io.haisala.navi-bds`.
3. Version/build đúng artifact dự kiến.
4. `CFBundleExecutable` tồn tại và là `NaviBDS`.
5. Executable là iOS Mach-O đúng architecture.
6. Codesign/provisioning hợp lệ.
7. API base URL là staging HTTPS.
8. Artifact có provenance rõ ràng, không phải DerivedData cũ/stale.

Lịch sử đã ghi nhận artifact thiếu `CFBundleExecutable` dù Mach-O tồn tại. Đây là gate phải giải quyết trước password.

## 16. Lịch sử checkpoint quan trọng

| Giai đoạn | Kết quả |
|---|---|
| Staging local backend | Health 200, identity đúng |
| Public tunnel ban đầu | Từng HTTP 530 vì connector chưa active |
| Named tunnel riêng | Đã tạo/load, public health ổn định |
| Public TLS harness | urllib lỗi proxy/CA; system TLS curl PASS |
| ACK/PID/TTL | Nhiều lần PASS/rollback do TTL/PID stale |
| Synthetic auth | HTTP 401 PASS |
| Auth diagnostic | Không tạo event; writer/activation failure chưa phải root cause login |
| Synchronous writer contract | Patch thử nghiệm rollback; `_consume()` fail trước event |
| Full connectivity audit | Backend/tunnel/static auth PASS; NaviApp installed artifact UNKNOWN |

## 17. Quyết định và điều chưa được phép kết luận

### Đã xác nhận

- Backend staging đang sống.
- Public route hiện hoạt động.
- Auth handler nhận request.
- Synthetic invalid auth trả 401.
- Static app config trỏ staging.

### Chưa xác nhận

- App đang cài trên iPhone có đúng artifact không.
- Password Owner đúng hay sai.
- Account/allowlist có sẵn sàng không.
- Session sau login có hoạt động không.
- Protected API sau login có hoạt động không.

### Không được suy diễn

- HTTP 401 synthetic không chứng minh password Owner sai.
- Health 200 không chứng minh login thành công.
- Writer diagnostic lỗi không chứng minh auth backend lỗi.
- App hiển thị thông báo chung không chứng minh password sai.

## 18. Quy trình làm việc chuẩn ChatGPT–GitHub–Codex

1. ChatGPT đọc handover và Issue #16.
2. ChatGPT xác định **một gate duy nhất** cần xử lý.
3. ChatGPT gửi prompt có phạm vi, cấm vượt dự án và tiêu chí PASS/FAIL.
4. Codex thực hiện, bảo toàn dirty worktree và cập nhật Issue #16.
5. Codex trả chat ngắn: STATUS, KEY_RESULT, NEXT.
6. ChatGPT đọc kết quả, đối chiếu với handover.
7. Checkpoint quan trọng cập nhật đồng thời Library và GitHub.
8. Không chuyển gate khi gate trước còn UNKNOWN nếu chưa có lý do rõ ràng.

## 19. Master next action

**Chỉ làm một việc:** khôi phục read-only visibility của iPhone Hải Sala Home và xác minh NaviApp artifact đang cài. Không build/install/login/đổi password trước khi gate này PASS.

## 20. Mẫu báo cáo Codex

```text
STATUS: PASS / PARTIAL / BLOCKED / FAIL

KEY_RESULT:
- [gate đã kiểm tra]
- [bằng chứng chính]
- [root cause hoặc UNKNOWN]

NEXT:
[một hành động duy nhất]

Đã cập nhật chi tiết trên GitHub Issue #16.
Hãy đọc Issue để phân tích bước tiếp theo.
```
