"use strict";
const $ = (selector) => document.querySelector(selector);
const feed = $("#feed"), detail = $("#detail"), notice = $("#notice");
let allItems = [];
function escapeText(value) { return String(value ?? ""); }
function make(tag, cls, value) { const node = document.createElement(tag); if (cls) node.className = cls; if (value !== undefined) node.textContent = escapeText(value); return node; }
function formatPrice(value, transaction) { return `${new Intl.NumberFormat("vi-VN").format(value)} VNĐ${transaction === "RENT" ? "/tháng" : ""}`; }
function render(items) {
  feed.replaceChildren();
  $("#result-count").textContent = `${items.length} tin`;
  if (!items.length) { notice.textContent = "Chưa có bất động sản được Owner duyệt."; return; }
  notice.textContent = "";
  for (const item of items) {
    const card = make("article", "card"), media = make("div", "card-media");
    const first = item.media?.[0];
    if (first) { const img = document.createElement("img"); img.src = `/media/${encodeURIComponent(first.media_id)}`; img.alt = item.title; img.loading = "lazy"; media.append(img); }
    else media.textContent = "SALA BĐS";
    const body = make("div", "card-body"); body.append(make("p", "eyebrow", `${item.transaction_type === "RENT" ? "CHO THUÊ" : "MUA BÁN"} · ${item.property_type}`));
    body.append(make("h3", "", item.title), make("p", "", item.location), make("p", "price", formatPrice(item.price_vnd, item.transaction_type)), make("p", "", `${item.area_m2} m²`));
    const open = make("button", "open-detail", "Xem chi tiết"); open.type = "button"; open.addEventListener("click", () => showDetail(item.listing_id)); body.append(open); card.append(media, body); feed.append(card);
  }
}
async function load() {
  try { const params = new URLSearchParams(); const q = $("#search").value.trim(); const t = $("#transaction").value; if (q) params.set("q", q); if (t) params.set("transaction_type", t); const r = await fetch(`/api/listings?${params}`, {credentials:"same-origin"}); if (!r.ok) throw new Error("Không tải được danh sách"); allItems = (await r.json()).items; const sort = $("#sort").value; if (sort === "price-up") allItems.sort((a,b)=>a.price_vnd-b.price_vnd); if (sort === "price-down") allItems.sort((a,b)=>b.price_vnd-a.price_vnd); render(allItems); } catch { notice.textContent = "Chưa kết nối được nguồn dữ liệu. Vui lòng thử lại."; }
}
async function showDetail(id) {
  try { const r = await fetch(`/api/listings/${encodeURIComponent(id)}`, {credentials:"same-origin"}); if (!r.ok) throw new Error(); const item = await r.json(); detail.replaceChildren(); detail.classList.remove("hidden"); const box=make("article","detail-card"), gallery=make("div","gallery"); for(const media of item.media||[]){const img=document.createElement("img");img.src=`/media/${encodeURIComponent(media.media_id)}`;img.alt=item.title;gallery.append(img);} box.append(gallery,make("p","eyebrow",`${item.transaction_type} · ${item.property_type}`),make("h2","",item.title),make("p","price",formatPrice(item.price_vnd,item.transaction_type)),make("p","",`${item.area_m2} m² · ${item.location}`),make("p","",item.description)); const cta=make("button","open-detail","Yêu cầu tư vấn"); cta.type="button"; const message=make("p","notice",""); message.id="detail-message"; cta.addEventListener("click",()=>{message.textContent="Chưa kết nối gửi liên hệ; không có dữ liệu nào được gửi.";}); box.append(cta,message); detail.append(box); detail.scrollIntoView({behavior:"smooth",block:"start"}); } catch { notice.textContent="Không tìm thấy tin đã duyệt."; }
}
$("#search-button").addEventListener("click",load); $("#search").addEventListener("keydown",e=>{if(e.key==="Enter")load();}); $("#transaction").addEventListener("change",load); $("#sort").addEventListener("change",load);
$("#owner-open").addEventListener("click",()=>$("#owner-panel").classList.toggle("hidden"));
async function api(path, options={}) { return fetch(path,{credentials:"same-origin",...options}); }
$("#login-form").addEventListener("submit",async e=>{e.preventDefault();const password=new FormData(e.currentTarget).get("password");const r=await api("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password})});$("#login-error").textContent=r.ok?"":(await r.json()).error; if(r.ok){$("#login-form").classList.add("hidden");$("#owner-tools").classList.remove("hidden");$("#logout").classList.remove("hidden");await loadPending();}});
$("#logout").addEventListener("click",async()=>{await api("/api/logout",{method:"POST"});$("#owner-tools").classList.add("hidden");$("#login-form").classList.remove("hidden");$("#logout").classList.add("hidden");});
function editListing(item){const form=$("#intake-form");form.dataset.listingId=item.listing_id;for(const key of ["transaction_type","property_type","title","description","price_vnd","area_m2","location","source"]){const control=form.elements.namedItem(key);if(control)control.value=item[key]??"";}$("#intake-submit").textContent="Tạo revision để Owner duyệt";$("#clear-edit").classList.remove("hidden");$("#intake-message").textContent="Đã nạp bản hiện tại. Lưu sẽ tạo revision mới ở PENDING_REVIEW.";form.scrollIntoView({behavior:"smooth",block:"start"});}
function clearEdit(){const form=$("#intake-form");delete form.dataset.listingId;form.reset();$("#intake-submit").textContent="Gửi vào hàng chờ";$("#clear-edit").classList.add("hidden");$("#intake-message").textContent="";}
$("#clear-edit").addEventListener("click",clearEdit);
async function loadPending(){const r=await api("/api/admin/listings");if(!r.ok)return;const list=(await r.json()).items,root=$("#pending");root.replaceChildren();for(const item of list){const row=make("article","pending-item");row.append(make("h3","",item.title),make("p","",`${item.transaction_type} · ${formatPrice(item.price_vnd,item.transaction_type)} · ${item.location} · ${item.status} · revision ${item.revision_no}`));const actions=make("div","pending-actions");const edit=make("button","","Sửa / tạo revision");edit.type="button";edit.addEventListener("click",()=>editListing(item));actions.append(edit);if(item.status==="PENDING_REVIEW"){for(const action of ["approve","reject"]){const button=make("button",action==="reject"?"reject":"",action==="approve"?"Duyệt":"Từ chối");button.type="button";button.addEventListener("click",async()=>{const resp=await api(`/api/listings/${encodeURIComponent(item.listing_id)}/review`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action})});if(resp.ok){await loadPending();await load();}});actions.append(button);}}row.append(actions);root.append(row);}}
$("#intake-form").addEventListener("submit",async e=>{e.preventDefault();const form=e.currentTarget,data=new FormData(form),id=form.dataset.listingId;const path=id?`/api/listings/${encodeURIComponent(id)}/revisions`:"/api/intakes";const r=await api(path,{method:"POST",body:data});const output=$("#intake-message");if(r.status===201){const result=await r.json();output.textContent=`Đã lưu ${result.listing_id} · ${result.file_count} ảnh · PENDING_REVIEW`;clearEdit();await loadPending();await load();}else{const error=await r.json().catch(()=>({error:"Gửi tin thất bại"}));output.textContent=error.error||"Gửi tin thất bại";}});
load();
