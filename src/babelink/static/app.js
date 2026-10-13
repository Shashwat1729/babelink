"use strict";
// All data reaches the DOM through textContent / text nodes (el() appends strings as text), never innerHTML.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const enc = encodeURIComponent;

function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "for" || k === "list" || k === "role" || k.startsWith("data-") || k.startsWith("aria-")) n.setAttribute(k, v === true ? "" : v);
    else if (k in n) n[k] = v;
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) n.append(kid);
  return n;
}
const clear = (n) => { n.replaceChildren(); return n; };

// One request per action at a time: a second click (or Enter) while the first is still in flight does nothing.
// Actions that add things also send an Idempotency-Key, so the server returns the first answer if a repeat gets through anyway.
const inflight = new Set();
async function once(name, fn) {
  if (inflight.has(name)) return;
  inflight.add(name);
  try { return await fn(); } finally { inflight.delete(name); }
}
const newKey = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`);
let pasteKey = newKey(), workKey = newKey();
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

const ICONS = {
  plus: "M12 5v14M5 12h14", upload: "M12 16V4M7 9l5-5 5 5M4 20h16", download: "M12 4v12M7 11l5 5 5-5M4 20h16", check: "M5 12.5l4.5 4.5L19 7",
  x: "M6 6l12 12M18 6L6 18", left: "M15 5l-7 7 7 7", right: "M9 5l7 7-7 7", trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  play: "M7 4.5v15l12-7.5z", stop: "M6 6h12v12H6z", contrast: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 3v18M12 3a9 9 0 010 18",
  copy: "M9 9h11v11H9zM5 15V4h10", info: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 11v5M12 8h.01", file: "M7 3h7l5 5v13H7zM14 3v5h5",
  paste: "M9 4h6v3H9zM7 5H5v16h14V5h-2M9 12h6M9 16h4",
  image: "M4 5h16v14H4zM4 16l5-5 4 4 3-3 4 4M9 9h.01",
};
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", "i"); s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", ICONS[name]); s.append(p);
  return s;
}

async function api(method, path, body, headers) {
  const opt = { method, headers: { "X-Requested-With": "babelink", ...headers } };
  if (body instanceof FormData) opt.body = body;
  else if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  if (!r.ok) {
    let m = r.statusText;
    try { const j = await r.json(); m = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch (e) { /* not json */ }
    throw new Error(m);
  }
  return r.json();
}

// ---- constants ------------------------------------------------------------------------------------
const LANGS = { auto: "Detect automatically", zh: "Chinese", ja: "Japanese", ko: "Korean", en: "English", es: "Spanish", fr: "French", de: "German", ru: "Russian", pt: "Portuguese", it: "Italian", hi: "Hindi", ar: "Arabic" };
const langName = (c) => LANGS[c] || c;
const STYLES = {
  literal: ["Literal", "Stays close to the original wording."],
  "faithful-natural": ["Faithful and natural", "Accurate, but reads like native prose. Best default."],
  literary: ["Literary", "More polished prose; keeps the tone and rhythm."],
  "highly-literary": ["Highly literary", "Freest and most stylised. Check it more carefully."],
};
const styleLabel = (s) => (STYLES[s] || [s])[0];
const STATE = {
  UNPROCESSED: ["Not started", ""], ANALYZED: ["Not started", ""], TRANSLATED: ["Draft", "warn"], REFINED: ["Refined", "ok"],
  QA_PASSED: ["Checked", "ok"], NEEDS_REVIEW: ["Needs review", "err"], HUMAN_APPROVED: ["Approved", "ok"], STALE: ["Outdated", "warn"],
};
const DONE = ["REFINED", "QA_PASSED", "HUMAN_APPROVED"];
const OPEN = ["UNPROCESSED", "ANALYZED", "STALE"];
const ENTITY_SECTIONS = ["characters", "locations", "organizations", "items", "abilities", "factions"];
const PROV = { ollama: "Ollama", "openai-compatible": "OpenAI-compatible server", mock: "Demo" };
const TABS = [["chapters", "Chapters"], ["names", "Names"], ["bible", "Story Bible"], ["quality", "Quality"], ["settings", "Settings"]];
const PAGE_FILE = /\.(png|jpe?g|webp|bmp|cbz|zip)$/i, BOOK_ACCEPT = ".epub,.txt,.html,.htm,.xhtml,.md,.png,.jpg,.jpeg,.webp,.bmp,.cbz,.zip";
const JOB_NAMES = { import_images: "Reading pages", translate: "Translating", refine: "Refining", qa: "Checking", analyze: "Analyzing", suggest_terms: "Suggesting names" };
const THEMES = [["system", "System"], ["light", "Light"], ["dark", "Dark"]];
const ACCENTS = [["vermilion", "Vermilion", "#b4382a"], ["jade", "Jade", "#1f7a63"], ["indigo", "Indigo", "#3a56b0"], ["amber", "Amber", "#9a5b0a"], ["plum", "Plum", "#7a3f8f"]];

const S = {
  route: { view: "library" }, nav: 0, projects: [], pid: null, project: null, chapters: [], valid: null, terms: null, bible: null, qa: null,
  data: null, job: null, uploading: null, editing: null, editSrc: false, pageView: "both", flash: null, stale: false,
  chQuery: "", chShow: 100, nameQuery: "", nameShow: 40, onlyOpen: false, bilingual: false, aiReview: false,
  cfg: loadCfg(), mstat: null, look: loadLook(),
};

function loadCfg() {
  const d = { provider: "ollama", model: "hymt2-1.8b", baseUrl: "", refineModel: "", summarize: true, chosen: false, batch: 0 };
  try { return { ...d, ...JSON.parse(localStorage.getItem("st-cfg") || "{}") }; } catch (e) { return d; }
}
function saveCfg() { try { localStorage.setItem("st-cfg", JSON.stringify(S.cfg)); } catch (e) { /* storage blocked */ } }
function loadLook() { try { return JSON.parse(localStorage.getItem("st-look") || "{}"); } catch (e) { return {}; } }

let toastTimer;
function toast(msg, error) {
  const t = $("#toast");
  t.textContent = msg; t.className = "toast" + (error ? " error" : ""); t.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { t.hidden = true; }, error ? 7000 : 3500);
}
const fail = (e) => toast(e.message || String(e), true);
const running = () => S.job && S.job.project_id === S.pid && S.job.status === "running";

// ---- appearance -----------------------------------------------------------------------------------
function applyLook() {
  const r = document.documentElement, { theme, accent } = S.look;
  if (theme === "light" || theme === "dark") r.dataset.theme = theme; else delete r.dataset.theme;
  if (accent && accent !== "vermilion") r.dataset.accent = accent; else delete r.dataset.accent;
}
function setLook(patch) {
  S.look = { ...S.look, ...patch }; applyLook();
  try { localStorage.setItem("st-look", JSON.stringify(S.look)); } catch (e) { /* storage blocked */ }
  renderLook();
}
function renderLook() {
  const t = clear($("#theme-ctl")), a = clear($("#accent-ctl"));
  for (const [k, label] of THEMES) {
    t.append(el("button", { type: "button", role: "radio", "aria-checked": String((S.look.theme || "system") === k), onclick: () => setLook({ theme: k }) }, label));
  }
  for (const [k, label, color] of ACCENTS) {
    a.append(el("button", { type: "button", class: "swatch", role: "radio", title: label, "aria-label": label, "aria-checked": String((S.look.accent || "vermilion") === k),
      style: `--sw:${color}`, onclick: () => setLook({ accent: k }) }));
  }
}
function wireLook() {
  const btn = $("#look-btn"), pop = $("#look-pop");
  btn.append(icon("contrast"));
  const close = () => { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.addEventListener("click", () => { pop.hidden = !pop.hidden; btn.setAttribute("aria-expanded", String(!pop.hidden)); });
  document.addEventListener("click", (ev) => { if (!pop.hidden && !ev.composedPath().includes(pop.parentElement)) close(); });  // composedPath: the clicked control may already be re-rendered away
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && !pop.hidden) { close(); btn.focus(); } });
  applyLook(); renderLook();
}

// ---- model status ---------------------------------------------------------------------------------
const hasModel = (list, name) => list.some((m) => m.name === name || m.name === name + ":latest");

async function refreshModelStatus() {
  S.mstat = null; renderChip();
  if (S.cfg.provider === "mock") { renderChip(); return; }
  try {
    const m = await api("GET", `/api/models?provider=${enc(S.cfg.provider)}&base_url=${enc(S.cfg.baseUrl)}&target=${enc(S.project ? S.project.target_lang : "en")}`);
    S.mstat = m;
    if (!S.cfg.chosen && m.installed.length && !hasModel(m.installed, S.cfg.model)) { S.cfg.model = m.installed[0].name; saveCfg(); }  // first run: use what is installed
  } catch (e) { S.mstat = { reachable: false, installed: [], error: e.message }; }
  renderChip(); if (S.route.view === "work") softRender();
}
function modelState() {
  const c = S.cfg, m = S.mstat;
  if (c.provider === "mock") return ["", "Demo mode · copies the text"];
  if (!m) return ["", "Checking model…"];
  if (!m.reachable) return ["err", `${PROV[c.provider]} not reachable`];
  if (!c.model) return ["warn", "Choose a model"];
  if (m.installed.length && !hasModel(m.installed, c.model)) return ["warn", `${c.model} is not installed`];
  return ["ok", c.model];
}
function renderChip() {
  const [tone, text] = modelState();
  $("#model-dot").className = "dot " + tone; $("#model-text").textContent = text;
  $("#model-chip").title = tone === "ok" ? "Translation model is ready. Click to change." : "Click to choose or fix the translation model.";
}
// Returns true when a job may start; otherwise opens the picker so the user fixes it right there.
function requireModel() {
  const [tone] = modelState();
  if (tone === "err" || tone === "warn") { toast("Pick a working model first.", true); openModelDialog(); return false; }
  return true;
}

// ---- data loading ---------------------------------------------------------------------------------
async function loadLibrary() { S.projects = await api("GET", "/api/projects"); }

async function loadWork(pid, withJob = true) {
  const [project, chapters, valid, job] = await Promise.all([
    api("GET", `/api/projects/${pid}`), api("GET", `/api/projects/${pid}/chapters`), api("GET", `/api/projects/${pid}/validate`),
    withJob ? api("GET", `/api/projects/${pid}/job`) : Promise.resolve(undefined),
  ]);
  if (S.pid !== pid) Object.assign(S, { terms: null, bible: null, qa: null, data: null, chQuery: "", chShow: 100, nameQuery: "", nameShow: 40, job: null });
  Object.assign(S, { pid, project, chapters, valid });
  if (withJob) { if (job && job.status === "running") S.job = job; else if (S.job && S.job.project_id !== pid) S.job = null; }
}

async function loadTab(tab) {
  const pid = S.pid;
  if (tab === "names") S.terms = await api("GET", `/api/projects/${pid}/terms`);
  else if (tab === "bible") S.bible = await api("GET", `/api/projects/${pid}/bible`);
  else if (tab === "quality") S.qa = await api("GET", `/api/projects/${pid}/qa`);
}

async function loadChapter(cid) { S.data = await api("GET", `/api/projects/${S.pid}/chapters/${cid}/segments`); }

// Reload whatever the current view shows. Used after every change.
async function reload() {
  const r = S.route;
  if (r.view === "library") return loadLibrary();
  await loadWork(S.pid, false);
  if (r.view === "chapter") await loadChapter(r.cid); else await loadTab(r.tab);
}

// ---- routing --------------------------------------------------------------------------------------
function parseHash() {
  const p = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean), num = (x) => /^\d+$/.test(x || "");
  if (p[0] === "work" && num(p[1])) {
    if (p[2] === "chapter" && num(p[3])) return { view: "chapter", pid: +p[1], cid: +p[3] };
    return { view: "work", pid: +p[1], tab: TABS.some(([k]) => k === p[2]) ? p[2] : "chapters" };
  }
  return { view: "library" };
}

async function route() {
  const r = parseHash(), token = ++S.nav, same = r.pid === S.pid;
  if (S.job && S.job.status !== "running") S.job = null;  // a finished job's card belongs to the page it ran on
  S.route = r; S.editing = null; S.editSrc = false; S.stale = false; followId = null;
  try {
    if (r.view === "library") await loadLibrary();
    else {
      await loadWork(r.pid);
      if (r.view === "chapter") await loadChapter(r.cid); else await loadTab(r.tab);
    }
  } catch (e) {
    if (token !== S.nav) return;
    fail(e); if (r.view !== "library") { location.hash = "#/"; return; }
  }
  if (token !== S.nav) return;
  render();
  if (!same || r.view !== "work") window.scrollTo(0, 0);
  if (running()) watchJob(S.job);
}
const go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };

// ---- render ---------------------------------------------------------------------------------------
const typing = () => { const a = document.activeElement; return !!a && $("#app").contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName); };
function softRender() { if (S.editing != null || typing()) { S.stale = true; return; } render(); }

function render() {
  const app = clear($("#app")), r = S.route;
  renderCrumbs();
  if (r.view === "library") app.append(viewLibrary());
  else if (!S.project) app.append(el("p", { class: "muted" }, "Loading…"));
  else app.append(r.view === "work" ? viewWork() : viewChapter());
  renderJob();
  if (S.flash != null) { const n = $(`#seg-${S.flash}`); if (n) { n.scrollIntoView({ block: "center" }); n.classList.add("flash"); } S.flash = null; }
}

