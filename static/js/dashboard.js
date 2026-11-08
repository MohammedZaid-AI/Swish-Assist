(() => {
  const $ = (s) => document.querySelector(s);
  const params = new URLSearchParams(location.search);
  if (params.get("embed")) document.body.classList.add("embed");
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const rupee = (n) => "₹" + Number(n || 0).toLocaleString("en-IN");
  const ago = (iso) => { const s = (Date.now() - new Date(iso)) / 1000; return s < 60 ? "just now" : s < 3600 ? Math.round(s / 60) + " min ago" : Math.round(s / 3600) + " h ago"; };

  const DECISION = {
    auto_refund: ["✓", "Auto refunded", "ok"], auto_replace: ["↻", "Auto replaced", "ok"], escalated: ["⚑", "Escalated", "warn"],
    no_issue_found: ["◎", "No issue found", "info"], rejected: ["✕", "Rejected", "danger"],
  };
  const STAGE = { placed: ["Placed", "muted"], preparing: ["Preparing", "info"], packed: ["Packed", "info"], out_for_delivery: ["On the way", "ok"], delivered: ["Delivered", "muted"] };

  let known = new Set(), first = true, lastTiles = {};

  function tiles(t) {
    const avg = t.avg_resolution_seconds;
    const speedup = avg ? Math.round(t.manual_resolution_seconds / avg) : null;
    const defs = [
      ["complaints_today", "Complaints today", `${t.complaints_today}`, "photo and chat complaints"],
      ["auto_resolved_pct", "Auto resolved", `${t.auto_resolved_pct}<small>%</small>`, "no human needed"],
      ["avg_resolution_seconds", "Avg resolution time", avg != null ? `${avg}<small>s</small>` : "–", speedup ? `vs ~25 min manual · <b>${speedup}× faster</b>` : "vs ~25 min typical manual time", "hero"],
      ["refund_auto_approved_inr", "Refunds auto approved", rupee(t.refund_auto_approved_inr), "within the auto-approve cap"],
      ["fraud_flagged", "Flagged for fraud", `${t.fraud_flagged}`, "sent to a human instead"],
      ["avg_vision_latency_ms", "Avg vision latency", t.avg_vision_latency_ms != null ? `${t.avg_vision_latency_ms}<small>ms</small>` : "–", "photo check on Groq"],
      ["vision_calls_saved", "Vision calls saved", `${t.vision_calls_saved ?? 0}`, "flagged early by cheap checks"],
    ];
    $("#tiles").innerHTML = defs.map(([k, label, value, ctx, cls]) =>
      `<div class="tile ${cls || ""} ${!first && lastTiles[k] !== t[k] ? "changed" : ""}"><div class="label">${label}</div><div class="value">${value}</div><div class="ctx">${ctx}</div></div>`).join("");
    lastTiles = { ...t };
  }

  function confRows(items) {
    return (items || []).map((m) => {
      const pct = Math.round((m.confidence || 0) * 100);
      return `<div class="conf"><span title="${esc(m.name)}">${esc(m.name)}</span><span class="bar"><i class="${pct < 80 ? "low" : ""}" style="width:${pct}%"></i></span><span>${pct}%</span></div>`;
    }).join("");
  }

  function complaintCard(c, withActions) {
    const [icon, label, tone] = DECISION[c.decision] || ["•", c.decision, "muted"];
    const human = c.human_decision ? `<span class="badge ${c.human_decision === "approved" ? "ok" : "danger"}">${c.human_decision === "approved" ? "✓ Human approved" : "✕ Human rejected"}</span>` : "";
    const thumb = c.image_url ? `<img class="thumb" src="${esc(c.image_url)}" alt="Customer photo" loading="lazy">` : `<div class="thumb" aria-hidden="true">🧾</div>`;
    const flags = (c.flags || []).map((f) => `<span class="badge danger" title="${esc(f.detail)}">⚠ ${esc(f.label)}</span>`).join("");
    const meta = [
      c.refund ? `<span class="badge muted">${rupee(c.refund)}</span>` : "",
      c.seconds != null ? `<span class="badge muted">⏱ ${c.seconds}s</span>` : "",
      c.vision_skipped ? `<span class="badge info" title="Duplicate, timing, refund history or EXIF check flagged this, so no vision call was made">⚡ vision skipped, flagged early</span>` : "",
      c.model ? `<span class="badge muted">👁 ${esc(c.model)}${c.latency_ms ? " · " + c.latency_ms + " ms" : ""}</span>` : "",
    ].join("");
    const actions = withActions ? `<div class="c-actions"><button class="btn green small" data-decide="${esc(c.id)}" data-approve="1">Approve${c.refund ? " " + rupee(c.refund) : ""}</button><button class="btn danger small" data-decide="${esc(c.id)}" data-approve="0">Reject</button></div>` : "";
    return `<article class="c-card ${!first && !known.has(c.id + withActions) ? "new" : ""}">${thumb}<div>
      <div class="c-head"><div><strong>${esc(c.user)}</strong> <span class="ord">· ${esc(c.order_id)} · ${ago(c.created_at)}</span></div>
        <span class="badge ${tone}">${icon} ${label}</span></div>
      ${c.user_message ? `<div class="c-msg">“${esc(c.user_message)}”</div>` : ""}
      ${c.order_items ? `<div class="ord">Order: ${esc(c.order_items)}</div>` : ""}
      ${c.missing?.length ? `<div class="c-items">${confRows(c.missing)}</div>` : ""}
      <div class="c-reason">${esc(c.reason)}</div>
      <div class="c-meta">${flags}${human}${meta}</div>${actions}</div></article>`;
  }

  function orders(list) {
    $("#orders").innerHTML = list.length ? list.map((o) => {
      const [label, tone] = STAGE[o.stage] || [o.stage, "muted"];
      const delayed = o.delayed ? ` <span class="badge warn" title="${esc(o.delay_reason)}">⚠ Delayed</span>` : "";
      const btn = o.stage !== "delivered" ? `<button class="btn ghost small" data-advance="${esc(o.order_id)}" title="Demo: move to next stage">Next ›</button>` : "";
      return `<tr><td><b>${esc(o.order_id)}</b><span class="items">${esc(o.items)}</span></td><td>${esc(o.user)}</td>
        <td><span class="badge ${tone}">${label}</span>${delayed}</td><td>${o.stage === "delivered" ? "–" : o.eta + " min"}</td><td>${btn}</td></tr>`;
    }).join("") : `<tr><td colspan="5" class="ord">No live orders right now.</td></tr>`;
  }

  async function refresh() {
    try {
      const d = await fetch("/api/dashboard").then((r) => r.json());
      tiles(d.tiles);
      $("#feed").innerHTML = d.feed.length ? d.feed.map((c) => complaintCard(c, false)).join("") : `<div class="empty">No complaints yet.</div>`;
      $("#queue").innerHTML = d.escalations.length ? d.escalations.map((c) => complaintCard(c, true)).join("") : `<div class="empty">Queue is clear. ✓</div>`;
      $("#qCount").textContent = d.escalations.length;
      orders(d.live_orders);
      known = new Set([...d.feed.map((c) => c.id + false), ...d.escalations.map((c) => c.id + true)]);
      first = false;
    } catch (e) { console.error(e); }
  }

  document.addEventListener("click", async (e) => {
    const dec = e.target.closest("[data-decide]");
    if (dec) {
      dec.closest(".c-actions").querySelectorAll("button").forEach((b) => (b.disabled = true));
      await fetch(`/api/complaints/${dec.dataset.decide}/decision`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ approve: dec.dataset.approve === "1" }) });
      refresh();
    }
    const adv = e.target.closest("[data-advance]");
    if (adv) { adv.disabled = true; await fetch(`/demo/advance/${adv.dataset.advance}`, { method: "POST" }); refresh(); }
  });

  // live: SSE first, polling every 3 s as fallback
  let poll = null, pending = null;
  const startPolling = () => { if (!poll) poll = setInterval(refresh, 3000); };
  const setLive = (on, text) => { $("#liveDot").classList.toggle("on", on); $("#liveText").textContent = text; };
  const soon = () => { clearTimeout(pending); pending = setTimeout(refresh, 150); };
  if ("EventSource" in window) {
    const es = new EventSource("/api/events");
    es.onopen = () => { setLive(true, "Live"); clearInterval(poll); poll = null; refresh(); };
    es.onmessage = soon;
    es.onerror = () => { setLive(false, "Reconnecting… (polling)"); startPolling(); };
  } else { setLive(true, "Live (polling)"); startPolling(); }
  setInterval(refresh, 15000); // keep ETAs and "x min ago" fresh
  refresh();
})();
