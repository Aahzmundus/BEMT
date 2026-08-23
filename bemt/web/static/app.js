// BEMT front end. One page, no framework, no build step.
// Everything renders from the single state object the server hands back.

let STATE = null;
let LANG = "en";
let BUSY = false;

const $ = (id) => document.getElementById(id);

function t(key, vars) {
  const table = window.STRINGS[LANG] || window.STRINGS.en;
  let s = table[key] ?? window.STRINGS.en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
  return s;
}

async function api(path, opts) {
  const resp = await fetch(path, opts);
  let body = null;
  try { body = await resp.json(); } catch { /* empty body */ }
  if (!resp.ok) {
    const err = new Error((body && (body.detail || body.error)) || resp.statusText);
    err.status = resp.status;
    err.body = body || {};
    throw err;
  }
  return body;
}

const jsonPost = (path, payload, method = "POST") => api(path, {
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload || {}),
});

// ------------------------------------------------------------------ helpers

function ago(seconds) {
  if (seconds == null) return null;
  if (seconds < 90) return t("refreshed_just_now");
  const mins = Math.round(seconds / 60);
  if (mins < 60) return t("refreshed_ago", { t: t("minutes", { n: mins }) });
  const hours = Math.round(mins / 60);
  if (hours < 48) return t("refreshed_ago", { t: t("hours", { n: hours }) });
  return t("refreshed_ago", { t: t("days", { n: Math.round(hours / 24) }) });
}

function notice(message, kind = "") {
  const el = $("notice");
  el.textContent = message;
  el.className = "notice" + (kind ? " " + kind : "");
  el.hidden = !message;
}

function applyStaticText() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPh);
  });
  $("lang-toggle").textContent = LANG === "en" ? "EN" : "HR";
  document.documentElement.lang = LANG;
}

// ------------------------------------------------------------------- render

function render() {
  if (!STATE) return;
  applyStaticText();

  const s = STATE.settings || {};
  const totals = STATE.totals || {};
  const character = STATE.character;

  // who + market
  const who = $("who");
  who.innerHTML = "";
  if (character) {
    who.textContent = `${t("logged_in_as")} ${character.name}`;
  } else {
    const a = document.createElement("a");
    a.href = "/auth/login";
    a.textContent = t("log_in");
    who.appendChild(a);
  }
  $("market-label").textContent = s.location_name || "";

  // hero
  const buying = totals.buy_lines || 0;
  const heroNum = $("hero-number");
  heroNum.textContent = STATE.last_refresh ? buying : "—";
  heroNum.className = "hero-number " + (buying ? "some" : "zero");
  $("hero-label").textContent = !STATE.last_refresh
    ? t("items_to_buy")
    : buying === 0 ? t("all_stocked")
      : buying === 1 ? t("item_to_buy") : t("items_to_buy");

  const stamp = $("stamp");
  const when = ago(STATE.last_refresh_age);
  stamp.textContent = when || t("never_refreshed");
  // A day-old list is a stale list: an order can sell out in an hour.
  stamp.className = "stamp" + ((STATE.last_refresh_age ?? 0) > 86400 || !when ? " stale" : "");

  $("copy").disabled = !STATE.multibuy;

  // settings panel
  $("set-hangar").checked = !!s.count_hangar;
  $("set-import").checked = !!s.auto_import;
  $("set-lot").value = s.buy_lot_size ?? 0;
  document.querySelectorAll(".hangar-col").forEach((el) => {
    el.style.display = s.count_hangar ? "" : "none";
  });

  // scope re-login prompt
  if (character && character.missing_scopes && character.missing_scopes.length) {
    notice(t("scopes_needed"), "warn");
  }

  renderRows();
}