function renderCrumbs() {
  const c = clear($("#crumbs")), r = S.route, sep = () => el("span", { class: "sep", "aria-hidden": "true" }, "/");
  let title = "Babelink";
  if (r.view !== "library" && S.project) {
    c.append(el("a", { href: "#/" }, "Library"), sep());
    if (r.view === "work") { c.append(el("span", { class: "here", "aria-current": "page" }, S.project.name)); title = S.project.name + " · Babelink"; }
    else {
      const i = S.chapters.findIndex((x) => x.id === r.cid);
      c.append(el("a", { href: `#/work/${S.pid}` }, S.project.name), sep(), el("span", { class: "here", "aria-current": "page" }, `Chapter ${i + 1}`));
      title = `Chapter ${i + 1} · ${S.project.name}`;
    }
  }
  document.title = title;
}

function hue(s) { let h = 0; for (const ch of s) h = (h * 31 + ch.codePointAt(0)) % 360; return h; }
function cover(name, big) {
  const first = Array.from(name.trim())[0] || "?";
  return el("div", { class: "cover" + (big ? " lg" : ""), style: `--h:${hue(name)}`, "aria-hidden": "true" }, first.toUpperCase());
}
function progress(done, total) {
  return el("div", { class: "bar", role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(total ? Math.round((100 * done) / total) : 0) },
    el("span", { style: `width:${total ? (100 * done) / total : 0}%` }));
}

// ---- library view ---------------------------------------------------------------------------------
function viewLibrary() {
  const ps = S.projects;
  if (!ps.length) return el("section", { class: "welcome" },
    el("div", { class: "welcome-top" },
      el("div", { class: "stack" },
        el("div", { class: "eyebrow" }, "Local · private · yours"),
        el("h1", { class: "serif" }, "Translate a whole novel, chapter by chapter."),
        el("p", { class: "lead" }, "Create a work for your book, add its EPUB, and every chapter appears. Names, notes and earlier chapters carry into each new one, so the translation stays consistent. Then download the result as an EPUB."),
        el("div", { class: "actions" }, el("button", { class: "btn primary", type: "button", onclick: openWorkDialog }, icon("plus"), "Create your first work"))),
      el("div", { class: "glyph", "aria-hidden": "true" }, "译")),
    el("ol", { class: "steps", style: "list-style:none;padding:0;margin:0" },
      step("Name your work", "One work per novel or series."), step("Add the EPUB", "All chapters are read in and split into paragraphs."),
      step("Choose a model", "Runs on your computer with Ollama, or any server you pick."), step("Translate and download", "Get a translated EPUB with the original layout.")));
  return el("div", { class: "stack-lg" },
    el("div", { class: "page-head" }, el("div", {}, el("h1", { class: "serif" }, "Your library"), el("p", { class: "muted" }, plural(ps.length, "work"))),
      el("button", { class: "btn primary", type: "button", onclick: openWorkDialog }, icon("plus"), "New work")),
    el("div", { class: "grid" }, ps.map(workCard)));
}
const step = (t, d) => el("li", { class: "step" }, el("strong", {}, t), el("span", { class: "muted small" }, d));

function workCard(p) {
  return el("a", { class: "work-card", href: `#/work/${p.id}` }, cover(p.name),
    el("div", { class: "body" }, el("h3", {}, p.name),
      el("span", { class: "muted small" }, `${langName(p.source_lang)} → ${langName(p.target_lang)}`),
      p.chapters ? [progress(p.translated, p.segments), el("span", { class: "small muted" }, `${plural(p.chapters, "chapter")} · ${p.segments ? Math.round((100 * p.translated) / p.segments) : 0}% translated`)]
        : el("span", { class: "small muted" }, "No chapters yet. Open it to add an EPUB.")));
}

// ---- work view ------------------------------------------------------------------------------------
const remaining = (p) => p.segments - p.translated + (p.states.STALE || 0);

function viewWork() {
  return el("div", { class: "stack-lg" }, workHero(), el("div", { id: "job-slot" }), tabsBar(), el("div", { class: "panel" }, tabPanel()));
}

function workHero() {
  const p = S.project, left = remaining(p), n = p.chapters, pct = p.segments ? Math.round((100 * p.translated) / p.segments) : 0;
  return el("section", { class: "hero card" },
    el("div", { class: "hero-main" }, cover(p.name, true),
      el("div", { class: "hero-text" },
        el("h1", { class: "serif" }, p.name),
        el("p", { class: "muted" }, `${langName(p.source_lang)} → ${langName(p.target_lang)} · ${styleLabel(p.style)}`),
        n ? el("div", { class: "stats" }, stat(n, "chapters"), stat(`${pct}%`, "translated"), stat(p.terms_with_translation, "names locked"), stat(p.qa_errors, "QA errors")) : null,
        n ? progress(p.translated, p.segments) : null,
        el("div", { class: "actions" },
          addEpubButton(!n),
          el("button", { class: "btn", type: "button", onclick: openPasteDialog }, icon("paste"), "Paste chapter"),
          addPagesButton(),
          n && left ? el("button", { class: "btn primary", type: "button", disabled: running() || !!S.uploading, onclick: () => translateRemaining() }, icon("play"), batchWords() ? `Translate next ~${batchWords().toLocaleString()} ${batchUnit()}` : `Translate remaining (${left})`) : null,
          n && left ? batchPicker() : null),
        nextStep())),
    downloadCard());
}
const stat = (v, l) => el("div", { class: "stat" }, el("b", {}, String(v)), el("span", {}, l));

function addEpubButton(primary) {
  const input = el("input", { type: "file", accept: BOOK_ACCEPT, multiple: true, class: "sr-only", tabindex: "-1", onchange: (ev) => { addFiles(ev.target.files); ev.target.value = ""; } });
  return [el("button", { class: "btn" + (primary ? " primary" : ""), type: "button", disabled: !!S.uploading, onclick: () => input.click() }, icon("upload"), "Add EPUB"), input];
}

