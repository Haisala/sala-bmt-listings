(() => {
  const E = (v) =>
      String(v ?? "").replace(
        /[&<>"']/g,
        (c) =>
          ({
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
          })[c],
      ),
    M = (v) => new Intl.NumberFormat("vi-VN").format(v) + " VNĐ",
    D =
      document.body.dataset.page === "detail" ||
      location.pathname.includes("/listings/"),
    B = D && location.pathname.includes("/listings/") ? "../../" : "./",
    S = {
      a: "",
      q: "",
      p: "",
      x: "",
      t: "",
      saved: JSON.parse(localStorage.getItem("bmt-saved") || "[]"),
      savedView: new URLSearchParams(location.search).get("saved") === "1",
    },
    T = (n, d = {}) => {
      window.__bmtEvents = window.__bmtEvents || [];
      const event = {
        name: n,
        detail: d,
        at: new Date().toISOString(),
      };
      window.__bmtEvents.push(event);
      const endpoint = document.body.dataset.analyticsEndpoint || window.__BMT_ANALYTICS_ENDPOINT;
      if (endpoint) {
        const payload = JSON.stringify({ name: n, detail: d });
        try {
          if (navigator.sendBeacon) navigator.sendBeacon(endpoint, new Blob([payload], { type: "application/json" }));
          else fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: payload, keepalive: true });
        } catch (_) { /* analytics must never affect site UX */ }
      }
    },
    rental = (x) => x.transactionType === "RENTAL",
    img = (x) => (x.images?.[0] ? B + x.images[0] : ""),
    matches = (x) =>
      (!S.a || (x.areas || []).includes(S.a)) &&
      (!S.x || (x.transactionType || "SALE") === S.x) &&
      (!S.t || S.t === "Dự án" || x.propertyType === S.t) &&
      (!S.q ||
        `${x.title} ${x.subtitle || ""} ${x.addressText} ${(x.areas || []).join(" ")}`
          .toLowerCase()
          .includes(S.q.toLowerCase())) &&
      (!S.p ||
        rental(x) ||
        {
          u: x.price < 1e9,
          1: x.price >= 1e9 && x.price <= 2e9,
          2: x.price > 2e9 && x.price <= 3e9,
          3: x.price > 3e9 && x.price <= 5e9,
          5: x.price > 5e9 && x.price <= 1e10,
        }[S.p]);
  function gallery(images, title, detail = false) {
    if (!images?.length)
      return '<div class="empty-photo">Ảnh thật sẽ cập nhật khi được kiểm chứng.</div>';
    let list = E(JSON.stringify(images));
    let slides = images
      .map(
        (v, i) =>
          `<img src="${E(B + v)}" alt="${E(title)} — ảnh ${i + 1} trên ${images.length}" loading="${i ? "lazy" : "eager"}" fetchpriority="${i ? "auto" : "high"}">`,
      )
      .join("");
    let dots =
      images.length > 1
        ? `<div class="gallery-dots" aria-label="Vị trí ảnh">${images.map((_, i) => `<span class="gallery-dot${i ? "" : " active"}" data-dot="${i}"></span>`).join("")}</div>`
        : "";
    return `<div class="gallery-swipe ${detail ? "gallery-detail" : "gallery-card"}" data-images="${list}" data-index="0"><div class="gallery-track">${slides}</div>${dots}<span class="gallery-indicator" aria-live="polite">1/${images.length}</span>${detail ? `<div class="thumbs">${images.map((v, i) => `<button type="button" data-thumb="${i}" aria-label="Ảnh ${i + 1}"><img src="${E(B + v)}" alt="Ảnh ${i + 1}" loading="lazy"></button>`).join("")}</div>` : ""}</div>`;
  }
  function card(x) {
    let sv = S.saved.includes(x.id),
      legal =
        x.legal || (rental(x) ? "Thông tin theo nguồn" : "Pháp lý cập nhật"),
      location =
        x.id === "nha-xanh-co-ban-cu-ebur"
          ? "P. Buôn Ma Thuột"
          : (x.areas || [x.addressText || "BMT"])[0],
      title =
        x.id === "nha-xanh-co-ban-cu-ebur" ? "Nhà TDP 8, Cư Êbur" : x.title;
    let price = x.priceLabel || M(x.price);
    if (!rental(x) && x.price)
      price =
        new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(
          x.price / 1e9,
        ) + " tỷ";
    return `<article class="card"><a class="media" href="${B}listings/${x.slug}/" data-track="property_view">${gallery(x.images, x.title)}<span class="count"><svg width="10" height="10" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><rect x="1.5" y="2.5" width="13" height="11" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="5" cy="6" r="1.25" fill="currentColor"/><path d="m3.5 11 2.7-2.7 2.2 2.1 1.7-1.7 2.4 2.3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg> ${x.images?.length || 0}</span><span class="pill">${E(x.status)}</span></a><div class="card-body"><h3><a href="${B}listings/${x.slug}/">${E(title)}</a><button data-save="${E(x.id)}" class="save ${sv ? "saved" : ""}" aria-label="${sv ? "Bỏ lưu" : "Lưu căn này"}">${sv ? "♥" : "♡"}</button></h3><div class="facts"><span>⌖ ${E(location)}</span><span>▣ ${E(String(x.area || `${x.landArea || ""}m²`).replace(/m²/g, " m²"))}</span><span>↗ ${E(
      String(x.dimensions || "")
        .replace(/\s*[×x]\s*/g, " × ")
        .replace(/m$/, " m"),
    )}</span><span>▤ ${E(legal)}</span></div><p class="price">${E(price)}</p><div class="card-ctas"><a class="card-cta outline" href="${x.contact?.phone ? "tel:" + E(x.contact.phone) : "#"}"><span class="cta-icon" aria-hidden="true">☎</span><span>Gọi ngay</span></a><a class="card-cta outline" href="${x.contact?.phone ? "https://zalo.me/" + E(x.contact.phone) : "#"}" target="_blank" rel="noopener"><span class="cta-icon" aria-hidden="true">◉</span><span>Chat Zalo</span></a><a class="card-cta primary" href="${B}listings/${x.slug}/">Xem chi tiết →</a></div></div></article>`;
  }
  function setupGalleries() {
    document.querySelectorAll(".gallery-swipe").forEach((g) => {
      if (g.dataset.bound) return;
      g.dataset.bound = "1";
      let images = JSON.parse(g.dataset.images || "[]"),
        track = g.querySelector(".gallery-track"),
        i = 0,
        startX = 0,
        startY = 0,
        moved = false;
      const update = () => {
        i = Math.max(0, Math.min(images.length - 1, i));
        g.dataset.index = i;
        let indicator = g.querySelector(".gallery-indicator");
        if (track)
          track.scrollTo({
            left: i * (track.clientWidth || 1),
            behavior: "smooth",
          });
        if (indicator) indicator.textContent = `${i + 1}/${images.length}`;
        g.querySelectorAll("[data-dot]").forEach((b, n) =>
          b.classList.toggle("active", n === i),
        );
        g.querySelectorAll("[data-thumb]").forEach((b, n) =>
          b.classList.toggle("active", n === i),
        );
      };
      const step = (n) => {
        i += n;
        update();
      };
      track?.addEventListener(
        "scroll",
        () => {
          let n = Math.round(track.scrollLeft / (track.clientWidth || 1));
          if (n !== i) {
            i = n;
            update();
          }
        },
        { passive: true },
      );
      g.addEventListener(
        "touchstart",
        (e) => {
          let t = e.changedTouches[0];
          startX = t.clientX;
          startY = t.clientY;
          moved = false;
        },
        { passive: true },
      );
      g.addEventListener(
        "touchmove",
        (e) => {
          let t = e.changedTouches[0];
          if (
            Math.abs(t.clientX - startX) > Math.abs(t.clientY - startY) &&
            Math.abs(t.clientX - startX) > 8
          )
            e.preventDefault();
        },
        { passive: false },
      );
      g.addEventListener(
        "touchend",
        (e) => {
          let t = e.changedTouches[0],
            dx = t.clientX - startX,
            dy = t.clientY - startY;
          if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.2) {
            e.preventDefault();
            moved = true;
            step(dx < 0 ? 1 : -1);
            setTimeout(() => {
              moved = false;
            }, 350);
          }
        },
        { passive: false },
      );
      g.addEventListener("click", (e) => {
        if (moved) {
          e.preventDefault();
          e.stopPropagation();
          moved = false;
        }
      });
      g.querySelectorAll("[data-thumb]").forEach((b) =>
        b.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          i = Number(b.dataset.thumb);
          update();
        }),
      );
      g.querySelectorAll("[data-dot]").forEach((b) =>
        b.addEventListener("click", (e) => {
          e.preventDefault();
          e.stopPropagation();
          i = Number(b.dataset.dot);
          update();
        }),
      );
      update();
    });
  }
  function render() {
    let f = document.querySelector("#feed");
    if (!f) return;
    let xs = (
      S.savedView ? S.data.filter((x) => S.saved.includes(x.id)) : S.data
    ).filter(S.savedView ? () => true : matches);
    document.querySelector("#feed-title").textContent = S.savedView
      ? "Đã lưu"
      : S.a || S.q || S.x || S.t
        ? "Kết quả phù hợp"
        : "Nhà đất mới nhất";
    f.innerHTML = xs.length
      ? xs.map(card).join("")
      : `<div class="empty">${S.savedView ? "Chưa có căn nào được lưu trên thiết bị này." : "Chưa có căn phù hợp"}<br><small>${S.savedView ? "Lưu một căn để xem lại tại đây." : "Thử bỏ bớt một bộ lọc."}</small></div>`;
    bind();
    setupGalleries();
    setupMotion();
  }
  function setupMotion() {
    const prices = document.querySelectorAll(".card:first-of-type .price:not([data-motion-bound])");
    const titles = document.querySelectorAll(".card h3:not(.is-visible)");
    titles.forEach((title) => title.classList.add("scroll-cue"));
    if (!window.IntersectionObserver) { titles.forEach((title) => title.classList.add("is-visible")); return; }
    const titleObserver = new IntersectionObserver((entries, obs) => entries.forEach((entry) => {
      if (entry.isIntersecting) { entry.target.classList.add("is-visible"); obs.unobserve(entry.target); }
    }), { threshold: 0.2 });
    titles.forEach((title) => titleObserver.observe(title));
    if (!prices.length) return;
    prices.forEach((price) => price.setAttribute("data-motion-bound", "1"));
    const observer = new IntersectionObserver((entries, obs) => entries.forEach((entry) => {
      if (entry.isIntersecting) { entry.target.classList.add("price-shimmer"); obs.unobserve(entry.target); }
    }), { threshold: 0.45 });
    prices.forEach((price) => observer.observe(price));
  }
  function chips() {
    let r = document.querySelector("#chips");
    if (!r) return;
    r.innerHTML = ["Thôn 8", "EcoCity", "Ea Tu", "Ea Kao", "Tân An"]
      .map(
        (v) =>
          `<button class="chip ${S.a === v || (!S.a && v === "Thôn 8") ? "active" : ""}" data-area="${v}">${v}</button>`,
      )
      .join("");
    r.querySelectorAll("[data-area]").forEach(
      (b) =>
        (b.onclick = () => {
          S.a = S.a === b.dataset.area ? "" : b.dataset.area;
          T("filter_area", { value: S.a });
          chips();
          render();
        }),
    );
  }
  function filters() {
    let r = document.querySelector("#filter-options");
    if (!r) return;
    r.innerHTML = `<fieldset><legend>Khu vực</legend>${["Thôn 8", "EcoCity", "Ea Tu", "Ea Kao", "Tân An", "Trung tâm"].map((v) => `<button class="opt ${S.a === v ? "selected" : ""}" data-a="${v}">${v}</button>`).join("")}</fieldset><fieldset><legend>Giao dịch</legend>${[
      ["SALE", "Mua bán"],
      ["RENTAL", "Cho thuê"],
    ]
      .map(
        (v) =>
          `<button class="opt ${S.x === v[0] ? "selected" : ""}" data-x="${v[0]}">${v[1]}</button>`,
      )
      .join("")}</fieldset><fieldset><legend>Khoảng giá bán</legend>${[
      ["u", "&lt;1 tỷ"],
      ["1", "1–2 tỷ"],
      ["2", "2–3 tỷ"],
      ["3", "3–5 tỷ"],
      ["5", "5–10 tỷ"],
    ]
      .map(
        (v) =>
          `<button class="opt ${S.p === v[0] ? "selected" : ""}" data-p="${v[0]}">${v[1]}</button>`,
      )
      .join("")}</fieldset>`;
    r.querySelectorAll("[data-a]").forEach(
      (b) =>
        (b.onclick = () => {
          S.a = S.a === b.dataset.a ? "" : b.dataset.a;
          filters();
        }),
    );
    r.querySelectorAll("[data-x]").forEach(
      (b) =>
        (b.onclick = () => {
          S.x = S.x === b.dataset.x ? "" : b.dataset.x;
          filters();
        }),
    );
    r.querySelectorAll("[data-p]").forEach(
      (b) =>
        (b.onclick = () => {
          S.p = S.p === b.dataset.p ? "" : b.dataset.p;
          filters();
        }),
    );
  }
  function refreshSaveButtons() {
    document.querySelectorAll("[data-save]").forEach((b) => {
      let on = S.saved.includes(b.dataset.save);
      b.classList.toggle("saved", on);
      if (b.dataset.saveLabel)
        b.textContent = on ? "♥ Đã lưu" : "♡ Lưu căn này";
      else b.textContent = on ? "♥" : "♡";
    });
  }
  function bind() {
    document.querySelectorAll("[data-save]").forEach(
      (b) =>
        (b.onclick = () => {
          S.saved = S.saved.includes(b.dataset.save)
            ? S.saved.filter((v) => v !== b.dataset.save)
            : [...S.saved, b.dataset.save];
          localStorage.setItem("bmt-saved", JSON.stringify(S.saved));
          T("favorite", { id: b.dataset.save });
          refreshSaveButtons();
          if (!D) render();
        }),
    );
    document.querySelectorAll("[data-share]").forEach(
      (b) =>
        (b.onclick = async () => {
          let u = new URL(`${B}listings/${b.dataset.share}/`, location.href)
            .href;
          try {
            await navigator.clipboard.writeText(u);
            b.textContent = "Đã sao chép";
          } catch {
            b.textContent = "Sao chép link";
          }
          T("share", { slug: b.dataset.share });
        }),
    );
  }
  function detail(x) {
    let r = document.querySelector("#detail");
    if (!x) {
      r.innerHTML =
        '<div class="empty"><h1>Không tìm thấy bất động sản</h1><a class="primary" href="./">Về trang chủ</a></div>';
      return;
    }
    T("property_view", { id: x.id });
    document.title = x.title + " | BMT Property Hub";
    let d =
        x.transactionType === "RENTAL"
          ? null
          : x.landArea
            ? {
                m: Math.round(x.price / x.landArea),
                f: Math.round(x.price / (x.width || 1)),
                five: Math.round((x.price * 5) / (x.width || 1)),
              }
            : null,
      contact = x.contact || {},
      rel = S.data
        .filter(
          (y) =>
            y.id !== x.id &&
            ((y.areas || []).some((a) => (x.areas || []).includes(a)) ||
              y.propertyType === x.propertyType),
        )
        .slice(0, 3);
    r.innerHTML = `<article class="detail"><section class="gallery">${gallery(x.images, x.title, true)}</section><section class="summary"><div><span class="pill static">${E(x.status)}</span> <span class="location">${E(x.category || "Nhà đất")}</span></div><h1>${E(x.title)}</h1><p class="detail-location">⌖ ${E(x.addressText || x.areas?.[0] || "Buôn Ma Thuột")}</p><p class="detail-price">${E(x.priceLabel || M(x.price))}</p><p class="note">${E(x.priceNote || "Đây là giá chào, không phải giá giao dịch.")}</p><div class="keyfacts"><b>${E(x.area || `${x.landArea || ""}m²`)}<small>${rental(x) ? "Diện tích căn hộ" : "Diện tích đất"}</small></b><b>${E(x.dimensions || "—")}<small>Kích thước</small></b><b>${E(x.legal || "—")}<small>Pháp lý</small></b></div><div class="ctas"><a class="primary" href="${contact.phone ? "tel:" + E(contact.phone) : "#"}">☎ Gọi Hải Sala</a><a class="primary" href="${contact.phone ? "https://zalo.me/" + E(contact.phone) : "#"}" target="_blank" rel="noopener">Zalo</a><button type="button" class="primary save-detail ${S.saved.includes(x.id) ? "saved" : ""}" data-save="${E(x.id)}" data-save-label>${S.saved.includes(x.id) ? "♥ Đã lưu" : "♡ Lưu căn này"}</button></div></section><section class="detail-section"><p class="kicker">VÌ SAO NÊN XEM CĂN NÀY</p><h2>${rental(x) ? "Nội thất & tiện ích" : "Điểm đáng xem"}</h2><div class="advantages">${(
      x.features || []
    )
      .concat(x.locationNotes || [])
      .map((v) => `<div>✦ <b>${E(v)}</b></div>`)
      .join(
        "",
      )}</div></section><section class="detail-section"><p class="kicker">THÔNG TIN CHI TIẾT</p><h2>Thông tin căn này</h2><p>${E(x.description || "Thông tin theo dữ liệu được cung cấp.")}</p>${d ? `<p class="calculation">Phép tính từ GIÁ CHÀO, không phải giá giao dịch: ${M(d.m)}/m² đất · ${M(d.f)}/m ngang · quy đổi 5m: ${M(d.five)}.</p>` : ""}</section><section class="detail-section"><h2>Có thể bạn cũng quan tâm</h2><div class="related">${rel.length ? rel.map(card).join("") : '<p class="muted">Chưa có căn tương tự trong dữ liệu hiện có.</p>'}</div></section></article>`;
    bind();
    setupGalleries();
  }
  function actions() {
    document.querySelectorAll('[data-action="search"]').forEach(
      (b) =>
        (b.onclick = () => {
          document.querySelector("#query")?.focus();
          document
            .querySelector("#search")
            ?.scrollIntoView({ behavior: "smooth" });
        }),
    );
    document.querySelectorAll('[data-action="clear"]').forEach(
      (b) =>
        (b.onclick = () => {
          document.querySelector("#query").value = "";
          S.q = "";
          render();
        }),
    );
    document
      .querySelectorAll('[data-action="filters"]')
      .forEach(
        (b) =>
          (b.onclick = () =>
            document.querySelector("#sheet").classList.add("open")),
      );
    document
      .querySelectorAll('[data-action="close"]')
      .forEach(
        (b) =>
          (b.onclick = () =>
            document.querySelector("#sheet").classList.remove("open")),
      );
    document.querySelectorAll('[data-action="apply"]').forEach(
      (b) =>
        (b.onclick = () => {
          document.querySelector("#sheet").classList.remove("open");
          T("search", { area: S.a, price: S.p, transactionType: S.x });
          render();
        }),
    );
    document.querySelectorAll('[data-action="all"]').forEach(
      (b) =>
        (b.onclick = () => {
          S.savedView = false;
          S.a = "";
          S.q = "";
          S.p = "";
          S.x = "";
          S.t = "";
          render();
          window.scrollTo({ top: 0, behavior: "smooth" });
        }),
    );
    document.querySelectorAll('[data-action="saved"]').forEach(
      (b) =>
        (b.onclick = () => {
          if (D) {
            location.href = B + "?saved=1";
            return;
          }
          S.savedView = !S.savedView;
          render();
          window.scrollTo({ top: 0, behavior: "smooth" });
        }),
    );
    document.querySelectorAll("[data-type]").forEach((b) =>
      b.addEventListener("click", () => {
        S.t = b.dataset.type;
        document
          .querySelectorAll("[data-type]")
          .forEach((t) => t.classList.toggle("active", t === b));
        render();
      }),
    );
  }
  function discovery() {
    const hero = document.querySelector(".hero");
    const headerShell = document.querySelector(".home-header-shell");
    const section = document.querySelector(".listing-section");
    if (!hero || !headerShell || !section) return;
    const contentHeader = document.querySelector(".listing-nav .section-heading");
    if (!contentHeader) return;
    let state = "full",
      lastY = window.scrollY,
      lastSectionTop = section.getBoundingClientRect().top,
      compactAnchor = lastSectionTop,
      stateChangedAt = 0;
    const setState = (next) => {
      if (state === next) return;
      state = next;
      stateChangedAt = performance.now();
      hero.dataset.headerState = state;
      headerShell.dataset.headerState = state;
      headerShell.classList.toggle("is-collapsed", state !== "full");
      headerShell.classList.toggle("is-mini", state === "mini");
      hero.classList.toggle("is-collapsed", state !== "full");
      hero.classList.toggle("is-mini", state === "mini");
      const prompt = hero.querySelector(".ai-prompt span:last-child");
      const query = hero.querySelector("#query");
      const promptText =
        state === "mini"
          ? "Tìm nhà..."
          : state === "compact"
            ? "Bạn đang tìm nhà..."
            : "Bạn đang tìm nhà như thế nào?";
      if (prompt)
        prompt.textContent = promptText;
      if (query) query.setAttribute("aria-label", promptText);
    };
    const update = () => {
      const y = window.scrollY,
        delta = y - lastY,
        heroBottom = hero.getBoundingClientRect().bottom,
        sectionTop = section.getBoundingClientRect().top,
        contentHeaderTop = contentHeader.getBoundingClientRect().top,
        sectionDelta = sectionTop - lastSectionTop;
      lastY = y;
      lastSectionTop = sectionTop;
      if (performance.now() - stateChangedAt < 250) return;
      if (y <= 24) {
        setState("full");
        return;
      }
      const maxScrollY = Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
      const atDocumentEnd = y >= maxScrollY - 1;
      const compactCollision = contentHeaderTop <= heroBottom + 8;
      if (state === "compact" && delta > 2 && (compactCollision || atDocumentEnd)) {
        setState("mini");
        return;
      }
      if (delta > 2) {
        if (state === "full" && heroBottom <= 120) {
          setState("compact");
          compactAnchor = section.getBoundingClientRect().top;
          if (section.getBoundingClientRect().top <= 0) setState("mini");
        }
      } else if (delta < -2) {
        if (state === "mini" && (sectionDelta >= 20 || sectionTop > 0)) setState("compact");
        else if (state !== "full") setState("compact");
      }
    };
    let frame = 0;
    window.addEventListener(
      "scroll",
      () => {
        if (frame) return;
        frame = requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            frame = 0;
            update();
          });
        });
      },
      { passive: true },
    );
    update();
  }
  fetch(B + "data/listings.json")
    .then((r) => r.json())
    .then((data) => {
      S.data = data.filter((x) => x.status !== "Đã bán");
      if (D) {
        let slug = location.pathname.includes("/listings/")
          ? location.pathname.split("/").filter(Boolean).at(-1)
          : new URLSearchParams(location.search).get("slug");
        detail(S.data.find((x) => x.slug === slug));
      } else {
        chips();
        filters();
        render();
        document.querySelector("#query").oninput = (e) => {
          S.q = e.target.value;
          document.querySelector("#search")?.classList.toggle("has-query", Boolean(S.q));
          T("search", { query: S.q });
          render();
        };
      }
      actions();
      if (!D) discovery();
    });
})();