function renderRows() {
  const tbody = $("rows");
  tbody.innerHTML = "";
  const rows = STATE.rows || [];
  $("empty").hidden = rows.length > 0;
  $("empty").textContent = t("empty");

  for (const r of rows) {
    const tr = document.createElement("tr");
    tr.className = r.status;

    const name = document.createElement("td");
    name.className = "c-name";
    const dot = document.createElement("span");
    dot.className = "dot";
    dot.title = t(r.status === "untracked" ? "ok" : r.status);
    name.append(dot, document.createTextNode(r.name));
    tr.appendChild(name);

    const target = document.createElement("td");
    target.className = "num";
    const input = document.createElement("input");
    input.type = "number";
    input.min = "0";
    input.className = "target";
    input.value = r.target_qty;
    input.addEventListener("change", () => saveTarget(r.type_id, input));
    target.appendChild(input);
    tr.appendChild(target);

    tr.appendChild(cell(r.listed_qty, "num"));
    tr.appendChild(cell(r.hangar_qty, "num hangar-col"));
    // A zero here is left blank on purpose: "nothing to buy" should read as
    // quiet, not as a column of noisy zeroes competing with the real numbers.
    tr.appendChild(cell(r.buy_qty ? r.buy_qty : "", "num buy"));

    const act = document.createElement("td");
    act.className = "c-act";
    // Icon plus label: the label is hidden on a narrow screen (CSS) so the
    // action column can't push the table into a horizontal scroll.
    const pause = linkButton("", () => patchItem(r.type_id, { active: !r.active }),
      r.active ? t("pause") : t("resume"));
    const ico = document.createElement("span");
    ico.textContent = r.active ? "⏸" : "▶";
    const label = document.createElement("span");
    label.className = "act-label";
    label.textContent = r.active ? t("pause") : t("resume");
    pause.append(ico, label);
    act.appendChild(pause);
    act.appendChild(linkButton("✕", () => {
      if (confirm(t("remove_confirm", { name: r.name }))) removeItem(r.type_id);
    }, t("remove")));
    tr.appendChild(act);

    tbody.appendChild(tr);
  }
  const s = STATE.settings || {};
  document.querySelectorAll(".hangar-col").forEach((el) => {
    el.style.display = s.count_hangar ? "" : "none";
  });
}

function cell(text, cls) {
  const td = document.createElement("td");
  td.className = cls || "";
  td.textContent = text === 0 ? "0" : (text || (text === "" ? "" : "0"));
  return td;
}

function linkButton(label, onClick, title) {
  const b = document.createElement("button");
  b.className = "link";
  b.textContent = label;
  if (title) b.title = title;
  b.addEventListener("click", onClick);
  return b;
}

// ------------------------------------------------------------------ actions

async function saveTarget(typeId, input) {
  const value = Math.max(0, parseInt(input.value, 10) || 0);
  input.value = value;
  STATE = await jsonPost(`/api/items/${typeId}`, { target_qty: value }, "PATCH");
  input.classList.add("saved");
  render();
  // Re-find the (re-rendered) input and flash it, so an edit visibly landed.
  setTimeout(() => document.querySelectorAll(".target.saved")
    .forEach((el) => el.classList.remove("saved")), 700);
}

async function patchItem(typeId, payload) {
  STATE = await jsonPost(`/api/items/${typeId}`, payload, "PATCH");
  render();
}

async function removeItem(typeId) {
  STATE = await api(`/api/items/${typeId}`, { method: "DELETE" });
  render();
}

async function addItem() {
  const nameEl = $("add-name");
  const qtyEl = $("add-qty");
  const name = nameEl.value.trim();
  if (!name) return;
  try {
    const result = await jsonPost("/api/items", {
      name,
      target_qty: parseInt(qtyEl.value, 10) || 0,
    });
    STATE = result;
    nameEl.value = "";
    qtyEl.value = "";
    notice("", "");
    render();
  } catch (e) {
    notice(e.message, "bad");
  }
}

async function doRefresh() {
  if (BUSY) return;
  BUSY = true;
  const btn = $("refresh");
  btn.disabled = true;
  btn.textContent = t("refreshing");
  notice("", "");
  $("setup").hidden = true;
  try {
    const result = await jsonPost("/api/refresh", {});
    STATE = result;
    const sum = result.summary || {};
    const parts = [];
    if (sum.imported) parts.push(t("imported_n", { n: sum.imported }));
    if (sum.hangar_error) parts.push(t("hangar_failed"));
    notice(parts.join(" · "), sum.hangar_error ? "warn" : "good");
    render();
  } catch (e) {
    if (e.status === 409) handleSetup(e.body);
    else notice(e.message, "bad");
  } finally {
    BUSY = false;
    btn.disabled = false;
    btn.textContent = t("refresh");
  }
}