function addPagesButton() {
  const input = el("input", { type: "file", accept: ".png,.jpg,.jpeg,.webp,.bmp,.cbz,.zip", multiple: true, class: "sr-only", tabindex: "-1", onchange: (ev) => { addFiles(ev.target.files); ev.target.value = ""; } });
  return [el("button", { class: "btn", type: "button", title: "Comic or manhua pages: images or a CBZ. You can also paste an image from the clipboard.", disabled: !!S.uploading || running(), onclick: () => input.click() }, icon("image"), "Add comic pages"), input];
}

function nextStep() {
  const p = S.project, [tone] = modelState(), link = (t, fn) => el("button", { class: "link", type: "button", onclick: fn }, t);
  let msg;
  if (S.uploading) return null;
  if (!p.chapters) msg = ["Start by adding the book's EPUB, or comic pages. Every chapter or page will appear below."];
  else if (tone === "err" || tone === "warn") msg = ["The translation model needs attention. ", link("Choose a model", openModelDialog)];
  else if (!p.translated && p.terms) msg = [`${plural(p.terms, "name")} found. Review them first so they stay consistent, then translate. `, link("Review names", () => go(`#/work/${S.pid}/names`))];
  else if (remaining(p)) msg = [`${plural(remaining(p), "paragraph")} left to translate. `];
  else if (p.qa_errors) msg = [`${plural(p.qa_errors, "quality error")} to fix before export. `, link("Open Quality", () => go(`#/work/${S.pid}/quality`))];
  else msg = ["Everything is translated. Download the EPUB, or read through the chapters first."];
  return el("div", { class: "next" }, icon("info"), el("span", {}, msg));
}

function exportProblems(v) {
  const bits = [];
  if (v.untranslated) bits.push(`${plural(v.untranslated, "paragraph")} not translated`);
  if (v.stale) bits.push(`${v.stale} outdated`);
  if (v.needs_review) bits.push(`${v.needs_review} need review`);
  if (v.qa_errors) bits.push(plural(v.qa_errors, "quality error"));
  return bits;
}

function downloadCard() {
  const p = S.project, v = S.valid, n = p.chapters, comic = p.pages > 0;
  let state, tone = "warn";
  if (!n) state = "Add an EPUB, comic pages or a pasted chapter first.";
  else if (v && v.ok) { state = "Everything is translated and checked."; tone = "ok"; }
  else state = v ? "Not finished: " + exportProblems(v).join(", ") + "." : "";
  return el("aside", { class: "dl", "aria-label": "Download" },
    el("div", { class: "eyebrow" }, "Translated book"),
    el("p", { class: "dl-state " + tone }, state),
    comic ? el("button", { class: "btn primary block", type: "button", onclick: () => downloadBook("cbz") }, icon("download"), "Download translated pages (CBZ)")
      : el("button", { class: "btn primary block", type: "button", disabled: !n, onclick: () => downloadBook("epub") }, icon("download"), "Download translated EPUB"),
    el("details", {}, el("summary", {}, "Other formats"),
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: S.bilingual, onchange: (ev) => { S.bilingual = ev.target.checked; } }), "Show the original under each paragraph"),
      comic && p.pages < n ? el("button", { class: "btn block", type: "button", onclick: () => downloadBook("epub") }, "Download translated EPUB") : null,
      el("button", { class: "btn block", type: "button", disabled: !n, onclick: () => downloadBook("txt") }, "Download as text (.txt)")),
    el("p", { class: "hint" }, comic ? "Pages keep their artwork. Only the lettering is replaced. Each page can also be saved as a PNG from its chapter view." : "An imported EPUB keeps its own layout, images and table of contents."));
}

async function downloadBook(format) {
  const v = S.valid;
  if (v && !v.ok) {
    const probs = exportProblems(v);
    const yes = await confirmDialog("Download an unfinished book?", [el("p", {}, probs.join(", ") + "."), el("p", { class: "muted small" }, "Paragraphs that are not translated stay in the original language. You can finish them and download again.")], "Download anyway");
    if (!yes) return;
  }
  const a = el("a", { href: `/api/projects/${S.pid}/export?format=${format}${S.bilingual ? "&bilingual=true" : ""}`, download: "" });
  document.body.append(a); a.click(); a.remove();
  toast("Preparing your download…");
}

function tabsBar() {
  const p = S.project, count = { chapters: p.chapters, names: p.terms, quality: p.qa_errors + p.qa_warnings };
  return el("nav", { class: "tabs", "aria-label": "Sections" }, TABS.map(([k, label]) =>
    el("a", { href: k === "chapters" ? `#/work/${S.pid}` : `#/work/${S.pid}/${k}`, "aria-current": S.route.tab === k ? "page" : null }, label,
      count[k] ? el("span", { class: "n" }, String(count[k])) : null)));
}

function tabPanel() {
  const t = S.route.tab;
  return t === "names" ? panelNames() : t === "bible" ? panelBible() : t === "quality" ? panelQuality() : t === "settings" ? panelSettings() : panelChapters();
}

// ---- chapters tab ---------------------------------------------------------------------------------
function chapterBadge(c) {
  const st = c.states, total = c.segments, n = (...ks) => ks.reduce((a, k) => a + (st[k] || 0), 0), open = n("UNPROCESSED", "ANALYZED");
  if (!total) return [c.page ? "No text found" : "Empty", ""];
  if (n("NEEDS_REVIEW")) return ["Needs review", "err"];
  if (n("STALE")) return ["Outdated", "warn"];
  if (open === total) return ["Not started", ""];
  if (open) return [`${total - open}/${total}`, "warn"];
  if (n("HUMAN_APPROVED") === total) return ["Approved", "ok"];
  return n(...DONE) === total ? ["Done", "ok"] : ["Draft", "warn"];
}
const chapterOpen = (c) => OPEN.reduce((a, k) => a + (c.states[k] || 0), 0);

function panelChapters() {
  if (!S.chapters.length) return emptyChapters();
  const q = el("input", { type: "search", placeholder: "Find a chapter…", "aria-label": "Find a chapter", value: S.chQuery,
    oninput: (ev) => { S.chQuery = ev.target.value; S.chShow = 100; clear($("#ch-list")).append(...chapterRows()); } });
  return [el("div", { class: "toolbar" }, q,
    el("label", { class: "check" }, el("input", { type: "checkbox", checked: S.onlyOpen, onchange: (ev) => { S.onlyOpen = ev.target.checked; clear($("#ch-list")).append(...chapterRows()); } }), "Only unfinished")),
  el("div", { class: "ch-list", id: "ch-list" }, chapterRows())];
}

function chapterRows() {
  const q = S.chQuery.trim().toLowerCase();
  const all = S.chapters.filter((c) => (!q || (c.title + " " + c.title_translated).toLowerCase().includes(q)) && (!S.onlyOpen || chapterOpen(c) > 0));
  const rows = all.slice(0, S.chShow).map((c) => {
    const [label, tone] = chapterBadge(c), open = chapterOpen(c);
    return el("div", { class: "ch-row", "data-cid": String(c.id) },
      el("a", { class: "ch-main", href: `#/work/${S.pid}/chapter/${c.id}` },
        el("span", { class: "ch-num" }, String(c.index + 1)),
        el("span", { class: "ch-title" }, el("b", {}, c.page ? [icon("image"), c.title] : c.title), c.title_translated ? el("span", {}, c.title_translated) : null),
        el("span", { class: "ch-prog" }, progress(c.segments - open, c.segments), `${c.segments - open}/${c.segments}`),
        el("span", { class: "chip " + tone }, label)),
      open ? el("button", { class: "btn small", type: "button", title: "Translate this chapter", disabled: running(), "aria-label": `Translate chapter ${c.index + 1}`, onclick: () => translateChapter(c.id) }, icon("play"), el("span", {}, "Translate")) : null);
  });
  if (!rows.length) rows.push(el("p", { class: "muted", style: "padding:16px" }, "No chapters match."));
  if (all.length > S.chShow) rows.push(el("div", { style: "padding:12px;text-align:center" }, el("button", { class: "btn small", type: "button", onclick: () => { S.chShow += 200; clear($("#ch-list")).append(...chapterRows()); } }, `Show more (${all.length - S.chShow} hidden)`)));
  return rows;
}

function emptyChapters() {
  return el("div", { class: "stack" }, dropzone(true),
    el("p", { class: "muted small", style: "text-align:center" }, "No EPUB? Use ", el("button", { class: "link", type: "button", onclick: openPasteDialog }, "Paste chapter"), " to add text one chapter at a time."));
}

function dropzone(big) {
  const input = el("input", { type: "file", accept: BOOK_ACCEPT, multiple: true, class: "sr-only", onchange: (ev) => { addFiles(ev.target.files); ev.target.value = ""; } });
  return el("label", { class: "drop" + (big ? " big" : "") }, input, icon("upload"),
    el("span", { class: "drop-title" }, "Drop an EPUB or comic pages here, or click to choose"),
    el("span", { class: "small" }, "Chapters or pages are read in and appear in this list. Comic pages (images or CBZ) are read with OCR. You can paste an image too."));
}

// Files dropped anywhere on a work page are imported; elsewhere the browser must not navigate away.
async function addFiles(files) {
  files = [...files];
  if (!files.length || S.uploading) return;
  if (S.route.view === "library" || S.pid == null) { toast("Open a work first, then drop the EPUB on it.", true); return; }
  const pid = S.pid; let added = 0;
  const pages = files.filter((f) => PAGE_FILE.test(f.name)); files = files.filter((f) => !PAGE_FILE.test(f.name));
  for (let i = 0; i < files.length; i++) {
    S.uploading = { name: files[i].name, i: i + 1, n: files.length }; render();
    try {
      const fd = new FormData(); fd.append("file", files[i]);
      added += (await api("POST", `/api/projects/${pid}/import`, fd)).chapters_added;
    } catch (e) { fail(new Error(`${files[i].name}: ${e.message}`)); }
  }
  S.uploading = null;
  if (pages.length) {
    S.uploading = { name: plural(pages.length, "page file"), i: 1, n: 1, pages: true }; render();
    try {
      const fd = new FormData(); pages.forEach((f) => fd.append("files", f));
      const { job } = await api("POST", `/api/projects/${pid}/pages`, fd);
      S.uploading = null; watchJob(job);
    } catch (e) { S.uploading = null; fail(e); }
  }
  try { await reload(); } catch (e) { fail(e); }
  render();
  if (added) { toast(`Added ${plural(added, "chapter")}.`); if (S.route.view === "work" && S.route.tab !== "chapters") go(`#/work/${pid}`); }
}

