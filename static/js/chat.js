(() => {
  const $ = (s) => document.querySelector(s);
  const thread = $("#thread"), userSel = $("#user"), textIn = $("#text"), form = $("#composer");
  const params = new URLSearchParams(location.search);
  if (params.get("embed")) document.body.classList.add("embed");

  let users = [], current = null, attachment = null, busy = false;
  const seen = new Set();

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const timeOf = (iso) => { const d = iso ? new Date(iso) : new Date(); return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }); };
  const rupee = (n) => "₹" + Number(n || 0).toLocaleString("en-IN");
  const store = { get(k) { try { return localStorage.getItem(k); } catch { return null; } }, set(k, v) { try { localStorage.setItem(k, v); } catch {} } };

  function scroll() { requestAnimationFrame(() => { thread.scrollTop = thread.scrollHeight; }); }

  function bubble(role, text, opts = {}) {
    const el = document.createElement("div");
    el.className = `msg ${role === "user" ? "me" : "bot"}${opts.human ? " human" : ""}`;
    let html = "";
    if (opts.human) html += `<span class="tag">Swish support team</span>`;
    if (opts.image) html += `<img class="photo" src="${esc(opts.image)}" alt="Photo sent">`;
    const clean = String(text || "").replace(/\n?\[user attached image img_[a-f0-9]+\]/g, "").replace(/^\(photo\)$/, "");
    html += esc(clean).replace(/\*([^*\n]+)\*/g, "<b>$1</b>");
    const ticks = role === "user" ? `<span class="ticks${opts.read ? " read" : ""}">✓✓</span>` : "";
    html += `<span class="meta">${timeOf(opts.at)}${ticks}</span>`;
    el.innerHTML = html;
    thread.appendChild(el);
    scroll();
    return el;
  }

  function notice(text) {
    const el = document.createElement("div");
    el.className = "notice"; el.textContent = text;
    thread.appendChild(el); scroll();
  }

  function typing(on) {
    let t = $("#typing");
    if (on && !t) {
      t = document.createElement("div"); t.id = "typing"; t.className = "typing"; t.innerHTML = "<i></i><i></i><i></i>";
      thread.appendChild(t); scroll();
    } else if (!on && t) t.remove();
    $("#presence").textContent = on ? "typing…" : "online";
  }

  // ---------------------------------------------------------------- cards
  const STAGES = [["placed", "Placed"], ["preparing", "Preparing"], ["packed", "Packed"], ["out_for_delivery", "On the way"], ["delivered", "Delivered"]];

  function statusCard(c) {
    const idx = STAGES.findIndex(([k]) => k === c.stage);
    const bars = STAGES.map((_, i) => `<span class="${i < idx || c.stage === "delivered" ? "done" : i === idx ? "now" : ""}"></span>`).join("");
    const labels = STAGES.map(([k, l], i) => i === idx ? `<b>${l}</b>` : `<span>${l}</span>`).join("");
    const eta = c.is_live
      ? `<div class="eta"><strong>${c.eta_minutes_remaining} min</strong><span class="muted">estimated arrival</span></div>`
      : `<div class="eta"><strong>Delivered</strong><span class="muted">${c.delivered_minutes_ago != null ? c.delivered_minutes_ago + " min ago" : ""}</span></div>`;
    return `<h4>Order status <span class="id">${esc(c.order_id)}</span></h4>
      <div class="muted">${esc(c.items)}</div>
      <div class="progress">${bars}</div><div class="stages">${labels}</div>${eta}
      ${c.is_delayed ? `<div class="delay">⚠ Delayed: ${esc(c.delay_reason)}</div>` : ""}
      <div class="why">${esc(c.kitchen)} · ${c.kitchen_distance_km} km${c.rider ? " · Rider " + esc(c.rider) : ""}</div>`;
  }

  function cartCard(c) {
    const lines = c.lines.map((l) => `<div class="row"><span>${l.qty}× ${esc(l.name)}</span><span>${rupee(l.price * l.qty)}</span></div>`).join("");
    const coupon = c.coupon
      ? `<div class="row"><span class="coupon-chip">🏷 ${esc(c.coupon)}</span><span class="save">−${rupee(c.saving)}</span></div>`
      : `<div class="row muted"><span>No coupon applies</span><span></span></div>`;
    const why = (c.not_applied || []).length
      ? `<details class="why"><summary>Why not other coupons</summary><ul>${c.not_applied.map((n) => `<li>${esc(n.reason)}</li>`).join("")}</ul></details>` : "";
    return `<h4>Your cart</h4>${lines}
      <div class="row muted"><span>Subtotal</span><span>${rupee(c.subtotal)}</span></div>${coupon}
      <div class="row total"><span>Total</span><span>${rupee(c.total)}</span></div>${why}
      <div class="actions"><button class="btn green small" data-say="Yes, place the order">Confirm order</button></div>`;
  }

  const DECISION = {
    auto_refund: ["Refunded", "ok"], auto_replace: ["Replacement sent", "ok"], escalated: ["Sent to support team", "warn"],
    no_issue_found: ["No issue found", "info"], rejected: ["Rejected", "danger"],
  };

  function refundCard(c) {
    const [label, tone] = DECISION[c.decision] || [c.decision, "muted"];
    const items = (c.items || []).map((i) => `<div class="conf"><span style="min-width:120px">${esc(i.name)} <span class="muted">${i.kind}</span></span>
      <span class="bar"><i class="${i.confidence < 0.8 ? "low" : ""}" style="width:${Math.round(i.confidence * 100)}%"></i></span><span>${Math.round(i.confidence * 100)}%</span></div>`).join("");
    const amount = ["auto_refund", "auto_replace"].includes(c.decision)
      ? `<div class="refund-amt">${c.decision === "auto_refund" ? rupee(c.amount) : "On its way"}</div><div class="muted">${c.decision === "auto_refund" ? "to your Swish wallet" : "replacement from the kitchen"}</div>` : "";
    return `<h4>Photo check <span class="badge ${tone}">${label}</span></h4>${amount}
      ${items ? `<div class="items">${items}</div>` : ""}
      ${(c.flags || []).length ? `<div class="stats">${c.flags.map((f) => `<span class="badge danger">${esc(f)}</span>`).join("")}</div>` : ""}
      <div class="stats"><span class="badge muted">⚡ ${c.seconds ?? "–"}s total</span><span class="badge muted">👁 ${esc(c.model)} · ${c.latency_ms} ms</span></div>`;
  }

  function renderCards(cards) {
    for (const c of cards || []) {
      const el = document.createElement("div");
      el.className = `card ${c.type}`;
      el.innerHTML = c.type === "status" ? statusCard(c) : c.type === "cart" ? cartCard(c) : c.type === "refund" ? refundCard(c) : "";
      thread.appendChild(el);
    }
    scroll();
  }

  thread.addEventListener("click", (e) => {
    const b = e.target.closest("[data-say]");
    if (b && !busy) { b.disabled = true; send(b.dataset.say); }
  });

  // ---------------------------------------------------------------- history
  function renderRow(m) {
    if (seen.has(m.id)) return;
    seen.add(m.id);
    if (m.role === "user") bubble("user", m.text, { at: m.created_at, image: m.image_url, read: true });
    else { bubble("bot", m.text, { at: m.created_at, human: m.origin === "human_agent" }); renderCards(m.cards); }
  }

  async function loadHistory() {
    thread.innerHTML = `<div class="day">Today</div>`;
    seen.clear();
    const rows = await fetch(`/api/history?user_id=${encodeURIComponent(current)}`).then((r) => r.json());
    rows.forEach(renderRow);
    if (!rows.length) {
      const u = users.find((x) => x.id === current);
      const hint = document.createElement("div"); hint.className = "day";
      hint.textContent = `Chatting as ${u ? u.name : current}. Try a quick reply below.`;
      thread.appendChild(hint);
    }
  }

  async function syncNew() {
    const rows = await fetch(`/api/history?user_id=${encodeURIComponent(current)}`).then((r) => r.json());
    rows.filter((m) => !seen.has(m.id)).forEach(renderRow);
  }

  // ---------------------------------------------------------------- sending
  function setAttachment(a) {
    attachment = a;
    $("#attachment").hidden = !a;
    if (a) { $("#attThumb").src = a.url; $("#attName").textContent = a.name; }
  }

  async function send(text) {
    text = (text || "").trim();
    if (busy || (!text && !attachment)) return;
    busy = true; form.querySelector(".send").disabled = true;
    const fd = new FormData();
    fd.append("user_id", current);
    fd.append("text", text);
    const att = attachment;
    if (att?.file) fd.append("image", att.file);
    if (att?.sample) fd.append("sample", att.sample);
    const mine = bubble("user", text, { image: att?.url });
    setAttachment(null); textIn.value = "";
    setTimeout(() => typing(true), 250);
    try {
      const r = await fetch("/api/chat", { method: "POST", body: fd });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "Request failed");
      (data.ids || []).forEach((id) => id && seen.add(id));
      typing(false);
      mine.querySelector(".ticks")?.classList.add("read");
      (data.notices || []).forEach(notice);
      bubble("bot", data.reply);
      renderCards(data.cards);
    } catch (err) {
      typing(false);
      bubble("bot", "Sorry, something went wrong on my side. Please try again.");
      console.error(err);
    } finally {
      busy = false; form.querySelector(".send").disabled = false; textIn.focus();
    }
  }

  form.addEventListener("submit", (e) => { e.preventDefault(); send(textIn.value); });
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => send(c.dataset.text)));
  $("#file").addEventListener("change", (e) => {
    const f = e.target.files[0]; if (!f) return;
    setAttachment({ file: f, name: f.name, url: URL.createObjectURL(f) }); e.target.value = ""; textIn.focus();
  });
  $("#attClear").addEventListener("click", () => setAttachment(null));
  $("#sampleBtn").addEventListener("click", async () => {
    const samples = await fetch("/api/samples").then((r) => r.json());
    const s = samples[0];
    if (s) setAttachment({ sample: s.file, name: s.label, url: s.url });
    textIn.focus();
  });
  $("#resetBtn").addEventListener("click", async () => {
    const b = $("#resetBtn"); b.disabled = true; b.textContent = "Resetting…";
    await fetch("/demo/reset", { method: "POST" });
    b.disabled = false; b.textContent = "Reset demo";
    await loadHistory();
  });

  userSel.addEventListener("change", () => { current = userSel.value; store.set("swish_user", current); showBlurb(); loadHistory(); });
  function showBlurb() { const u = users.find((x) => x.id === current); $("#blurb").textContent = u ? u.blurb : ""; }

  // ---------------------------------------------------------------- live updates (human decisions, resets)
  function live() {
    try {
      const es = new EventSource("/api/events");
      es.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        if (ev.type === "message" && ev.user_id === current && !busy) syncNew();
        if (ev.type === "reset") loadHistory();
      };
      es.onerror = () => {}; // browser retries automatically
    } catch { setInterval(() => { if (!busy) syncNew(); }, 4000); }
  }

  (async function init() {
    const [cfg, us] = await Promise.all([fetch("/api/config").then((r) => r.json()), fetch("/api/users").then((r) => r.json())]);
    users = us;
    $("#mode").textContent = cfg.llm_enabled ? "Groq live" : "Offline mode";
    userSel.innerHTML = users.map((u) => `<option value="${esc(u.id)}">${esc(u.name)}</option>`).join("");
    current = params.get("user") || store.get("swish_user") || "priya";
    if (!users.some((u) => u.id === current)) current = users[0]?.id;
    userSel.value = current; showBlurb();
    await loadHistory();
    if (!params.get("nolive")) live();
  })();
})();
