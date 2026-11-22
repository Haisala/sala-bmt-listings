"use strict";
const $ = (selector) => document.querySelector(selector);
const feed = $("#feed"), detail = $("#detail"), notice = $("#notice");
let allItems = [];
function escapeText(value) { return String(value ?? ""); }
function make(tag, cls, value) { const node = document.createElement(tag); if (cls) node.className = cls; if (value !== undefined) node.textContent = escapeText(value); return node; }
function formatPrice(value, transaction) { return `${new Intl.NumberFormat("vi-VN").format(value)} VNĐ${transaction === "RENT" ? "/tháng" : ""}`; }
function eventId() { if (crypto.randomUUID) return crypto.randomUUID(); if (!crypto.getRandomValues) return null; const b=crypto.getRandomValues(new Uint8Array(16)); b[6]=(b[6]&15)|64; b[8]=(b[8]&63)|128; const h=Array.from(b,x=>x.toString(16).padStart(2,"0")).join(""); return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`; }
function attribution() { const p=new URLSearchParams(location.search), allowedSource=new Set(["google","facebook","zalo","direct","qr","other"]),allowedMedium=new Set(["organic","paid","social","referral","email","qr","other"]), out={}; if(allowedSource.has(p.get("utm_source")))out.utm_source=p.get("utm_source");if(allowedMedium.has(p.get("utm_medium")))out.utm_medium=p.get("utm_medium");const c=p.get("utm_campaign");if(c&&/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,39}$/.test(c))out.utm_campaign=c;try{if(document.referrer)out.referrer_host=new URL(document.referrer).hostname.toLowerCase()}catch{}return out; }
function track(event_type, fields={}) { const id=eventId(); if(!id)return; const payload={event_id:id,event_type,...attribution(),...fields}; fetch("/api/analytics/events",{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload),keepalive:true}).catch(()=>{}); }
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
  try { const r = await fetch(`/api/listings/${encodeURIComponent(id)}`, {credentials:"same-origin"}); if (!r.ok) throw new Error(); const item = await r.json(); track("page_view",{page_type:"listing_detail",property_id:item.property_id,publication_id:item.publication_id});track("property_detail_view",{property_id:item.property_id,publication_id:item.publication_id});detail.replaceChildren(); detail.classList.remove("hidden"); const box=make("article","detail-card"), gallery=make("div","gallery"); for(const media of item.media||[]){const img=document.createElement("img");img.src=`/media/${encodeURIComponent(media.media_id)}`;img.alt=item.title;gallery.append(img);} box.append(gallery,make("p","eyebrow",`${item.transaction_type} · ${item.property_type}`),make("h2","",item.title),make("p","price",formatPrice(item.price_vnd,item.transaction_type)),make("p","",`${item.area_m2} m² · ${item.location}`),make("p","",item.description)); const cta=make("button","open-detail","Yêu cầu tư vấn"); cta.type="button"; const message=make("p","notice",""); message.id="detail-message"; cta.addEventListener("click",()=>{track("contact_cta_click",{property_id:item.property_id,publication_id:item.publication_id,channel:"other"});message.textContent="Chưa kết nối gửi liên hệ; không có dữ liệu nào được gửi.";}); box.append(cta,message); detail.append(box); detail.scrollIntoView({behavior:"smooth",block:"start"}); } catch { notice.textContent="Không tìm thấy tin đã duyệt."; }
}
$("#search-button").addEventListener("click",()=>{const type=$("#search").value.trim()?"query_used":"transaction", value=type==="query_used"?"yes":($("#transaction").value==="RENT"?"rent":"sale");track("search_filter",{filter_type:type,filter_value:value});load();}); $("#search").addEventListener("keydown",e=>{if(e.key==="Enter")$("#search-button").click();}); $("#transaction").addEventListener("change",()=>{$("#search-button").click();}); $("#sort").addEventListener("change",load);
track("page_view",{page_type:"home"});load();