// ---- names tab ------------------------------------------------------------------------------------
function panelNames() {
  if (!S.terms) return el("p", { class: "muted" }, "Loading…");
  if (!S.terms.length) return el("div", { class: "card stack" }, el("h2", { class: "serif" }, "No names yet"),
    el("p", { class: "muted" }, "Names, places and terms that repeat in the book are picked up automatically when you add it. Add a book first."));
  const pending = S.terms.filter((t) => t.status === "suggested");
  const withRender = pending.filter((t) => t.translation).length;
  return [
    el("p", { class: "muted" }, "Lock a name's translation once and every later chapter uses it. Changing a locked name marks the paragraphs that use it as outdated."),
    el("div", { class: "toolbar" },
      el("input", { type: "search", placeholder: "Search names…", "aria-label": "Search names", value: S.nameQuery, oninput: (ev) => { S.nameQuery = ev.target.value; S.nameShow = 40; renderNameLists(); } }),
      el("button", { class: "btn", type: "button", disabled: running() || !pending.length, onclick: suggestNames }, "Suggest translations with the model"),
      el("button", { class: "btn", type: "button", disabled: !withRender, onclick: acceptAll }, icon("check"), `Accept all with a translation (${withRender})`)),
    el("div", { id: "name-lists", class: "stack-lg" }, nameLists())];
}
function renderNameLists() { clear($("#name-lists")).append(...nameLists()); }

function nameLists() {
  const q = S.nameQuery.trim().toLowerCase(), hit = (t) => !q || (t.forms.join(" ") + " " + t.translation).toLowerCase().includes(q);
  const pending = S.terms.filter((t) => t.status === "suggested" && hit(t)).sort((a, b) => b.occurrences - a.occurrences);
  const locked = S.terms.filter((t) => t.status === "confirmed" && t.translation && hit(t)).sort((a, b) => b.occurrences - a.occurrences);
  const target = langName(S.project.target_lang);
  const more = (list) => list.length > S.nameShow ? el("div", { style: "padding:12px;text-align:center" }, el("button", { class: "btn small", type: "button", onclick: () => { S.nameShow += 60; renderNameLists(); } }, `Show more (${list.length - S.nameShow})`)) : null;
  const out = [];
  out.push(el("section", { class: "stack" }, el("div", { class: "section-head" }, el("h2", {}, `To review (${pending.length})`)),
    pending.length ? el("div", { class: "ch-list" }, pending.slice(0, S.nameShow).map((t) => termRow(t, target, true)), more(pending)) : el("p", { class: "muted small" }, "Nothing to review.")));
  out.push(el("section", { class: "stack" }, el("div", { class: "section-head" }, el("h2", {}, `Locked (${locked.length})`)),
    locked.length ? el("div", { class: "ch-list" }, locked.slice(0, S.nameShow).map((t) => termRow(t, target, false)), more(locked)) : el("p", { class: "muted small" }, "Accept a suggested name to lock it.")));
  return out;
}

function termRow(t, target, isPending) {
  const input = el("input", { value: t.translation, placeholder: `${target} translation`, "aria-label": `${target} translation of ${t.forms[0]}`,
    onchange: isPending ? null : () => input.value.trim() && patchTerm(t, { translation: input.value.trim() }) });
  return el("div", { class: "term-row" },
    el("div", { class: "term-src" }, el("b", {}, t.forms[0]), el("span", {}, `seen ${t.occurrences}×`)), input,
    isPending ? el("div", { class: "actions" },
      el("button", { class: "btn small primary", type: "button", onclick: () => patchTerm(t, { translation: input.value.trim(), status: "confirmed" }, input) }, "Accept"),
      el("button", { class: "btn small", type: "button", onclick: () => patchTerm(t, { status: "ignored" }) }, "Ignore"))
      : el("div", { class: "actions" }, el("button", { class: "btn small ghost", type: "button", onclick: () => patchTerm(t, { status: "ignored" }) }, "Remove")));
}

async function patchTerm(t, body, input) {
  if (body.status === "confirmed" && !body.translation) { toast("Type a translation first.", true); if (input) input.focus(); return; }
  try {
    await api("PATCH", `/api/projects/${S.pid}/terms/${t.id}`, body);
    toast(body.status === "ignored" ? "Name removed" : "Saved. Paragraphs that use it are marked outdated.");
    await reload(); S.stale = false; render();
  } catch (e) { fail(e); }
}
const acceptAll = () => once("accept-all", async () => {
  try { const r = await api("POST", `/api/projects/${S.pid}/terms/accept-all`); toast(`Locked ${plural(r.accepted, "name")}.`); await reload(); render(); } catch (e) { fail(e); }
});
function suggestNames() { if (requireModel()) startJob("suggest_terms", { limit: 80 }); }

// ---- story bible tab ------------------------------------------------------------------------------
function panelBible() {
  if (!S.bible) return el("p", { class: "muted" }, "Loading…");
  const secs = ENTITY_SECTIONS.map((sec) => [sec, (S.bible[sec] || []).filter((e) => e.source)]);
  const total = secs.reduce((a, [, items]) => a + items.length, 0);
  return [
    el("div", { class: "toolbar" }, el("p", { class: "muted grow" }, "Notes for the translator: who is who, genders and pronouns, how people address each other. They are sent with every relevant paragraph."),
      el("button", { class: "btn primary", type: "button", onclick: () => openBible() }, icon("plus"), "Add entry")),
    total ? el("div", { class: "bible stack" }, secs.filter(([, items]) => items.length).map(([sec, items]) =>
      el("details", { open: true }, el("summary", {}, `${sec} (${items.length})`),
        items.slice(0, 80).map((e) => el("button", { class: "entity", type: "button", onclick: () => openBible(sec, e) },
          el("span", {}, el("span", { class: "cjk" }, e.source), ` → ${e.translation || "?"}`),
          el("span", { class: "meta" }, [e.gender, e.pronouns, e.notes ? "has notes" : ""].filter(Boolean).join(" · ")))))))
      : el("div", { class: "card" }, el("p", { class: "muted" }, "Empty so far. Locking names on the Names tab fills this in; add notes here so pronouns and tone stay right.")),
  ];
}

// ---- quality tab ----------------------------------------------------------------------------------
function panelQuality() {
  const v = S.valid, qa = S.qa;
  const card = (n, l, bad) => el("div", { class: "qa-card " + (n ? (bad ? "bad" : "") : "good") }, el("b", {}, String(n)), el("span", { class: "small muted" }, l));
  return [
    v ? el("div", { class: "qa-cards" }, card(v.untranslated, "not translated", true), card(v.stale, "outdated", true), card(v.needs_review, "need review", true), card(v.qa_errors, "quality errors", true)) : null,
    el("div", { class: "toolbar" },
      el("button", { class: "btn primary", type: "button", disabled: running() || !S.project.translated, onclick: () => { if (!S.aiReview || requireModel()) startJob("qa", { ai_review: S.aiReview }); } }, "Run checks on all chapters"),
      el("label", { class: "check" }, el("input", { type: "checkbox", checked: S.aiReview, onchange: (ev) => { S.aiReview = ev.target.checked; } }), "Also ask the model to review (slower)")),
    el("p", { class: "muted small" }, "Checks look for missing or extra text, wrong names, leftover source characters and inconsistent terms."),
    !qa ? el("p", { class: "muted" }, "Loading…") : qa.length
      ? el("div", { class: "ch-list" }, qa.slice(0, 100).map((q) => el("button", { class: "finding-row " + (q.severity === "error" ? "error" : ""), type: "button", onclick: () => gotoSegment(q) },
        el("strong", {}, `Chapter ${q.chapter_idx + 1}, paragraph ${q.idx}`), ` · ${q.type.replaceAll("_", " ")}: ${q.message}`)),
        qa.length > 100 ? el("p", { class: "muted small", style: "padding:12px" }, `${qa.length - 100} more not shown.`) : null)
      : el("p", { class: "muted" }, "No findings. Run the checks after translating."),
  ];
}
function gotoSegment(q) { S.flash = q.segment_id; go(`#/work/${S.pid}/chapter/${q.chapter_id}`); }

// ---- settings tab ---------------------------------------------------------------------------------
function panelSettings() {
  const p = S.project;
  const name = el("input", { name: "name", value: p.name, maxlength: 120, required: true });
  const src = fillSelect(el("select", { name: "source_lang" }), langItems(true), p.source_lang);
  const tgt = fillSelect(el("select", { name: "target_lang" }), langItems(false), p.target_lang);
  const sty = fillSelect(el("select", { name: "style" }), styleItems(), p.style);
  const hint = el("span", { class: "hint" }, (STYLES[p.style] || ["", ""])[1]);
  sty.addEventListener("change", () => { hint.textContent = (STYLES[sty.value] || ["", ""])[1]; });
  const form = el("form", { class: "form card", onsubmit: async (ev) => {
    ev.preventDefault();
    try {
      await api("PATCH", `/api/projects/${S.pid}`, { name: name.value.trim(), source_lang: src.value, target_lang: tgt.value, style: sty.value });
      toast("Saved. Existing translations are not rewritten."); await reload(); render();
    } catch (e) { fail(e); }
  } },
  el("h2", { class: "serif" }, "This work"), el("label", {}, "Title", name),
  el("div", { class: "row2" }, el("label", {}, "Language of the book", src), el("label", {}, "Translate into", tgt)),
  el("label", {}, "Style", sty, hint), el("div", { class: "actions" }, el("button", { class: "btn primary", type: "submit" }, "Save changes")));
  const [tone, text] = modelState();
  return [form,
    el("div", { class: "card stack", style: "max-width:560px" }, el("h2", { class: "serif" }, "Translation model"),
      el("div", { class: "status-line" }, el("span", { class: "dot " + tone }), el("span", {}, `${PROV[S.cfg.provider]} · ${text}`)),
      el("div", { class: "actions" }, el("button", { class: "btn", type: "button", onclick: openModelDialog }, "Change model"))),
    el("div", { class: "danger-zone" }, el("h2", { class: "serif" }, "Delete this work"),
      el("p", { class: "muted small" }, "Removes its chapters, translations, names, notes and uploaded files from this computer. This cannot be undone."),
      el("div", {}, el("button", { class: "btn danger", type: "button", disabled: running(), onclick: deleteWork }, icon("trash"), "Delete work")))];
}

