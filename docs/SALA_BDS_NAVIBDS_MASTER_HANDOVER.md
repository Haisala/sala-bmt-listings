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
- Source static có `CFBundleExecutable=\$(EXECUTABLE_NAME)`, target dự kiến executable `NaviBDS`.
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

## 11. Mẫu báo cáo Codex

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
