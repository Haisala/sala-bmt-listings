#!/usr/bin/env node
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const source = fs.readFileSync("admin/admin_server.py", "utf8");
const workflowSource = fs.readFileSync("admin/intake_workflow_ui.py", "utf8");
const consentUiSource = fs.readFileSync("admin/one_touch_ui.py", "utf8");
assert.match(workflowSource, /Thông tin sản phẩm/);
assert.match(workflowSource, /XÁC NHẬN & TIẾP TỤC/);
assert.match(workflowSource, /owner-variant-picker/);
assert.match(workflowSource, /TẠO LẠI 5 CONTENT THEO DỮ KIỆN MỚI/);
assert.match(source, /refresh_requested = payload\.get\("refresh_content"\) is True/);
assert.match(source, /closest\('#missing-fields-dialog'\)\?\.querySelector\('#missing-fields-status'\)/,
  "pre-dispatch failures must be visible inside the dialog");
assert.match(source, /window\.openMissingFields\?\.\(pendingSavedIntakeId/,
  "new Intake opens rights/consent UI before any generation request");
assert.match(source, /contentAttemptState==='SAFE_PREFLIGHT_FAILURE'.+TIẾP TỤC CÙNG INTAKE/,
  "confirmed pre-dispatch failures can continue on the same saved Intake after a new explicit revision");
assert.doesNotMatch(source, /createContent\(pendingSavedIntakeId,saveIntake\)/,
  "Intake save is not an implicit Content generation request");
assert.match(consentUiSource, /Quyền nguồn &amp; tạo Content/);
assert.match(consentUiSource, /TẠO 5 CONTENT/);
assert.match(consentUiSource, /Quay lại/);
assert.match(consentUiSource, /record\?\.contentConsent\?\.external_text_processing_consent===true/,
  "only server-projected, revision-scoped consent is reused");
assert.match(consentUiSource, /Lần trước dừng trước khi gọi AI/);
assert.match(consentUiSource, /AI-proposed facts are reviewed on Screen A/);
assert.doesNotMatch(consentUiSource, /BỔ SUNG & TIẾP TỤC TẠO CONTENT/);
const start = source.indexOf("const contentRequestsInFlight=new Set();function contentRequestKey");
assert.notEqual(start, -1, "actual Intake Content UI handler exists");
const end = source.indexOf("\nconst isImageFile=", start);
assert.notEqual(end, -1, "actual Intake Content UI handler has a bounded source block");
const handlerSource = source.slice(start, end);

function makeContext({ fetch, localStore = new Map(), opened = [], loadReview = async () => {}, onBatch = () => {} }) {
  const makeStorage = (store) => ({
    getItem: (key) => store.get(key) ?? null,
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: (key) => store.delete(key),
  });
  const status = { className: "", textContent: "" };
  const context = {
    AbortController,
    console,
    crypto: { randomUUID: () => "TEST_ONLY-uuid-1234-5678" },
    fetch,
    localStorage: makeStorage(localStore),
    sessionStorage: makeStorage(new Map()),
    window: {
      clearTimeout() {},
      intakeRecords: [{ id: "TEST_ONLY-intake-resume", inputRevision: 1 }],
      loadReview,
      onContentBatchCreated: onBatch,
      openMissingFields: (...args) => opened.push(args),
      setTimeout() { return 1; },
    },
    $: () => status,
    encodeURIComponent,
    load: async () => {},
    loadReviews: async () => {},
  };
  vm.createContext(context);
  vm.runInContext(`${handlerSource}\nglobalThis.runCreateContent=createContent;`, context);
  return { context, status, localStore, opened };
}

async function main() {
  let posts = [];
  const prerequisiteContext = makeContext({
    localStore: new Map(),
    fetch: async (url, options) => {
      posts.push({ url, body: JSON.parse(options.body) });
      return { status: 409, json: async () => ({
        error: "CONTENT_PREREQUISITES_REQUIRED",
        missing_fields: ["transaction_type"],
        missing_evidence: ["rights_status"],
        conflicts: [], provider_calls: 0, published: false,
      }) };
    },
  });
  const alreadyDisabled = { disabled: true, closest: () => null };
  assert.equal(await prerequisiteContext.context.runCreateContent("TEST_ONLY-intake-resume", alreadyDisabled), false);
  assert.equal(posts.length, 1, "submit path posts even though its caller disabled the button");
  assert.equal(posts[0].body.external_text_processing_consent, false, "first request cannot imply external paid consent");
  assert.equal(posts[0].body.source_media_rights_confirmed, false, "first request cannot imply source/media rights confirmation");
  assert.equal(prerequisiteContext.opened.length, 1, "server blockers open the same-Intake prerequisite sheet");
  assert.equal(prerequisiteContext.opened[0][0], "TEST_ONLY-intake-resume");
  assert.equal(alreadyDisabled.disabled, false, "button is re-enabled after a blocked response");
  assert.equal(prerequisiteContext.localStore.size, 0, "a confirmed zero-dispatch prerequisite block may rotate the key");

  let modalText = "";
  const modalStatus = { className: "", set textContent(value) { modalText = value; }, get textContent() { return modalText; } };
  const failedPreflight = makeContext({
    fetch: async () => ({ status: 422, json: async () => ({
      error: "CONTENT_INPUT_LIMIT_EXCEEDED", provider_calls: 0, dispatch_started: false, published: false,
    }) }),
  });
  const modalButton = { disabled: false, closest: (selector) => selector === "#missing-fields-dialog"
    ? { querySelector: () => modalStatus } : null };
  assert.equal(await failedPreflight.context.runCreateContent("TEST_ONLY-intake-resume", modalButton, true, true), false);
  assert.match(modalText, /CONTENT_INPUT_LIMIT_EXCEEDED/);
  assert.match(modalText, /AI chưa được gọi/);
  assert.equal(failedPreflight.localStore.size, 0, "confirmed zero-dispatch error allows safe key rotation");

  const consentContext = makeContext({
    fetch: async (_url, options) => {
      assert.equal(JSON.parse(options.body).external_text_processing_consent, true);
      assert.equal(JSON.parse(options.body).source_media_rights_confirmed, false);
      return { status: 409, json: async () => ({
        error: "CONTENT_SOURCE_MEDIA_RIGHTS_CONFIRMATION_REQUIRED", provider_calls: 0, published: false,
      }) };
    },
  });
  await consentContext.context.runCreateContent("TEST_ONLY-intake-resume", { disabled: true, closest: () => null }, true);
  assert.equal(consentContext.opened.length, 1, "consent gate opens the same-Intake confirmation sheet");

  const approvedContext = makeContext({
    fetch: async (_url, options) => {
      const body = JSON.parse(options.body);
      assert.equal(body.external_text_processing_consent, true);
      assert.equal(body.source_media_rights_confirmed, true);
      return { status: 201, json: async () => ({ review_id: "TEST_ONLY-review", content_batch_id: "TEST_ONLY-batch" }) };
    },
  });
  assert.equal(await approvedContext.context.runCreateContent("TEST_ONLY-intake-resume",
    { disabled: true, closest: () => null }, true, true), true);
  assert.match(approvedContext.status.textContent, /Đã tạo 5 bản nháp riêng tư/);

  let resolveFirst;
  posts = [];
  const concurrent = makeContext({
    fetch: (url, options) => {
      posts.push({ url, body: JSON.parse(options.body) });
      return new Promise((resolve) => { resolveFirst = resolve; });
    },
  });
  const firstButton = { disabled: true, closest: () => null };
  const secondButton = { disabled: false, closest: () => null };
  const first = concurrent.context.runCreateContent("TEST_ONLY-intake-resume", firstButton);
  const second = await concurrent.context.runCreateContent("TEST_ONLY-intake-resume", secondButton);
  assert.equal(second, false);
  assert.equal(posts.length, 1, "same-Intake double action creates one HTTP request");
  resolveFirst({ status: 409, json: async () => ({
    error: "CONTENT_PREREQUISITES_REQUIRED", provider_calls: 0,
  }) });
  await first;

  posts = [];
  const uncertain = makeContext({
    fetch: async () => { const error = new Error("timed out"); error.name = "AbortError"; throw error; },
  });
  await uncertain.context.runCreateContent("TEST_ONLY-intake-resume", { disabled: true, closest: () => null });
  assert.equal(uncertain.localStore.size, 1, "uncertain network outcome preserves durable idempotency key");
  assert.match(uncertain.status.textContent, /Kết quả chưa rõ/);

  let replayedKey = null;
  const resumed = makeContext({
    localStore: uncertain.localStore,
    fetch: async (_url, options) => {
      replayedKey = JSON.parse(options.body).idempotency_key;
      return { status: 409, json: async () => ({ error: "CONTENT_REQUEST_OUTCOME_UNKNOWN" }) };
    },
  });
  await resumed.context.runCreateContent("TEST_ONLY-intake-resume", { disabled: false, closest: () => null });
  assert.equal(replayedKey, uncertain.localStore.get("sala-content-idem:TEST_ONLY-intake-resume"));
  assert.equal(uncertain.localStore.size, 1, "same-Intake resume does not rotate key on uncertain outcome");

  let confirmedPosts = 0;
  const renderFailure = makeContext({
    fetch: async () => { confirmedPosts += 1; return { status: 201, json: async () => ({ review_id: "TEST_ONLY-review", content_batch_id: "TEST_ONLY-batch" }) }; },
    loadReview: async () => { throw new Error("TEST_ONLY-render-failure"); },
  });
  assert.equal(await renderFailure.context.runCreateContent("TEST_ONLY-intake-resume", { disabled: true, closest: () => null }), false);
  assert.match(renderFailure.status.textContent, /Content đã lưu/);
  assert.doesNotMatch(renderFailure.status.textContent, /Kết quả chưa rõ/);
  assert.equal(renderFailure.localStore.size, 0, "confirmed HTTP 201 does not retain an ambiguous retry key");
  assert.equal(confirmedPosts, 1, "a view-render failure does not retry the confirmed generation");

  console.log("PASS: disabled submit dispatch, separate consent gates, double-action guard, timeout persistence, idempotent recovery, confirmed-201/render-failure handling");
}

main().catch((error) => { console.error(error.message); process.exitCode = 1; });