async function deleteWork() {
  const p = S.project;
  const yes = await confirmDialog(`Delete “${p.name}”?`, [el("p", {}, `${plural(p.chapters, "chapter")} and ${plural(p.translated, "translated paragraph")} will be removed from this computer.`), el("p", { class: "muted small" }, "Download the translated EPUB first if you want to keep it.")], "Delete work", true);
  if (!yes) return;
  await once("delete", async () => { try { await api("DELETE", `/api/projects/${S.pid}`); toast("Work deleted."); S.pid = null; S.project = null; go("#/"); } catch (e) { fail(e); } });
}

// ---- chapter view ---------------------------------------------------------------------------------
function viewChapter() {
  const r = S.route, i = S.chapters.findIndex((c) => c.id === r.cid), c = S.chapters[i], d = S.data;
  if (!c || !d) return el("p", { class: "muted" }, "Loading…");
  const prev = S.chapters[i - 1], next = S.chapters[i + 1], open = chapterOpen(c), busy = running();
  const nav = (ch, dir) => el("a", { class: "btn small", href: ch ? `#/work/${S.pid}/chapter/${ch.id}` : null, "aria-disabled": ch ? null : "true", style: ch ? null : "opacity:.4;pointer-events:none", "aria-label": dir === "left" ? "Previous chapter" : "Next chapter" }, dir === "left" ? [icon("left"), "Previous"] : ["Next", icon("right")]);
  const names = chapterNames(d);
  return el("div", { class: "stack-lg" },
    el("header", { class: "ch-head" },
      el("div", { class: "nav" }, el("a", { class: "btn small ghost", href: `#/work/${S.pid}` }, icon("left"), "All chapters"), el("span", { class: "grow" }), nav(prev, "left"), nav(next, "right")),
      el("div", {}, el("div", { class: "eyebrow" }, `${d.chapter.page ? "Page" : "Chapter"} ${i + 1} · ${plural(d.segments.length, d.chapter.page ? "text block" : "paragraph")}`),
        el("h1", { class: "serif" }, d.chapter.title, d.chapter.title_translated ? el("span", { class: "muted" }, ` · ${d.chapter.title_translated}`) : null)),
      el("div", { class: "actions" },
        el("button", { class: "btn primary", type: "button", disabled: busy || !open, onclick: () => translateChapter(c.id) }, icon("play"), open ? `Translate ${plural(open, "paragraph")}` : "All translated"),
        el("button", { class: "btn", type: "button", onclick: () => { S.proof = !S.proof; render(); } }, S.proof ? "Editing view" : "Proofread"),
        el("button", { class: "btn", type: "button", disabled: busy || open === c.segments, title: "A second pass that polishes the prose without changing meaning", onclick: () => { if (requireModel()) startJob("refine", { chapter_id: c.id }); } }, "Refine"),
        el("button", { class: "btn", type: "button", disabled: busy || open === c.segments, onclick: () => startJob("qa", { chapter_id: c.id }) }, "Check quality"))),
    el("div", { id: "job-slot" }),
    d.chapter.page ? pageCard(c, d) : null,
    S.proof ? proofView(d) : el("div", { class: "ch-layout" }, el("div", { class: "segs" }, d.segments.length ? d.segments.map(segmentCard) : el("p", { class: "card muted" }, "No text was found on this page. If it has lettering, try a larger or sharper image.")),
      el("aside", { class: "rail" },
        el("section", { class: "card" }, el("h2", {}, "Names in this chapter"),
          names.length ? names.map(([s, t]) => el("div", { class: "kv" }, el("span", { class: "cjk" }, s), el("strong", {}, t))) : el("p", { class: "muted small" }, "No locked names appear here yet."),
          el("p", { class: "small", style: "margin-top:8px" }, el("a", { href: `#/work/${S.pid}/names` }, "Review all names"))),
        d.chapter.summary ? el("section", { class: "card" }, el("h2", {}, "Summary"), el("p", { class: "small muted" }, d.chapter.summary)) : null,
        el("section", { class: "card small muted" }, `Project memory: ${S.project.terms_with_translation} locked names, ${S.project.tm_entries} remembered lines, ${plural(S.project.summaries, "chapter summary")}. Use Context on a paragraph to see what it used.`))));
}

const strHash = (str) => { let h = 0; for (const ch of str) h = (h * 33 + ch.codePointAt(0)) | 0; return (h >>> 0).toString(36); };
function pageCard(c, d) {
  const rev = strHash(d.segments.map((s) => s.translation).join("|")), url = (v) => `/api/projects/${S.pid}/chapters/${c.id}/page?version=${v}&r=${rev}`;
  const mode = (m, label) => el("button", { type: "button", role: "radio", "aria-checked": String(S.pageView === m), onclick: () => { S.pageView = m; render(); } }, label);
  const fig = (v, label) => el("figure", { class: "page-fig" }, el("img", { class: "page-img", src: url(v), alt: `${label} page`, loading: "lazy" }), el("figcaption", {}, label));
  return el("section", { class: "card stack page-card" },
    el("div", { class: "page-bar" }, el("h2", {}, "Page"), el("div", { class: "seg-ctl", role: "radiogroup", "aria-label": "Page view" }, mode("both", "Side by side"), mode("translated", "Translated"), mode("original", "Original")),
      el("a", { class: "btn small", href: url("translated"), download: `${c.title}.png` }, icon("download"), "Save PNG")),
    el("div", { class: "page-compare " + S.pageView }, S.pageView !== "translated" ? fig("original", "Original") : null, S.pageView !== "original" ? fig("translated", "Translated") : null),
    el("p", { class: "muted small" }, "The original lettering is erased and the translation is set in the same bubble. Fix an OCR slip with Fix text on a block, then translate it again."));
}

function proofView(d) {
  // Reading view for proofreaders: continuous two-column prose, original
  // left, translation right, one row per paragraph. Paragraphs with quality
  // findings are edge-marked; use the browser's Print to take it to paper.
  const col = (cls, title, cell) => el("div", { class: "proof-col" }, el("h3", {}, title),
    d.segments.map((s) => el("p", { class: (s.findings.length ? "warn " : "") + cls, lang: cls === "po" ? S.project.source_lang : null }, cell(s))));
  return el("div", { class: "proof-cols", role: "document", "aria-label": "Side-by-side proofreading view" },
    col("po", "Original", (s) => s.source || "—"),
    col("pt", "Translation", (s) => s.translation || el("span", { class: "missing" }, "Not translated yet")));
}

function chapterNames(d) {  const seen = new Map();
  for (const s of d.segments) for (const t of s.terms) if (!seen.has(t.source)) seen.set(t.source, t.target);
  return [...seen.entries()].slice(0, 60);
}