function handleSetup(body) {
  const panel = $("setup");
  panel.innerHTML = "";
  if (body.reason === "no_character" || body.reason === "missing_scopes") {
    notice(body.reason === "no_character" ? t("login_needed") : t("scopes_needed"), "warn");
    const wrap = document.createElement("div");
    wrap.className = "center-cta";
    const a = document.createElement("a");
    a.href = "/auth/login";
    const b = document.createElement("button");
    b.className = "primary";
    b.textContent = t("log_in");
    a.appendChild(b);
    wrap.appendChild(a);
    panel.appendChild(wrap);
    panel.hidden = false;
    return;
  }
  if (body.reason === "choose_location") {
    showMarketPicker(body.locations, panel);
    return;
  }
  if (body.reason === "no_orders") {
    notice(t("no_orders"), "warn");
    return;
  }
  notice(body.error || "Setup needed", "warn");
}

function showMarketPicker(locations, panel) {
  panel.innerHTML = "";
  const h = document.createElement("h2");
  h.textContent = t("choose_market");
  const p = document.createElement("p");
  p.className = "hint";
  p.style.margin = "0 0 10px";
  p.textContent = t("choose_market_hint");
  panel.append(h, p);
  const list = document.createElement("div");
  list.className = "market-picker";
  for (const loc of locations || []) {
    const b = document.createElement("button");
    b.className = "market-option";
    if (STATE && STATE.settings.location_id === loc.location_id) b.classList.add("current");
    const nm = document.createElement("span");
    nm.textContent = loc.name || `Location ${loc.location_id}`;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = t("n_orders", { n: loc.orders });
    b.append(nm, meta);
    b.addEventListener("click", async () => {
      STATE = await jsonPost("/api/settings", {
        location_id: loc.location_id,
        location_name: loc.name,
      });
      panel.hidden = true;
      render();
      doRefresh();
    });
    list.appendChild(b);
  }
  panel.appendChild(list);
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function copyMultibuy() {
  const text = STATE && STATE.multibuy;
  if (!text) { notice(t("nothing_to_copy"), "warn"); return; }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Fallback for a browser that refuses the async clipboard API.
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  notice(t("copied"), "good");
}

async function saveSettings() {
  STATE = await jsonPost("/api/settings", {
    count_hangar: $("set-hangar").checked,
    auto_import: $("set-import").checked,
    buy_lot_size: parseInt($("set-lot").value, 10) || 0,
  });
  render();
}

async function loadSuggestions() {
  const q = $("add-name").value.trim();
  const { matches } = await api(`/api/suggest?q=${encodeURIComponent(q)}`);
  const list = $("name-suggestions");
  list.innerHTML = "";
  for (const m of matches) {
    const opt = document.createElement("option");
    opt.value = m.name;
    list.appendChild(opt);
  }
}

// --------------------------------------------------------------------- boot

function wire() {
  $("refresh").addEventListener("click", doRefresh);
  $("copy").addEventListener("click", copyMultibuy);
  $("add-btn").addEventListener("click", addItem);
  $("add-name").addEventListener("keydown", (e) => { if (e.key === "Enter") addItem(); });
  $("add-qty").addEventListener("keydown", (e) => { if (e.key === "Enter") addItem(); });

  let suggestTimer = null;
  $("add-name").addEventListener("input", () => {
    clearTimeout(suggestTimer);
    suggestTimer = setTimeout(loadSuggestions, 150);
  });

  $("settings-toggle").addEventListener("click", () => {
    $("settings").hidden = !$("settings").hidden;
  });
  $("set-hangar").addEventListener("change", saveSettings);
  $("set-import").addEventListener("change", saveSettings);
  $("set-lot").addEventListener("change", saveSettings);
  $("logout").addEventListener("click", async () => {
    await jsonPost("/api/logout", {});
    location.reload();
  });
  $("change-market").addEventListener("click", async () => {
    try {
      const { locations } = await api("/api/locations");
      showMarketPicker(locations, $("setup"));
    } catch (e) {
      if (e.status === 409) handleSetup(e.body);
      else notice(e.message, "bad");
    }
  });

  $("lang-toggle").addEventListener("click", async () => {
    LANG = LANG === "en" ? "hr" : "en";
    STATE = await jsonPost("/api/settings", { language: LANG });
    render();
  });
}

async function boot() {
  wire();
  STATE = await api("/api/state");
  LANG = (STATE.settings && STATE.settings.language) || "en";
  render();
  if (!STATE.character) notice(t("login_needed"), "warn");
  loadSuggestions();
}

boot();
