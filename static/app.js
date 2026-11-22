/* Drawing → BOM demo frontend (vanilla JS, no build step). */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    config: null,
    runId: null,
    result: null,
    page: 1,
    zoom: 0,          // 0 = fit to width, otherwise scale factor of fit width
    tab: "title",
    selected: null,   // row_id
    editing: null,    // row_id being edited
  };
  const STAGES = ["rendering", "reading_text", "extracting", "checking"];
  const STATUS_TEXT = { verified: "Verified", needs_review: "Needs review", failed: "Failed check" };
  const PILL_TEXT = { verified: "Verified", needs_review: "Review", failed: "Failed" };
  const TAB_SECTIONS = { title: ["title"], bom: ["bom"], dim: ["dim", "note"] };
  const BOM_COLS = [["item_no", "Item"], ["part_number", "Part no."], ["description", "Description"], ["material", "Material"], ["quantity", "Qty"]];

  // ------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  async function api(path, opts = {}) {
    let res;
    try {
      res = await fetch(path, opts);
    } catch {
      throw new Error("Can't reach the server. Is it still running?");
    }
    let body = null;
    try { body = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) throw new Error((body && body.error) || `Request failed (${res.status}).`);
    return body;
  }

  function showError(msg) {
    const b = $("error-banner");
    if (!msg) { b.hidden = true; return; }
    b.textContent = msg;
    b.hidden = false;
  }

  let toastTimer;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.hidden = true), 2200);
  }

  function rowTip(row) {
    const lines = [`${STATUS_TEXT[row.status]}`];
    for (const [name, f] of Object.entries(row.fields)) {
      const prefix = Object.keys(row.fields).length > 1 ? `${name}: ` : "";
      if (f.human) lines.push(`${prefix}edited by reviewer`);
      for (const issue of f.issues || []) lines.push(`• ${prefix}${issue}`);
      if (!f.issues?.length && f.verified) lines.push(`• ${prefix}${f.verify_msg}`);
    }
    if (row.review) lines.push(`\n${row.review.state === "approved" ? "Approved" : "Edited"} ${new Date(row.review.at).toLocaleTimeString()}`);
    return lines.join("\n");
  }

  function fieldTip(f) {
    const lines = [];
    if (f.human) lines.push("Edited by reviewer");
    lines.push(...(f.issues || []));
    if (!lines.length) lines.push(f.verify_msg || "");
    if (f.source_span_ids?.length) lines.push(`Source: ${f.source_span_ids.join(", ")}`);
    return lines.join("\n");
  }

  // ------------------------------------------------------------ tooltip
  const tip = $("tooltip");
  document.addEventListener("mouseover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el) { tip.hidden = true; return; }
    tip.textContent = el.dataset.tip;
    tip.hidden = false;
    placeTip(e);
  });
  document.addEventListener("mousemove", (e) => { if (!tip.hidden) placeTip(e); });
  function placeTip(e) {
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let x = e.clientX + pad, y = e.clientY + pad;
    if (x + w > innerWidth - 8) x = e.clientX - w - pad;
    if (y + h > innerHeight - 8) y = e.clientY - h - pad;
    tip.style.left = x + "px";
    tip.style.top = y + "px";
  }

  // ------------------------------------------------------------ init
  async function init() {
    try {
      const [cfg, samples] = await Promise.all([api("/api/config"), api("/api/samples")]);
      state.config = cfg;
      renderEnv(cfg);
      const sel = $("sample-select");
      for (const s of samples.samples) {
        const o = document.createElement("option");
        o.value = s; o.textContent = s;
        sel.appendChild(o);
      }
      if (!samples.samples.length) sel.options[0].textContent = "No samples in samples/";
    } catch (e) {
      showError(e.message);
    }
    const params = new URLSearchParams(location.search);
    if (params.get("run")) {
      await loadRun(params.get("run"));
      // deep links: ?run=<id>&tab=bom&select=bom.1
      if (state.result && TAB_SECTIONS[params.get("tab")] || params.get("tab") === "checks") setTab(params.get("tab"));
      const sel = params.get("select");
      if (state.result && sel && state.result.rows.some((r) => r.row_id === sel)) {
        const row = state.result.rows.find((r) => r.row_id === sel);
        setTab(row.section === "note" ? "dim" : row.section);
        if (params.get("tab") === "checks") setTab("checks");
        select(sel, { scrollRow: true });
      }
    }
  }

  function renderEnv(cfg) {
    const parts = [`<span class="chip">Model: ${esc(cfg.model)}</span>`];
    parts.push(cfg.has_api_key
      ? `<span class="chip ok">API key set</span>`
      : `<span class="chip bad" data-tip="Copy .env.example to .env and set OPENAI_API_KEY, then restart the server.">No API key</span>`);
    parts.push(cfg.ocr_available
      ? `<span class="chip ok">OCR ready</span>`
      : `<span class="chip" data-tip="${esc(cfg.ocr_message)}">OCR off</span>`);
    $("env").innerHTML = parts.join("");
  }

  // ------------------------------------------------------------ start runs
  $("sample-select").addEventListener("change", (e) => ($("sample-btn").disabled = !e.target.value));
  $("sample-btn").addEventListener("click", () => {
    const name = $("sample-select").value;
    if (name) startRun(() => api("/api/runs/sample", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) }), name);
  });
  $("file-input").addEventListener("change", (e) => {
    const f = e.target.files[0];
    if (f) uploadFile(f);
    e.target.value = "";
  });

  function uploadFile(file) {
    if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
      showError("Please choose a PDF file.");
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    startRun(() => api("/api/runs/upload", { method: "POST", body: fd }), file.name);
  }

  // whole-page drag and drop
  let dragDepth = 0;
  window.addEventListener("dragenter", (e) => { if (e.dataTransfer?.types?.includes("Files")) { dragDepth++; $("drag-overlay").hidden = false; $("dropzone").classList.add("hover"); } });
  window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("drag-overlay").hidden = true; $("dropzone").classList.remove("hover"); } });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    dragDepth = 0;
    $("drag-overlay").hidden = true;
    $("dropzone").classList.remove("hover");
    const f = e.dataTransfer?.files?.[0];
    if (f) uploadFile(f);
  });

  let busy = false;
  async function startRun(starter, name) {
    if (busy) return;
    busy = true;
    showError(null);
    setControlsDisabled(true);
    state.result = null;
    state.selected = null;
    state.editing = null;
    $("file-name").textContent = name;
    $("viewer-empty").hidden = false;
    $("page-wrap").hidden = true;
    $("viewer-empty").innerHTML = `<div class="spinner"></div><p>Processing ${esc(name)}…</p>`;
    $("tab-body").innerHTML = `<div class="empty small"><p class="muted">Working…</p></div>`;
    $("summary").innerHTML = `<span class="muted">Processing…</span>`;
    $("review-progress").hidden = true;
    $("warn-banner").hidden = true;
    setExports(false);
    setStepper("rendering");
    try {
      const { run_id } = await starter();
      state.runId = run_id;
      await poll(run_id);
      await loadRun(run_id);
      history.replaceState(null, "", `?run=${encodeURIComponent(run_id)}`);
    } catch (e) {
      showError(e.message);
      setStepper(null, true);
      $("viewer-empty").innerHTML = `<svg viewBox="0 0 64 64"><rect x="8" y="6" width="48" height="52" rx="3"/><path d="M22 24l20 20M42 24L22 44"/></svg><p>${esc(e.message)}</p>`;
      $("tab-body").innerHTML = `<div class="empty small"><p class="muted">No results.</p></div>`;
      $("summary").innerHTML = `<span class="muted">Run failed.</span>`;
    } finally {
      busy = false;
      setControlsDisabled(false);
    }
  }

  function setControlsDisabled(d) {
    $("sample-btn").disabled = d || !$("sample-select").value;
    $("sample-select").disabled = d;
    $("dropzone").style.pointerEvents = d ? "none" : "";
    $("dropzone").style.opacity = d ? ".55" : "";
  }

  let failedStage = null;
  function setStepper(stage, error = false) {
    if (stage) failedStage = stage;
    const idx = stage === "done" ? STAGES.length : STAGES.indexOf(stage ?? failedStage);
    document.querySelectorAll("#stepper li").forEach((li, i) => {
      li.className = "";
      if (error && i === idx) li.className = "error";
      else if (i < idx) li.className = "done";
      else if (i === idx && !error) li.className = "active";
    });
  }

  async function poll(runId) {
    for (;;) {
      const s = await api(`/api/runs/${encodeURIComponent(runId)}/status`);
      if (s.stage && s.stage !== "queued") setStepper(s.stage);
      if (s.state === "done") { setStepper("done"); return; }
      if (s.state === "error") throw new Error(s.error || "The run failed.");
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  async function loadRun(runId) {
    try {
      const result = await api(`/api/runs/${encodeURIComponent(runId)}`);
      state.runId = runId;
      state.result = result;
      state.page = 1;
      $("file-name").textContent = result.filename;
      setStepper("done");
      setExports(true);
      renderAll();
      showPage(1);
    } catch (e) {
      showError(e.message);
    }
  }

  function setExports(on) {
    for (const [id, ext] of [["export-csv", "csv"], ["export-json", "json"]]) {
      const a = $(id);
      a.setAttribute("aria-disabled", on ? "false" : "true");
      if (on) a.href = `/api/runs/${encodeURIComponent(state.runId)}/export.${ext}`;
      else a.removeAttribute("href");
    }
  }

  // ------------------------------------------------------------ viewer
  function showPage(n) {
    const r = state.result;
    if (!r) return;
    n = Math.max(1, Math.min(n, r.pages.length));
    state.page = n;
    const img = $("page-img");
    $("viewer-empty").hidden = true;
    $("page-wrap").hidden = false;
    const src = `/api/runs/${encodeURIComponent(state.runId)}/pages/${n}`;
    if (!img.src.endsWith(src)) img.src = src;
    $("page-label").textContent = `Page ${n} / ${r.pages.length}`;
    $("prev-page").disabled = n <= 1;
    $("next-page").disabled = n >= r.pages.length;
    applyZoom();
    renderBoxes();
  }

  function fitWidth() {
    const v = $("viewer");
    const p = state.result?.pages[state.page - 1];
    const availW = v.clientWidth - 28;
    if (!p) return availW;
    const availH = v.clientHeight - 28;
    // fit whole page on screen when height-constrained
    return Math.max(200, Math.min(availW, availH * (p.width_pt / p.height_pt)));
  }

  function applyZoom() {
    const scale = state.zoom || 1;
    $("page-wrap").style.width = `${Math.round(fitWidth() * scale)}px`;
    $("zoom-label").textContent = state.zoom ? `${Math.round(scale * 100)}%` : "Fit";
  }

  function setZoom(z, anchor) {
    const v = $("viewer");
    const before = { x: (v.scrollLeft + v.clientWidth / 2) / v.scrollWidth, y: (v.scrollTop + v.clientHeight / 2) / v.scrollHeight };
    state.zoom = z <= 1.001 ? 0 : Math.min(z, 5);
    applyZoom();
    const a = anchor || before;
    v.scrollLeft = a.x * v.scrollWidth - v.clientWidth / 2;
    v.scrollTop = a.y * v.scrollHeight - v.clientHeight / 2;
  }

  $("zoom-in").addEventListener("click", () => setZoom((state.zoom || 1) * 1.35));
  $("zoom-out").addEventListener("click", () => setZoom((state.zoom || 1) / 1.35));
  $("zoom-fit").addEventListener("click", () => setZoom(0));
  $("prev-page").addEventListener("click", () => showPage(state.page - 1));
  $("next-page").addEventListener("click", () => showPage(state.page + 1));
  $("show-boxes").addEventListener("change", (e) => $("overlay").classList.toggle("hide-idle", !e.target.checked));
  window.addEventListener("resize", () => state.result && applyZoom());
  $("viewer").addEventListener("wheel", (e) => {
    if (!e.ctrlKey || !state.result) return;
    e.preventDefault();
    setZoom((state.zoom || 1) * (e.deltaY < 0 ? 1.15 : 1 / 1.15));
  }, { passive: false });

  function renderBoxes() {
    const r = state.result;
    const ov = $("overlay");
    ov.innerHTML = "";
    if (!r) return;
    const p = r.pages[state.page - 1];
    const pad = 2.5; // points
    for (const row of r.rows) {
      for (const [name, f] of Object.entries(row.fields)) {
        for (const b of f.boxes || []) {
          if (b.page !== state.page) continue;
          const [x0, y0, x1, y1] = b.bbox;
          const d = document.createElement("div");
          d.className = `box ${f.status}`;
          d.dataset.row = row.row_id;
          d.dataset.field = name;
          d.style.left = `${((x0 - pad) / p.width_pt) * 100}%`;
          d.style.top = `${((y0 - pad) / p.height_pt) * 100}%`;
          d.style.width = `${((x1 - x0 + 2 * pad) / p.width_pt) * 100}%`;
          d.style.height = `${((y1 - y0 + 2 * pad) / p.height_pt) * 100}%`;
          const label = row.section === "title" ? row.label : `${row.label} · ${name.replace("_", " ")}`;
          d.dataset.tip = `${label}: ${f.value ?? "—"}\n${STATUS_TEXT[f.status]}\n${fieldTip(f)}`;
          if (row.row_id === state.selected) d.classList.add("selected");
          ov.appendChild(d);
        }
      }
    }
  }

  $("overlay").addEventListener("click", (e) => {
    const box = e.target.closest(".box");
    if (!box) return;
    const row = state.result.rows.find((r) => r.row_id === box.dataset.row);
    if (!row) return;
    const tab = row.section === "note" ? "dim" : row.section;
    if (state.tab !== tab) setTab(tab);
    select(row.row_id, { scrollRow: true, scrollBox: false });
  });

  function scrollToBox(rowId) {
    const boxes = [...$("overlay").querySelectorAll(`.box[data-row="${CSS.escape(rowId)}"]`)];
    if (!boxes.length) return;
    const v = $("viewer");
    const vr = v.getBoundingClientRect();
    const rects = boxes.map((b) => b.getBoundingClientRect());
    const cx = (Math.min(...rects.map((r) => r.left)) + Math.max(...rects.map((r) => r.right))) / 2;
    const cy = (Math.min(...rects.map((r) => r.top)) + Math.max(...rects.map((r) => r.bottom))) / 2;
    v.scrollTo({ left: v.scrollLeft + cx - vr.left - vr.width / 2, top: v.scrollTop + cy - vr.top - vr.height / 2, behavior: "smooth" });
  }

  function select(rowId, { scrollRow = false, scrollBox = true } = {}) {
    state.selected = rowId;
    const row = state.result.rows.find((r) => r.row_id === rowId);
    const firstBox = row && Object.values(row.fields).flatMap((f) => f.boxes || [])[0];
    if (firstBox && firstBox.page !== state.page) showPage(firstBox.page);
    else renderBoxes();
    document.querySelectorAll("tr.row").forEach((tr) => tr.classList.toggle("selected", tr.dataset.row === rowId));
    if (scrollRow) document.querySelector(`tr.row[data-row="${CSS.escape(rowId)}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    if (scrollBox) {
      // zoom in a little so the link is obvious on a big sheet
      if (!state.zoom && firstBox) setZoom(1.8);
      requestAnimationFrame(() => scrollToBox(rowId));
    }
    if (!firstBox && row) toast("No source location for this value");
  }

  // ------------------------------------------------------------ data panel
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

  function setTab(tab) {
    state.tab = tab;
    document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    renderTab();
  }

  function renderAll() {
    renderSummary();
    renderBadges();
    renderTab();
    const w = state.result.warnings || [];
    $("warn-banner").hidden = !w.length;
    $("warn-banner").textContent = w.join(" ");
  }

  function renderSummary() {
    const s = state.result.summary;
    $("summary").innerHTML = [
      `<span class="count verified">${s.rows.verified} verified</span>`,
      `<span class="count needs_review">${s.rows.needs_review} need review</span>`,
      `<span class="count failed">${s.rows.failed} failed</span>`,
    ].join("");
    $("review-progress").hidden = false;
    $("rp-text").innerHTML = `<b>${s.reviewed}</b> of ${s.total} fields reviewed`;
    $("rp-fill").style.width = `${s.total ? (100 * s.reviewed) / s.total : 0}%`;
  }

  function renderBadges() {
    const rows = state.result.rows;
    for (const [tab, sections] of Object.entries(TAB_SECTIONS)) {
      const rs = rows.filter((r) => sections.includes(r.section) && !r.review);
      const failed = rs.filter((r) => r.status === "failed").length;
      const review = rs.filter((r) => r.status === "needs_review").length;
      const el = document.querySelector(`[data-badge="${tab}"]`);
      const n = failed + review;
      el.textContent = n ? n : "";
      el.className = `badge ${failed ? "failed" : review ? "needs_review" : ""}`;
    }
    const c = state.result.summary.checks;
    const el = document.querySelector(`[data-badge="checks"]`);
    el.textContent = c.fail ? `${c.fail} fail` : c.warn ? `${c.warn} warn` : "✓";
    el.className = `badge ${c.fail ? "failed" : c.warn ? "needs_review" : "ok"}`;
  }

  function renderTab() {
    const body = $("tab-body");
    if (!state.result) return;
    const rows = state.result.rows;
    let html = "";
    if (state.tab === "title") {
      html = table(["Field", "Value", "Conf.", "Status", ""], rows.filter((r) => r.section === "title").map((r) => titleRow(r)));
    } else if (state.tab === "bom") {
      const bom = rows.filter((r) => r.section === "bom");
      html = bom.length
        ? table([...BOM_COLS.map((c) => c[1]), "Status", ""], bom.map((r) => multiRow(r, BOM_COLS.map((c) => c[0]))))
        : `<div class="empty small"><p>No parts list / BOM table was found on this drawing.</p><p class="muted">Single-part drawings export as one BOM line.</p></div>`;
    } else if (state.tab === "dim") {
      const dims = rows.filter((r) => r.section === "dim");
      const notes = rows.filter((r) => r.section === "note");
      html = `<div class="section-title">Key dimensions</div>` +
        (dims.length ? table(["Feature", "Nominal", "Tolerance", "Unit", "Status", ""], dims.map((r) => multiRow(r, ["nominal", "tolerance"], { lead: r.label, unit: r.unit }))) : `<p class="muted" style="padding:0 12px">None extracted.</p>`) +
        `<div class="section-title">Notes &amp; specs</div>` +
        (notes.length ? table(["Type", "Note", "Status", ""], notes.map((r) => multiRow(r, ["text"], { lead: r.label }))) : `<p class="muted" style="padding:0 12px">None extracted.</p>`);
    } else {
      html = checksList();
    }
    body.innerHTML = html;
    if (state.selected) document.querySelector(`tr.row[data-row="${CSS.escape(state.selected)}"]`)?.classList.add("selected");
  }

  function table(headers, rowsHtml) {
    return `<table class="grid"><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rowsHtml.join("")}</tbody></table>`;
  }

  function valueCell(row, name, extraClass = "") {
    const f = row.fields[name];
    if (state.editing === row.row_id) {
      return `<td class="val ${extraClass}"><input class="edit" size="3" data-field="${name}" value="${esc(f.value ?? "")}" placeholder="(empty)"></td>`;
    }
    const cls = f.status !== "verified" ? `cell-${f.status}` : "";
    const v = f.value == null ? `<td class="val null ${cls} ${extraClass}" data-tip="${esc(fieldTip(f))}">not found</td>`
      : `<td class="val ${cls} ${extraClass}" data-tip="${esc(fieldTip(f))}">${esc(f.value)}${f.human ? " ✎" : ""}</td>`;
    return v;
  }

  function statusCell(row) {
    return `<td><span class="pill ${row.status}" data-tip="${esc(rowTip(row))}">${PILL_TEXT[row.status]}</span></td>`;
  }

  function actionsCell(row) {
    if (state.editing === row.row_id) {
      return `<td class="actions"><button class="act save" data-act="save">Save</button><button class="act" data-act="cancel">Cancel</button></td>`;
    }
    if (row.review) {
      const label = row.review.state === "approved" ? "✓ Approved" : "✎ Edited";
      return `<td class="actions"><span class="reviewed">${label}</span><button class="act" data-act="edit" title="Edit">Edit</button><button class="act" data-act="reset" title="Undo review">Undo</button></td>`;
    }
    return `<td class="actions"><button class="act approve" data-act="approve" title="Approve value">Approve</button><button class="act" data-act="edit" title="Edit value">Edit</button></td>`;
  }

  function titleRow(r) {
    const f = r.fields.value;
    return `<tr class="row" data-row="${r.row_id}"><td class="label">${esc(r.label)}</td>${valueCell(r, "value")}<td><span class="conf ${f.confidence}">${f.human ? "human" : f.confidence}</span></td>${statusCell(r)}${actionsCell(r)}</tr>`;
  }

  function multiRow(r, names, opts = {}) {
    let cells = "";
    if (opts.lead !== undefined) cells += `<td class="label">${esc(opts.lead)}</td>`;
    cells += names.map((n) => valueCell(r, n, n === "quantity" ? "num nowrap" : ["item_no", "part_number", "nominal", "tolerance"].includes(n) ? "nowrap" : "")).join("");
    if ("unit" in opts) cells += `<td class="label">${esc(opts.unit ?? "—")}</td>`;
    return `<tr class="row" data-row="${r.row_id}">${cells}${statusCell(r)}${actionsCell(r)}</tr>`;
  }

  function checksList() {
    const order = { fail: 0, warn: 1, pass: 2 };
    const checks = [...state.result.checks].sort((a, b) => order[a.status] - order[b.status]);
    const icon = { pass: "✓", warn: "!", fail: "✕" };
    return `<ul class="checks">${checks.map((c) => {
      const target = c.targets[0]?.split(":")[0];
      return `<li class="check ${c.status} ${target ? "clickable" : ""}" ${target ? `data-target="${esc(target)}"` : ""}>
        <span class="ic">${icon[c.status]}</span>
        <div><div class="nm">${esc(c.name)}</div><div class="msg">${esc(c.message)}</div></div>
        ${target ? `<span class="go">Show →</span>` : ""}
      </li>`;
    }).join("")}</ul>`;
  }

  $("tab-body").addEventListener("click", async (e) => {
    const chk = e.target.closest(".check[data-target]");
    if (chk) {
      const row = state.result.rows.find((r) => r.row_id === chk.dataset.target);
      if (row) { setTab(row.section === "note" ? "dim" : row.section); select(row.row_id, { scrollRow: true }); }
      return;
    }
    const btn = e.target.closest("button[data-act]");
    const tr = e.target.closest("tr.row");
    if (!tr) return;
    const rowId = tr.dataset.row;
    if (!btn) {
      if (!e.target.closest("input")) select(rowId);
      return;
    }
    const act = btn.dataset.act;
    if (act === "edit") { state.editing = rowId; state.selected = rowId; renderTab(); tr.parentElement.querySelector(`tr[data-row="${CSS.escape(rowId)}"] input`)?.focus(); select(rowId); return; }
    if (act === "cancel") { state.editing = null; renderTab(); return; }
    if (act === "save") return saveEdit(rowId);
    if (act === "approve" || act === "reset") return review(rowId, act);
  });

  $("tab-body").addEventListener("keydown", (e) => {
    if (!e.target.matches("input.edit")) return;
    if (e.key === "Enter") saveEdit(state.editing);
    if (e.key === "Escape") { state.editing = null; renderTab(); }
  });

  async function saveEdit(rowId) {
    const tr = document.querySelector(`tr.row[data-row="${CSS.escape(rowId)}"]`);
    const changes = {};
    tr.querySelectorAll("input.edit").forEach((i) => (changes[i.dataset.field] = i.value));
    state.editing = null;
    await review(rowId, "edit", changes);
  }

  async function review(rowId, action, changes) {
    try {
      const result = await api(`/api/runs/${encodeURIComponent(state.runId)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ row_id: rowId, action, changes }),
      });
      state.result = result;
      renderAll();
      renderBoxes();
      toast(action === "approve" ? "Approved · logged to audit trail" : action === "edit" ? "Edit saved · checks re-run · logged" : "Review undone · logged");
    } catch (e) {
      showError(e.message);
    }
  }

  init();
})();