function highlight(text, forms) {
  const words = [...new Set(forms)].filter(Boolean).sort((a, b) => b.length - a.length);
  if (!words.length) return text;
  const re = new RegExp("(" + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
  return text.split(re).map((part, i) => (i % 2 ? el("mark", {}, part) : part));
}

function segmentCard(s) {
  const [label, tone] = STATE[s.state] || [s.state, ""], editing = S.editing === s.id && !S.editSrc, fixing = S.editing === s.id && S.editSrc, lang = S.project.source_lang;
  const isPage = S.data && S.data.chapter.page;
  const tgt = el("div", { class: "tgt" + (!s.translation && !editing ? " pending" : "") });
  tgt.append(el("div", { class: "meta" }, el("span", { class: "chip " + tone }, editing ? "Editing" : label)));
  if (editing) {
    const ta = el("textarea", { rows: Math.max(3, Math.ceil(s.translation.length / 55)), "aria-label": `Translation of paragraph ${s.index}`, value: s.translation });
    tgt.append(ta, el("div", { class: "seg-actions" },
      el("button", { class: "btn small primary", type: "button", onclick: () => saveEdit(s, ta.value, "local") }, "Save"),
      el("button", { class: "btn small", type: "button", title: "Also remember this line for similar paragraphs later", onclick: () => saveEdit(s, ta.value, "project") }, "Save and reuse"),
      el("button", { class: "btn small ghost", type: "button", onclick: () => { S.editing = null; S.editSrc = false; render(); } }, "Cancel")));
    setTimeout(() => ta.focus(), 0);
  } else {
    tgt.append(el("div", { class: "txt" }, s.translation ? highlight(s.translation, s.terms.map((t) => t.target)) : "Not translated yet"));
    for (const f of s.findings) tgt.append(el("div", { class: "finding " + (f.severity === "error" ? "error" : "") }, `${f.type.replaceAll("_", " ")}: ${f.message}`));
    tgt.append(el("div", { class: "seg-actions" },
      el("button", { class: "btn small", type: "button", onclick: () => { S.editing = s.id; render(); } }, "Edit"),
      s.translation && s.state !== "HUMAN_APPROVED" ? el("button", { class: "btn small ok", type: "button", onclick: () => approve(s) }, icon("check"), "Approve") : null,
      el("button", { class: "btn small", type: "button", disabled: running(), onclick: () => retranslate(s) }, s.translation ? "Retranslate" : "Translate"),
      el("button", { class: "btn small ghost", type: "button", onclick: () => showContext(s) }, "Context"),
      isPage ? el("button", { class: "btn small ghost", type: "button", title: "Correct what the OCR read", onclick: () => { S.editing = s.id; S.editSrc = true; render(); } }, "Fix text") : null));
  }
  const srcBody = fixing ? fixSource(s) : el("div", {}, highlight(s.source, s.terms.map((t) => t.source)));
  return el("article", { class: "seg", id: `seg-${s.id}` },
    el("div", { class: "src cjk", lang: lang === "auto" ? null : lang }, el("div", { class: "meta" }, el("span", { class: "idx" }, isPage ? `Block ${s.index}` : `¶ ${s.index}`)), srcBody), tgt);
}

function fixSource(s) {
  const ta = el("textarea", { rows: Math.max(2, Math.ceil(s.source.length / 18)), class: "cjk", "aria-label": `Text read from block ${s.index}`, value: s.source });
  setTimeout(() => ta.focus(), 0);
  return el("div", { class: "stack" }, ta, el("div", { class: "seg-actions" },
    el("button", { class: "btn small primary", type: "button", onclick: () => saveSource(s, ta.value) }, "Save text"),
    el("button", { class: "btn small ghost", type: "button", onclick: () => { S.editing = null; S.editSrc = false; render(); } }, "Cancel")));
}
const saveSource = (s, text) => once(`source:${s.id}`, async () => {
  if (!text.trim()) { toast("The text cannot be empty.", true); return; }
  try {
    await api("PUT", `/api/projects/${S.pid}/segments/${s.id}/source`, { text });
    S.editing = null; S.editSrc = false; toast("Text corrected. Translate this block again.");
    await reload(); render();
  } catch (e) { fail(e); }
});

const saveEdit = (s, text, scope) => once(`save:${s.id}`, async () => {
  if (!text.trim()) { toast("The translation cannot be empty.", true); return; }
  try {
    await api("PUT", `/api/projects/${S.pid}/segments/${s.id}`, { text, scope });
    S.editing = null; S.editSrc = false; toast(scope === "project" ? "Saved and remembered for the project." : "Saved.");
    await reload(); render();
  } catch (e) { fail(e); }
});
const approve = (s) => once(`approve:${s.id}`, async () => { try { await api("POST", `/api/projects/${S.pid}/segments/${s.id}/approve`); await reload(); render(); } catch (e) { fail(e); } });
function retranslate(s) {
  if (!requireModel()) return;
  return once("job", async () => { try { watchJob(await api("POST", `/api/projects/${S.pid}/segments/${s.id}/retranslate`, jobBody("translate"))); } catch (e) { fail(e); } });
}

async function showContext(s) {
  const body = clear($("#context-body"));
  body.append(el("p", { class: "muted" }, "Loading…"));
  $("#dlg-context").showModal();
  try {
    const c = await api("GET", `/api/projects/${S.pid}/segments/${s.id}/context`), terms = Object.entries(c.glossary);
    clear(body).append(
      el("h2", {}, "Names applied"), terms.length ? el("div", {}, terms.map(([k, v]) => el("div", { class: "kv" }, el("span", { class: "cjk" }, k), el("strong", {}, v)))) : el("p", { class: "muted small" }, "No locked names in this paragraph."),
      el("h2", {}, "Similar earlier lines"), c.tm_hints.length ? el("div", {}, c.tm_hints.map((h) => el("div", { class: "kv" }, el("span", {}, `${Math.round(h.similarity * 100)}% · ${h.source}`), el("span", {}, h.translation)))) : el("p", { class: "muted small" }, "No similar lines yet."),
      el("h2", {}, "Context sent to the model"), c.context ? el("pre", { class: "ctx" }, c.context) : el("p", { class: "muted small" }, "Nothing yet. Earlier chapters and paragraphs appear here once translated."));
  } catch (e) { clear(body).append(el("p", { class: "alert" }, e.message)); }
}

// ---- jobs -----------------------------------------------------------------------------------------
function jobBody(kind, extra) {
  const c = S.cfg;
  return { kind, provider: c.provider, model: c.model, base_url: c.baseUrl, refine_model: c.refineModel, summarize: c.summarize, ...extra };
}
function startJob(kind, extra) {
  return once("job", async () => { try { watchJob(await api("POST", `/api/projects/${S.pid}/jobs`, jobBody(kind, extra))); } catch (e) { fail(e); } });
}
// A batch is a word limit for one run: finished paragraphs show up sooner and you can read them before going on.
// CJK text has no spaces, so it is counted by character (same rule as the server's count_words).
const isCjk = () => ["zh", "ja", "ko"].includes(S.project.source_lang);
const batchUnit = () => (isCjk() ? "characters" : "words");
const batchSizes = () => [0, ...(isCjk() ? [2000, 5000, 20000] : [1000, 3000, 10000])];  // index 0 = everything left; S.cfg.batch stores the index
const batchWords = () => batchSizes()[S.cfg.batch] || 0;
function batchPicker() {
  return el("select", { class: "batch", "aria-label": "How much to translate per run", disabled: running(),
    title: "Smaller batches show finished text sooner and let you check it before going on",
    onchange: (ev) => { S.cfg.batch = +ev.target.value; saveCfg(); softRender(); } },
    batchSizes().map((w, i) => el("option", { value: String(i), selected: (S.cfg.batch || 0) === i }, w ? `~${w.toLocaleString()} ${batchUnit()} at a time` : "Everything left, in one run")));
}
function translateRemaining() { if (requireModel()) startJob("translate", batchWords() ? { max_words: batchWords() } : {}); }
function translateChapter(cid) { if (requireModel()) startJob("translate", { chapter_id: cid }); }

function renderJob() { drawJob(); paintLive(); }
function drawJob() {
  const slot = $("#job-slot");
  if (!slot) return;
  clear(slot);
  if (S.uploading) {
    slot.append(el("div", { class: "job" }, el("div", { class: "top" }, el("div", { class: "spin" }),
      el("span", {}, S.uploading.pages ? `Uploading ${S.uploading.name}…` : `Reading ${S.uploading.name}${S.uploading.n > 1 ? ` (${S.uploading.i} of ${S.uploading.n})` : ""}… splitting chapters and finding names. Large books take a moment.`))));
    return;
  }
  const j = S.job;
  if (!j || j.project_id !== S.pid) return;
  if (j.status === "error") {
    slot.append(el("div", { class: "job error", role: "alert" }, el("div", { class: "top" }, el("span", {}, `${JOB_NAMES[j.kind] || j.kind} stopped: ${j.error}. Finished paragraphs are saved; run it again to continue.`), dismissButton())));
    return;
  }
  const live = j.status === "running", pct = j.total ? (100 * j.done) / j.total : 100, writing = live && j.kind === "translate";
  slot.append(el("div", { class: "job", role: "status" },
    el("div", { class: "top" }, live ? el("div", { class: "spin" }) : icon("check"),
      el("span", {}, live ? `${JOB_NAMES[j.kind] || j.kind} ${j.total ? `${j.done} of ${j.total}` : "…"}${writing ? (j.words ? ` · ${j.words.toLocaleString()} ${batchUnit()}` : "") : j.message ? ` · ${j.message}` : ""}` : `${j.status === "cancelled" ? "Stopped" : "Done"}: ${(JOB_NAMES[j.kind] || j.kind).toLowerCase()}${resultText(j.result)} · ${j.seconds}s`),
      live ? el("button", { class: "btn small", type: "button", disabled: !!j.stopping, onclick: cancelJob }, icon("stop"), j.stopping ? "Stopping…" : "Stop") : dismissButton()),
    live && j.total ? el("div", { class: "bar" }, el("span", { style: `width:${pct}%` })) : null,
    writing ? liveBlock(j) : null));
}

// What the model is writing right now, plus the last few finished paragraphs. On the chapter being written the text goes
// straight into its paragraph card instead (paintLive), so this block only says where it is.
function liveBlock(j) {
  const L = j.live, here = S.route.view === "chapter" && L && L.chapter_id === S.route.cid;
  const page = (S.chapters.find((c) => c.id === (L || {}).chapter_id) || {}).page, unit = page ? "Block" : "¶";
  const link = (cid) => `#/work/${S.pid}/chapter/${cid}`;
  const done = j.recent.filter((r) => !L || r.segment_id !== L.segment_id).slice(-3);
  const kids = [];
  if (!here) for (const r of done) kids.push(el("a", { class: "live-done", href: link(r.chapter_id) }, el("span", { class: "idx" }, `${r.chapter}.${r.index}`), el("span", { class: "live-line" }, r.text || "Needs review")));
  if (L && L.segment_id) {
    kids.push(el("div", { class: "live-head" }, el("span", { class: "idx" }, `Chapter ${L.chapter} · ${unit} ${L.index}`),
      here ? null : el("a", { href: link(L.chapter_id) }, "Open chapter")));
    if (!here) kids.push(el("div", { class: "live-src cjk", lang: S.project.source_lang === "auto" ? null : S.project.source_lang }, L.source),
      el("div", { class: "live-txt" }, L.text || el("span", { class: "muted" }, "Waiting for the model…"), el("span", { class: "caret", "aria-hidden": "true" })));
  }
  return kids.length ? el("div", { class: "live", "aria-live": "off" }, kids) : null;
}

// Put the text being written into its paragraph card without rebuilding the page, and mark where work is happening.
let followId = null;
const near = (n) => { const r = n.getBoundingClientRect(); return r.bottom > 0 && r.top < innerHeight; };
function paintLive() {
  const j = S.job, L = j && j.status === "running" && j.kind === "translate" && j.project_id === S.pid ? j.live : null;
  for (const n of $$(".ch-row.working")) if (!L || n.dataset.cid !== String(L.chapter_id)) n.classList.remove("working");
  for (const n of $$(".seg.writing")) if (!L || n.id !== `seg-${L.segment_id}`) n.classList.remove("writing");
  if (!L || !L.segment_id) return;
  const row = $(`.ch-row[data-cid="${L.chapter_id}"]`);
  if (row) row.classList.add("working");
  const seg = $(`#seg-${L.segment_id}`);
  if (!seg || L.final || S.editing === L.segment_id) return;  // a final paragraph is already in the page from the reload
  const tgt = seg.querySelector(".tgt"), txt = tgt && tgt.querySelector(".txt"), chip = tgt && tgt.querySelector(".chip");
  if (!txt) return;
  seg.classList.add("writing"); tgt.classList.remove("pending");
  const text = L.text || "Writing…";
  if (txt.textContent !== text) txt.textContent = text;
  if (chip) { chip.textContent = "Writing…"; chip.className = "chip live"; }
  if (followId !== L.segment_id) {  // keep the paragraph in view for someone who is following along; leave anyone who scrolled away alone
    const prev = followId == null ? null : $(`#seg-${followId}`);
    if (followId == null ? !near(seg) : prev && near(prev)) seg.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    followId = L.segment_id;
  }
}
const dismissButton = () => el("button", { class: "btn small ghost", type: "button", "aria-label": "Dismiss", onclick: () => { S.job = null; renderJob(); } }, icon("x"));
function resultText(r) {
  const parts = Object.entries(r || {}).filter(([, v]) => typeof v !== "object").map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`);
  return parts.length ? ` (${parts.join(", ")})` : "";
}
async function cancelJob() { if (S.job) { try { S.job = await api("POST", `/api/jobs/${S.job.id}/cancel`); renderJob(); } catch (e) { fail(e); } } }

let watching = null;
async function watchJob(job) {
  if (watching === job.id) return;
  watching = job.id; S.job = job; softRender(); renderJob();
  let seen = -1, last = 0, misses = 0, gone = false;
  try {
    while (S.job && S.job.id === job.id && S.job.status === "running") {
      await sleep(600);
      try { S.job = await api("GET", `/api/jobs/${job.id}`); misses = 0; } catch (e) {
        if (/not found/i.test(e.message)) { S.job = null; gone = true; toast("The server restarted, so that job is no longer running. Finished paragraphs are saved.", true); break; }
        if (++misses > 5) throw e;  // a few failed polls are a network blip, not the end of the job
        continue;
      }
      renderJob();
      // a paragraph finished: refresh the lists and the chapter (at most every 1.5 s; skipped while the user is typing)
      if (S.job.status === "running" && S.job.done !== seen && S.editing == null && !typing() && Date.now() - last > 1500) {
        seen = S.job.done; last = Date.now();
        const nav = S.nav;
        try { await reload(); if (nav === S.nav) softRender(); } catch (e) { /* the next finished paragraph tries again */ }
      }
    }
  } catch (e) { fail(e); } finally { watching = null; followId = null; }
  if (gone || (S.job && S.job.id === job.id)) { try { await reload(); } catch (e) { fail(e); } S.stale = false; if (S.editing == null && !typing()) render(); else S.stale = true; }
}

// ---- dialogs --------------------------------------------------------------------------------------
function fillSelect(sel, items, value) {
  clear(sel);
  for (const [k, v] of items) sel.append(el("option", { value: k, selected: k === value }, v));
  return sel;
}
const langItems = (withAuto) => Object.entries(LANGS).filter(([k]) => withAuto || k !== "auto");
const styleItems = () => Object.entries(STYLES).map(([k, [label]]) => [k, label]);

function confirmDialog(title, body, okLabel = "OK", danger = false) {
  return new Promise((resolve) => {
    const dlg = $("#dlg-confirm"); let yes = false;
    $("#confirm-title").textContent = title; clear($("#confirm-body")).append(...body);
    const ok = $("#confirm-ok"); ok.textContent = okLabel; ok.className = "btn " + (danger ? "danger" : "primary");
    $("#form-confirm").onsubmit = (ev) => { ev.preventDefault(); yes = true; dlg.close(); };
    dlg.addEventListener("close", () => resolve(yes), { once: true });
    dlg.showModal();
  });
}

function openWorkDialog() {
  const f = $("#form-work");
  f.reset(); wireWorkDrop(null); workKey = newKey();
  fillSelect($("#lang-source"), langItems(true), "auto");
  fillSelect($("#lang-target"), langItems(false), "en");
  fillSelect($("#style-new"), styleItems(), "faithful-natural");
  $("#style-new-hint").textContent = STYLES["faithful-natural"][1];
  $("#work-submit").disabled = false; $("#work-submit").textContent = "Create work";
  $("#dlg-work").showModal(); f.name.focus();
}
function wireWorkDrop(file) {
  $("#work-drop").classList.toggle("filled", !!file);
  $("#work-drop-title").textContent = file ? file.name : "Add the book now (optional)";
  $("#work-drop-sub").textContent = file ? "This book is read in as soon as the work is created." : "Drop an EPUB here or click to choose. You can also add it later.";
}

function openPasteDialog() {
  const p = S.project, n = S.chapters.length;
  $("#paste-eyebrow").textContent = `${p.name} · adds chapter ${n + 1}`;
  $("#form-paste").reset(); $("#paste-count").textContent = ""; pasteKey = newKey();
  clear($("#carried")).append(el("h2", {}, n ? `Carried over from ${plural(n, "chapter")}` : "Nothing carried over yet"),
    el("div", {}, [["Locked names", p.terms_with_translation], ["Remembered lines", p.tm_entries], ["Chapter summaries", p.summaries], ["Style", styleLabel(p.style)]]
      .map(([k, v]) => el("div", { class: "kv" }, el("span", {}, k), el("strong", {}, String(v))))),
    el("div", { class: "note" }, "The last few translated paragraphs of the previous chapter go into the first prompt of the new one, so tone and pronouns continue across the break."));
  $("#dlg-paste").showModal(); $("#paste-text").focus();
}

function openBible(section, e) {
  const f = $("#form-bible");
  f.reset();
  fillSelect($("#bible-section"), ENTITY_SECTIONS.map((x) => [x, x]), section || "characters");
  for (const k of ["source", "translation", "gender", "pronouns", "notes"]) f[k].value = (e && e[k]) || "";
  f.source.readOnly = !!e; $("#bible-section").disabled = !!e;
  $("#dlg-bible").showModal();
}

// ---- model dialog ---------------------------------------------------------------------------------
let md = null;
async function openModelDialog() {
  md = { draft: { ...S.cfg }, info: null, busy: true, test: null };
  renderModelBody(); $("#dlg-model").showModal();
  fetchModels();
}
async function fetchModels() {
  const d = md.draft; md.busy = true; md.test = null; renderModelBody();
  if (d.provider === "mock") { md.busy = false; md.info = null; renderModelBody(); return; }
  try { md.info = await api("GET", `/api/models?provider=${enc(d.provider)}&base_url=${enc(d.baseUrl)}&target=${enc(S.project ? S.project.target_lang : "en")}`); }
  catch (e) { md.info = { reachable: false, error: e.message, installed: [], recommended: [] }; }
  md.busy = false;
  if (d.provider === "ollama" && md.info.installed.length && !d.model.trim()) d.model = md.info.installed[0].name;
  renderModelBody();
}

function renderModelBody() {
  const d = md.draft, info = md.info, body = clear($("#model-body"));
  const seg = el("div", { class: "seg-ctl", role: "radiogroup", "aria-label": "Where the model runs" }, Object.entries(PROV).map(([k, label]) =>
    el("button", { type: "button", role: "radio", "aria-checked": String(d.provider === k), onclick: () => { if (d.provider === k) return; const same = S.cfg.provider === k; d.provider = k; d.model = k === "mock" ? d.model : same ? S.cfg.model : ""; d.baseUrl = same ? S.cfg.baseUrl : ""; fetchModels(); } },
      k === "ollama" ? "Ollama (this computer)" : label)));
  body.append(seg);
  if (d.provider === "mock") { body.append(el("div", { class: "status-line" }, icon("info"), "Demo mode copies the original text instead of translating. Use it to try the app without a model.")); return; }

  const addr = el("input", { type: "url", value: d.baseUrl, placeholder: d.provider === "ollama" ? "http://localhost:11434 (default)" : "http://localhost:8080/v1", "aria-label": "Server address",
    onchange: (ev) => { d.baseUrl = ev.target.value.trim(); fetchModels(); } });
  body.append(el("label", {}, d.provider === "ollama" ? "Ollama address" : "Server address", addr));
  if (md.busy) { body.append(el("div", { class: "status-line" }, el("div", { class: "spin" }), "Looking for models…")); return; }
  if (info.remote) body.append(el("div", { class: "status-line warn" }, icon("info"), `${info.url} is not on this computer. Your chapter text will be sent to it.`));
  if (!info.reachable) {
    body.append(el("div", { class: "status-line err" }, icon("info"), d.provider === "ollama"
      ? "Ollama is not reachable. Install it from ollama.com, start it, then press Refresh." : "The server did not answer. Check the address, then press Refresh."));
    if (info.error) body.append(el("p", { class: "hint" }, info.error));
  } else {
    const hw = info.hardware || {};
    body.append(el("div", { class: "status-line" }, el("span", { class: "dot ok" }), `Connected · ${plural(info.installed.length, "model")} available${hw.ram_gb ? ` · this computer has ${hw.ram_gb} GB RAM${hw.vram_gb ? `, ${hw.vram_gb} GB GPU memory` : ", no GPU found"}` : ""}`));
  }
  body.append(el("div", { class: "actions" }, el("button", { class: "btn small", type: "button", onclick: fetchModels }, "Refresh")));

  const name = el("input", { value: d.model, placeholder: "model name", "aria-label": "Model name", autocomplete: "off",
    oninput: (ev) => { d.model = ev.target.value.trim(); md.test = null; for (const r of body.querySelectorAll("input[name=m]")) r.checked = r.value === d.model; } });
  if (info.installed.length) {
    body.append(el("div", { class: "eyebrow" }, "Installed"), el("div", { class: "opts" }, info.installed.map((m) =>
      el("label", { class: "opt" }, el("input", { type: "radio", name: "m", value: m.name, checked: m.name === d.model, onchange: () => { d.model = m.name; name.value = m.name; md.test = null; } }),
        el("span", { class: "grow" }, el("div", { class: "name" }, m.name), el("div", { class: "sub" }, [m.params, m.quant, m.size_gb ? `${m.size_gb} GB` : ""].filter(Boolean).join(" · ") || "installed")),
        m.name === d.model ? el("span", { class: "chip ok" }, "In use") : null))));
  }
  body.append(el("label", {}, info.installed.length ? "Or type a model name" : "Model name", name));

  const rec = (info.recommended || []).filter((m) => !m.installed).slice(0, 5);
  if (rec.length) {
    body.append(el("div", { class: "eyebrow" }, "Suggested for this computer (not installed)"), el("div", { class: "opts" }, rec.map((m) =>
      el("div", { class: "rec" }, el("div", { class: "grow" }, el("div", { class: "name" }, m.name),
        el("div", { class: "sub muted small" }, `needs about ${m.ram_gb} GB memory${m.long_context ? " · handles long chapters" : ""}`),
        el("code", {}, `ollama pull ${m.name}`)),
      el("button", { class: "btn small", type: "button", onclick: () => copyText(`ollama pull ${m.name}`) }, icon("copy"), "Copy command")))));
  }

  const refine = el("select", { "aria-label": "Refinement model", onchange: (ev) => { d.refineModel = ev.target.value; } },
    el("option", { value: "" }, "Same as the translation model"), info.installed.filter((m) => m.name !== d.model).map((m) => el("option", { value: m.name, selected: m.name === d.refineModel }, m.name)));
  body.append(el("label", {}, "Refinement and review model (optional)", refine, el("span", { class: "hint" }, "A larger model for the polish pass and AI review. Leave as is: translation-only models automatically use the helper model for this.")));
  body.append(el("label", { class: "check" }, el("input", { type: "checkbox", checked: d.summarize, onchange: (ev) => { d.summarize = ev.target.checked; } }),
    el("span", {}, "Summarize each chapter so later chapters remember the plot ", el("span", { class: "hint" }, "(a little slower, better continuity)"))));

  const out = el("div", { id: "test-out" });
  body.append(el("div", { class: "actions" }, el("button", { class: "btn", type: "button", onclick: () => testModel(out) }, icon("play"), "Test this model"), el("span", { class: "hint" }, "Translates one short sentence. The first run can take a minute while the model loads.")), out);
  if (md.test) showTest(out, md.test);
}

function showTest(out, r) {
  clear(out).append(el("div", { class: "test-out " + (r.ok ? "ok" : "bad") }, r.ok ? `“${r.text}” · ${r.seconds}s` : `Failed after ${r.seconds}s: ${r.error}`));
}
const testModel = (out) => once("model-test", async () => {
  const d = md.draft;
  clear(out).append(el("div", { class: "status-line" }, el("div", { class: "spin" }), "Testing… this can take a minute on the first run."));
  try { md.test = await api("POST", "/api/models/test", { provider: d.provider, model: d.model, base_url: d.baseUrl }); } catch (e) { md.test = { ok: false, error: e.message, seconds: 0 }; }
  showTest(out, md.test);
});
async function copyText(t) {
  try { await navigator.clipboard.writeText(t); toast("Copied. Paste it into a terminal."); } catch (e) { toast(`Run this in a terminal: ${t}`); }
}

// ---- wiring ---------------------------------------------------------------------------------------
function wireDialogs() {
  document.addEventListener("click", (ev) => { const b = ev.target.closest("[data-close]"); if (b) b.closest("dialog").close(); });

  const drop = $("#work-drop"), fileIn = drop.querySelector("input");
  fileIn.addEventListener("change", () => wireWorkDrop(fileIn.files[0] || null));
  for (const t of ["dragenter", "dragover"]) drop.addEventListener(t, (ev) => { ev.preventDefault(); ev.stopPropagation(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (ev) => {
    ev.preventDefault(); ev.stopPropagation(); drop.classList.remove("over");
    if (ev.dataTransfer.files.length) { fileIn.files = ev.dataTransfer.files; wireWorkDrop(fileIn.files[0]); }
  });
  $("#style-new").addEventListener("change", (ev) => { $("#style-new-hint").textContent = (STYLES[ev.target.value] || ["", ""])[1]; });

  $("#form-work").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target, file = f.file.files[0], btn = $("#work-submit");
    await once("work", async () => {
      btn.disabled = true; btn.textContent = file ? "Creating and reading the book…" : "Creating…";
      let id = null;
      try {
        const p = await api("POST", "/api/projects", { name: f.name.value.trim(), source_lang: f.source_lang.value, target_lang: f.target_lang.value, style: f.style.value }, { "Idempotency-Key": workKey });
        id = p.id;
        if (file) { const fd = new FormData(); fd.append("file", file); const r = await api("POST", `/api/projects/${id}/import`, fd); toast(`Added ${plural(r.chapters_added, "chapter")}.`); }
        else toast("Work created. Add its EPUB to bring in the chapters.");
      } catch (e) { fail(e); }
      $("#dlg-work").close();
      if (id != null) go(`#/work/${id}`);
    });
  });

  $("#paste-text").addEventListener("input", (ev) => {
    const n = ev.target.value.split(/\n\s*\n/).filter((x) => x.trim()).length;
    $("#paste-count").textContent = n ? plural(n, "paragraph") : "";
  });
  $("#form-paste").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target, translate = !!ev.submitter && ev.submitter.value === "translate";
    if (translate && !requireModel()) return;
    const c = S.cfg, buttons = $$("#form-paste button[type=submit]");
    await once("paste", async () => {
      buttons.forEach((b) => { b.disabled = true; });
      try {
        const r = await api("POST", `/api/projects/${S.pid}/paste`, { text: f.text.value, title: f.title.value || "Pasted", translate, provider: c.provider, model: c.model, base_url: c.baseUrl }, { "Idempotency-Key": pasteKey });
        $("#dlg-paste").close();
        if (r.job_error) toast(`Added ${plural(r.chapter_ids.length, "chapter")}, but translating did not start: ${r.job_error}`, true);
        else toast(`Added ${plural(r.chapter_ids.length, "chapter")} to this work.`);
        if (r.job) S.job = r.job;
        await reload();
        if (r.chapter_ids.length === 1) go(`#/work/${S.pid}/chapter/${r.chapter_ids[0]}`); else render();
        if (r.job) watchJob(r.job);
      } catch (e) { fail(e); } finally { buttons.forEach((b) => { b.disabled = false; }); }
    });
  });

  $("#form-model").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const d = md.draft;
    if (d.provider !== "mock" && !d.model.trim()) { toast("Choose or type a model name first.", true); return; }
    if (d.provider === "openai-compatible" && !d.baseUrl) { toast("Enter the server address first.", true); return; }
    S.cfg = { ...d, model: d.model.trim(), chosen: true }; saveCfg();
    $("#dlg-model").close(); toast(d.provider === "mock" ? "Demo mode on." : `Using ${S.cfg.model}.`);
    refreshModelStatus();
  });

  $("#form-bible").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const f = ev.target, body = { source: f.source.value.trim() };
    for (const k of ["translation", "gender", "pronouns", "notes"]) if (f[k].value) body[k] = f[k].value;
    try {
      await api("PUT", `/api/projects/${S.pid}/bible/${$("#bible-section").value}`, body);
      $("#dlg-bible").close(); toast("Saved to the Story Bible.");
      await reload(); render();
    } catch (e) { fail(e); }
  });
}

function wire() {
  wireLook(); wireDialogs();
  $("#model-chip").addEventListener("click", openModelDialog);
  // a file dropped outside a drop zone would make the browser open it and leave the app
  document.addEventListener("dragover", (ev) => { if (ev.dataTransfer && [...ev.dataTransfer.types].includes("Files")) ev.preventDefault(); });
  document.addEventListener("drop", (ev) => { if (ev.defaultPrevented) return; ev.preventDefault(); if (ev.dataTransfer && ev.dataTransfer.files.length && !$("dialog[open]")) addFiles(ev.dataTransfer.files); });
  $("#app").addEventListener("dragover", (ev) => { const z = ev.target.closest(".drop"); if (z) z.classList.add("over"); });
  $("#app").addEventListener("dragleave", (ev) => { const z = ev.target.closest(".drop"); if (z) z.classList.remove("over"); });
  $("#app").addEventListener("focusout", () => setTimeout(() => { if (S.stale && S.editing == null && !typing()) { S.stale = false; render(); } }, 0));
  // an image copied from a browser, a screenshot tool or a chat app can be pasted straight onto a work
  document.addEventListener("paste", (ev) => {
    const files = [...(ev.clipboardData ? ev.clipboardData.files : [])].filter((f) => f.type.startsWith("image/"));
    if (!files.length || typing() || $("dialog[open]")) return;
    ev.preventDefault();
    addFiles(files.map((f, i) => (PAGE_FILE.test(f.name) ? f : new File([f], `pasted-${Date.now()}-${i + 1}.${(f.type.split("/")[1] || "png").replace("jpeg", "jpg")}`, { type: f.type }))));
  });
  window.addEventListener("hashchange", route);
}

wire();
route();
refreshModelStatus();
