(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    settings: null,      // as saved on the Pi
    draft: {},           // unsaved edits, keyed by "section.key"
    fonts: [],
    tab: "clock",
    mode: null,          // what the panel is showing
    panel: { width: 250, height: 122, kind: "eink" },
  };

  // the clock tab holds two screens; its entry flips between "clock" and "weather" with the switch on the card
  const TAB_MODE = { clock: "clock", weather: "weather", music: "music", message: "message", draw: "image", read: "reader", camera: "camera", crypto: "crypto", system: "system" };
  const WIDE = window.matchMedia("(min-width: 900px)");     // a wide screen: columns, the side column, the action in the card
  const MODE_LABELS = { clock: "Clock", weather: "Weather", me: "Me", earnings: "Earnings", music: "Music", message: "Message", image: "Drawing", reader: "Book", camera: "Camera", postcard: "Postcard", groupchat: "Group chat", crypto: "Finance", system: "System", buddy: "Friend", off: "nothing" };
  // every font file on the Pi becomes a web font here too, so dropdowns and the canvas use the real face
  const fontFamily = (id) => "pf-" + id.replace(/[^a-z0-9]/gi, "_");
  function loadWebFonts(fonts) {
    const css = fonts.map((f) => `@font-face{font-family:"${fontFamily(f.id)}";src:url("/fonts/${encodeURIComponent(f.id)}");font-display:swap}`).join("\n");
    let el = $("#font-faces");
    if (!el) { el = document.createElement("style"); el.id = "font-faces"; document.head.appendChild(el); }
    el.textContent = css;
    for (const f of fonts) document.fonts.load(`16px "${fontFamily(f.id)}"`).catch(() => {});
  }

  // -- helpers -------------------------------------------------------------------

  async function api(path, opts = {}) {
    const res = await fetch("/api" + path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    const data = await res.json().catch(() => ({ ok: false, error: res.statusText }));
    if (!data.ok) throw new Error(data.error || "Request failed");
    return data;
  }

  let toastTimer;
  function toast(text) {
    const el = $("#toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (el.hidden = true), 2200);
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // -- settings bound to inputs -----------------------------------------------------

  function getSetting(path) {
    if (path in state.draft) return state.draft[path];
    const [section, key] = path.split(".");
    return state.settings?.[section]?.[key];
  }
  function setDraft(path, value) {
    const [section, key] = path.split(".");
    if (state.settings?.[section]?.[key] === value) delete state.draft[path];
    else state.draft[path] = value;
  }
  function draftPatch(sections) {
    const patch = {};
    for (const [path, value] of Object.entries(state.draft)) {
      const [section, key] = path.split(".");
      if (!sections || sections.includes(section)) (patch[section] ??= {})[key] = value;
    }
    return patch;
  }
  function hasDraft(sections) { return Object.keys(draftPatch(sections)).length > 0; }
  function clearDraft(sections) {
    for (const k of Object.keys(state.draft)) if (sections.includes(k.split(".")[0])) delete state.draft[k];
  }

  function renderInputs() {
    for (const el of $$("[data-setting]")) {
      const value = getSetting(el.dataset.setting);
      if (value === undefined) continue;
      if (el.classList.contains("segmented")) {
        for (const b of $$("button", el)) b.classList.toggle("active", b.dataset.value === String(value));
      } else if (el.type === "checkbox") {
        el.checked = !!value;
      } else if (document.activeElement !== el) {
        el.value = value;
        // a sound device saved by its old number (plughw:1,0) is the option with that alias
        if (el.tagName === "SELECT" && el.selectedIndex < 0 && value) {
          const byAlias = $$("option", el).find((o) => o.dataset.alias === String(value));
          if (byAlias) byAlias.selected = true;
        }
      }
    }
    renderHashrates();
    $("#lcd-crypto-field").hidden = getSetting("lcd.screen") !== "crypto";
    $("#lcd-camera-hint").hidden = getSetting("lcd.screen") !== "camera";
    syncDrawTarget();
    syncMining();
    updateOutputs();
    const src = getSetting("message.source");
    $("#message-text-field").hidden = src !== "message";
    $("#message-cycle-field").hidden = src === "message";
    $("#message-speak-field").style.display = ["joke", "fortune", "topic"].includes(src) ? "" : "none";
    $("#msg-speak").hidden = src === "bot";
    $$('[data-setting="message.valign"]').forEach((el) => (el.style.display = src === "bot" ? "none" : ""));
    $("#buddy-night-row").hidden = getSetting("buddy.night") === false;
    renderScreensCount();
  }

  // Settings → Screen: one screen or two — and with two, which LCD. (setup.sh asks the same; either way
  // it's lcd.available, and with one screen the page shows nothing about a second.)
  function renderScreensCount() {
    const two = !!getSetting("lcd.available");
    for (const b of $$("#screens-count button")) b.classList.toggle("active", (b.dataset.value === "2") === two);
    const model = String(getSetting("lcd.model") || "1.69");
    for (const b of $$("#lcd-model-pick button")) b.classList.toggle("active", b.dataset.value === model);
    $("#screens-row").hidden = !!state.panel.colour;            // a colour main screen (a Whisplay) is the only one
    $("#screens-model-field").hidden = !two;
  }
  $("#screens-count").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-value]"); if (!b) return;
    const two = b.dataset.value === "2";
    setDraft("lcd.available", two);
    setDraft("lcd.enabled", two);              // two: the LCD is used from the save on; one: it's off
    onEdit();
    applyPanel(state.panel);                   // the second screen's settings come into view (or go)
    if (two) loadLcd();
  });
  $("#lcd-model-pick").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-value]"); if (!b) return;
    setDraft("lcd.model", b.dataset.value);
    onEdit();
  });

  const OUTPUTS = {
    "clock.size": ["#clock-size-out", (v) => `${v}px`],
    "message.size": ["#message-size-out", (v) => `${v}px`],
    "message.cycle_seconds": ["#message-cycle-out", (v) => (Number(v) ? `${v}s` : "never")],
    "crypto.graph_days": ["#crypto-days-out", (v) => `${v} day${v == 1 ? "" : "s"}`],
    "crypto.coin_seconds": ["#crypto-coin-out", (v) => `${v}s`],
    "crypto.refresh_seconds": ["#crypto-refresh-out", (v) => `${v}s`],
    "holdings.move_pct": ["#holdings-move-out", (v) => `±${v}%`],
    "display.full_refresh_every": ["#full-out", (v) => `${v}`],
    "lcd.brightness": ["#lcd-bright-out", (v) => `${v}%`],
    "pet.tempo": ["#pet-tempo-out", (v) => `${Number(v).toFixed(1)} a second`],
    "snake.decide_ms": ["#snake-decide-out", (v) => `${v} ms`],
    "snake.speed": ["#snake-speed-out", (v) => `${v} px/s`],
    "snake.bored_minutes": ["#snake-bored-out", (v) => (Number(v) ? `${v} min` : "never")],
    "display.brightness": ["#panel-bright-out", (v) => `${v}%`],
    "camera.interval": ["#cam-interval-out", (v) => `${Number(v).toFixed(1)}s`],
    "audio.piper_speed": ["#piper-speed-out", (v) => `${Number(v).toFixed(1)}×`],
    "llm.chatter_minutes": ["#llm-chatter-out", (v) => (Number(v) ? `about every ${v} min` : "off")],
    "cycle.seconds": ["#cycle-out", (v) => (Number(v) >= 60 ? `${Math.round(v / 60)} min` : `${v}s`)],
    "weather.refresh_minutes": ["#wx-refresh-out", (v) => `${v} min`],
    "camera.rotation": ["#cam-rot-out", (v) => `${v}°`],
    "reader.size": ["#rd-size-out", (v) => `${v}px`],
    "reader.margin": ["#rd-margin-out", (v) => `${v}px`],
    "reader.zoom": ["#rd-zoom-out", (v) => `${Number(v).toFixed(1)}×`],
    "reader.auto_seconds": ["#rd-auto-out", (v) => (Number(v) ? `every ${v}s` : "off")],
    "reader.rotation": ["#rd-rot-out", (v) => `${v}°`],
    "camera_watch.look_gap": ["#watch-gap-out", (v) => `${v}s`],
    "camera_watch.check_minutes": ["#watch-check-out", (v) => (Number(v) ? `${v} min` : "never")],
    "agent.every_minutes": ["#agent-every-out", (v) => `${v} min`],
    "ptz.speed": ["#ptz-speed-out", (v) => `${v}°/s`],
    "ptz.sweep_degrees": ["#ptz-sweep-out", (v) => `${v}° each way`],
    "ptz.lowest": ["#ptz-lowest-out", (v) => `${v}°`],
    "ptz.gentle": ["#ptz-gentle-out", (v) => `${v}°/s`],
    "buddy.every_seconds": ["#buddy-every-out", (v) => (Number(v) >= 60 ? `${+(v / 60).toFixed(1)} min` : `${v}s`)],
    "buddy.reach": ["#buddy-reach-out", (v) => `${v}° each way`],
    "buddy.rest": ["#buddy-rest-out", (v) => `${v} s`],
    "buddy.greet_minutes": ["#buddy-greet-out", (v) => `${v} min away`],
  };
  function updateOutputs() {
    for (const [path, [sel, fmt]] of Object.entries(OUTPUTS)) {
      const v = getSetting(path);
      if (v !== undefined) $(sel).textContent = fmt(v);
    }
  }

  function bindInputs() {
    $("#panel-select").addEventListener("change", (e) => {
      const p = (state.panels || []).find((x) => x.id === e.target.value);
      if (p && p.colour && !(Number(getSetting("display.rotation")) % 180)) setDraft("display.rotation", 90);
    });
    for (const el of $$("[data-setting]")) {
      const path = el.dataset.setting;
      if (el.classList.contains("segmented")) {
        el.addEventListener("click", (e) => {
          const b = e.target.closest("button"); if (!b) return;
          setDraft(path, b.dataset.value); onEdit();
        });
      } else if (el.type === "checkbox") {
        el.addEventListener("change", () => { setDraft(path, el.checked); onEdit(); });
      } else if (el.type === "range") {
        el.addEventListener("input", () => { setDraft(path, Number(el.value)); onEdit(); });
      } else if (el.tagName === "SELECT") {
        el.addEventListener("change", () => { setDraft(path, el.classList.contains("font-select") || el.classList.contains("words") || el.id === "lcd-model" || isNaN(el.value) ? el.value : Number(el.value)); onEdit(); });
      } else if (el.type === "number") {
        el.addEventListener("input", () => { setDraft(path, el.value === "" ? 0 : Number(el.value)); onEdit(); });
      } else {
        el.addEventListener("input", () => { setDraft(path, el.value); onEdit(); });
      }
    }
    bindHashrates();
  }

  // -- a hashrate as a number and a unit, kept in the settings as H/s -----------------------
  const HASH_UNITS = [1e9, 1e6, 1e3, 1];
  const hashUnit = {};                                    // the unit you picked, per pool
  function autoUnit(v) { return HASH_UNITS.find((u) => v >= u) || 1; }
  function renderHashrates() {
    for (const num of $$(".hash-num")) {
      const pool = num.dataset.pool, sel = $(`.hash-unit[data-pool="${pool}"]`);
      const v = Number(getSetting(`${pool}.min_hashrate`)) || 0;
      if (document.activeElement === num || document.activeElement === sel) continue;
      const unit = hashUnit[pool] || (v > 0 ? autoUnit(v) : (pool === "verus" ? 1e6 : 1));
      hashUnit[pool] = unit;
      sel.value = String(unit);
      num.value = v > 0 ? String(Number((v / unit).toFixed(3))) : "";
    }
  }
  function bindHashrates() {
    for (const num of $$(".hash-num")) {
      const pool = num.dataset.pool, sel = $(`.hash-unit[data-pool="${pool}"]`);
      const push = () => { setDraft(`${pool}.min_hashrate`, Math.max(0, Math.round((Number(num.value) || 0) * hashUnit[pool]))); onEdit(); };
      num.addEventListener("input", push);
      sel.addEventListener("change", () => { hashUnit[pool] = Number(sel.value) || 1; push(); });   // the number stays, the unit moves
    }
  }

  // some tabs edit more than their own section: the Me tab also owns the
  // creature and its voice, so those save with it too
  const TAB_SECTIONS = { message: ["message", "llm"], music: ["music"], clock: ["clock"], weather: ["weather"], crypto: ["crypto", "duco", "verus", "holdings"], camera: ["camera", "ptz", "pet"] };
  // what each panel saves — up here so the save buttons can ask before the panels are built
  const SHEET_SECTIONS = ["display", "buttons", "audio", "listen", "cycle", "me", "earnings", "schedule",
                          "camera_watch", "telegram", "lcd", "whisplay", "home", "gestures", "agent", "buddy", "duco", "verus", "snake"];
  const BOT_SECTIONS = ["llm", "audio", "message"];
  function tabSections(tab = state.tab) { return TAB_SECTIONS[tab] || [TAB_MODE[tab]]; }

  function onEdit() {
    renderInputs(); updateButtons(); schedulePreview();
    if (state.tab === "camera") applyCamShape();
    if (hasDraft(["lcd"])) scheduleLcdPreview();
  }

  function updateButtons() {
    const mode = TAB_MODE[state.tab];
    const label = (MODE_LABELS[mode] || mode).toLowerCase();
    $("#action").textContent = mode === "image" ? (state.panel.colour ? (liveLcd() ? "Show drawing" : "Send drawing")
        : draw.target === "lcd" ? (liveLcd() ? "Show on the LCD" : "Send to the LCD") : "Send drawing")
      : (hasDraft([...tabSections(), "display"]) ? `Save & show ${label}` : `Show ${label}`);
    const liveWrap = $("#draw-live-wrap");
    if (liveWrap) { liveWrap.hidden = !forLcd(); $("#draw-live").checked = liveLcd(); }
    const save = $("#settings-save");
    if (save) save.disabled = !hasDraft(SHEET_SECTIONS);
    const botSave = $("#bot-save");
    if (botSave) botSave.disabled = !hasDraft(BOT_SECTIONS);
  }

  // -- preview -----------------------------------------------------------------------

  const previewImg = $("#preview");
  let previewUrl = null;
  function setPreviewBlob(blob) {
    const url = URL.createObjectURL(blob);
    previewImg.onload = () => { if (previewUrl) URL.revokeObjectURL(previewUrl); previewUrl = url; };
    previewImg.src = url;
  }

  const schedulePreview = debounce(refreshPreview, 200);

  async function refreshPreview() {
    const mode = TAB_MODE[state.tab];
    const draft = hasDraft([...tabSections(), "display"]);
    $("#draft-tag").hidden = !draft;
    try {
      let res;
      if (draft) {
        res = await fetch("/api/render.png", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ mode, settings: draftPatch() }),
        });
      } else {
        res = await fetch("/api/preview.png?t=" + Date.now());
      }
      if (res.ok) setPreviewBlob(await res.blob());
    } catch (e) { /* offline; status poll reports it */ }
  }

  function flashPanel() {
    const bezel = $("#bezel");
    bezel.classList.add("flash");
    setTimeout(() => bezel.classList.remove("flash"), 220);
  }

  // -- status ----------------------------------------------------------------------

  async function pollStatus() {
    try {
      const data = await api("/status");
      state.settings = data.settings;
      state.mode = data.service.mode;
      state.modes = data.modes;
      renderScreenGrid();
      if (JSON.stringify(data.fonts) !== JSON.stringify(state.fonts)) { state.fonts = data.fonts; fillFontSelects(); renderInputs(); }
      state.panels = data.panels;
      if (!$("#panel-select").options.length) {
        $("#panel-select").innerHTML = data.panels.map((p) => `<option value="${p.id}">${esc(p.label)}</option>`).join("");
      }
      applyPanel(data.service.panel);
      const showing = $("#showing");
      showing.textContent = data.service.error ? `error — ${data.service.error}`
        : data.service.warning ? "no answer from the panel" : (MODE_LABELS[state.mode] || "nothing");
      showing.title = data.service.warning || data.service.error || "";
      const panelNote = $("#panel-note");                      // under the panel dropdown, where it's put right
      if (panelNote) { panelNote.textContent = data.service.warning || ""; panelNote.hidden = !data.service.warning; }
      $("#live-text").textContent = data.service.panel.driver.startsWith("mock") ? `${data.hostname} · no panel` : data.hostname;
      if (data.hostname) {
        $("#pi-name").textContent = data.hostname;                           // the Pi's card is named after it
        $("#crypto-username").placeholder = data.hostname;                     // and so is the prompt on the screens, until a name is set
      }
      renderInputs();
      updateButtons();
    } catch (e) {
      $("#live-text").textContent = "offline";
    }
  }

  // the preview, the drawing canvas and the e-ink-only controls follow the panel's shape
  const KEY_ACTIONS = [["prev", "Previous"], ["next", "Next"], ["next_mode", "Next screen"], ["prev_mode", "Previous screen"],
    ["refresh", "Full refresh"], ["off", "Screen off"], ["none", "Nothing"]];
  for (const sel of $$(".key-select")) sel.innerHTML = KEY_ACTIONS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("");

  function applyPanel(panel) {
    const changed = panel.width !== state.panel.width || panel.height !== state.panel.height || panel.colour !== state.panel.colour;
    state.panel = panel;
    $("#keys-group").hidden = !panel.buttons;
    for (const row of $$("#keys .field")) row.hidden = Number(row.querySelector("button")?.dataset.key || 0) > (panel.buttons || 0);
    $("#keys-label").textContent = panel.buttons === 1 ? "The button" : "Keys on the HAT";
    $("#full-refresh-field").hidden = panel.has_partial === false || !!panel.colour;
    $("#lcd-panel-fields").hidden = !panel.colour;
    // a second screen is only offered once setup.sh has been told there is one (./setup.sh, or --lcd);
    // on a colour main screen there is no second one either way
    const twoScreens = !!getSetting("lcd.available") && !panel.colour;
    document.body.classList.toggle("one-screen", !twoScreens);
    $("#lcd-group").hidden = !twoScreens;
    $("#whisplay-group").hidden = panel.driver !== "whisplay" && panel.driver !== "mock_whisplay";
    if (panel.driver === "whisplay" || panel.driver === "mock_whisplay") loadWhisplay();
    // the GPi Case: its buttons; its screen turns only upside down, and its backlight isn't dimmable
    const gpi = panel.driver === "gpicase" || panel.driver === "mock_gpicase";
    $("#gpi-group").hidden = !gpi;
    if (gpi) loadGpi();
    for (const o of $$("#rotation-select option")) o.hidden = gpi && (o.value === "90" || o.value === "270");
    const bright = $('[data-setting="display.brightness"]');
    if (bright) bright.closest(".field").hidden = gpi;
    const [gw, gh] = panel.glass || [panel.width, panel.height];
    previewImg.width = gw; previewImg.height = gh;
    previewImg.style.aspectRatio = `${gw} / ${gh}`;
    previewImg.style.borderRadius = panel.kind === "lcd" ? "9%" : "";        // the LCD's rounded corners
    previewImg.style.maxWidth = panel.height <= 64 ? "440px" : "";
    $("#btn-refresh").hidden = panel.kind !== "eink";
    if (changed || !canvas.dataset.shaped) resizeCanvas(panel);
    if (state.tab === "camera") applyCamShape();
  }

  function fillFontSelects() {
    loadWebFonts(state.fonts);
    const css = (id) => `"${fontFamily(id)}", sans-serif`;
    $("#draw-text-font").innerHTML = state.fonts.map((f) => `<option value='${css(f.id)}' style='font-family:${css(f.id)}'>${esc(f.name)}</option>`).join("");
    for (const sel of $$(".font-select")) {
      sel.innerHTML = state.fonts.map((f) => `<option value="${esc(f.id)}" style='font-family:${css(f.id)}'>${esc(f.name)}</option>`).join("");
    }
  }

  // -- tabs ------------------------------------------------------------------------

  function showTab(name) {
    state.tab = name;
    for (const b of $$("#tabs button")) b.classList.toggle("active", b.dataset.tab === name);
    for (const s of $$("section.tab")) s.hidden = s.dataset.tab !== name;
    if (name === "system") { pollSystem(); pollUpdate(); loadFriends(); renderCycle(); }
    if (name === "crypto") { loadWatchlist(); loadMining(); loadHoldings(); }
    if (name === "weather") loadWeather();
    if (name === "music") loadMusic();
    if (name === "message") loadAudio();
    if (name === "message" && getSetting("message.source") === "bot") loadLlm(true);
    if (name === "read") loadBooks();
    setCameraStream(name === "camera");
    if (name === "camera") { loadCameras(true); loadEvents(); loadPtz(); }
    else { ptzRelease(); document.body.classList.remove("cam-turns"); }
    placeAction();
    updateButtons();
    renderPitatoAction();
    refreshPreview();
    if (!WIDE.matches) {                                  // the chosen chip in view in the sideways row
      const chip = $(`#tabs button[data-tab="${name}"]`);
      if (chip && chip.scrollIntoView) chip.scrollIntoView({ block: "nearest", inline: "nearest" });
    }
  }

  // a Pietato about: every tab can put its screen on the Pietato's too — full screen, laid out for its
  // shape — with the button beside the one that puts it on the panel. The clock and the live screens
  // are kept up to date there each minute; the button stops that.
  const PITATO_TABS = { clock: "clock", message: "message", draw: "drawing" };
  let pitatoScreens = [];
  async function loadPitatoScreens() {
    try { pitatoScreens = (await api("/pitato/screens")).screens || []; } catch (e) { pitatoScreens = []; }
    renderPitatoAction();
  }
  function pitatoKind(tab = state.tab) {
    if (PITATO_TABS[tab]) return { kind: PITATO_TABS[tab], name: PITATO_TABS[tab] };
    const mode = TAB_MODE[tab];
    return mode ? { kind: "screen", name: mode } : null;
  }
  function renderPitatoAction() {
    const b = $("#action-pitato");
    if (!b) return;
    const usable = pitatoScreens.filter((p) => !p.locked);
    const what = pitatoKind();
    b.hidden = !what || !usable.length;
    if (b.hidden) return;
    const kept = usable.some((p) => p.showing && p.showing === what.name);
    b.classList.toggle("on", kept);
    b.querySelector("span").textContent = kept ? "Stop sending" : usable.length > 1 ? "On a Pietato…" : "On the Pietato";
    const label = (MODE_LABELS[TAB_MODE[state.tab]] || what.name).toLowerCase();
    const where = usable.length > 1 ? "a Pietato's screen (or all of them)" : `${usable[0].name}'s screen`;
    b.title = kept ? `The ${label} screen is on a Pietato, kept up to date — press to stop updating it`
      : what.kind === "message" ? `These words on ${where}` : what.kind === "drawing" ? `This drawing on ${where}`
      : `The ${label} screen on ${where}, full screen`;
  }
  async function sendToPitato(to) {
    const b = $("#action-pitato"), what = pitatoKind();
    if (!what) return;
    const body = b.classList.contains("on") ? { kind: "stop", to: "all" } : { kind: what.kind, name: what.name, to };
    if (body.kind === "message") body.text = String(getSetting("message.text") || "");
    if (body.kind === "drawing") { stopEditing(true); body.image = flattened(); }
    b.disabled = true;
    try { toast((await api("/pitato/show", { method: "POST", body })).said); }
    catch (e) { toast(e.message); }
    b.disabled = false;
    loadPitatoScreens();
  }
  // more than one Pietato about: which one (or all of them), from a small menu under the button
  const pitatoMenu = document.createElement("div");
  pitatoMenu.className = "pop-menu"; pitatoMenu.id = "pitato-menu"; pitatoMenu.hidden = true; pitatoMenu.setAttribute("role", "menu");
  document.body.appendChild(pitatoMenu);
  function closePitatoMenu() { pitatoMenu.hidden = true; }
  function openPitatoMenu(anchor, usable) {
    pitatoMenu.innerHTML = usable.map((p) => `<button type="button" role="menuitem" data-to="${esc(p.ip)}">${esc(p.name)}</button>`).join("")
      + `<button type="button" role="menuitem" data-to="all">${usable.length === 2 ? "Both" : `All ${usable.length}`}</button>`;
    pitatoMenu.hidden = false;
    const r = anchor.getBoundingClientRect(), m = pitatoMenu.getBoundingClientRect();
    const below = r.bottom + 6 + m.height < window.innerHeight;
    pitatoMenu.style.top = `${below ? r.bottom + 6 : Math.max(8, r.top - 6 - m.height)}px`;
    pitatoMenu.style.left = `${Math.max(8, Math.min(window.innerWidth - m.width - 8, r.right - m.width))}px`;
    pitatoMenu.querySelector("button").focus();
  }
  pitatoMenu.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-to]"); if (!b) return;
    closePitatoMenu();
    sendToPitato(b.dataset.to);
  });
  document.addEventListener("click", (e) => { if (!pitatoMenu.hidden && !pitatoMenu.contains(e.target) && !e.target.closest("#action-pitato")) closePitatoMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !pitatoMenu.hidden) closePitatoMenu(); });
  $("#action-pitato").addEventListener("click", (e) => {
    const b = $("#action-pitato"), usable = pitatoScreens.filter((p) => !p.locked);
    if (!pitatoMenu.hidden) { closePitatoMenu(); return; }
    if (!b.classList.contains("on") && usable.length > 1) { openPitatoMenu(b, usable); return; }
    sendToPitato(usable.length ? usable[0].ip : undefined);
  });

  // on a wide screen the page is a dashboard: the tabs, the Pi's vitals and the second screen
  // live in a rail down the left, the panel's screens sit under the device, and the action
  // button in the tab card's head; on a phone the tabs and the button stay in the deck,
  // reachable without scrolling
  function placeAction() {
    const btn = $("#action"), tabs = $("#tabs"), rail = $("#rail");
    const panel = $("#panel-card"), lcd = $("#lcd-card"), pi = $("#pi-card"), sidebar = $("#sidebar");
    const pit = $("#action-pitato"), now = $("#now-card");
    if (WIDE.matches) {
      const head = $(`section.tab[data-tab="${state.tab}"] > .card-head`);
      if (!head) return;
      rail.hidden = false;
      if (tabs.parentElement !== rail) rail.appendChild(tabs);
      if (pi.parentElement !== sidebar) sidebar.appendChild(pi);  // the Pi's vitals under the device, below "Put on the panel"
      if (lcd.parentElement !== rail) rail.appendChild(lcd);
      if (btn.parentElement !== head) head.appendChild(btn);
      if (pit.parentElement !== head) head.appendChild(pit);
    } else {
      const deck = $("#deck"), home = $("#action-home");
      if (tabs.parentElement !== deck) deck.appendChild(tabs);
      if (btn.parentElement !== home) home.appendChild(btn);
      if (pit.parentElement !== home) home.appendChild(pit);
      if (pi.parentElement !== sidebar) sidebar.appendChild(pi);
      if (lcd.parentElement !== sidebar) sidebar.insertBefore(lcd, panel);
      rail.hidden = true;
    }
  }
  WIDE.addEventListener("change", placeAction);

  // -- actions ---------------------------------------------------------------------

  async function sendMode(mode) {
    try {
      const sections = [...tabSections(), "display"];
      await api("/mode", { method: "POST", body: { mode, settings: draftPatch(sections) } });
      clearDraft(sections);
      flashPanel();
      toast(mode === "off" ? "Screen off" : `Showing ${(MODE_LABELS[mode] || mode).toLowerCase()}`);
      await pollStatus();
      setTimeout(refreshPreview, 900);
    } catch (e) { toast(e.message); }
  }

  // -- drawing ------------------------------------------------------------------------
  // A stack of layers in the order they were made: ink (a transparent raster),
  // photos and text. New ink goes on the top ink layer, or a fresh one if the
  // top layer is a photo/text — so what you draw later covers what came before,
  // and a photo added later covers earlier ink. Flattened when sent.

  const canvas = $("#draw-canvas");
  const ctx = canvas.getContext("2d");
  const layers = [];                // {kind:"ink", c, x} | {kind:"image", img, x, y, scale, rot} | {kind:"text", text, x, y, size, rot, font}
  const draw = { tool: "pen", brush: 3, filled: false, down: false, start: null, last: null, snapshot: null, undo: [], redo: [],
                 target: "eink", colour: "#000000" };
  // the e-ink and the LCD each keep their own picture; only one is on the canvas at a time
  const docs = { eink: { layers: [], undo: [], redo: [] }, lcd: { layers: [], undo: [], redo: [], loaded: false } };
  const forLcd = () => draw.target === "lcd" || !!state.panel.colour;      // a colour main screen is drawn for in colour too
  let selected = null;
  let drag = null;
  let objSeq = 0;
  const HINTS = {
    select: "Tap a photo or text to select it. Drag to move; drag the corner to resize and rotate.",
    pen: "Draw with a finger or the mouse.",
    line: "Drag to draw a straight line.",
    rect: "Drag out a box. Tick Solid for a filled one.",
    circle: "Drag out a circle or oval. Tick Solid to fill it.",
    fill: "Tap inside a closed shape to fill it with black.",
    text: "Tap where the text should go and type. Drag to move, drag the corner to resize and rotate, tap again to edit.",
    erase: "Drag to erase.",
  };
  const SHAPES = new Set(["line", "rect", "circle"]);

  function newInk() {
    const c = document.createElement("canvas");
    c.width = canvas.width; c.height = canvas.height;
    return { kind: "ink", id: ++objSeq, c, x: c.getContext("2d", { willReadFrequently: true }) };
  }
  function topInk() {                // the ink layer to draw on: the top one, or a new one above whatever is on top
    const top = layers[layers.length - 1];
    if (top && top.kind === "ink") return top;
    const l = newInk(); layers.push(l); return l;
  }
  const inkLayers = () => layers.filter((l) => l.kind === "ink");
  const objectLayers = () => layers.filter((l) => l.kind !== "ink");

  function applyCanvasSize(w, h, scale) {
    canvas.width = w * scale;
    canvas.height = h * scale;
    canvas.style.aspectRatio = `${w} / ${h}`;
    $("#draw-wrap").classList.toggle("portrait", h > w);
  }
  // the LCDs this can drive (kept in step with pie_ink/lcd.py's MODELS)
  const LCD_MODELS = { "1.69": { w: 240, h: 280, label: "1.69″ · 240×280" }, "1.9": { w: 170, h: 320, label: "1.9″ · 170×320" } };
  function lcdCanvas() {                       // the LCD's picture, the way round it is mounted
    if (state.panel.colour) return [state.panel.width, state.panel.height];
    const m = LCD_MODELS[String(getSetting("lcd.model") || "1.69")] || LCD_MODELS["1.69"];
    return Number(getSetting("lcd.rotation") || 0) % 180 === 90 ? [m.h, m.w] : [m.w, m.h];
  }
  function resizeCanvas(panel) {
    // the panel changed shape: its picture starts over
    canvas.dataset.shaped = "1";
    docs.eink = { layers: [], undo: [], redo: [] };
    if (panel.colour) {                        // a colour main screen: draw in colour, its shape, live
      docs.lcd = { layers: [], undo: [], redo: [], loaded: false };
      draw.target = "lcd";
      applyCanvasSize(panel.width, panel.height, 2);
      draw.undo.length = 0; draw.redo.length = 0; layers.length = 0; selected = null;
      $("#draw-colours").hidden = false; $("#draw-dither-wrap").hidden = true;
      loadPainting(); redraw(); renderOther(); return;
    }
    if (draw.target === "lcd") { draw.target = "eink"; $("#draw-colours").hidden = true; $("#draw-dither-wrap").hidden = false; }
    applyCanvasSize(panel.width, panel.height, panel.width < 200 ? 4 : 2);
    draw.undo.length = 0; draw.redo.length = 0; layers.length = 0; selected = null;
    redraw(); renderOther();
  }
  window.pieDraw = { layers };          // handy for debugging from the console

  // -- rendering ----------------------------------------------------------------------

  function cssScale() { return canvas.getBoundingClientRect().width / canvas.width; }

  function objSize(o) {
    if (o.kind === "image") return { w: o.img.naturalWidth * o.scale, h: o.img.naturalHeight * o.scale };
    ctx.save(); ctx.font = `${o.size}px ${o.font}`;
    const w = Math.max(ctx.measureText(o.text || " ").width, o.size * 0.6);
    ctx.restore();
    return { w, h: o.size * 1.2 };
  }
  function drawObj(c, o) {
    const { w, h } = objSize(o);
    c.save();
    c.translate(o.x, o.y); c.rotate(o.rot);
    if (o.kind === "image") c.drawImage(o.img, -w / 2, -h / 2, w, h);
    else { c.font = `${o.size}px ${o.font}`; c.fillStyle = o.colour || "#000"; c.textAlign = "center"; c.textBaseline = "middle"; c.fillText(o.text, 0, 0); }
    c.restore();
  }
  function drawLayer(c, l) {
    if (l.kind === "ink") c.drawImage(l.c, 0, 0); else drawObj(c, l);
  }
  function handlePos(o) {
    const { w, h } = objSize(o), lx = w / 2 + 6, ly = h / 2 + 6;
    return { x: o.x + lx * Math.cos(o.rot) - ly * Math.sin(o.rot), y: o.y + lx * Math.sin(o.rot) + ly * Math.cos(o.rot) };
  }
  function drawSelection(o) {
    const { w, h } = objSize(o);
    const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#c2417f";
    const k = 1 / cssScale();
    ctx.save();
    ctx.translate(o.x, o.y); ctx.rotate(o.rot);
    ctx.strokeStyle = accent; ctx.lineWidth = 1.5 * k; ctx.setLineDash([4 * k, 4 * k]);
    ctx.strokeRect(-w / 2 - 6, -h / 2 - 6, w + 12, h + 12);
    ctx.setLineDash([]);
    ctx.fillStyle = accent; ctx.beginPath(); ctx.arc(w / 2 + 6, h / 2 + 6, 8 * k, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(w / 2 + 6, h / 2 + 6, 3 * k, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }
  function redraw() {
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (const l of layers) drawLayer(ctx, l);
    if (selected && !editing) drawSelection(selected);
    $("#draw-obj-delete").classList.toggle("on", !!selected);
  }

  // -- hit testing ------------------------------------------------------------------

  function toLocal(o, p) {
    const dx = p.x - o.x, dy = p.y - o.y, c = Math.cos(-o.rot), s = Math.sin(-o.rot);
    return { x: dx * c - dy * s, y: dx * s + dy * c };
  }
  function hitObj(p, kind = null) {
    for (let i = layers.length - 1; i >= 0; i--) {
      const o = layers[i];
      if (o.kind === "ink" || (kind && o.kind !== kind)) continue;
      const { w, h } = objSize(o), l = toLocal(o, p);
      if (Math.abs(l.x) <= w / 2 + 8 && Math.abs(l.y) <= h / 2 + 8) return o;
    }
    return null;
  }
  function hitHandle(o, p) {
    const h = handlePos(o);
    return Math.hypot(p.x - h.x, p.y - h.y) <= 14 / cssScale();
  }

  // -- undo ---------------------------------------------------------------------------

  function snapshotState() {
    return layers.map((l) => (l.kind === "ink" ? { kind: "ink", id: l.id, img: l.x.getImageData(0, 0, l.c.width, l.c.height) } : { ...l }));
  }
  function restoreState(st) {
    layers.length = 0;
    for (const l of st) {
      if (l.kind === "ink") { const n = newInk(); n.id = l.id; n.x.putImageData(l.img, 0, 0); layers.push(n); }
      else layers.push({ ...l });
    }
    selected = null; stopEditing(false); redraw();
  }
  function pushUndo() {
    draw.undo.push(snapshotState());
    if (draw.undo.length > 20) draw.undo.shift();
    draw.redo.length = 0;
  }

  // -- raster tools ------------------------------------------------------------------------

  function pos(e) {
    const r = canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) * canvas.width / r.width, y: (e.clientY - r.top) * canvas.height / r.height };
  }
  function pen(x, erase = false) {
    x.lineCap = "round"; x.lineJoin = "round";
    x.lineWidth = Math.max(2, draw.brush * 2);
    x.globalCompositeOperation = erase ? "destination-out" : "source-over";
    const c = forLcd() ? draw.colour : "#000";
    x.strokeStyle = c; x.fillStyle = c;
  }
  function strokeOn(x, a, b, erase = false) { pen(x, erase); x.beginPath(); x.moveTo(a.x, a.y); x.lineTo(b.x, b.y); x.stroke(); }
  function stroke(a, b) {
    if (draw.tool === "erase") { for (const l of inkLayers()) strokeOn(l.x, a, b, true); }   // the eraser reaches every ink layer
    else strokeOn(topInk().x, a, b);
  }
  function shape(a, b) {
    const x = topInk().x; pen(x);
    if (draw.tool === "line") { strokeOn(x, a, b); return; }
    const px = Math.min(a.x, b.x), py = Math.min(a.y, b.y), w = Math.abs(b.x - a.x), h = Math.abs(b.y - a.y);
    x.beginPath();
    if (draw.tool === "rect") x.rect(px, py, w, h);
    else x.ellipse(px + w / 2, py + h / 2, Math.max(1, w / 2), Math.max(1, h / 2), 0, 0, Math.PI * 2);
    if (draw.filled) x.fill(); else x.stroke();
  }
  function binarize(l) {                        // snap an ink layer: anti-aliased edges become solid or nothing
    l.x.globalCompositeOperation = "source-over";
    const img = l.x.getImageData(0, 0, l.c.width, l.c.height), d = img.data;
    for (let i = 0; i < d.length; i += 4) { const on = d[i + 3] >= 128; d[i] = d[i + 1] = d[i + 2] = 0; d[i + 3] = on ? 255 : 0; }
    l.x.putImageData(img, 0, 0);
  }
  function floodFill(p) {
    if (forLcd()) return floodFillColour(p);
    // boundaries come from all ink layers together; the fill is painted on the top one
    const W = canvas.width, H = canvas.height;
    const all = document.createElement("canvas"); all.width = W; all.height = H;
    const ax = all.getContext("2d", { willReadFrequently: true });
    for (const l of inkLayers()) ax.drawImage(l.c, 0, 0);
    const d = ax.getImageData(0, 0, W, H).data;
    const ink = (i) => d[i + 3] >= 128;
    const sx = Math.floor(p.x), sy = Math.floor(p.y);
    if (sx < 0 || sy < 0 || sx >= W || sy >= H) return;
    const target = ink((sy * W + sx) * 4);
    if (target) return;                          // already inked: nothing to fill
    const seen = new Uint8Array(W * H), stack = [sx, sy];
    const top = topInk(); top.x.globalCompositeOperation = "source-over";
    const out = top.x.getImageData(0, 0, W, H), od = out.data;
    const [cr, cg, cb] = forLcd() ? hexRgb(draw.colour) : [0, 0, 0];
    while (stack.length) {
      const y = stack.pop(), x0 = stack.pop();
      let x = x0;
      while (x >= 0 && !seen[y * W + x] && ink((y * W + x) * 4) === target) x--;
      x++;
      let up = false, down = false;
      while (x < W && !seen[y * W + x] && ink((y * W + x) * 4) === target) {
        const i = y * W + x;
        seen[i] = 1; od[i * 4] = cr; od[i * 4 + 1] = cg; od[i * 4 + 2] = cb; od[i * 4 + 3] = target ? 0 : 255;
        if (y > 0) { const a = (y - 1) * W + x, ok = !seen[a] && ink(a * 4) === target; if (ok && !up) { stack.push(x, y - 1); up = true; } else if (!ok) up = false; }
        if (y < H - 1) { const a = (y + 1) * W + x, ok = !seen[a] && ink(a * 4) === target; if (ok && !down) { stack.push(x, y + 1); down = true; } else if (!ok) down = false; }
        x++;
      }
    }
    top.x.putImageData(out, 0, 0);
  }

  function floodFillColour(p) {
    // the colour version, for the LCD: floods everything about the same colour as the
    // tapped spot (strokes are smooth, so "the same" allows a little leeway), then
    // blends the strokes' soft edges over the new colour so no light seam is left
    const W = canvas.width, H = canvas.height;
    const all = document.createElement("canvas"); all.width = W; all.height = H;
    const ax = all.getContext("2d", { willReadFrequently: true });
    ax.fillStyle = "#fff"; ax.fillRect(0, 0, W, H);
    for (const l of inkLayers()) ax.drawImage(l.c, 0, 0);
    const d = ax.getImageData(0, 0, W, H).data;
    const sx = Math.floor(p.x), sy = Math.floor(p.y);
    if (sx < 0 || sy < 0 || sx >= W || sy >= H) return;
    const si = (sy * W + sx) * 4, sr = d[si], sg = d[si + 1], sb = d[si + 2], TOL = 48;
    const like = (i) => Math.abs(d[i] - sr) <= TOL && Math.abs(d[i + 1] - sg) <= TOL && Math.abs(d[i + 2] - sb) <= TOL;
    const [cr, cg, cb] = hexRgb(draw.colour);
    const filled = new Uint8Array(W * H), stack = [sx, sy];
    while (stack.length) {
      const y = stack.pop(), x0 = stack.pop();
      let x = x0;
      while (x >= 0 && !filled[y * W + x] && like((y * W + x) * 4)) x--;
      x++;
      let up = false, down = false;
      while (x < W && !filled[y * W + x] && like((y * W + x) * 4)) {
        filled[y * W + x] = 1;
        if (y > 0) { const a = (y - 1) * W + x, ok = !filled[a] && like(a * 4); if (ok && !up) { stack.push(x, y - 1); up = true; } else if (!ok) up = false; }
        if (y < H - 1) { const a = (y + 1) * W + x, ok = !filled[a] && like(a * 4); if (ok && !down) { stack.push(x, y + 1); down = true; } else if (!ok) down = false; }
        x++;
      }
    }
    const top = topInk(); top.x.globalCompositeOperation = "source-over";
    const out = top.x.getImageData(0, 0, W, H), od = out.data;
    for (let i = 0; i < W * H; i++) if (filled[i]) { od[i * 4] = cr; od[i * 4 + 1] = cg; od[i * 4 + 2] = cb; od[i * 4 + 3] = 255; }
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const i = y * W + x, a = od[i * 4 + 3];
      if (filled[i] || a === 0 || a === 255) continue;
      if ((x > 0 && filled[i - 1]) || (x < W - 1 && filled[i + 1]) || (y > 0 && filled[i - W]) || (y < H - 1 && filled[i + W])) {
        const k = a / 255;
        od[i * 4] = Math.round(od[i * 4] * k + cr * (1 - k));
        od[i * 4 + 1] = Math.round(od[i * 4 + 1] * k + cg * (1 - k));
        od[i * 4 + 2] = Math.round(od[i * 4 + 2] * k + cb * (1 - k));
        od[i * 4 + 3] = 255;
      }
    }
    top.x.putImageData(out, 0, 0);
  }

  // -- text editing (inline) ---------------------------------------------------------------

  const editor = $("#draw-text-editor");
  const textSize = () => 22;
  let editing = null;
  function startEditing(t) {
    editing = t; selected = t;
    const k = cssScale(), r = canvas.getBoundingClientRect(), wrap = canvas.parentElement.getBoundingClientRect();
    const { w } = objSize(t);
    editor.hidden = false;
    editor.value = t.text;
    editor.style.font = `${t.size * k}px ${t.font}`;
    editor.style.left = `${(t.x - Math.max(w, t.size * 6) / 2) * k + (r.left - wrap.left)}px`;
    editor.style.top = `${(t.y - t.size * 0.7) * k + (r.top - wrap.top)}px`;
    editor.style.width = `${Math.max(w, t.size * 6) * k + 16}px`;
    editor.style.transform = `rotate(${t.rot}rad)`;
    editor.focus(); editor.select();
    redraw();
  }
  function stopEditing(commit = true) {
    if (!editing) return;
    const t = editing; editing = null; editor.hidden = true;
    if (commit) {
      t.text = editor.value.trim();
      if (!t.text) { layers.splice(layers.indexOf(t), 1); selected = null; }
      touched(true);
    }
    redraw();
  }
  editor.addEventListener("input", () => { if (editing) { editing.text = editor.value; redraw(); touched(); } });
  editor.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === "Escape") { e.preventDefault(); stopEditing(true); } });
  editor.addEventListener("blur", () => stopEditing(true));
  $("#draw-text-font").addEventListener("change", (e) => {
    if (selected && selected.kind === "text") { pushUndo(); selected.font = e.target.value; if (editing) editor.style.font = `${editing.size * cssScale()}px ${editing.font}`; redraw(); touched(true); }
  });
  $("#draw-obj-delete").addEventListener("click", () => {
    if (!selected) return;
    pushUndo(); stopEditing(false); layers.splice(layers.indexOf(selected), 1); selected = null; redraw(); touched(true);
  });

  // -- pointer handling --------------------------------------------------------------------

  let lastTap = { o: null, at: 0 };
  function start(e) {
    e.preventDefault();
    const p = pos(e);
    if (editing) stopEditing(true);

    // Select grabs anything; the Text tool only grabs text (so text can go on top of photos)
    const grabbing = draw.tool === "select" || draw.tool === "text";
    if (grabbing && selected && (draw.tool === "select" || selected.kind === "text") && hitHandle(selected, p)) {
      pushUndo();
      const { w, h } = objSize(selected);
      drag = { kind: "handle", o: selected, d0: Math.hypot(w / 2 + 6, h / 2 + 6), a0: Math.atan2(p.y - selected.y, p.x - selected.x) - selected.rot,
               size0: selected.size, scale0: selected.scale };
      draw.down = true; return;
    }
    const hit = draw.tool === "select" ? hitObj(p) : draw.tool === "text" ? hitObj(p, "text") : null;
    if (hit) {
      if (hit === selected && hit.kind === "text" && Date.now() - lastTap.at < 450 && lastTap.o === hit) { startEditing(hit); draw.down = false; return; }
      selected = hit; pushUndo();
      drag = { kind: "move", o: hit, dx: p.x - hit.x, dy: p.y - hit.y, moved: false };
      draw.down = true; lastTap = { o: hit, at: Date.now() }; redraw(); return;
    }
    if (selected) { selected = null; redraw(); }

    if (draw.tool === "select") return;
    if (draw.tool === "text") {
      pushUndo();
      const t = { kind: "text", id: ++objSeq, text: "", x: p.x, y: p.y, size: textSize(), rot: 0, font: $("#draw-text-font").value || "sans-serif",
                  colour: forLcd() ? draw.colour : "#000" };
      layers.push(t); startEditing(t);
      return;
    }
    if (draw.tool === "fill") { pushUndo(); floodFill(p); redraw(); touched(true); return; }
    pushUndo();
    draw.down = true; draw.start = p; draw.last = p; drag = null;
    if (SHAPES.has(draw.tool)) { const l = topInk(); draw.snapshot = l.x.getImageData(0, 0, l.c.width, l.c.height); draw.layer = l; }
    else stroke(p, p);
    redraw(); touched();
  }
  function move(e) {
    if (!draw.down) return;
    e.preventDefault();
    const p = pos(e);
    if (drag) {
      const o = drag.o;
      if (drag.kind === "move") { o.x = p.x - drag.dx; o.y = p.y - drag.dy; drag.moved = true; }
      else {
        const f = Math.hypot(p.x - o.x, p.y - o.y) / drag.d0;
        if (o.kind === "image") o.scale = Math.max(0.05, drag.scale0 * f);
        else o.size = Math.max(8, Math.min(200, drag.size0 * f));
        o.rot = Math.atan2(p.y - o.y, p.x - o.x) - drag.a0;
      }
      redraw(); touched(); return;
    }
    if (SHAPES.has(draw.tool)) { draw.layer.x.putImageData(draw.snapshot, 0, 0); shape(draw.start, p); }
    else { stroke(draw.last, p); draw.last = p; }
    redraw(); touched();
  }
  function end() {
    const was = draw.down;
    if (draw.down && !drag && !forLcd()) {                     // the e-ink wants crisp edges; the LCD keeps them smooth
      if (draw.tool === "erase") inkLayers().forEach(binarize);
      else { const top = layers[layers.length - 1]; if (top && top.kind === "ink") binarize(top); }
    }
    if (drag && drag.kind === "move" && !drag.moved) draw.undo.pop();
    draw.down = false; draw.snapshot = null; draw.layer = null; drag = null;
    redraw();
    if (was) touched(true);
  }
  canvas.addEventListener("pointerdown", start);
  canvas.addEventListener("pointermove", move);
  window.addEventListener("pointerup", end);
  window.addEventListener("pointercancel", end);
  document.addEventListener("keydown", (e) => {
    if (state.tab !== "draw" || editing || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) return;
    if ((e.key === "Delete" || e.key === "Backspace") && selected) { e.preventDefault(); $("#draw-obj-delete").click(); }
    if (e.key === "Escape" && selected) { selected = null; redraw(); }
  });

  // -- toolbar ------------------------------------------------------------------------------

  for (const b of $$("#draw-tool button[data-value]")) b.title = HINTS[b.dataset.value] || "";
  function selectTool(name) {
    draw.tool = name;
    for (const x of $$("#draw-tool button[data-value]")) x.classList.toggle("active", x.dataset.value === name);
    $("#draw-filled-wrap").hidden = !(name === "rect" || name === "circle");
    if (name !== "select" && name !== "text" && selected) { stopEditing(true); selected = null; redraw(); }
  }
  $("#draw-tool").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.dataset.action === "import") { $("#draw-file").click(); return; }
    selectTool(b.dataset.value);
  });
  selectTool("pen");
  $("#draw-brush").addEventListener("input", (e) => (draw.brush = Number(e.target.value)));
  $("#draw-filled").addEventListener("change", (e) => (draw.filled = e.target.checked));
  $("#draw-undo").addEventListener("click", () => { if (draw.undo.length) { draw.redo.push(snapshotState()); restoreState(draw.undo.pop()); touched(true); } });
  $("#draw-redo").addEventListener("click", () => { if (draw.redo.length) { draw.undo.push(snapshotState()); restoreState(draw.redo.pop()); touched(true); } });
  $("#draw-clear").addEventListener("click", () => { pushUndo(); stopEditing(false); layers.length = 0; selected = null; redraw(); touched(true); });
  // the bot takes a look at what you drew and says what it sees
  $("#draw-ai").addEventListener("click", async () => {
    const note = $("#draw-ai-note");
    note.hidden = false; note.textContent = "Having a look…";
    $("#draw-ai").disabled = true;
    try {
      const d = await api("/draw/describe", { method: "POST", body: { image: flattened() } });
      note.textContent = d.text || "It had nothing to say.";
    } catch (e) { note.textContent = e.message; }
    $("#draw-ai").disabled = false;
  });
  // a photo becomes a layer on top of everything so far; movable/resizable/rotatable like text
  $("#draw-file").addEventListener("change", (e) => {
    const file = e.target.files[0]; if (!file) return;
    const img = new Image();
    img.onload = () => {
      pushUndo();
      const fit = Math.min(canvas.width / img.naturalWidth, canvas.height / img.naturalHeight) * 0.8;
      const o = { kind: "image", id: ++objSeq, img, x: canvas.width / 2, y: canvas.height / 2, scale: fit, rot: 0 };
      layers.push(o); selected = o;
      selectTool("select");
      redraw(); touched(true);
    };
    img.src = URL.createObjectURL(file);
    e.target.value = "";
  });

  // -- send: flatten the stack in order -------------------------------------------------

  function flattened() {
    const out = document.createElement("canvas");
    out.width = canvas.width; out.height = canvas.height;
    const c = out.getContext("2d", { willReadFrequently: true });
    c.fillStyle = "#fff"; c.fillRect(0, 0, out.width, out.height);
    const tmp = document.createElement("canvas"); tmp.width = out.width; tmp.height = out.height;
    const tx = tmp.getContext("2d", { willReadFrequently: true });
    for (const l of layers) {
      if (l.kind !== "text" || forLcd()) { drawLayer(c, l); continue; }  // ink is already crisp; photos keep their greys
      // text: draw on a scratch layer, snap its edges, then composite in order
      tx.clearRect(0, 0, tmp.width, tmp.height); drawObj(tx, l);
      const img = tx.getImageData(0, 0, tmp.width, tmp.height), d = img.data;
      for (let i = 0; i < d.length; i += 4) { const on = d[i + 3] >= 128; d[i] = d[i + 1] = d[i + 2] = 0; d[i + 3] = on ? 255 : 0; }
      tx.putImageData(img, 0, 0);
      c.drawImage(tmp, 0, 0);
    }
    if (forLcd()) {                                                       // the LCD's own size, smoothly scaled
      const [lw, lh] = lcdCanvas();
      const small = document.createElement("canvas"); small.width = lw; small.height = lh;
      const sc = small.getContext("2d");
      sc.imageSmoothingEnabled = true; sc.imageSmoothingQuality = "high";
      sc.drawImage(out, 0, 0, lw, lh);
      return small.toDataURL("image/png");
    }
    return out.toDataURL("image/png");
  }

  // -- drawing for the LCD: colour, and live ---------------------------------------------------

  function hexRgb(hex) { const v = parseInt(hex.slice(1), 16); return [(v >> 16) & 255, (v >> 8) & 255, v & 255]; }
  let liveTimer = null, liveLast = 0, livePending = false, liveInFlight = false;
  async function postPaint() {
    if (liveInFlight) { livePending = true; return; }
    liveInFlight = true; livePending = false; liveLast = Date.now();
    try {
      const d = await api("/paint", { method: "POST", body: { image: flattened(), show: true } });
      if (!d.lcd_on && !state.panel.colour) $("#draw-target-hint").textContent = "The second screen is off — switch it on in Settings → Screen.";
    } catch (e) { $("#draft-tag").hidden = true; toast(e.message); }
    liveInFlight = false;
    if (livePending) postPaint();
    else if (state.panel.colour) {                  // the main screen: show it on the page too
      if (state.mode !== "image") pollStatus();
      setTimeout(refreshPreview, 400);
    }
  }
  // an edit happened: with Live on, the LCD gets the picture at most every 120 ms while drawing, and once more
  // when the hand lifts; otherwise nothing goes until the button
  const liveLcd = () => !!getSetting("image.live_lcd");
  function touched(final = false) {
    if (!forLcd() || !liveLcd()) return;
    clearTimeout(liveTimer);
    const wait = final ? 0 : Math.max(0, 120 - (Date.now() - liveLast));
    liveTimer = setTimeout(postPaint, wait);
  }
  function setColour(hex, el) {
    draw.colour = hex;
    for (const b of $$("#draw-colours .swatch")) b.classList.toggle("active", b === el);
    if (el && el.classList.contains("custom")) el.style.setProperty("--c", hex);
    if (selected && selected.kind === "text" && selected.colour !== hex) { pushUndo(); selected.colour = hex; redraw(); touched(true); }
  }
  $("#draw-colours").addEventListener("click", (e) => {
    const b = e.target.closest("button.swatch"); if (b) setColour(b.dataset.colour, b);
  });
  $("#draw-colour-custom").addEventListener("input", (e) => setColour(e.target.value, e.target.closest(".swatch")));
  async function loadPainting() {                    // what is on the LCD already, so you can carry on with it
    if (docs.lcd.loaded) return;
    docs.lcd.loaded = true;
    try {
      const res = await fetch(`/api/paint.png?t=${Date.now()}`);
      if (!res.ok) return;
      const img = new Image();
      img.onload = () => {
        if (!forLcd() || layers.length) return;      // they started drawing in the meantime
        const l = newInk();
        const k = Math.min(canvas.width / img.naturalWidth, canvas.height / img.naturalHeight);   // whole and centred, whatever the panel
        const dw = img.naturalWidth * k, dh = img.naturalHeight * k;
        l.x.drawImage(img, (canvas.width - dw) / 2, (canvas.height - dh) / 2, dw, dh);
        layers.push(l); redraw();
      };
      img.src = URL.createObjectURL(await res.blob());
    } catch (e) { /* a blank canvas, then */ }
  }
  function setTarget(t) {
    if (t === draw.target) return;
    stopEditing(true);
    const cur = docs[draw.target];
    cur.layers = layers.slice(); cur.undo = draw.undo.slice(); cur.redo = draw.redo.slice();
    draw.target = t; selected = null;
    const next = docs[t];
    layers.length = 0; layers.push(...next.layers);
    draw.undo.length = 0; draw.undo.push(...next.undo);
    draw.redo.length = 0; draw.redo.push(...next.redo);
    if (t === "lcd") { const [w, h] = lcdCanvas(); applyCanvasSize(w, h, 2); }
    else applyCanvasSize(state.panel.width, state.panel.height, state.panel.width < 200 ? 4 : 2);
    for (const b of $$("#draw-target button")) b.classList.toggle("active", b.dataset.value === t);
    $("#draw-colours").hidden = t !== "lcd";
    $("#draw-dither-wrap").hidden = t === "lcd";
    targetHint();
    if (t === "lcd") loadPainting();
    redraw(); renderOther(); updateButtons();
  }
  function targetHint() {
    const what = draw.target === "lcd"
      ? (liveLcd() ? "In colour, live: every stroke goes straight to the LCD." : "In colour, for the LCD.")
      : "Black and white, for the e-ink.";
    $("#draw-target-hint").textContent = twoUp() ? `${what} Tap the other canvas to draw for that screen.` : what;
  }
  $("#draw-live").addEventListener("change", async (e) => {
    const on = e.target.checked;
    try {
      await api("/settings", { method: "PUT", body: { image: { live_lcd: on } } });
      if (state.settings && state.settings.image) state.settings.image.live_lcd = on;
      targetHint(); updateButtons();
      if (on && layers.length) touched(true);       // what is on the page goes up now
      toast(on ? "Live: strokes go straight to the screen" : "Sent when you press the button");
    } catch (err) { e.target.checked = !on; toast(err.message); }
  });
  $("#draw-target").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) setTarget(b.dataset.value); });
  function syncDrawTarget() {                        // the target switch only exists while a second LCD is switched on
    if (state.panel.colour) { $("#draw-target-row").hidden = true; renderOther(); return; }
    const on = !!getSetting("lcd.enabled");
    $("#draw-target-row").hidden = !on;
    if (!on && forLcd()) setTarget("eink");
    else if (forLcd()) {                             // the LCD was turned the other way round: follow it
      const [w, h] = lcdCanvas();
      if (canvas.width !== w * 2 || canvas.height !== h * 2) { applyCanvasSize(w, h, 2); redraw(); }
    }
    const was = canvasRow.classList.contains("two");
    renderOther();
    if (was !== canvasRow.classList.contains("two")) targetHint();
  }

  // -- the other screen's canvas, beside the one being drawn on ---------------------------------------
  // With a second LCD on and a screen wide enough, both pictures are on the page at once: the e-ink's on the
  // left, the LCD's on the right. Only one is drawn on — the outlined one; the other shows that screen's picture
  // (the drawing kept for it, or what the LCD is showing now) and a tap on it swaps the two over.
  const otherWrap = $("#draw-other-wrap"), otherCanvas = $("#draw-other"), canvasRow = $("#draw-canvases");
  const TWO_UP = window.matchMedia("(min-width: 760px)");
  const twoUp = () => !state.panel.colour && !!getSetting("lcd.enabled") && TWO_UP.matches;
  const otherTarget = () => (draw.target === "lcd" ? "eink" : "lcd");
  let otherShown = 0;                                 // which fetch of the LCD's picture is the latest
  function renderOther() {
    const two = twoUp();
    canvasRow.classList.toggle("two", two);
    otherWrap.hidden = !two;
    $("#draw-target").hidden = two; $(".draw-target-label").hidden = two;     // the canvases are the switch
    $("#draw-wrap").dataset.target = draw.target;
    if (!two) return;
    const other = otherTarget();
    otherWrap.dataset.target = other;
    $("#draw-wrap .canvas-tag").textContent = draw.target === "lcd" ? "LCD" : "E-ink";
    $("#draw-other-wrap .canvas-tag").textContent = other === "lcd" ? "LCD" : "E-ink";
    const [lw, lh] = lcdCanvas(), ew = state.panel.width, eh = state.panel.height;
    canvasRow.style.setProperty("--r1", (ew / eh).toFixed(3));
    canvasRow.style.setProperty("--r2", (lw / lh).toFixed(3));
    const [w, h, scale] = other === "lcd" ? [lw, lh, 2] : [ew, eh, ew < 200 ? 4 : 2];
    if (otherCanvas.width !== w * scale || otherCanvas.height !== h * scale) { otherCanvas.width = w * scale; otherCanvas.height = h * scale; }
    otherCanvas.style.aspectRatio = `${w} / ${h}`;
    const c = otherCanvas.getContext("2d");
    c.fillStyle = "#fff"; c.fillRect(0, 0, otherCanvas.width, otherCanvas.height);
    const doc = docs[other];
    if (other === "lcd" && !doc.loaded) { showLcdPicture(++otherShown); return; }
    for (const l of doc.layers) drawLayer(c, l);
  }
  async function showLcdPicture(n) {                // what the LCD has on it now, until a drawing for it is started here
    try {
      const res = await fetch(`/api/paint.png?t=${Date.now()}`);
      if (!res.ok) return;
      const img = new Image();
      img.onload = () => {
        if (n !== otherShown || !twoUp() || otherTarget() !== "lcd" || docs.lcd.loaded) return;   // things moved on
        const c = otherCanvas.getContext("2d");
        const k = Math.min(otherCanvas.width / img.naturalWidth, otherCanvas.height / img.naturalHeight);
        const dw = img.naturalWidth * k, dh = img.naturalHeight * k;
        c.drawImage(img, (otherCanvas.width - dw) / 2, (otherCanvas.height - dh) / 2, dw, dh);
      };
      img.src = URL.createObjectURL(await res.blob());
    } catch (e) { /* a blank canvas, then */ }
  }
  otherWrap.addEventListener("click", () => setTarget(otherTarget()));
  TWO_UP.addEventListener("change", () => { renderOther(); targetHint(); });

  async function sendDrawing() {
    stopEditing(true);
    const btn = $("#action");
    btn.disabled = true;
    if (forLcd()) {                                   // it is live already; this just makes sure the screen is showing it
      try {
        const d = await api("/paint", { method: "POST", body: { image: flattened(), show: true } });
        toast(state.panel.colour ? "Showing the drawing" : d.lcd_on ? "Sent to the LCD" : "Switch the second screen on in Settings → Screen");
        if (state.panel.colour) { await pollStatus(); setTimeout(refreshPreview, 900); }
      } catch (e) { toast(e.message); }
      btn.disabled = false;
      return;
    }
    try {
      if (hasDraft(["image", "display"])) {
        await api("/settings", { method: "PUT", body: draftPatch(["image", "display"]) });
        clearDraft(["image", "display"]);
      }
      await api("/image", { method: "POST", body: { image: flattened() } });
      flashPanel();
      toast("Drawing sent");
      await pollStatus();
      setTimeout(refreshPreview, 1200);
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  }

  // -- reader --------------------------------------------------------------------------

  let books = [];
  let reading = null;

  function renderReading(r) {
    reading = r;
    const b = r && r.book;
    $("#rd-title").textContent = b ? b.title : "No book open";
    let pos = "—";
    if (!b) pos = "Pick one from the library below";
    if (b) {
      if (r.error) pos = r.error;
      else if (!r.ready) pos = `Preparing… ${Math.round((r.progress || 0) * 100)}%`;
      else pos = `Page ${r.page + 1} of ${r.total}` + (r.tiles > 1 ? ` · panel ${r.tile + 1}/${r.tiles}` : "");
    }
    $("#rd-pos").textContent = pos;
    $("#rd-book-settings").hidden = !!b && b.kind !== "text";
    $("#rd-comic-settings").hidden = !b || b.kind !== "pages";
    const sl = $("#rd-slider");
    sl.max = Math.max(0, (r?.total || 1) - 1);
    if (document.activeElement !== sl) sl.value = r?.page || 0;
    sl.disabled = !b || !r.ready;
    for (const li of $$("#rd-list li[data-id]")) li.classList.toggle("active", !!b && li.dataset.id === b.id);
  }

  function renderBooks() {
    const list = $("#rd-list");
    if (!books.length) { list.innerHTML = `<li class="empty">Nothing here yet — add a book.</li>`; return; }
    list.innerHTML = books.map((b) => `
      <li data-id="${esc(b.id)}" class="${reading?.book?.id === b.id ? "active" : ""}">
        <span class="title">${esc(b.title)}<span class="bkind">${b.kind === "text" ? "book" : "comic"}</span></span>
        <span class="prog">p.${(b.page || 0) + 1}</span>
        <button class="remove" aria-label="Remove ${esc(b.title)}">×</button>
      </li>`).join("");
  }

  let jobsWere = 0;
  function renderJobs(jobs) {
    $("#rd-jobs").innerHTML = jobs.filter((j) => !j.done).map((j) => `
      <div class="job"><span>Downloading ${esc(j.title)}</span><span class="bar"><i style="width:${Math.round((j.progress || 0) * 100)}%"></i></span></div>`).join("");
    for (const j of jobs) {
      if (j.done && j.error) toast(`${j.title}: ${j.error}`);
      else if (j.done && j.book) { toast(`Added ${j.book.title}`); readerAction({ book: j.book.id }); }
    }
    jobsWere = jobs.filter((j) => !j.done).length;
  }

  async function loadBooks() {
    try {
      const d = await api("/books");
      books = d.books; renderBooks(); renderReading(d.reading); renderJobs(d.jobs || []);
      if (d.pdf_ok === false) $("#rd-hint").textContent = "EPUB, TXT, CBZ and images. PDFs need poppler-utils on the Pi (sudo apt install poppler-utils).";
    } catch (e) { toast(e.message); }
  }

  // -- free sources -----------------------------------------------------------------------

  const SRC_HINT = {
    upload: "",
    gutenberg: "Project Gutenberg: 75,000 public-domain books. Search by title or author.",
    xkcd: "xkcd: black-and-white strips, perfect for this screen. Random, latest, or by number.",
    archive: "Internet Archive: scanned Golden Age comics (1930s–50s). Big files, mixed scan quality.",
  };
  let source = "upload";

  function renderResults(results) {
    const list = $("#src-results");
    if (!results.length) { list.innerHTML = `<li class="empty">Nothing found.</li>`; return; }
    list.innerHTML = results.map((r, i) => `
      <li data-i="${i}">
        <span class="title">${esc(r.title)}<span class="by">${esc(r.by || "")}</span></span>
        <button class="ghost add">Add</button>
      </li>`).join("");
    list._results = results;
  }

  const searchSource = debounce(async (q) => {
    const list = $("#src-results");
    if (source === "gutenberg" && !q) { list.innerHTML = ""; return; }
    list.innerHTML = `<li class="empty">Searching…</li>`;
    try { renderResults((await api(`/books/search?source=${source}&q=${encodeURIComponent(q)}`)).results); }
    catch (e) { list.innerHTML = `<li class="empty">${esc(e.message)}</li>`; }
  }, 400);

  function pickSource(name) {
    source = name;
    for (const b of $$("#src-pick button")) b.classList.toggle("active", b.dataset.value === name);
    $("#src-hint").textContent = SRC_HINT[name];
    $("#src-hint").hidden = !SRC_HINT[name];
    $("#src-upload").hidden = name !== "upload";
    $("#src-search-wrap").hidden = name !== "gutenberg" && name !== "archive";
    $("#src-xkcd").hidden = name !== "xkcd";
    $("#src-search").placeholder = name === "archive" ? "Search old comics — e.g. Captain Marvel, Blue Beetle…" : "Search Gutenberg…";
    $("#src-results").innerHTML = "";
    if (name === "archive") searchSource("");
  }
  $("#src-pick").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) pickSource(b.dataset.value); });
  $("#src-search").addEventListener("input", (e) => searchSource(e.target.value.trim()));
  $("#src-xkcd").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const which = b.dataset.xkcd || $("#src-xkcd-num").value || "random";
    searchSource(which);
  });
  $("#src-results").addEventListener("click", async (e) => {
    const btn = e.target.closest(".add"); if (!btn) return;
    const li = btn.closest("li"); const r = $("#src-results")._results?.[Number(li.dataset.i)]; if (!r) return;
    btn.disabled = true; btn.textContent = "…";
    try {
      await api("/books/fetch", { method: "POST", body: r });
      toast(`Downloading ${r.title}`);
      $("#rd-add-panel").hidden = true; $("#rd-add").textContent = "+ Add";
      loadBooks();
    } catch (err) { toast(err.message); btn.disabled = false; btn.textContent = "Add"; }
  });

  async function readerAction(body) {
    const wasShowing = state.mode === "reader";
    try {
      const d = await api("/reader", { method: "POST", body });
      renderReading(d.reading);
      if (!wasShowing) { toast("Reader is on the screen"); pollStatus(); }
      setTimeout(refreshPreview, 600);
    } catch (e) { toast(e.message); }
  }

  $("#rd-prev").addEventListener("click", () => readerAction({ action: "prev" }));
  $("#rd-rotate").addEventListener("click", async () => {
    const next = (Number(getSetting("reader.rotation")) + 90) % 360;
    try {
      await api("/settings", { method: "PUT", body: { reader: { rotation: next } } });
      delete state.draft["reader.rotation"];
      await pollStatus();
      toast(`Page rotated ${next}°`);
      setTimeout(refreshPreview, 900);
    } catch (e) { toast(e.message); }
  });
  $("#rd-next").addEventListener("click", () => readerAction({ action: "next" }));
  $("#rd-slider").addEventListener("change", (e) => readerAction({ action: "goto", page: Number(e.target.value) }));
  $("#rd-list").addEventListener("click", async (e) => {
    const rm = e.target.closest(".remove");
    const li = e.target.closest("li[data-id]"); if (!li) return;
    if (rm) {
      if (!confirm("Remove this book from the Pi?")) return;
      try { const d = await api(`/books/${li.dataset.id}`, { method: "DELETE" }); books = d.books; renderBooks(); renderReading(d.reading); }
      catch (err) { toast(err.message); }
      return;
    }
    readerAction({ book: li.dataset.id });
  });
  $("#rd-add").addEventListener("click", () => {
    const panel = $("#rd-add-panel");
    panel.hidden = !panel.hidden;
    $("#rd-add").textContent = panel.hidden ? "+ Add" : "Done";
    if (!panel.hidden) pickSource("upload");
  });
  $("#rd-upload").addEventListener("click", () => $("#rd-file").click());
  $("#rd-file").addEventListener("change", async (e) => {
    const file = e.target.files[0]; if (!file) return;
    e.target.value = "";
    $("#rd-hint").textContent = `Uploading ${file.name}…`;
    const fd = new FormData(); fd.append("file", file);
    if (file.name.toLowerCase().endsWith(".pdf")) {
      fd.append("kind", confirm("Is this PDF a comic (pages as pictures)?\nOK = comic, Cancel = text book") ? "pages" : "text");
    }
    try {
      const res = await fetch("/api/books", { method: "POST", body: fd });
      const d = await res.json();
      if (!d.ok) throw new Error(d.error || "upload failed");
      books = d.books; renderBooks();
      toast(`Added ${d.book.title}`);
      $("#rd-add-panel").hidden = true; $("#rd-add").textContent = "+ Add";
      readerAction({ book: d.book.id });
    } catch (err) { toast(err.message); }
    $("#rd-hint").textContent = "EPUB, TXT and PDF for books · CBZ, PDF and images for comics.";
  });
  // swipe on the preview and arrow keys turn pages while the Read tab is open
  (() => {
    let start = null;
    const bezel = $("#bezel");
    bezel.addEventListener("pointerdown", (e) => { start = { x: e.clientX, y: e.clientY }; });
    bezel.addEventListener("pointerup", (e) => {
      if (!start || state.tab !== "read") { start = null; return; }
      const dx = e.clientX - start.x; start = null;
      if (Math.abs(dx) > 30) readerAction({ action: dx < 0 ? "next" : "prev" });
    });
  })();
  document.addEventListener("keydown", (e) => {
    if (state.tab !== "read" || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName)) return;
    if (e.key === "ArrowRight" || e.key === " ") { e.preventDefault(); readerAction({ action: "next" }); }
    if (e.key === "ArrowLeft") { e.preventDefault(); readerAction({ action: "prev" }); }
  });

  // -- music ------------------------------------------------------------------------------

  const MODE_PICK_TIME = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  let musicTimer = null;
  function renderMusic(d) {
    const now = d.now;
    const where = now.on_page ? (here.live ? "on this device" : "on your other device") : "";
    const line = [esc(now.folder || ""), `${MODE_PICK_TIME(now.position)} of ${MODE_PICK_TIME(now.duration)}`, where]
      .filter(Boolean).join(" · ");
    $("#mu-now").innerHTML = now.title
      ? `${esc(now.title)}<small>${line}</small>`
      : `Nothing playing<small>${d.tracks.length} track${d.tracks.length === 1 ? "" : "s"} in ${esc(d.folder)}</small>`;
    $("#mu-toggle").textContent = now.state === "playing" ? "Pause" : "Play";
    $("#mu-list").innerHTML = d.tracks.map((t, i) => `
      <li data-i="${i}" class="${i === now.index ? "on" : ""}">
        <span class="title">${esc(t.title)}<span class="bkind">${esc(t.folder || "")}</span></span>
        <span class="prog">${i === now.index ? now.state : ""}</span><span></span></li>`).join("")
      || `<li class="empty">Nothing here yet — add files, or copy them into the folder on the Pi.</li>`;
    $("#mu-hint").textContent = now.error || "";
    // keep the numbers moving while something is playing
    clearInterval(musicTimer);
    if (now.state === "playing") musicTimer = setInterval(loadMusic, 3000);
  }
  async function loadMusic() {
    try { renderMusic(await api("/music")); } catch (e) { clearInterval(musicTimer); }
  }
  async function musicDo(action, body) {
    try { renderMusic(await api(`/music/${action}`, { method: "POST", body: body || {} })); if (state.mode === "music") setTimeout(refreshPreview, 800); }
    catch (e) { toast(e.message); }
  }
  $("#mu-toggle").addEventListener("click", () => musicDo("toggle"));
  $("#mu-next").addEventListener("click", () => musicDo("next"));
  $("#mu-prev").addEventListener("click", () => musicDo("prev"));
  $("#mu-stop").addEventListener("click", () => musicDo("stop"));
  $("#mu-list").addEventListener("click", (e) => {
    const li = e.target.closest("li[data-i]"); if (!li) return;
    musicDo("play", { index: Number(li.dataset.i) });
  });
  $("#mu-add").addEventListener("click", () => $("#mu-file").click());
  $("#mu-file").addEventListener("change", async (e) => {
    const files = [...e.target.files]; if (!files.length) return;
    $("#mu-hint").textContent = `Copying ${files.length} file${files.length === 1 ? "" : "s"}…`;
    for (const file of files) {
      const form = new FormData(); form.append("file", file);
      try { await fetch("/api/music/upload", { method: "POST", body: form }); }
      catch (err) { toast(err.message); }
    }
    e.target.value = "";
    $("#mu-hint").textContent = "";
    loadMusic();
  });
  // one level, two sliders: the Music tab's and the Sound settings'
  const volume = $("#mu-volume"), sndVolume = $("#snd-volume");
  function showVolume(v) {
    volume.value = v; sndVolume.value = v;
    $("#mu-volume-out").textContent = `${v}%`; $("#snd-volume-out").textContent = `${v}%`;
  }
  async function sendVolume(v) {
    try {
      const d = await api("/audio/volume", { method: "POST", body: { volume: Number(v) } });
      state.settings.audio.volume = Number(v);
      const note = d.how === "software"
        ? "That output has no volume control of its own, so the Pi turns the sound down before it goes out."
        : "";
      $("#snd-volume-note").textContent = note;
    } catch (e) { toast(e.message); }
  }
  for (const el of [volume, sndVolume]) {
    el.addEventListener("input", () => { showVolume(el.value); hereVolume(el.value); });
    el.addEventListener("change", () => sendVolume(el.value));
  }

  // -- "Play on this device": PiE-ink's voice and music out of this phone or computer ----------
  // While it's on, the page keeps asking the Pi what to play (a long poll: the answer comes the
  // moment there's something). Speech comes as short WAVs, played back to back through Web Audio
  // so there's no gap; music plays in an <audio> from the file, and the Pi hears how far it got.
  // Browsers only let a page make sound after a tap, so until the first one it waits.

  const HERE_KEY = "pieink.playHere";
  const here = {
    on: false, live: false, polling: false, needTap: false, primed: false,
    id: Math.random().toString(36).slice(2, 10), seq: -1, mv: -1,
    ctx: null, gain: null, line: 0, until: 0, nodes: [], chain: Promise.resolve(),
    el: new Audio(), v: -1, path: "", start: -1, seekTo: 0, wantPlay: false, reportAt: 0, level: 0.8,
  };
  here.el.preload = "auto";
  try { here.on = localStorage.getItem(HERE_KEY) === "1"; } catch (e) { /* private mode */ }

  function silentWav() {
    const n = 1600, buf = new ArrayBuffer(44 + n), v = new DataView(buf);
    const put = (at, s) => [...s].forEach((ch, i) => v.setUint8(at + i, ch.charCodeAt(0)));
    put(0, "RIFF"); v.setUint32(4, 36 + n, true); put(8, "WAVE"); put(12, "fmt ");
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, 8000, true); v.setUint32(28, 16000, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
    put(36, "data"); v.setUint32(40, n, true);
    return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
  }
  function hereVolume(v) {
    here.level = Math.max(0, Math.min(1, Number(v) / 100));
    here.el.volume = here.level;
    if (here.gain) here.gain.gain.value = here.level;
  }
  async function hereUnlock(tapped) {
    try {
      if (!here.ctx) {
        here.ctx = new (window.AudioContext || window.webkitAudioContext)();
        here.gain = here.ctx.createGain();
        here.gain.connect(here.ctx.destination);
        hereVolume((getSetting("audio.volume") ?? 80));
      }
      if (here.ctx.state !== "running") await Promise.race([here.ctx.resume(), new Promise((r) => setTimeout(r, 400))]);
    } catch (e) { /* no Web Audio here */ }
    if (tapped && !here.primed && !here.path) {
      // an <audio> started from a tap may start again later on its own (iPhones need this)
      try { here.el.src = silentWav(); await here.el.play(); here.el.pause(); here.primed = true; } catch (e) { /* later */ }
    }
    return !!here.ctx && here.ctx.state === "running";
  }
  function hereShow() {
    const text = !here.on ? ""
      : !here.live || here.needTap ? "Tap anywhere on this page to start the sound here."
        : "Its voice and music play here while this page is open.";
    for (const p of $$(".here-note")) { p.textContent = text; p.hidden = !text; }
    for (const s of $$(".here-switch")) s.checked = here.on;
  }
  async function hereStart(tapped) {
    if (!here.on) return;
    const unlocked = await hereUnlock(tapped);
    if (!here.on) return;                                   // switched off meanwhile
    here.live = unlocked;
    if (tapped && here.needTap && here.wantPlay) {
      here.needTap = false;
      here.el.play().catch(hereBlocked);
    }
    hereShow();
    if (here.live) hereLoop();
  }
  function hereBye() {
    const body = JSON.stringify({ id: here.id });
    if (!(navigator.sendBeacon && navigator.sendBeacon("/api/audio/here/off", new Blob([body], { type: "application/json" })))) {
      fetch("/api/audio/here/off", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
    }
  }
  function hereStop() {
    const was = here.live;
    here.live = false; here.needTap = false; here.wantPlay = false;
    hereHush();
    if (here.path) { here.el.pause(); here.el.removeAttribute("src"); here.el.load(); here.path = ""; }
    if (was) hereBye();
    hereShow();
    setTimeout(loadMusic, 400);
  }
  function hereSet(on) {
    here.on = on;
    try { localStorage.setItem(HERE_KEY, on ? "1" : ""); } catch (e) { /* private mode */ }
    if (on) hereStart(true); else hereStop();
    hereShow();
  }
  async function hereLoop() {
    if (here.polling) return;
    here.polling = true;
    let fails = 0;
    while (here.on && here.live) {
      try {
        const r = await fetch(`/api/audio/here?id=${here.id}&after=${here.seq}&mv=${here.mv}`, { cache: "no-store" });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || "no answer");
        fails = 0;
        if (!here.live) break;
        here.seq = d.seq;
        for (const ev of d.events || []) hereEvent(ev);
        if (d.music) hereMusic(d.music);
        here.mv = d.mv;
      } catch (e) {
        fails += 1;
        await new Promise((r) => setTimeout(r, Math.min(10000, 1000 * fails)));
      }
    }
    here.polling = false;
  }
  function hereHush() {
    for (const n of here.nodes) { try { n.stop(); } catch (e) { /* done already */ } }
    here.nodes = []; here.until = 0;
  }
  function hereEvent(ev) {
    if (ev.kind === "hush") { if (ev.line == null || ev.line >= here.line) hereHush(); return; }
    if (ev.kind !== "say" || ev.line < here.line) return;
    if (ev.line > here.line) { hereHush(); here.line = ev.line; }
    const line = ev.line;
    const got = fetch(`/api/audio/here/clip/${ev.clip}`, { cache: "no-store" }).then((r) => (r.ok ? r.arrayBuffer() : null));
    // fetched side by side, played in order: each piece starts the moment the last one ends
    here.chain = here.chain.then(async () => {
      const data = await got.catch(() => null);
      if (!data || line !== here.line || !here.ctx) return;
      const buf = await new Promise((ok, no) => here.ctx.decodeAudioData(data, ok, no)).catch(() => null);
      if (!buf || line !== here.line) return;
      const src = here.ctx.createBufferSource();
      src.buffer = buf;
      src.connect(here.gain);
      const at = Math.max(here.ctx.currentTime + 0.03, here.until);
      src.start(at);
      here.until = at + buf.duration;
      here.nodes.push(src);
      src.onended = () => { here.nodes = here.nodes.filter((n) => n !== src); };
    });
  }
  function hereBlocked(e) {
    if (e && e.name === "NotAllowedError") { here.needTap = true; hereShow(); }
  }
  function hereMusic(m) {
    const el = here.el;
    here.v = m.v;
    here.wantPlay = m.state === "playing";
    if (m.state !== "playing" && m.state !== "paused") {
      if (here.path) { el.pause(); el.removeAttribute("src"); el.load(); here.path = ""; }
    } else {
      const restart = m.start !== here.start;                 // played (again): from where the Pi says
      here.start = m.start;
      if (m.path !== here.path) {
        here.path = m.path;
        here.seekTo = m.position > 1 ? m.position : 0;
        el.src = "/api/music/file?p=" + encodeURIComponent(m.path);
      } else if (restart || Math.abs((el.currentTime || 0) - m.position) > 4) {
        if (el.readyState >= 1) {
          try { el.currentTime = m.position; } catch (e) { here.seekTo = m.position; }
        } else here.seekTo = m.position;
      }
      if (here.wantPlay) el.play().catch(hereBlocked); else el.pause();
    }
    if (state.tab === "music") loadMusic();
  }
  function hereReport(body) {
    here.reportAt = Date.now();
    if (body.duration !== undefined && !isFinite(body.duration)) delete body.duration;
    fetch("/api/music/here", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ v: here.v, ...body }) }).catch(() => {});
  }
  here.el.addEventListener("loadedmetadata", () => {
    if (here.seekTo) { try { here.el.currentTime = here.seekTo; } catch (e) { /* from the start then */ } here.seekTo = 0; }
  });
  here.el.addEventListener("timeupdate", () => {
    // only while it plays, and not before a seek lands: a paused track's place isn't to be lost
    if (here.path && !here.el.paused && !here.seekTo && Date.now() - here.reportAt > 2000) {
      hereReport({ position: here.el.currentTime, duration: here.el.duration });
    }
  });
  here.el.addEventListener("ended", () => {
    if (here.path) hereReport({ ended: true, position: here.el.currentTime, duration: here.el.duration });
  });
  here.el.addEventListener("error", () => {
    if (!here.path) return;                                  // the silent start, or nothing loaded
    const code = here.el.error ? here.el.error.code : 0;
    hereReport({ error: code === 4 ? "not a kind of file it plays" : "it wouldn't load" });
  });
  for (const s of $$(".here-switch")) s.addEventListener("change", () => hereSet(s.checked));
  // the events that count as a tap for sound (a touch's pointerdown doesn't; its touchend does)
  const hereTap = (e) => {
    const label = e.target && e.target.closest ? e.target.closest("label") : null;
    if (!here.on || (label && label.querySelector(".here-switch"))) return;   // the switch itself decides
    if (!here.live || here.needTap) hereStart(true);
  };
  for (const type of ["click", "touchend", "keydown"]) document.addEventListener(type, hereTap, true);
  window.addEventListener("pagehide", () => { if (here.live) hereBye(); });
  window.addEventListener("pageshow", (e) => { if (e.persisted && here.on) { here.live = false; hereStart(false); } });
  hereShow();
  if (here.on) hereStart(false);

  // a speaker or mic picker: the saved one selected, whether it was saved by name or (older) by number;
  // one saved that isn't here now stays listed, so saving something else doesn't quietly change it
  function deviceOptions(list, chosen) {
    const has = list.some((x) => x.id === chosen || (chosen && x.alias === chosen));
    const opts = list.map((x) => `<option value="${esc(x.id)}" data-alias="${esc(x.alias || "")}"`
      + ` ${x.id === chosen || (chosen && x.alias === chosen) ? "selected" : ""}>${esc(x.label)}</option>`);
    if (chosen && !has) opts.push(`<option value="${esc(chosen)}" selected>Missing (${esc(chosen)})</option>`);
    return opts.join("");
  }

  let piperTimer = null;
  async function loadAudio() {
    showVolume(getSetting("audio.volume") ?? 80);
    try {
      const d = await api("/audio");
      const dev = $("#mu-device"), chosen = getSetting("audio.device") || "";
      dev.innerHTML = deviceOptions(d.devices, chosen);
      renderPiper(d.piper);
      const pace = d.timing && d.timing.pace;
      const parts = [pace > 1 ? d.voice_note : (d.advice || d.voice_note)];
      if (d.timing && d.timing.first_ms) parts.push(`Last line started after ${(d.timing.first_ms / 1000).toFixed(1)}s.`);
      if (pace > 1) {
        parts.push(`This voice takes ${pace.toFixed(1)}s to make each second of speech here, so it waits before`
          + " speaking rather than stopping halfway; a quick voice starts sooner.");
      }
      $("#mu-audio-hint").textContent = d.missing.length
        ? `Not installed yet: ${d.missing.join(", ")} — run ./setup.sh on the Pi.`
        : parts.filter(Boolean).map((p) => (/[.!?]$/.test(p) ? p : `${p}.`)).join(" ");
    } catch (e) { /* ignore */ }
  }
  function renderPiper(p) {
    if (!p) return;
    const have = $("#piper-voice"), chosen = getSetting("audio.piper_voice") || "";
    have.innerHTML = p.installed.length
      ? p.installed.map((v) => `<option value="${esc(v.id)}" ${v.id === chosen ? "selected" : ""}>${esc(v.label)}</option>`).join("")
      : `<option value="">— none downloaded yet —</option>`;
    const got = new Set(p.installed.map((v) => v.id));
    $("#piper-get").innerHTML = p.offered.filter((v) => !got.has(v.id))
      .map((v) => `<option value="${esc(v.id)}">${esc(v.label)}</option>`).join("") || `<option value="">all downloaded</option>`;
    $("#piper-install").hidden = !!p.ready;
    if (!p.ready || !p.installed.length) $("#piper-box").open = true;
    if (p.running) {
      $("#mu-audio-hint").textContent = "Installing Piper — this takes a few minutes on a Pi.";
      if (!piperTimer) piperTimer = setInterval(async () => {
        try {
          const s = (await api("/audio/piper")).piper;
          if (!s.running) {
            clearInterval(piperTimer); piperTimer = null;
            $("#mu-audio-hint").textContent = s.ok ? "Piper is installed — now pick a voice." : "Install failed; see the Pi's log.";
            loadAudio();
          }
        } catch (e) { clearInterval(piperTimer); piperTimer = null; }
      }, 4000);
    }
  }
  $("#piper-install").addEventListener("click", async () => {
    try { renderPiper((await api("/audio/piper/install", { method: "POST" })).piper); } catch (e) { toast(e.message); }
    $("#mu-audio-hint").textContent = "Installing Piper — this takes a few minutes on a Pi.";
    if (!piperTimer) loadAudio();
  });
  $("#piper-url-add").addEventListener("click", async () => {
    const url = $("#piper-url").value.trim();
    if (!url) return;
    $("#piper-url-add").disabled = true;
    $("#mu-audio-hint").textContent = "Fetching that voice…";
    try {
      const d = await api("/audio/piper/voice", { method: "POST", body: { url } });
      $("#mu-audio-hint").textContent = d.message;
      $("#piper-url").value = "";
      await pollStatus(); renderInputs(); loadAudio();
    } catch (e) { $("#mu-audio-hint").textContent = e.message; }
    $("#piper-url-add").disabled = false;
  });
  $("#piper-download").addEventListener("click", async () => {
    const voice = $("#piper-get").value;
    if (!voice) return;
    $("#piper-download").disabled = true;
    $("#mu-audio-hint").textContent = "Downloading the voice — about 60 MB.";
    try {
      const d = await api("/audio/piper/voice", { method: "POST", body: { voice } });
      $("#mu-audio-hint").textContent = d.message;
      await pollStatus(); renderInputs(); loadAudio();
    } catch (e) { $("#mu-audio-hint").textContent = e.message; }
    $("#piper-download").disabled = false;
  });
  $("#msg-speak").addEventListener("click", async () => {
    if (hasDraft(["audio", "message"])) {
      try { await api("/settings", { method: "PUT", body: draftPatch(["audio", "message"]) }); clearDraft(["audio", "message"]); await pollStatus(); updateButtons(); }
      catch (e) { toast(e.message); }
    }
    // typed text is read straight from the box, so you needn't send it first
    const typed = getSetting("message.source") === "message" ? (getSetting("message.text") || "") : "";
    $("#msg-note").textContent = "Reading it…";
    try {
      const d = await api("/message/say", { method: "POST", body: { text: typed } });
      $("#msg-note").textContent = `Reading: ${d.said}`;
    } catch (e) { $("#msg-note").textContent = e.message; }
  });
  $("#msg-mp3").addEventListener("click", async () => {
    const typed = getSetting("message.source") === "message" ? (getSetting("message.text") || "") : "";
    $("#msg-mp3").disabled = true;
    $("#msg-note").textContent = "Recording it… a long line can take a moment on a Pi.";
    try {
      const r = await fetch("/api/message/mp3", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: typed }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(d.error || "couldn't record it");
      }
      const blob = await r.blob();
      const name = (r.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)/)?.[1] || "message.mp3";
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      $("#msg-note").textContent = `Saved ${name}`;
    } catch (e) { $("#msg-note").textContent = e.message; }
    $("#msg-mp3").disabled = false;
  });
  $("#mu-test").addEventListener("click", async () => {
    if (hasDraft(["audio"])) {
      try { await api("/settings", { method: "PUT", body: draftPatch(["audio"]) }); clearDraft(["audio"]); await pollStatus(); updateButtons(); }
      catch (e) { toast(e.message); }
    }
    const hint = $("#mu-audio-hint"), btn = $("#mu-test");
    hint.textContent = "Playing…"; btn.disabled = true;
    try {
      // wait for the result: which output it went to, or what aplay said when it wouldn't
      const d = await api("/audio/say", { method: "POST", body: { wait: true } });
      const m = d.message || "";
      hint.textContent = /phone or computer/.test(m) ? (here.live ? "Played on this device. Hear it? If not, check its volume."
        : "Played on your other phone or computer, where Play on this device is on.")
        : /^played/.test(m) ? `${m[0].toUpperCase()}${m.slice(1)}. Hear it? If not, pick another output above.`
          : m === "still speaking" ? "Still playing — a long one." : "Said it — hear anything?";
    } catch (e) { hint.textContent = `No sound: ${e.message}`; }
    btn.disabled = false;
  });

  // a speaker or mic that isn't listed: what the Pi sees, and why
  $("#snd-why").addEventListener("click", async () => {
    const box = $("#snd-probe"), btn = $("#snd-why");
    if (!box.hidden && box.dataset.done) { box.hidden = true; delete box.dataset.done; return; }
    box.hidden = false; box.textContent = "Checking the USB bus, the sound cards, the power and the kernel log…"; btn.disabled = true;
    try {
      const d = await api("/audio/probe");
      box.textContent = d.text; box.dataset.done = "1";
      box.scrollTop = 0;                                          // the verdict first, the details under it
      const r = d.report || {};
      $("#snd-pause-row").hidden = !(r.crowded || r.camera_paused_for);
      showCameraPause(r.camera_paused_for || 0);
    } catch (e) { box.textContent = e.message; }
    btn.disabled = false;
    loadAudio(); loadEars();                                      // plugged in since the lists were made: look again
  });
  // watching the USB bus: plug something in and see what the Pi makes of it, line by line, as it happens
  let usbWatch = null;
  const USB_WATCH_FOR = 120;
  function stopUsbWatch(why) {
    if (!usbWatch) return;
    clearTimeout(usbWatch.timer);
    usbWatch = null;
    $("#snd-watch").textContent = "Watch the USB bus";
    if (why) usbWatchSay(why);
  }
  function usbWatchSay(line) {
    const box = $("#snd-watch-log");
    box.textContent += (box.textContent ? "\n" : "") + line;
    box.scrollTop = box.scrollHeight;
  }
  function usbClock(ts, w) {
    // a kernel timestamp is seconds since boot: shown as a clock time, counted from when the watch began
    return new Date(w.wall0 + (ts - w.now0) * 1000).toTimeString().slice(0, 8);
  }
  async function usbWatchTick() {
    const w = usbWatch;
    if (!w) return;
    try {
      const d = await api(`/usb/watch?since=${w.since}`);
      if (!usbWatch || usbWatch !== w) return;
      if (w.since < 0) {                                          // the first look: where the log is now, and what's there
        w.since = d.now; w.now0 = d.now; w.wall0 = Date.now();
        const here = d.devices.filter((u) => !u.classes.includes("hub")).map((u) => `${u.name} (${u.where})`);
        usbWatchSay(`Watching for ${USB_WATCH_FOR} s — plug the speaker or mic in now. `
          + (here.length ? `On the bus already: ${here.join(", ")}.` : "Nothing on the bus but the hub, if there is one."));
        if (d.note) usbWatchSay(d.note);
        w.cards = new Set(d.cards.map((c) => c.id));
      } else {
        w.since = Math.max(w.since, d.now);
        for (const ev of d.events) {
          usbWatchSay(`${usbClock(ev.ts, w)}  ${ev.where ? ev.where + ": " : ""}${ev.text}`);
        }
        const fresh = d.cards.filter((c) => !w.cards.has(c.id) && !c.camera);
        if (fresh.length) {
          for (const c of fresh) w.cards.add(c.id);
          usbWatchSay(`Sound card found: ${fresh.map((c) => c.name).join(", ")} — it's in the lists above now; pick it and save.`);
          loadAudio(); loadEars();
        }
      }
      const left = Math.round((w.until - Date.now()) / 1000);
      $("#snd-watch").textContent = `Watching… ${left} s — stop`;
      if (left <= 0) { stopUsbWatch("Done watching."); loadAudio(); loadEars(); return; }
    } catch (e) {
      if (!usbWatch) return;
      usbWatchSay(`Couldn't read the log: ${e.message}`);
    }
    if (usbWatch === w) w.timer = setTimeout(usbWatchTick, 1500);
  }
  $("#snd-watch").addEventListener("click", () => {
    const box = $("#snd-watch-log");
    if (usbWatch) { stopUsbWatch("Stopped."); return; }
    box.hidden = false; box.textContent = "";
    usbWatch = { since: -1, until: Date.now() + USB_WATCH_FOR * 1000, timer: null, cards: new Set(), now0: 0, wall0: 0 };
    $("#snd-watch").textContent = "Watching…";
    usbWatchTick();
  });

  // the camera let go for two minutes, so a speaker or mic plugged in has the USB link to itself
  let camPauseTimer = null;
  function showCameraPause(left) {
    const btn = $("#snd-pause");
    clearInterval(camPauseTimer); camPauseTimer = null;
    let t = Math.round(left);
    const tick = () => {
      if (t <= 0) {
        clearInterval(camPauseTimer); camPauseTimer = null;
        btn.textContent = "Pause the camera for 2 minutes";
        return;
      }
      btn.textContent = `Camera paused, ${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")} left — resume now`;
      t -= 1;
    };
    tick();
    if (t > 0) camPauseTimer = setInterval(tick, 1000);
  }
  $("#snd-pause").addEventListener("click", async () => {
    const pausing = !camPauseTimer;
    try {
      const d = await api("/camera/pause", { method: "POST", body: { seconds: pausing ? 120 : 0 } });
      showCameraPause(d.paused_for);
      toast(pausing ? "Camera paused — plug the speaker and mic in, then check again" : "The camera's back");
    } catch (e) { toast(e.message); }
  });

  // -- more than one camera ---------------------------------------------------------------

  async function loadCameras(refresh) {
    try {
      const d = await api(`/camera/list${refresh ? "?refresh=1" : ""}`);
      const sel = $("#cam-pick"), chosen = getSetting("camera.source") || "auto";
      sel.innerHTML = `<option value="auto">Auto</option>` + (d.cameras || []).map((c) =>
        `<option value="${esc(c.id)}" ${c.id === chosen ? "selected" : ""}>${esc(c.label)}${c.id === d.using ? " ●" : ""}</option>`).join("");
      if (chosen === "auto") sel.value = "auto";
      $("#cam-next").disabled = (d.cameras || []).filter((c) => c.id !== "mock").length < 2;
    } catch (e) { /* ignore */ }
  }
  $("#cam-pick").addEventListener("change", async (e) => {
    try { await api("/camera/switch", { method: "POST", body: { source: e.target.value } }); await pollStatus(); loadCameras(); }
    catch (err) { toast(err.message); }
  });
  $("#cam-why").addEventListener("click", async () => {
    const box = $("#cam-probe"), btn = $("#cam-why");
    if (!box.hidden && box.dataset.done) { box.hidden = true; return; }
    box.hidden = false; box.textContent = "Checking the USB bus, the video nodes and the driver… (a few seconds)"; btn.disabled = true;
    try { const d = await api("/camera/probe"); box.textContent = d.text; box.dataset.done = "1"; }
    catch (e) { box.textContent = e.message; }
    btn.disabled = false;
  });
  $("#cam-next").addEventListener("click", async () => {
    try {
      const d = await api("/camera/switch", { method: "POST", body: { next: true } });
      await pollStatus(); renderInputs(); loadCameras();
      toast(`Now on ${d.status?.source || d.source}`);
    } catch (err) { toast(err.message); }
  });

  // -- watching the room ---------------------------------------------------------------------

  const WATCH_WORDS = { off: "Not watching", watching: "Watching", something: "Something moved", error: "" };
  let watchTimer = null;
  async function loadWatch() {
    try {
      const d = await api("/watch");
      const s = d.status || {};
      let note = s.error || WATCH_WORDS[s.state] || "";
      if (s.state === "watching") note += ` — ${s.level}% different just now`;
      if (s.told_ago != null) note += `; last remark ${s.told_ago}s ago`;
      note += ` · on screen: ${d.on_screen}`;
      $("#watch-note").textContent = note;
      clearInterval(watchTimer);
      if (s.running) watchTimer = setInterval(loadWatch, 5000);
    } catch (e) { clearInterval(watchTimer); }
  }

  // -- listening for a wake phrase ---------------------------------------------------------

  const EAR_WORDS = { off: "Not listening", waiting: "Listening for your phrase", listening: "Go on, I'm listening", thinking: "Thinking…", error: "Something's wrong" };
  let earTimer = null;
  function renderEars(d) {
    const got = new Set(d.installed.map((m) => m.id));
    const model = $("#ear-model"), chosen = getSetting("listen.model") || "";
    model.innerHTML = d.installed.length
      ? d.installed.map((m) => `<option value="${esc(m.id)}" ${m.id === chosen ? "selected" : ""}>${esc(m.label)}</option>`).join("")
      : `<option value="">— none yet —</option>`;
    $("#ear-get").innerHTML = d.models.filter((m) => !got.has(m.id))
      .map((m) => `<option value="${esc(m.id)}">${esc(m.label)}</option>`).join("") || `<option value="">all downloaded</option>`;
    const dev = $("#ear-device"), chosenDev = getSetting("listen.device") || "";
    dev.innerHTML = deviceOptions(d.devices, chosenDev);
    $("#ear-install").hidden = !!d.install.ready;
    if (!d.install.ready || !d.installed.length) $("#ear-box").open = true;
    const s = d.status;
    let note = EAR_WORDS[s.state] || "";
    if (s.state === "waiting") note = s.always ? "Listening — everything goes to the bot" : `Listening for “${s.wake}”`;
    if (s.state === "waiting" && s.partial) note += ` · hearing: ${s.partial}`;
    if (s.heard && (s.state === "thinking")) note += ` — you said: ${s.heard}`;
    if (s.error) note = s.error;
    if (d.fetch.running) note = d.fetch.message;
    else if (d.fetch.ok === false) note = d.fetch.message;
    if (d.install.running) note = "Installing the listener — a few minutes.";
    $("#ear-hint").textContent = note;
    const busy = d.status.running || d.fetch.running || d.install.running;
    clearInterval(earTimer);
    if (busy) earTimer = setInterval(loadEars, 3000);
  }
  async function loadEars() {
    try { renderEars(await api("/listen")); } catch (e) { clearInterval(earTimer); }
  }
  $("#ear-install").addEventListener("click", async () => {
    $("#ear-hint").textContent = "Installing the listener — a few minutes.";
    try { await api("/listen/install", { method: "POST" }); } catch (e) { toast(e.message); }
    loadEars();
  });
  $("#ear-test").addEventListener("click", async () => {
    if (hasDraft(["listen"])) {
      try { await api("/settings", { method: "PUT", body: draftPatch(["listen"]) }); clearDraft(["listen"]); await pollStatus(); updateButtons(); }
      catch (e) { toast(e.message); }
    }
    $("#ear-test").disabled = true;
    $("#ear-hint").textContent = "Say something — recording for four seconds…";
    try {
      const d = await api("/listen/test", { method: "POST", body: {} });
      $("#ear-hint").textContent = d.message;
    } catch (e) { $("#ear-hint").textContent = e.message; }
    $("#ear-test").disabled = false;
  });
  $("#ear-download").addEventListener("click", async () => {
    const model = $("#ear-get").value;
    if (!model) return;
    $("#ear-hint").textContent = "Downloading the speech model…";
    try { await api("/listen/model", { method: "POST", body: { model } }); } catch (e) { toast(e.message); }
    loadEars();
  });

  // -- the chat bar --------------------------------------------------------------------------

  let chatTimer = null;
  const HOW = { spoken: "out loud", remark: "unprompted", screen: "about your drawing", friend: "with a friend", pitato: "on the Pietato" };

  function renderChat(items) {
    const box = $("#bubbles");
    box.innerHTML = items.length
      ? items.map((m) => `<div class="bubble ${m.role === "you" ? "you" : "bot"}"${m.role === "bot" ? ' title="Tap to hear it again"' : ""}>${esc(m.text)}${
          HOW[m.how] ? `<span class="how">${HOW[m.how]}</span>` : ""}</div>`).join("")
      : `<div class="empty">Nothing yet — say something below.</div>`;
    box.scrollTop = box.scrollHeight;
  }
  async function loadChat(open) {
    try {
      const d = await api("/chat");
      renderChat(d.history);
      if (open) showChat(true);
    } catch (e) { /* ignore */ }
  }
  // on a wide screen the conversation is a column of its own, always open
  const DOCKED = window.matchMedia("(min-width: 1200px)");
  function showChat(on) {
    if (DOCKED.matches) on = true;
    $("#chatlog").hidden = !on;
    if (on) $("#chat-latest").hidden = true;
    $("#chat-toggle").classList.toggle("on", on);
    clearInterval(chatTimer);
    if (on) chatTimer = setInterval(() => loadChat(false), 5000);   // catch spoken and unprompted lines
  }
  $("#chat-toggle").addEventListener("click", () => {
    const open = $("#chatlog").hidden;
    if (open) loadChat(true); else showChat(false);
  });
  $("#chat-clear").addEventListener("click", async () => {
    try { renderChat((await api("/chat", { method: "DELETE" })).history); } catch (e) { toast(e.message); }
  });
  $("#bubbles").addEventListener("click", (e) => {
    const b = e.target.closest(".bubble.bot"); if (!b) return;
    api("/audio/say", { method: "POST", body: { text: b.childNodes[0].textContent } }).catch(() => {});
  });
  $("#chatbar").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = $("#chat-input"), message = input.value.trim();
    if (!message) return;
    input.value = "";
    const open = !$("#chatlog").hidden;
    const box = $("#bubbles");
    if (open) {
      const empty = box.querySelector(".empty"); if (empty) empty.remove();
      box.insertAdjacentHTML("beforeend", `<div class="bubble you">${esc(message)}</div><div class="bubble bot pending">…</div>`);
      box.scrollTop = box.scrollHeight;
    } else {
      showLatest("…", true);
    }
    $("#chat-send").disabled = true;
    try {
      const d = await api("/llm/chat", { method: "POST", body: { message } });
      if (open) renderChat(d.history || []);
      else showLatest(d.reply);
      if (d.head && d.head.error) toast(`Its head couldn't answer: ${d.head.error}`);
      if (state.mode === "message" && getSetting("message.source") === "bot") setTimeout(refreshPreview, 800);
    } catch (err) {
      if (open) {
        const pending = box.querySelector(".bubble.pending");
        if (pending) { pending.classList.remove("pending"); pending.textContent = err.message; }
      } else {
        showLatest(err.message);
      }
    }
    $("#chat-send").disabled = false;
  });
  // the conversation only opens when you ask for it
  let latestTimer = null;
  function showLatest(text, sticky) {
    const box = $("#chat-latest");
    if (!text) { box.hidden = true; return; }
    $("#chat-latest-text").textContent = text;
    box.hidden = false;
    clearTimeout(latestTimer);
    if (!sticky) latestTimer = setTimeout(() => (box.hidden = true), 25000);
  }
  $("#chat-latest").addEventListener("click", () => { $("#chat-latest").hidden = true; loadChat(true); });
  if (DOCKED.matches) loadChat(true);
  DOCKED.addEventListener("change", (e) => { if (e.matches) loadChat(true); else showChat(false); });
  function setChatCollapsed(on) {
    document.body.classList.toggle("chat-collapsed", on);
    try { localStorage.setItem("pieink.chatCollapsed", on ? "1" : ""); } catch (e) { /* fine */ }
  }
  $("#chat-collapse").addEventListener("click", () => setChatCollapsed(true));
  $("#chat-show").addEventListener("click", () => { setChatCollapsed(false); loadChat(true); });
  try { if (localStorage.getItem("pieink.chatCollapsed") === "1") setChatCollapsed(true); } catch (e) { /* fine */ }

  // -- group chat with the other PiE-inks ---------------------------------------------------

  let groupTimer = null, groupSeen = 0, groupMode = "text";
  function renderGroup(d) {
    const box = $("#group-bubbles");
    const items = d.history || [];
    box.innerHTML = items.length
      ? items.map((m) => {
          const mine = !!m.mine;                       // sent here → right; arrived → left
          const who = (m.from_ai ? `${m.from}'s bot` : m.from) + (m.to && m.to !== "all" ? ` → ${m.to}` : "");
          const body = m.kind === "postcard" ? "🖼 a postcard" + (m.text ? ` — ${esc(m.text)}` : "")
                     : m.kind === "stop" ? "stopped the bots" : esc(m.text || "");
          return `<div class="bubble ${mine ? "you" : "bot"} ${m.kind === "ai" ? "ai" : ""}"><span class="who">${esc(who)}</span>${body}</div>`;
        }).join("")
      : `<div class="empty">Nothing yet. Other PiE-inks on your network appear above; say hello.</div>`;
    box.scrollTop = box.scrollHeight;
    const friends = d.friends || [], rooms = new Set(d.rooms || []);
    const named = (f) => rooms.has(f.host) ? `${f.host} (your other room)` : f.host;
    $("#group-who").textContent = friends.length
      ? friends.map(named).join(", ") : "no other PiE-inks found yet";
    const to = $("#group-to"), chosen = to.value || "all";
    to.innerHTML = `<option value="all">Everyone</option>` + friends.map((f) =>
      `<option value="${esc(f.host)}" ${f.host === chosen ? "selected" : ""}>${esc(named(f))}</option>`).join("");
    // "their bot" is for a friend's Pi (another house, another bot); your other rooms are the same bot
    const onlyRooms = friends.length > 0 && friends.every((f) => rooms.has(f.host));
    const theirBot = $("#group-mode button[data-value=ai]");
    theirBot.hidden = onlyRooms;
    if (onlyRooms && groupMode === "ai") {
      groupMode = "text";
      for (const x of $$("#group-mode button")) x.classList.toggle("active", x.dataset.value === "text");
      $("#group-input").placeholder = "Say something…";
    }
    for (const id of ["group-stop", "group-speak-ai", "group-show-screen", "group-indicator"]) {
      const el = $(`#${id}`); (el.closest("label") || el).hidden = onlyRooms;
    }
    $("#group-speak-ai").checked = !!getSetting("social.speak_ai");
    $("#group-show-screen").checked = !!getSetting("social.show_on_screen");
    $("#group-indicator").checked = getSetting("social.indicator") !== false;
    $("#group-stop").textContent = d.ai_on ? "Stop the AIs" : "Let the AIs talk again";
    $("#group-stop").classList.toggle("secondary", !d.ai_on);
    if (items.length > groupSeen && !groupSeen) groupSeen = items.length;
  }
  async function loadGroup(quiet) {
    try {
      const d = await api("/social");
      renderGroup(d);
      const unread = (d.history || []).filter((m) => !m.mine).length;
      $("#group-dot").hidden = $("#group-sheet").hidden ? unread <= groupSeen : true;
      if (!$("#group-sheet").hidden) groupSeen = unread;
    } catch (e) { if (!quiet) toast(e.message); }
  }
  function openGroup() {
    openSheet("group-sheet");
    loadGroup();
    clearInterval(groupTimer);
    groupTimer = setInterval(() => loadGroup(true), 4000);
    setTimeout(() => $("#group-input").focus(), 200);
  }
  $("#group-btn").addEventListener("click", openGroup);
  $("#group-close").addEventListener("click", () => { clearInterval(groupTimer); closeSheets(); });
  $("#group-mode").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-value]"); if (!b) return;
    groupMode = b.dataset.value;
    for (const x of $$("#group-mode button")) x.classList.toggle("active", x === b);
    $("#group-input").placeholder = groupMode === "ai" ? "Ask their bot something…" : "Say something…";
  });
  async function groupSend() {
    const input = $("#group-input"), text = input.value.trim();
    if (!text) return;
    input.value = "";
    try {
      const d = await api("/social/send", { method: "POST", body: { kind: groupMode, text, to: $("#group-to").value } });
      renderGroup({ ...(await api("/social")), history: d.history });
      if (d.problems?.length) $("#group-note").textContent = d.problems.join("; ");
    } catch (e) { $("#group-note").textContent = e.message; }
  }
  $("#group-send").addEventListener("click", groupSend);
  $("#group-input").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); groupSend(); } });
  for (const [id, key] of [["group-speak-ai", "speak_ai"], ["group-show-screen", "show_on_screen"], ["group-indicator", "indicator"]]) {
    $(`#${id}`).addEventListener("change", async (e) => {
      try { await api("/settings", { method: "PUT", body: { social: { [key]: e.target.checked } } }); await pollStatus(); }
      catch (err) { toast(err.message); }
    });
  }
  $("#group-to-screen").addEventListener("click", async () => {
    try { await api("/social/show", { method: "POST" }); await pollStatus(); setTimeout(refreshPreview, 800); toast("Showing the conversation"); }
    catch (err) { toast(err.message); }
  });
  $("#group-clear").addEventListener("click", async () => {
    try { await api("/social", { method: "DELETE" }); loadGroup(); } catch (e) { toast(e.message); }
  });
  $("#group-stop").addEventListener("click", async () => {
    const on = $("#group-stop").textContent.startsWith("Stop");
    try { await api(`/social/ai/${on ? "stop" : "start"}`, { method: "POST" }); loadGroup(); }
    catch (e) { toast(e.message); }
  });
  setInterval(() => { if ($("#group-sheet").hidden) loadGroup(true); }, 30000);   // the red dot

  // a drawing to another Pi
  async function fillPostcardTargets() {
    try {
      const d = await api("/social");
      const sel = $("#draw-postcard");
      sel.innerHTML = `<option value="">Send to…</option>` + (d.friends || []).map((f) =>
        `<option value="${esc(f.host)}">${esc(f.host)}</option>`).join("") +
        ((d.friends || []).length > 1 ? `<option value="all">Everyone</option>` : "");
    } catch (e) { /* ignore */ }
  }
  $("#draw-postcard").addEventListener("focus", fillPostcardTargets);
  $("#draw-postcard").addEventListener("change", async (e) => {
    const to = e.target.value; if (!to) return;
    e.target.value = "";
    const note = $("#draw-ai-note"); note.hidden = false; note.textContent = `Sending to ${to}…`;
    try {
      const d = await api("/social/send", { method: "POST", body: { kind: "postcard", to, image: flattened() } });
      note.textContent = d.problems?.length ? d.problems.join("; ") : `Sent to ${to === "all" ? "everyone" : to}.`;
    } catch (err) { note.textContent = err.message; }
  });

  // -- Telegram ----------------------------------------------------------------------------

  let tgTimer = null;
  async function loadTelegram() {
    try {
      const d = await api("/telegram");
      let note;
      if (!d.configured) note = "Paste a bot token above and save, then come back here to pair.";
      else if (!d.paired) note = `Now open your bot in Telegram and send it this code:  ${d.code}`;
      else note = `Paired with ${d.chat_name || "your chat"}.` + (d.error ? ` Last problem: ${d.error}` : "");
      $("#tg-note").textContent = note;
      $("#tg-test").hidden = !d.paired;
      $("#tg-unpair").hidden = !d.paired;
      clearInterval(tgTimer);
      if (d.configured && !d.paired && !$("#settings-sheet").hidden) tgTimer = setInterval(loadTelegram, 4000);
    } catch (e) { clearInterval(tgTimer); }
  }
  $("#tg-test").addEventListener("click", async () => {
    try { await api("/telegram/test", { method: "POST" }); $("#tg-note").textContent = "Sent — check your phone."; }
    catch (e) { $("#tg-note").textContent = e.message; }
  });
  $("#tg-unpair").addEventListener("click", async () => {
    try { await api("/telegram/unpair", { method: "POST" }); await pollStatus(); loadTelegram(); } catch (e) { toast(e.message); }
  });

  // -- Home Assistant ------------------------------------------------------------------------

  function fillHomePickers(players, remotes) {
    // the lists from Home Assistant, keeping whatever is picked even if it isn't in them right now
    for (const [sel, items, none] of [[$("#home-tv"), players, "— pick one —"], [$("#home-remote"), remotes, "none"]]) {
      const current = getSetting(sel.dataset.setting) || "";
      sel.innerHTML = "";
      sel.append(new Option(none, ""));
      for (const it of items || []) sel.append(new Option(`${it.name} (${it.id.split(".")[1]})${it.via ? ` · ${it.via}` : ""}${it.can_on === false ? " — can't turn on from off" : ""}`, it.id));
      if (current && !(items || []).some((it) => it.id === current)) sel.append(new Option(current, current));
      sel.value = current;
    }
  }
  async function loadHome() {
    try {
      const d = await api("/home");
      const ps = $("#home-preset");
      if (!ps.options.length) for (const p of d.presets || []) ps.append(new Option(p.label, p.id));
      fillHomePickers(d.players, d.remotes);
      renderInputs();
      showHomeMac(d.tv_mac_found);
      const note = $("#home-note");
      if (!getSetting("home.enabled")) note.textContent = "Off. Switch it on, paste the address and token, press Check, pick the TV and save.";
      else if (!d.configured) note.textContent = "An address and a token are needed.";
      else if (d.error) note.textContent = `Last problem: ${d.error}`;
      else if (d.info && d.info.version) note.textContent = `Connected to ${d.info.name || "Home Assistant"} (${d.info.version}).` + (d.tv ? "" : " Pick the TV below and save.");
      else note.textContent = d.tv ? "Ready." : "Press Check, then pick the TV.";
      if (d.last && d.last.reply) $("#home-result").textContent = `Last: "${d.last.text}" → ${d.last.reply}`;
    } catch (e) { $("#home-note").textContent = e.message; }
  }
  $("#home-check").addEventListener("click", async () => {
    const note = $("#home-note");
    note.textContent = "Checking…";
    try {
      const d = await api("/home/check", { method: "POST", body: { url: getSetting("home.url"), token: getSetting("home.token") } });
      if (!d.ok) { note.textContent = `Couldn't: ${d.error}`; return; }
      fillHomePickers(d.players, d.remotes);
      showHomeMac(d.tv_mac_found);
      const n = (d.players || []).length;
      note.textContent = `Connected to ${d.name || "Home Assistant"} (${d.version}) — ${n} media player${n === 1 ? "" : "s"}` +
        (n ? ". Pick the TV, then save." : ". No media players there yet — add the TV to Home Assistant first.");
    } catch (e) { note.textContent = e.message; }
  });
  function showHomeMac(found) {
    // what Home Assistant knows the TV as, so nothing need be typed; the field is only for when it doesn't
    $("#home-mac").placeholder = found || "found from Home Assistant when it knows it";
    $("#home-mac-note").textContent = found ? "— known" : "";
  }
  async function homeSay(text) {
    const out = $("#home-result");
    const first = (getSetting("home.names") || "tv").split(",")[0].trim() || "tv";
    text = text.replace(/\bthe tv\b/, `the ${first}`);
    out.textContent = `"${text}" …`;
    try {
      const d = await api("/home/do", { method: "POST", body: { text } });
      const trail = (d.last && d.last.trail || []).join(" · ");
      out.textContent = `"${text}" → ${d.reply}` + (trail ? ` (${trail})` : "");
    } catch (e) { out.textContent = `"${text}" → ${e.message}`; }
  }
  for (const b of $$("#home-try [data-say]")) b.addEventListener("click", () => homeSay(b.dataset.say));
  $("#home-say-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#home-say").value.trim();
    if (text) homeSay(text);
  });

  // -- hand gestures ----------------------------------------------------------------------------

  let gestTimer = null;
  let gestBuilt = false;
  function buildGestMap(d) {
    // one row per gesture, each with what it does — the list is the help, and changing it is the remap
    const box = $("#gest-map");
    box.innerHTML = "";
    for (const s of d.slots || []) {
      const label = document.createElement("span");
      label.textContent = s.label;
      const sel = document.createElement("select");
      sel.className = "words";
      sel.dataset.setting = `gestures.${s.id}`;
      for (const [id, name] of Object.entries(s.kind === "hold" ? d.holds : d.presses)) sel.append(new Option(name, id));
      sel.addEventListener("change", () => { setDraft(sel.dataset.setting, sel.value); onEdit(); });
      box.append(label, sel);
    }
    gestBuilt = true;
    renderInputs();
  }
  function buildGestLights(d) {
    const box = $("#gest-lights");
    const picked = new Set(getSetting("gestures.lights") || []);
    box.innerHTML = "";
    if (!d.lights) { box.textContent = "Set up Home Assistant above first."; return; }
    if (!d.lights.length) { box.textContent = "No lights in Home Assistant yet."; return; }
    for (const l of d.lights) {
      const row = document.createElement("label");
      row.className = "switch";
      row.innerHTML = `<input type="checkbox"><span>${esc(l.name)}${(d.room || []).includes(l.id) ? " · the TV's room" : ""}</span>`;
      const box_ = row.querySelector("input");
      box_.checked = picked.has(l.id);
      box_.addEventListener("change", () => {
        const now = $$("#gest-lights input").map((el, i) => el.checked ? d.lights[i].id : null).filter(Boolean);
        const saved = (state.settings?.gestures?.lights) || [];
        if (JSON.stringify(now) === JSON.stringify(saved)) delete state.draft["gestures.lights"];
        else state.draft["gestures.lights"] = now;
        onEdit();
      });
      box.append(row);
    }
    $("#gest-lights-hint").textContent = (d.room || []).length
      ? "None ticked: the TV's room's lights." : "None ticked: every light Home Assistant has.";
  }
  async function loadGestures(withLights) {
    try {
      const d = await api("/gestures" + (withLights ? "?lights=1" : ""));
      if (!gestBuilt) buildGestMap(d);
      if (withLights) buildGestLights(d);
      const note = $("#gest-note");
      const on = getSetting("gestures.enabled");
      let text;
      if (d.why_not) text = d.why_not;
      else if (!on) text = "Off.";
      else if (d.state !== "watching") text = d.why || "Starting…";
      else if (d.hands) text = `Seeing ${d.hands === 1 ? "a hand" : "both hands"}: ${d.mode}` + (d.last && d.last.ago < 3 ? ` — ${d.last.label}` : "");
      else text = `Watching for your hands (${d.fps} a second).` + (d.last ? ` Last: ${d.last.label}, ${Math.round(d.last.ago)} s ago.` : "");
      if (on && d.error) text += ` Home Assistant: ${d.error}`;
      note.textContent = text;
      const view = $("#gest-view");
      const showing = on && d.state === "watching" && !$("#settings-sheet").hidden;
      view.hidden = !showing;
      if (showing) view.src = `/api/gestures/view?t=${Date.now()}`;
      clearTimeout(gestTimer);
      if (on && !$("#settings-sheet").hidden && !$('[data-pane="home"]').hidden) gestTimer = setTimeout(() => loadGestures(false), 700);
    } catch (e) { $("#gest-note").textContent = e.message; }
  }
  $("#gest-view").addEventListener("error", () => { $("#gest-view").hidden = true; });
  $("#gest-lights-more").addEventListener("toggle", (e) => { if (e.target.open) loadGestures(true); });

  // -- the second screen (1.69" LCD) --------------------------------------------------------

  let lcdTimer = null;
  let lcdPreviewUrl = null;
  async function loadLcd() {
    if ($("#lcd-group").hidden) return;                     // one screen: nothing to show or ask
    try {
      const d = await api("/lcd");
      const sel = $("#lcd-screen");
      if (!sel.options.length) {
        for (const [value, label] of d.screens) sel.append(new Option(label, value));
        const ms = $("#lcd-model");
        for (const m of d.models || []) ms.append(new Option(m.label, m.id));
        renderInputs();
      }
      loadSnake();
      const st = d.status;
      const note = $("#lcd-note");
      if (!getSetting("lcd.enabled")) note.textContent = "Off. Switch it on, save, and the LCD comes to life.";
      else if (st.error) note.textContent = `Can't reach the LCD: ${st.error}`;
      else if (st.running) note.textContent = `${st.panel || "LCD"} — ${st.frames} frame${st.frames == 1 ? "" : "s"} so far.`;
      else note.textContent = "Starting…";
    } catch (e) { $("#lcd-note").textContent = e.message; }
    refreshLcdPreview();
    clearInterval(lcdTimer);
    if (!$("#settings-sheet").hidden) lcdTimer = setInterval(refreshLcdPreview, 5000);
  }
  // Snake: the models that can play (decision models on the bot's machine), and how the game is going
  const DECISION_MODEL = /^(tev1|nimble|clef)/;
  async function loadSnake() {
    const sel = $("#snake-model"), chosen = getSetting("snake.model") || "tev1";
    try {
      const d = await api("/llm");
      const names = (d.models || []).filter((m) => DECISION_MODEL.test(m));
      // "tev1" in the settings is "tev1:latest" on the machine: the same thing
      const same = names.find((m) => m === chosen || m === `${chosen}:latest` || (!chosen.includes(":") && m.split(":")[0] === chosen));
      if (!same) names.unshift(chosen);
      const html = names.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
      if (sel.innerHTML !== html) { sel.innerHTML = html; sel.value = same || chosen; }
    } catch (e) { if (!sel.options.length) sel.innerHTML = `<option value="${esc(chosen)}">${esc(chosen)}</option>`; }
    snakeStatus();
  }
  // -- yes and no with its head (the desk pet) ---------------------------------------------
  async function loadPet() {
    const sel = $("#pet-model"), chosen = getSetting("pet.model") || "tev1";
    try {
      const d = await api("/llm");
      const names = (d.models || []).filter((m) => DECISION_MODEL.test(m));
      const same = names.find((m) => m === chosen || m === `${chosen}:latest` || (!chosen.includes(":") && m.split(":")[0] === chosen));
      if (!same) names.unshift(chosen);
      const html = names.map((m) => `<option value="${esc(m)}">${esc(m)}</option>`).join("");
      if (sel.innerHTML !== html) { sel.innerHTML = html; sel.value = same || chosen; }
    } catch (e) { if (!sel.options.length) sel.innerHTML = `<option value="${esc(chosen)}">${esc(chosen)}</option>`; }
    petStatus();
  }
  async function petStatus() {
    try {
      const d = await api("/pet"), l = d.last || {};
      const pct = l.answer && l.probs && l.probs[l.answer] != null ? ` (${Math.round(l.probs[l.answer] * 100)}%)` : "";
      $("#pet-status").textContent = d.gesture ? `${d.gesture === "nod" ? "Nodding" : d.gesture === "shake" ? "Shaking its head" : "Tilting its head"}…`
        : l.error ? l.error
        : l.answer ? `"${l.question}" → ${l.answer}${pct}, ${l.ms} ms${l.done && !l.moved ? " — but the head didn't move" : ""}`
        : "Nothing asked yet.";
    } catch (e) { /* a nicety */ }
  }
  $("#pet-more").addEventListener("toggle", () => { if ($("#pet-more").open) loadPet(); });
  $$("#pet-more [data-gesture]").forEach((btn) => btn.addEventListener("click", async () => {
    try {
      await api("/pet/try", { method: "POST", body: { gesture: btn.dataset.gesture } });
      const t = setInterval(petStatus, 400);                   // "Nodding…" while it does, then the last answer again
      setTimeout(() => clearInterval(t), 6000);
    } catch (e) { toast(e.message); }
  }));
  async function snakeStatus() {
    try {
      const s = await api("/snake");
      const best = s.best ? ` · best ${s.best}` : "";
      $("#snake-status").textContent = s.error ? s.error
        : s.playing ? `Playing${s.bored ? " (bored)" : ""}: score ${s.score}${best}${s.avg_ms ? ` · ${s.avg_ms} ms a decision` : ""}`
        : s.games ? `Not playing. ${s.games} game${s.games === 1 ? "" : "s"} so far${best}${s.avg != null ? `, ${s.avg} a game lately` : ""}.`
        : "Not playing yet.";
    } catch (e) { /* the line is a nicety */ }
  }
  $("#snake-play").addEventListener("click", async () => {
    try {
      if (hasDraft(["snake", "lcd"])) { await api("/settings", { method: "PUT", body: draftPatch(["snake", "lcd"]) }); clearDraft(["snake", "lcd"]); }
      await api("/settings", { method: "PUT", body: { lcd: { enabled: true, screen: "snake" } } });
      await pollStatus(); renderInputs(); loadLcd();
      toast("Snake is on the little screen");
    } catch (e) { toast(e.message); }
  });
  async function refreshLcdPreview() {
    const img = $("#lcd-preview");
    if ($("#settings-sheet").hidden) { clearInterval(lcdTimer); return; }
    if ($("#snake-more").open) snakeStatus();                 // the game line, while the fold is open
    try {
      const draft = draftPatch(["lcd"]);
      const res = Object.keys(draft).length
        ? await fetch("/api/lcd/preview.png", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ settings: draft }) })
        : await fetch(`/api/lcd/preview.png?t=${Date.now()}`);
      if (!res.ok) return;
      const url = URL.createObjectURL(await res.blob());
      img.onload = () => { if (lcdPreviewUrl) URL.revokeObjectURL(lcdPreviewUrl); lcdPreviewUrl = url; };
      img.src = url;
    } catch (e) { /* the preview is a nicety */ }
  }
  const scheduleLcdPreview = debounce(refreshLcdPreview, 250);

  async function loadGpi() {
    try {
      const d = await api("/gpi");
      const pads = d.found || [];
      const keys = (d.buttons || []).filter((b) => !["L R", "B"].includes(b.key)).map((b) => `${b.key} ${b.does}`);
      $("#gpi-now").textContent = d.screen && keys.length ? `${d.screen}: ${keys.join(" · ")}` : "";
      $("#gpi-note").textContent = !d.running ? "The buttons are read while the case's screen is the panel."
        : d.problem && !pads.length ? d.problem.charAt(0).toUpperCase() + d.problem.slice(1) + "."
        : pads.length ? `Reading ${pads.join(", ")}` + (d.last ? ` · last pressed: ${d.last.button.toUpperCase()}${d.last.did ? ` → ${d.last.did}` : ""}` : "")
        : "No controller found yet — the case's buttons are a USB controller inside it; it's looked for every few seconds.";
    } catch (e) { /* the note is a nicety */ }
  }
  async function loadWhisplay() {
    try {
      const d = await api("/whisplay");
      const b = d.status, snd = d.sound || {};
      $("#whisplay-note").textContent = !d.panel ? "Save with the Whisplay HAT as the panel and the button and LED come alive."
        : b.error ? `The button and LED aren't reachable: ${b.error}`
        : b.running ? "Button and LED ready." : "Starting…";
      $("#whisplay-sound-note").textContent = !snd.found
        ? "Sound card: not found — run ./setup.sh --whisplay on the Pi and reboot."
        : snd.speaker_in_use && snd.mic_in_use ? `Sound card: whisplaysound (card ${snd.card.index}) — speaker and mic in use.`
        : `Sound card: whisplaysound (card ${snd.card.index}) found, but the voice is going elsewhere — press the button.`;
      $("#whisplay-sound").hidden = !snd.found || (snd.speaker_in_use && snd.mic_in_use);
    } catch (e) { /* not there */ }
  }
  $("#whisplay-sound").addEventListener("click", async () => {
    try {
      await api("/whisplay/sound", { method: "POST" });
      await pollStatus(); renderInputs(); loadAudio(); loadEars(); loadWhisplay();
      toast("The HAT's speaker and mic are in use");
    } catch (e) { toast(e.message); }
  });

  // -- the settings sheet -------------------------------------------------------------------

  function openSheet(id) {
    for (const sheet of $$(".sheet")) sheet.hidden = sheet.id !== id;
    $("#settings-scrim").hidden = false;
    document.body.classList.add("sheet-open");
    updateButtons();
  }
  function closeSheets() {
    for (const sheet of $$(".sheet")) sheet.hidden = true;
    $("#settings-scrim").hidden = true;
    document.body.classList.remove("sheet-open");
  }
  function openSettings(pane) {
    openSheet("settings-sheet");
    renderTheme();
    if (pane) showPane(pane);
    loadAudio(); loadEars(); renderCycle(); renderMe(); loadWatch(); loadTelegram(); loadHome(); loadGestures(false); loadLcd(); loadWhisplay(); pollSystem(); pollUpdate();
    loadEvents(); loadAgent(); loadBuddy();
  }

  // -- the agent ------------------------------------------------------------------------------
  let agentTimer = null;
  async function loadAgent() {
    clearInterval(agentTimer); agentTimer = null;
    try {
      const d = await api("/agent");
      const st = d.status || {};
      let note;
      if (!st.enabled) note = hasDraft(["agent"]) ? "Press Save to turn it on." : "Off.";
      else {
        note = st.busy ? "Checking now…" : `On — every ${st.every_minutes} min`;
        if (!st.busy && st.last_ago != null) note += ` · last check ${st.last_ago < 90 ? `${st.last_ago}s` : `${Math.round(st.last_ago / 60)} min`} ago (${(st.last || {}).level || "quiet"})`;
        if (!st.busy && st.next_in != null) note += ` · next in ${Math.max(1, Math.round(st.next_in / 60))} min`;
        if (st.error) note += ` · ${st.error}`;
      }
      $("#agent-note").textContent = note;
      for (const el of $$(".agent-turns")) el.hidden = !st.camera_turns;
      const list = $("#agent-journal");
      const items = d.journal || [];
      list.innerHTML = items.length ? items.map((e) => {
        const did = (e.actions || []).map((a) => `${a.do}: ${a.text}${a.result && a.result !== "done" ? ` (${a.result})` : ""}`).join(" · ");
        return `<li class="lv-${esc(e.level || "quiet")}"><time>${esc(eventTime(e.ts))}</time><span>${esc(e.thought || e.error || "…")}${did ? `<small>${esc(did)}</small>` : ""}${e.saw ? `<small>saw: ${esc(e.saw)}</small>` : ""}</span></li>`;
      }).join("") : `<li class="empty">No checks yet.</li>`;
      if (st.enabled && (st.busy || !$("#settings-sheet").hidden)) agentTimer = setInterval(loadAgent, st.busy ? 2000 : 15000);
    } catch (e) { $("#agent-note").textContent = e.message; }
  }
  $("#agent-run").addEventListener("click", async () => {
    if (hasDraft(["agent"])) {
      try { await api("/settings", { method: "PUT", body: draftPatch(["agent"]) }); clearDraft(["agent"]); await pollStatus(); updateButtons(); }
      catch (e) { toast(e.message); return; }
    }
    try {
      const d = await api("/agent/run", { method: "POST", body: {} });
      $("#agent-note").textContent = d.started ? "Checking now… (a look, then the model — half a minute or so)" : d.message;
      clearInterval(agentTimer); agentTimer = setInterval(loadAgent, 2000);
    } catch (e) { toast(e.message); }
  });
  $("#agent-clear").addEventListener("click", async () => {
    try { await api("/agent", { method: "DELETE" }); loadAgent(); } catch (e) { toast(e.message); }
  });
  function openBot() {
    openSheet("bot-sheet");
    renderWhere();
    loadLlm(true);
    loadNotes();
    loadReminders();
  }

  // -- what it remembers, and what it will remind you of ---------------------------------------
  function renderNotes(notes) {
    $("#note-list").innerHTML = notes.length
      ? notes.map((n) => `<li data-id="${esc(n.id)}"><span class="title">${esc(n.text)}</span><button class="remove" aria-label="Forget">×</button></li>`).join("")
      : `<li class="empty">Nothing yet. Tell it "remember that …", or type one here.</li>`;
  }
  async function loadNotes() {
    try { renderNotes((await api("/notes")).notes || []); } catch (e) { /* ignore */ }
  }
  $("#note-add").addEventListener("click", async () => {
    const text = $("#note-text").value.trim();
    if (!text) return;
    try { renderNotes((await api("/notes", { method: "POST", body: { text } })).notes); $("#note-text").value = ""; }
    catch (e) { toast(e.message); }
  });
  $("#note-text").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#note-add").click(); } });
  $("#note-list").addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-id]"); if (!li || !e.target.closest(".remove")) return;
    try { renderNotes((await api(`/notes/${encodeURIComponent(li.dataset.id)}`, { method: "DELETE" })).notes); } catch (err) { toast(err.message); }
  });
  function renderReminders(items) {
    $("#reminder-list").innerHTML = items.length
      ? items.map((r) => `<li data-id="${esc(r.id)}"><span class="title">${esc(r.words)}</span><button class="remove" aria-label="Cancel">×</button></li>`).join("")
      : `<li class="empty">None set.</li>`;
  }
  async function loadReminders() {
    try { renderReminders((await api("/reminders")).reminders || []); } catch (e) { /* ignore */ }
  }
  $("#reminder-add").addEventListener("click", async () => {
    const text = $("#reminder-text").value.trim();
    if (!text) return;
    try {
      const d = await api("/reminders", { method: "POST", body: { text } });
      renderReminders(d.reminders); $("#reminder-text").value = ""; $("#reminder-note").textContent = d.reply;
    } catch (e) { $("#reminder-note").textContent = e.message; }
  });
  $("#reminder-text").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#reminder-add").click(); } });
  $("#reminder-list").addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-id]"); if (!li || !e.target.closest(".remove")) return;
    try { renderReminders((await api(`/reminders/${encodeURIComponent(li.dataset.id)}`, { method: "DELETE" })).reminders); } catch (err) { toast(err.message); }
  });

  const closeSettings = closeSheets;
  function showPane(name) {
    for (const b of $$("#sheet-tabs button")) b.classList.toggle("active", b.dataset.pane === name);
    for (const p of $$(".sheet .pane")) p.hidden = p.dataset.pane !== name;
    $(".sheet-body").scrollTop = 0;
    if (name === "home") loadGestures(false);                  // (what the camera sees, while this is open)
  }
  $("#settings-btn").addEventListener("click", () => openSettings("screen"));   // always lands on Screen
  $("#show-me").addEventListener("click", async () => {
    if (hasDraft(SHEET_SECTIONS)) $("#settings-save").click();
    try {
      await api("/mode", { method: "POST", body: { mode: "me" } });
      await pollStatus();
      $("#settings-note").textContent = "Showing you on the screen";
      setTimeout(refreshPreview, 900);
    } catch (e) { $("#settings-note").textContent = e.message; }
  });
  $("#bot-btn").addEventListener("click", openBot);
  $("#bot-close").addEventListener("click", closeSheets);
  $("#settings-close").addEventListener("click", closeSheets);
  $("#settings-scrim").addEventListener("click", closeSheets);
  $("#bot-save").addEventListener("click", async () => {
    try {
      await api("/settings", { method: "PUT", body: draftPatch(BOT_SECTIONS) });
      clearDraft(BOT_SECTIONS);
      await pollStatus();
      renderInputs(); updateButtons(); loadLlm(true);
      $("#llm-note").textContent = "Saved";
    } catch (e) { $("#llm-note").textContent = e.message; }
  });
  $("#sheet-tabs").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-pane]"); if (!b) return;
    showPane(b.dataset.pane);
    if (b.dataset.pane === "sound") { loadAudio(); loadEars(); }  // a speaker plugged in since: listed now
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $$(".sheet").some((x) => !x.hidden)) closeSheets();
  });
  $("#settings-save").addEventListener("click", async () => {
    try {
      await api("/settings", { method: "PUT", body: draftPatch(SHEET_SECTIONS) });
      clearDraft(SHEET_SECTIONS);
      await pollStatus();
      renderInputs(); renderCycle(); updateButtons();
      $("#settings-note").textContent = "Saved";
      setTimeout(() => ($("#settings-note").textContent = ""), 2000);
      loadTelegram();
      setTimeout(loadHome, 300);
      setTimeout(loadLcd, 700);
      setTimeout(refreshPreview, 800);
    } catch (e) { $("#settings-note").textContent = e.message; }
  });

  // -- the bot's voice ---------------------------------------------------------------------

  function renderSpeech(speech) {
    const box = $("#llm-say");
    if (!speech || (!speech.text && !speech.error)) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = speech.text ? esc(speech.text) : `<small>${esc(speech.error)}</small>`;
    // it only reaches the panel while the screen is on Message → Bot
    $("#llm-speak").hidden = !speech.text;
  }
  $("#llm-speak").addEventListener("click", async () => {
    if (hasDraft(["audio"])) {
      try { await api("/settings", { method: "PUT", body: draftPatch(["audio"]) }); clearDraft(["audio"]); await pollStatus(); updateButtons(); }
      catch (e) { toast(e.message); }
    }
    try { await api("/audio/say", { method: "POST", body: { text: $("#llm-say").textContent } }); }
    catch (e) { $("#llm-note").textContent = e.message; }
  });
  $("#llm-put").addEventListener("click", async () => {
    // the bot screen is the message screen in bot mode — say so outright rather
    // than leaning on whichever tab happens to be open behind the panel
    try {
      await api("/mode", { method: "POST", body: { mode: "message", settings: { message: { source: "bot" } } } });
      await pollStatus();
      renderInputs();
      closeSheets();
      setTimeout(refreshPreview, 700);
    } catch (e) { $("#llm-note").textContent = e.message; }
  });

  let lastRemote = "";                       // so switching back doesn't lose the address
  function onThisPi(host) {
    const h = (host || "").replace(/^https?:\/\//, "");
    return h.startsWith("127.0.0.1") || h.startsWith("localhost");
  }
  function renderWhere() {
    const host = getSetting("llm.host") || "";
    if (!onThisPi(host) && host) lastRemote = host;
    const onPi = onThisPi(host);
    for (const b of $$("#llm-where button")) b.classList.toggle("active", (b.dataset.value === "pi") === onPi);
    $("#llm-host-row").hidden = onPi;
    $("#llm-backup").hidden = onPi;                       // nothing to fall back to
    $("#llm-backup-model").hidden = onPi || !getSetting("llm.use_backup");
  }
  $("#llm-where").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-value]"); if (!b) return;
    const pi = b.dataset.value === "pi";
    if (!pi && onThisPi(getSetting("llm.host"))) {          // going back out to the network
      setDraft("llm.host", lastRemote || "http://192.168.1.50:11434");
    } else if (pi) {
      setDraft("llm.host", "http://127.0.0.1:11434");
    }
    renderInputs(); renderWhere(); onEdit();
    await saveLlmDraft();               // the Pi probes the address it has, so store it first
    loadLlm(true);
  });
  document.addEventListener("change", (e) => {
    if (e.target.dataset?.setting === "llm.use_backup") renderWhere();
  });

  async function loadLlm(probe = false) {
    if (!probe && !getSetting("llm.enabled")) return;
    try {
      const d = await api("/llm");
      renderWhere();
      const pi = $("#llm-pi-model"), chosenPi = getSetting("llm.backup_model") || "";
      if (d.pi) {
        pi.innerHTML = d.pi.models.length
          ? d.pi.models.map((m) => `<option value="${esc(m)}" ${m === chosenPi ? "selected" : ""}>${esc(m)}</option>`).join("")
          : `<option value="">${esc(d.pi.error || "no models on the Pi")}</option>`;
      }
      const sel = $("#llm-model"), chosen = getSetting("llm.model") || "";
      const eyes = $("#llm-vision"), chosenEyes = getSetting("llm.vision_model") || "";
      if (d.models?.length) {
        sel.innerHTML = d.models.map((m) => `<option value="${esc(m)}" ${m === chosen ? "selected" : ""}>${esc(m)}</option>`).join("");
        eyes.innerHTML = `<option value="">— same as above —</option>` + d.models.map((m) => `<option value="${esc(m)}" ${m === chosenEyes ? "selected" : ""}>${esc(m)}</option>`).join("");
        $("#llm-note").textContent = `${d.models.length} model${d.models.length === 1 ? "" : "s"} on that machine.`;
      } else if (d.error) {
        $("#llm-note").textContent = d.error;
      }
      renderSpeech(d.speech);
    } catch (e) { $("#llm-note").textContent = e.message; }
  }
  async function saveLlmDraft() {
    if (!hasDraft(["llm"])) return true;
    try {
      await api("/settings", { method: "PUT", body: draftPatch(["llm"]) });
      clearDraft(["llm"]);
      await pollStatus();
      updateButtons();
      return true;
    } catch (e) { $("#llm-note").textContent = e.message; return false; }
  }
  $("#llm-test").addEventListener("click", async () => {
    $("#llm-note").textContent = "Looking…";
    await saveLlmDraft();                        // the address has to be on the Pi to probe it
    loadLlm(true);
  });

  // -- me: workdays and schedule (arrays, so they get their own editors) -------------------

  function renderMe() {
    const days = getSetting("schedule.workdays") || [];
    for (const b of $$("#me-days button")) b.classList.toggle("on", days.includes(b.dataset.day));
    const blocks = getSetting("schedule.blocks") || [];
    $("#me-blocks").innerHTML = blocks.map((b, i) => `
      <div class="block" data-i="${i}">
        <input type="time" value="${esc(b.start || "")}" data-k="start">
        <input type="time" value="${esc(b.end || "")}" data-k="end">
        <input type="text" value="${esc(b.activity || "")}" data-k="activity" placeholder="Working, Gym, Sleeping…">
        <label class="paid"><input type="checkbox" data-k="paid" ${b.paid ? "checked" : ""}>Paid</label>
        <button class="remove" aria-label="Remove">×</button>
      </div>`).join("") || `<p class="hint">No blocks yet. Add one — e.g. 06:00 to 17:00, Working, paid.</p>`;
  }
  $("#me-days").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-day]"); if (!b) return;
    const days = new Set(getSetting("schedule.workdays") || []);
    days.has(b.dataset.day) ? days.delete(b.dataset.day) : days.add(b.dataset.day);
    setDraft("schedule.workdays", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].filter((d) => days.has(d)));
    renderMe(); onEdit();
  });
  function meBlocks() { return (getSetting("schedule.blocks") || []).map((b) => ({ ...b })); }
  $("#me-add-block").addEventListener("click", () => {
    const blocks = meBlocks(); blocks.push({ start: "09:00", end: "17:00", activity: "", paid: true });
    setDraft("schedule.blocks", blocks); renderMe(); onEdit();
    $$("#me-blocks input[type=text]").at(-1)?.focus();
  });
  $("#me-blocks").addEventListener("input", (e) => {
    const row = e.target.closest(".block"); if (!row) return;
    const blocks = meBlocks(), b = blocks[Number(row.dataset.i)];
    const k = e.target.dataset.k; if (!k) return;
    b[k] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setDraft("schedule.blocks", blocks); onEdit();
  });
  $("#me-blocks").addEventListener("click", (e) => {
    if (!e.target.closest(".remove")) return;
    const row = e.target.closest(".block"); const blocks = meBlocks(); blocks.splice(Number(row.dataset.i), 1);
    setDraft("schedule.blocks", blocks); renderMe(); onEdit();
  });

  const MODE_PICK = [["clock", "Clock"], ["weather", "Weather"], ["me", "Me"], ["earnings", "Earnings"],
    ["message", "Message"], ["image", "Drawing"], ["reader", "Book"],
    ["camera", "Camera"], ["music", "Music"], ["groupchat", "Group chat"], ["crypto", "Finance"], ["system", "System"],
    ["buddy", "Friend"], ["off", "Screen off"]];

  // -- the slideshow -------------------------------------------------------------------
  const CYCLE_PICK = MODE_PICK.filter(([v]) => v !== "off");
  function renderCycle() {
    const chosen = getSetting("cycle.screens") || [];
    $("#cycle-screens").innerHTML = CYCLE_PICK.map(([v, l]) =>
      `<label class="switch"><input type="checkbox" data-screen="${v}" ${chosen.includes(v) ? "checked" : ""}><span>${l}</span></label>`).join("");
  }
  $("#cycle-screens").addEventListener("change", () => {
    const picked = $$("#cycle-screens input:checked").map((i) => i.dataset.screen);
    setDraft("cycle.screens", picked); onEdit();
  });

  // -- weather ---  // -- weather ---------------------------------------------------------------------------

  const SOURCE_WORDS = { network: " · from the Pi's internet address", typed: "" };
  async function loadWeather() {
    const loc = getSetting("weather.location");
    $("#wx-place").textContent = loc ? loc + (SOURCE_WORDS[getSetting("weather.source")] || "") : "not set — press Find me, or type a town";
    try {
      const d = await api("/weather");
      const box = $("#wx-now");
      if (!d.data) { box.hidden = true; return; }
      box.hidden = false;
      const u = d.data.units;
      $("#wx-temp").textContent = `${Math.round(d.data.temp)}°${u}`;
      const day = d.data.days?.[0];
      $("#wx-cond").textContent = weatherText(d.data.code);
      $("#wx-detail").textContent = `Feels ${Math.round(d.data.feels)}° · ${d.data.humidity}% humidity` + (day ? ` · H ${Math.round(day.hi)}° L ${Math.round(day.lo)}°` : "");
    } catch (e) { /* ignore */ }
  }
  function weatherText(code) {
    const m = { 0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Icy fog", 51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
      61: "Light rain", 63: "Rain", 65: "Heavy rain", 71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Showers", 81: "Showers", 82: "Heavy showers", 95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm" };
    return m[code] || "—";
  }
  const wxResults = $("#wx-results");
  const wxSearch = debounce(async (q) => {
    if (!q) { wxResults.hidden = true; return; }
    try {
      const { results } = await api(`/weather/lookup?q=${encodeURIComponent(q)}`);
      wxResults.innerHTML = results.length
        ? results.map((r, i) => `<li data-i="${i}"><span class="sym">${esc(r.name)}</span><span class="name">${esc([r.admin, r.country].filter(Boolean).join(", "))}</span></li>`).join("")
        : `<li class="empty">Nothing found for “${esc(q)}”</li>`;
      wxResults._results = results; wxResults.hidden = false;
    } catch (e) { wxResults.innerHTML = `<li class="empty">${esc(e.message)}</li>`; wxResults.hidden = false; }
  }, 350);
  $("#wx-search").addEventListener("input", (e) => wxSearch(e.target.value.trim()));
  wxResults.addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-i]"); if (!li) return;
    const r = wxResults._results[Number(li.dataset.i)];
    wxResults.hidden = true; $("#wx-search").value = "";
    try {
      const d = await api("/weather/place", { method: "POST", body: r });
      toast(`Weather for ${d.location}`);
      await pollStatus(); loadWeather();
      if (state.mode === "weather") setTimeout(refreshPreview, 2500);
      setTimeout(loadWeather, 4000);
    } catch (err) { toast(err.message); }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest("#wx-search, #wx-results")) wxResults.hidden = true; });
  // Find me: where the Pi's internet address says it is
  $("#wx-locate").addEventListener("click", async () => {
    const btn = $("#wx-locate");
    btn.disabled = true;
    try {
      const d = await api("/weather/locate", { method: "POST", body: {} });
      toast(`Weather for ${d.location} (roughly — the Pi's internet address)`);
      await pollStatus(); loadWeather();
      if (state.mode === "weather") setTimeout(refreshPreview, 2500);
      setTimeout(loadWeather, 4000);
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  });

  // -- friends: other PiE-inks on the network ---------------------------------------------

  let friendsOpen = false;
  const ago = (s) => (s < 90 ? `${s}s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`);
  async function loadFriends() {
    try {
      const d = await api("/friends");
      const pitatos = d.pitatos || [], cams = d.cameras || [];        // the handheld Pi, and Pietato cameras (they stream like a PiE-ink's)
      const withCamera = d.friends.concat(cams);
      $("#friends-count").textContent = withCamera.length ? `(${withCamera.length})` : "";
      $("#friends-group").hidden = !(d.friends.length || pitatos.length || cams.length);
      $("#friends-label").textContent = d.friends.length ? (pitatos.length || cams.length ? "Your other rooms, and the Pietato" : "Your other rooms")
        : "A Pietato on your network";
      const rooms = Object.fromEntries((d.rooms || []).map((r) => [r.host, r]));
      $("#friends-list").innerHTML = d.friends.map((f) => {
        const c = f.caps || {}, room = rooms[f.host] || {};
        const has = [c.camera && "camera", c.lcd && `LCD ${c.lcd}"`, c.speaker && "speaker", c.mic && "mic", c.llm && "a model",
          c.home && "Home Assistant", c.agent && "agent", c.battery != null && `battery ${c.battery}%`].filter(Boolean).join(" · ");
        const kept = [room.notes && `${room.notes} note${room.notes === 1 ? "" : "s"}`, room.reminders && `${room.reminders} reminder${room.reminders === 1 ? "" : "s"}`].filter(Boolean).join(", ");
        return `<li><span class="title"><a href="${esc(f.url)}" target="_blank" rel="noopener">${esc(f.host)}</a>${c.location ? ` <span class="bkind">${esc(c.location)}</span>` : ""}<span class="bkind">the same bot, in another room · ${esc(f.panel)} · ${esc(f.mode || "")}${has ? ` · ${esc(has)}` : ""}${kept ? ` · ${esc(kept)}` : ""}</span></span><span class="prog">${esc(f.ip)}</span></li>`;
      }).concat(pitatos.map((p) => {
        const what = [p.here ? "on this Pi" : "", p.activity || (p.asks ? "asks for the weather and prices" : ""), p.cameras.length ? `camera ${p.cameras.join(", ")}` : "", ago(p.ago)].filter(Boolean).join(" · ");
        const name = p.url ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a>` : esc(p.name);
        return `<li><span class="title">${name}${d.excited ? ` <span class="bkind">★</span>` : ""}<span class="bkind">Pietato · ${esc(what)}</span></span><span class="prog">${esc(p.ip)}</span></li>`;
      })).concat(cams.filter((c) => !pitatos.some((p) => p.ip === c.ip)).map((c) =>
        `<li><span class="title"><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.host)}</a><span class="bkind">Pietato camera${c.caps.turns ? " · turns" : ""} · ${ago(c.ago)}</span></span><span class="prog">${esc(c.ip)}</span></li>`)).join("");
      if (!friendsOpen) return;
      const grid = $("#friends-grid");
      if (!withCamera.length) { grid.innerHTML = `<div class="empty">No other room (another PiE-ink of yours) or Pietato camera found yet. They announce themselves every few seconds on the same Wi-Fi.</div>`; return; }
      const want = withCamera.map((f) => f.id).join(",");
      if (grid.dataset.ids === want) return;                       // keep the streams as they are
      grid.dataset.ids = want;
      grid.innerHTML = withCamera.map((f) => `
        <div class="tile"><img src="${esc(f.url)}/api/camera/stream?t=${Date.now()}" alt="${esc(f.host)}">
          <a href="${esc(f.url)}" target="_blank" rel="noopener" title="Open ${esc(f.host)}"></a><span>${esc(f.host)}${f.kind === "pitato" ? " · Pietato" : ""}</span></div>`).join("");
    } catch (e) { /* ignore */ }
  }
  $("#friends-btn").addEventListener("click", () => {
    friendsOpen = !friendsOpen;
    const grid = $("#friends-grid");
    grid.hidden = !friendsOpen;
    if (!friendsOpen) { grid.innerHTML = ""; delete grid.dataset.ids; }   // closes their streams
    else loadFriends();
  });

  // -- camera --------------------------------------------------------------------------

  const camImg = $("#cam-live");
  const camWrap = $("#cam-wrap");
  camWrap.dataset.rot = localStorage.getItem("camRot") || "0";
  $("#cam-rotate").addEventListener("click", () => {
    const next = (Number(camWrap.dataset.rot) + 90) % 360;
    camWrap.dataset.rot = String(next);
    localStorage.setItem("camRot", String(next));
    applyCamShape();
  });
  // the live view full screen
  $("#cam-full").addEventListener("click", () => {
    if (document.fullscreenElement) { document.exitFullscreen(); return; }
    const go = camWrap.requestFullscreen || camWrap.webkitRequestFullscreen;
    if (go) go.call(camWrap).then(applyCamShape).catch(() => toast("This browser won't go full screen here"));
    else toast("This browser won't go full screen here");
  });
  document.addEventListener("fullscreenchange", applyCamShape);
  // frame the live view like the panel: its aspect ratio, Fill/Whole, mirror (and the page-only rotation)
  function applyCamShape() {
    const p = state.panel, rot = Number(camWrap.dataset.rot);
    const full = document.fullscreenElement === camWrap;
    camWrap.style.aspectRatio = full ? "auto" : `${p.width} / ${p.height}`;   // the screen's shape, unless full screen
    camWrap.classList.toggle("contain", getSetting("camera.fit") === "contain");
    // sideways: the picture's own box is the container with its sides swapped, so the
    // rotated picture lands exactly on the box (Fill/Whole crop inside that box)
    requestAnimationFrame(() => {
      const r = camWrap.getBoundingClientRect();
      if (rot % 180) { camImg.style.width = `${r.height}px`; camImg.style.height = `${r.width}px`; }
      else { camImg.style.width = "100%"; camImg.style.height = "100%"; }
    });
    camWrap.classList.toggle("mirror", !!getSetting("camera.mirror"));
    $("#cam-shape").textContent = `${p.width}×${p.height}`;
  }
  let camStatusTimer = null, camStreamAt = 0;
  function restartStream() {
    camStreamAt = Date.now();
    camImg.removeAttribute("src");            // let go of the old connection first
    // a drawn photo filter (sketch, comic…) comes drawn from the Pi; the colour ones are CSS, here
    const f = currentFilter();
    camImg.src = "/api/camera/stream?t=" + Date.now() + (f.css === null ? `&filter=${encodeURIComponent(f.id)}` : "");
  }
  function setCameraStream(on) {
    clearInterval(camStatusTimer);            // one timer, however often the stream is restarted
    camStatusTimer = null;
    if (on) {
      applyCamShape();
      loadFriends();
      restartStream();
      loadPhotos();
      badgeKey = "";                            // the label shows again for a few seconds each time the tab opens
      pollCamera();
      camStatusTimer = setInterval(pollCamera, 5000);
    } else {
      camImg.removeAttribute("src");          // closes the stream so the Pi can stop the camera
      if (friendsOpen) $("#friends-btn").click();
    }
  }
  setInterval(() => { if (state.tab === "camera") loadFriends(); }, 6000);
  setInterval(() => { if (state.tab === "camera") loadEvents(); }, 10000);

  // -- what the camera has seen ------------------------------------------------------------
  const LEVEL_WORDS = { alert: "alert", note: "", info: "" };
  function eventTime(ts) {
    const d = new Date(ts * 1000), now = new Date();
    const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return d.toDateString() === now.toDateString() ? hm : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${hm}`;
  }
  function renderEvents(list, items, empty) {
    list.innerHTML = items.length ? items.map((e) => `<li class="lv-${esc(e.level || "info")}"><time>${esc(eventTime(e.ts))}</time><span>${esc(e.text)}</span></li>`).join("")
      : `<li class="empty">${esc(empty)}</li>`;
  }
  async function loadEvents() {
    try {
      const d = await api("/events?limit=60");
      const st = d.status || {};
      renderEvents($("#events-list"), d.events || [], d.watching ? "Nothing seen yet." : "Nothing yet — turn Watching on (Settings → Watching).");
      const how = d.detector === "model" ? "the vision model" : d.detector === "basic" ? "OpenCV on the Pi (people and cats)" : "";
      let note = how ? `Looking with ${how}` : "Nothing can look: no vision model, no OpenCV";
      if (st.looks) note += ` · ${st.looks} look${st.looks === 1 ? "" : "s"}` + (st.took_ms ? `, the last took ${(st.took_ms / 1000).toFixed(1)} s` : "");
      if (st.error) note += ` · ${st.error}`;
      $("#events-note").textContent = note;
      const det = $("#events-detector");
      if (det) det.textContent = note;
    } catch (e) { /* ignore */ }
  }
  $("#events-look").addEventListener("click", async () => {
    const btn = $("#events-look");
    btn.disabled = true; $("#events-note").textContent = "Looking…";
    try {
      const d = await api("/events/look", { method: "POST", body: {} });
      toast(d.events.length ? d.events.map((e) => e.text).join(" · ") : `Nothing new — ${d.saw}`);
    } catch (e) { toast(e.message); }
    btn.disabled = false;
    loadEvents();
  });
  $("#events-clear").addEventListener("click", async () => {
    try { await api("/events", { method: "DELETE" }); loadEvents(); } catch (e) { toast(e.message); }
  });

  // -- a camera that turns: drag the picture, zoom with the wheel or a pinch, the keys, a controller --------
  const ptzState = { on: false, sid: Math.random().toString(36).slice(2, 10), seq: 0, vx: 0, vy: 0, held: false,
    timer: null, inflight: false, pos: null, lookTimer: null, following: false, followBusy: false, restingSaid: 0 };
  const ptzOpts = () => ({ speed: Number(getSetting("ptz.speed")) || 60, swap_x: !!getSetting("ptz.swap_x"), swap_y: !!getSetting("ptz.swap_y") });
  // following: the camera turns after you by itself, so the keys and a controller rest (and say why when
  // touched); dragging the picture, Center and Home point it somewhere, which takes over
  function setFollowing(on) {
    ptzState.following = !!on;
    if (!ptzState.followBusy) $("#ptz-follow").checked = ptzState.following;
    if (ptzState.following) ptzRelease();
  }
  function stickResting() {
    if (!ptzState.following) return false;
    if (Date.now() - ptzState.restingSaid > 4000) { ptzState.restingSaid = Date.now(); toast("It's following you — switch off Follow me to steer"); }
    return true;
  }
  function ptzShow(pos) {
    if (!pos || !pos.has) return;
    ptzState.pos = pos;
    const bits = [];
    if (ptzState.sleepNote) bits.push(ptzState.sleepNote);
    else if (ptzState.following) bits.push("Following you");
    else {
      if (pos.has.pan) bits.push(`pan ${Math.round(pos.pan)}°`);
      if (pos.has.tilt) bits.push(`tilt ${Math.round(pos.tilt)}°`);
    }
    if (pos.has.zoom) bits.push(`zoom ${Math.round(pos.zoom * 100)}%`);
    $("#ptz-pos").textContent = bits.join(" · ");
    camWrap.classList.toggle("steer", ptzState.on && !!(pos.has.pan || pos.has.tilt || pos.has.zoom));
  }
  async function loadPtz() {
    try {
      const d = await api("/ptz");
      ptzState.on = !!d.available;
      $("#ptz").hidden = !d.available;
      $("#ptz-note").hidden = !d.available;
      $("#ptz-more").hidden = !d.available;
      $("#pet-more").hidden = !d.available;
      document.body.classList.toggle("cam-turns", !!d.available && state.tab === "camera");
      if (!d.available) { camWrap.classList.remove("steer"); return; }
      if ($("#pet-more").open) petStatus();                  // the last answer, while the fold is open
      const f = d.follow || {};
      $("#ptz-follow-row").hidden = !f.can;
      // an OBSBOT asleep (lens down): being woken, or left to sleep when waking is off
      const s = d.sleep || {};
      $("#ptz-wake-row").hidden = !s.can;
      ptzState.sleepNote = !s.can ? "" : s.waking ? "Waking the camera…"
        : s.asleep ? (s.wake ? "Asleep — waking it…" : "Asleep (lens down) — waking is off") : "";
      // the camera's own word, except while a look of its own has paused it (then: what the switch says)
      if (!ptzState.followBusy) setFollowing(f.can && (f.paused || f.on == null ? f.want : f.on));
      // a poll that lands mid-drag (or just after a move of ours) would carry an older position: skip it
      if (!steer.dragging && Date.now() - steer.at > 800) ptzShow(d.position);
      const h = d.home;
      $("#ptz-home-note").textContent = (h ? `Home: pan ${Math.round(h.pan)}°, tilt ${Math.round(h.tilt)}°${h.zoom ? `, zoom ${Math.round(h.zoom * 100)}%` : ""}.` : "Home: straight ahead, until you make one.")
        + (d.turned ? ` Turned ${d.turned} s in the last 10 min.` : "")
        + (d.stop_owed ? " It's owed a stop (it wouldn't take one) — it goes the moment it can." : "");
      if (!ptzState.lookTimer) { $("#ptz-look").disabled = d.looking; $("#ptz-look").textContent = d.looking ? "Looking…" : "Look around"; }
    } catch (e) { /* not there */ }
  }
  // turning at a speed (the arrow keys, a controller): drive beats a few times a second while held
  function ptzSend() {
    if (ptzState.inflight) return;                                 // one at a time: the next beat carries the newest
    ptzState.inflight = true;
    api("/ptz/drive", { method: "POST", body: { vx: ptzState.vx, vy: ptzState.vy, sid: ptzState.sid, seq: ++ptzState.seq, ...ptzOpts() } })
      .then((d) => { if (d.following) { setFollowing(true); stickResting(); } ptzShow(d.position); })
      .catch((e) => toast(e.message)).finally(() => { ptzState.inflight = false; });
  }
  function ptzHold() {
    if (ptzState.held) return;
    ptzState.held = true;
    ptzSend();
    ptzState.timer = setInterval(ptzSend, 150);                    // the heartbeat: silence stops the camera
  }
  function ptzRelease() {
    if (!ptzState.held) return;
    ptzState.held = false;
    clearInterval(ptzState.timer); ptzState.timer = null;
    ptzState.vx = ptzState.vy = 0;
    api("/ptz/stop", { method: "POST", body: { sid: ptzState.sid, seq: ++ptzState.seq } }).then((d) => ptzShow(d.position)).catch(() => {});
  }
  function driveTo(vx, vy) {                                       // a speed each way, -1…1, right and up positive
    if (Math.hypot(vx, vy) < 0.1) vx = vy = 0;                     // a small dead zone in the middle
    ptzState.vx = Math.max(-1, Math.min(1, vx)); ptzState.vy = Math.max(-1, Math.min(1, vy));
  }
  window.addEventListener("blur", () => { keysHeld.clear(); ptzRelease(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { keysHeld.clear(); ptzRelease(); } });

  // Dragging the picture turns the camera the way dragging a map moves it: drag left and the camera turns
  // right, so what was off to the right comes in and whatever is under your finger stays under it. Two
  // fingers apart (or the mouse wheel) zoom. Where it's told to point is sent as a place, not a speed, a
  // few times a second — and once more where you let go.
  const steer = { pts: new Map(), pan0: 0, tilt0: 0, zoom0: 0, x0: 0, y0: 0, dist0: 0, moved: false,
    want: null, goal: null, timer: null, at: 0, inflight: false, dragging: false, badgeTimer: null };
  const VIEW_DEG = 70, ZOOM_X = 4;      // about how wide the picture is, in degrees, at no zoom; how much the far end magnifies
  const viewDeg = (zoom) => VIEW_DEG / (1 + (ZOOM_X - 1) * (zoom || 0));
  const within = (v, range) => Math.max(range[0], Math.min(range[1], v));
  function steerable() {
    const p = ptzState.pos;
    return ptzState.on && !!p && !!p.has && !!(p.has.pan || p.has.tilt || p.has.zoom);
  }
  function toCamera(dx, dy) {           // a movement on the page → the same movement in the picture's own frame (shown rotated, mirrored)
    const rot = Number(camWrap.dataset.rot) || 0;
    let x = dx, y = dy;
    if (rot === 90) { x = dy; y = -dx; } else if (rot === 180) { x = -dx; y = -dy; } else if (rot === 270) { x = -dy; y = dx; }
    if (camWrap.classList.contains("mirror")) x = -x;
    return [x, y];
  }
  function pictureWidth() {             // the picture's own width on the page: it may be rotated, letterboxed or cropped
    const W = camImg.offsetWidth || camWrap.clientWidth || 1, H = camImg.offsetHeight || camWrap.clientHeight || 1;
    const nw = camImg.naturalWidth || 4, nh = camImg.naturalHeight || 3;
    const whole = camWrap.classList.contains("contain") || document.fullscreenElement === camWrap;
    return nw * (whole ? Math.min(W / nw, H / nh) : Math.max(W / nw, H / nh));
  }
  function pointers() {                 // the pointers' middle, and how far apart two are
    const pts = [...steer.pts.values()];
    const x = pts.reduce((s, p) => s + p.x, 0) / pts.length, y = pts.reduce((s, p) => s + p.y, 0) / pts.length;
    return { x, y, dist: pts.length > 1 ? Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y) : 0 };
  }
  function steerFrom() {                // where the pointers are now is where the camera points now
    const c = pointers(), p = ptzState.pos;
    const g = steer.goal && (steer.timer || steer.inflight || steer.dragging) ? steer.goal : {};
    steer.x0 = c.x; steer.y0 = c.y; steer.dist0 = c.dist;
    steer.pan0 = g.pan != null ? g.pan : p.pan; steer.tilt0 = g.tilt != null ? g.tilt : p.tilt; steer.zoom0 = g.zoom != null ? g.zoom : p.zoom;
  }
  function steerMove() {
    const p = ptzState.pos, c = pointers();
    const [mx, my] = toCamera(c.x - steer.x0, c.y - steer.y0);
    if (!steer.moved && Math.hypot(mx, my) < 4 && Math.abs(c.dist - steer.dist0) < 4) return;   // a tap is not a drag
    steer.moved = true;
    const k = viewDeg(steer.zoom0) / pictureWidth();                           // degrees a pixel
    const sx = getSetting("ptz.swap_x") ? -1 : 1, sy = getSetting("ptz.swap_y") ? -1 : 1;
    const want = {};
    if (p.has.pan) {
      want.pan = within(steer.pan0 - mx * k * sx, p.pan_range);                // left: the camera turns right
      steer.pan0 = want.pan + mx * k * sx;                                     // at the end of its travel, the finger starts there
    }
    if (p.has.tilt) {
      want.tilt = within(steer.tilt0 + my * k * sy, p.tilt_range);             // up: the camera looks down
      steer.tilt0 = want.tilt - my * k * sy;
    }
    if (p.has.zoom && steer.dist0 > 0 && c.dist > 0) {
      const mag = (1 + (ZOOM_X - 1) * steer.zoom0) * (c.dist / steer.dist0);  // the picture grows with the fingers
      want.zoom = Math.max(0, Math.min(1, (mag - 1) / (ZOOM_X - 1)));
    }
    ptzWant(want);
  }
  function ptzWant(want) {              // the newest place; sent at most every 120 ms, one request at a time, the last always goes
    steer.goal = Object.assign(steer.goal || {}, want);
    steer.want = Object.assign(steer.want || {}, want);
    if (want.zoom != null) zoomBadge(want.zoom);
    if (steer.timer || steer.inflight) return;
    steer.timer = setTimeout(ptzFlush, Math.max(0, 120 - (Date.now() - steer.at)));
  }
  async function ptzFlush() {
    clearTimeout(steer.timer); steer.timer = null;
    if (!steer.want || steer.inflight) return;
    const body = steer.want;
    steer.want = null; steer.inflight = true; steer.at = Date.now();
    try { const d = await api("/ptz/set", { method: "POST", body }); tookOver(d); ptzShow(d.position); steer.at = Date.now(); }
    catch (e) { toast(e.message); }
    steer.inflight = false;
    if (steer.want) steer.timer = setTimeout(ptzFlush, Math.max(0, 120 - (Date.now() - steer.at)));
  }
  function zoomBadge(zoom) {           // the zoom, over the picture, while it changes
    const b = $("#cam-zoom");
    b.textContent = `zoom ${Math.round(zoom * 100)}%`;
    b.hidden = false;
    clearTimeout(steer.badgeTimer);
    steer.badgeTimer = setTimeout(() => { b.hidden = true; }, 1200);
  }
  function zoomBy(dz) {                 // the wheel, + and −, a controller's triggers
    const p = ptzState.pos;
    if (!steerable() || !p.has.zoom) return;
    const base = steer.goal && steer.goal.zoom != null && (steer.timer || steer.inflight) ? steer.goal.zoom : p.zoom;
    ptzWant({ zoom: Math.max(0, Math.min(1, base + dz)) });
  }
  camWrap.addEventListener("pointerdown", (e) => {
    if (!steerable() || e.target.closest("button") || (e.pointerType === "mouse" && e.button !== 0)) return;
    if (steer.pts.size >= 2) return;                                           // two fingers are plenty
    e.preventDefault();
    try { camWrap.setPointerCapture(e.pointerId); } catch (err) { /* a pointer already gone */ }
    steer.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (steer.pts.size === 1) steer.moved = false;
    steerFrom();                                                               // (a first finger starts from where it points now)
    steer.dragging = true;
    camWrap.classList.add("dragging");
  });
  camWrap.addEventListener("pointermove", (e) => {
    if (!steer.pts.has(e.pointerId)) return;
    steer.pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    steerMove();
  });
  function steerEnd(e) {
    if (!steer.pts.delete(e.pointerId)) return;
    if (steer.pts.size) { steerFrom(); return; }                               // one finger left: it carries on from here
    steer.dragging = false;
    camWrap.classList.remove("dragging");
    if (steer.want) ptzFlush();                                                // where you let go
  }
  for (const ev of ["pointerup", "pointercancel", "lostpointercapture"]) camWrap.addEventListener(ev, steerEnd);
  camWrap.addEventListener("dragstart", (e) => e.preventDefault());
  camWrap.addEventListener("wheel", (e) => {
    if (!steerable() || !ptzState.pos.has.zoom) return;
    e.preventDefault();
    const px = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaMode === 2 ? e.deltaY * 400 : e.deltaY;   // lines or pages → about pixels
    zoomBy(-px / 1000);                                                        // a notch of the wheel: 10 %
  }, { passive: false });

  // the arrow keys turn it at a speed; + and − zoom, 0 centres
  const KEY_DIRS = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
  const keysHeld = new Set();
  function keysDrive() {
    let vx = 0, vy = 0;
    for (const k of keysHeld) { vx += KEY_DIRS[k][0]; vy += KEY_DIRS[k][1]; }
    if (!vx && !vy) { ptzRelease(); return; }
    driveTo(vx * 0.75, vy * 0.75);
    ptzHold();
  }
  document.addEventListener("keydown", (e) => {
    if (state.tab !== "camera" || !ptzState.on || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest && e.target.closest("input, textarea, select, [contenteditable]")) return;
    if (document.body.classList.contains("sheet-open")) return;   // settings, a photo: not for the camera
    if (KEY_DIRS[e.key]) {
      e.preventDefault();
      if (stickResting()) return;
      if (!keysHeld.has(e.key)) { keysHeld.add(e.key); keysDrive(); }
    } else if (e.key === "+" || e.key === "=" || e.key === "-" || e.key === "_") {
      e.preventDefault();
      zoomBy(e.key === "+" || e.key === "=" ? 0.1 : -0.1);
    } else if (e.key === "0") {
      e.preventDefault();
      $("#ptz-center").click();
    }
  });
  document.addEventListener("keyup", (e) => { if (keysHeld.delete(e.key)) keysDrive(); });

  // -- a game controller on this computer ---------------------------------------------------------
  // The browser's Gamepad API, read here and turned into the same drive beats the arrow keys send,
  // so nothing new reaches the Pi. The "standard" layout (Xbox, PlayStation, most others):
  // axes 0/1 the left stick, 2/3 the right; buttons 0–3 A B X Y (✕ ○ □ △), 4/5 the bumpers,
  // 6/7 the triggers (how far they're pulled), 9 Start, 12–15 the D-pad. Only on the Camera tab,
  // only while this page is the one in front — a game in another window keeps its controller.
  const pad = { on: true, id: "", seen: false, driving: false, was: [], zoomAt: 0 };
  try { pad.on = localStorage.getItem("pieink.pad") !== "off"; } catch (e) { /* no storage */ }
  const PAD_DEAD = 0.15;
  function padCurve(v) {                            // a dead zone, then gentle near the middle, full at the edge
    const a = Math.abs(v);
    if (a < PAD_DEAD) return 0;
    const t = (a - PAD_DEAD) / (1 - PAD_DEAD);
    return Math.sign(v) * t * t;
  }
  function padFind() {
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    for (const g of pads) if (g && g.connected) return g;
    return null;
  }
  function padWords(g) {                            // what the buttons do, in the controller's own names
    const sony = /playstation|dualshock|dualsense|sony|054c/i.test(g.id);
    const [a, b, x, y] = sony ? ["✕", "○", "□", "△"] : ["A", "B", "X", "Y"];
    const name = g.id.replace(/\s*\(.*$/, "").trim() || "Controller";
    return `${name}: the left stick turns it, the triggers (or the right stick) zoom, ${a} centres, ${b} goes home, `
      + `${x} looks around, ${y} takes a picture, the D-pad nudges, Start is Follow me.`
      + (g.mapping === "standard" ? "" : " (The browser doesn't know this controller's layout, so the buttons may differ.)");
  }
  function padNote(text) {
    const n = $("#ptz-pad-note");
    if (n.textContent !== text) { n.textContent = text; n.hidden = !text; }
    $("#ptz-pad-tag").hidden = !(pad.on && pad.id);
  }
  function padStop() {
    if (!pad.driving) return;
    pad.driving = false;
    if (!keysHeld.size) ptzRelease();
  }
  function padActive() {
    return pad.on && ptzState.on && state.tab === "camera" && !document.hidden && document.hasFocus()
      && !document.body.classList.contains("sheet-open");
  }
  function padTick() {
    if (!pad.on || !navigator.getGamepads) return;
    const g = padFind();
    if (!g) {
      if (pad.id) { pad.id = ""; padStop(); }
      if (ptzState.on) padNote(pad.seen ? "The controller went away — press a button on it." : "No controller seen yet — press a button on it.");
      return;
    }
    if (g.id !== pad.id) { pad.id = g.id; pad.seen = true; pad.was = []; padNote(padWords(g)); }
    if (!padActive()) { padStop(); pad.was = g.buttons.map((b) => b.pressed); return; }
    const axis = (i) => padCurve(g.axes[i] || 0);
    const down = (i) => !!(g.buttons[i] && g.buttons[i].pressed);
    const pull = (i) => (g.buttons[i] ? g.buttons[i].value : 0);
    // the left stick, or the D-pad at three-quarters
    let vx = axis(0), vy = -axis(1);
    if (down(14)) vx -= 0.75;
    if (down(15)) vx += 0.75;
    if (down(12)) vy += 0.75;
    if (down(13)) vy -= 0.75;
    if (vx || vy) {
      if (!stickResting()) {
        driveTo(vx, vy);
        pad.driving = true;
        ptzHold();
      }
    } else if (pad.driving) {
      padStop();
    }
    // zoom: the triggers, as far as they're pulled, or the right stick up and down; the bumpers step 10 %
    if (ptzState.pos && ptzState.pos.has && ptzState.pos.has.zoom) {
      const pressed = g.buttons.map((b) => b.pressed);
      const rate = (pull(7) - pull(6)) - axis(3);
      const now = performance.now();
      let step = 0;
      if (rate && now - pad.zoomAt > 100) { pad.zoomAt = now; step = rate * 4; }
      if (pressed[5] && !pad.was[5]) step += 10;
      if (pressed[4] && !pad.was[4]) step -= 10;
      if (step) zoomBy(step / 100);
    }
    // the buttons, on the press
    const pressed = g.buttons.map((b) => b.pressed);
    const hit = (i) => pressed[i] && !pad.was[i];
    if (hit(0)) $("#ptz-center").click();
    if (hit(1)) $("#ptz-home").click();
    if (hit(2)) $("#ptz-look").click();
    if (hit(3)) $("#photo-take").click();
    if (hit(9) && !$("#ptz-follow-row").hidden) $("#ptz-follow").click();
    pad.was = pressed;
  }
  if (navigator.getGamepads) {
    setInterval(padTick, 50);
    window.addEventListener("gamepadconnected", padTick);
    window.addEventListener("gamepaddisconnected", padTick);
  } else {
    $("#ptz-pad").closest(".switches").hidden = true;
  }
  $("#ptz-pad").checked = pad.on;
  $("#ptz-pad").addEventListener("change", (e) => {
    pad.on = e.target.checked;
    try { localStorage.setItem("pieink.pad", pad.on ? "on" : "off"); } catch (err) { /* ignore */ }
    if (!pad.on) { padStop(); pad.id = ""; padNote(""); } else padTick();
  });

  const tookOver = (d) => { if (d.stopped_following) { setFollowing(false); toast("Stopped following you"); } };
  $("#ptz-center").addEventListener("click", async () => {
    try { const d = await api("/ptz/set", { method: "POST", body: { center: true } }); tookOver(d); ptzShow(d.position); } catch (e) { toast(e.message); }
  });
  $("#ptz-home").addEventListener("click", async () => {
    try { const d = await api("/ptz/home", { method: "POST", body: {} }); tookOver(d); ptzShow(d.position); if (!d.home) toast("No home saved yet — pointing straight ahead"); }
    catch (e) { toast(e.message); }
  });
  $("#ptz-follow").addEventListener("change", async (e) => {
    const on = e.target.checked;
    ptzState.followBusy = true;
    e.target.disabled = true;
    try {
      const d = await api("/ptz/follow", { method: "POST", body: { on } });
      ptzState.followBusy = false;
      setFollowing(d.follow.on);
      ptzShow(d.position);
    } catch (err) {
      ptzState.followBusy = false;
      e.target.checked = ptzState.following;
      toast(err.message);
    }
    e.target.disabled = false;
  });
  $("#ptz-save-home").addEventListener("click", async () => {
    try { const d = await api("/ptz/home", { method: "POST", body: { save: true } }); ptzShow(d.position); toast(`Home is here now (pan ${Math.round(d.home.pan)}°, tilt ${Math.round(d.home.tilt)}°)`); await pollStatus(); loadPtz(); }
    catch (e) { toast(e.message); }
  });
  $("#ptz-look").addEventListener("click", async () => {
    const btn = $("#ptz-look");
    try { await api("/ptz/look", { method: "POST", body: { where: "around" } }); }
    catch (e) { toast(e.message); return; }
    btn.disabled = true; btn.textContent = "Looking…";
    const started = Date.now();
    clearInterval(ptzState.lookTimer);
    ptzState.lookTimer = setInterval(async () => {
      let d = null;
      try { d = await api("/ptz"); ptzShow(d.position); } catch (e) { /* keep waiting */ }
      const done = d && !d.looking && d.last_look;
      if (done || Date.now() - started > 180000) {
        clearInterval(ptzState.lookTimer); ptzState.lookTimer = null;
        btn.disabled = false; btn.textContent = "Look around";
        if (done) { toast(d.last_look.text.length > 180 ? d.last_look.text.slice(0, 177) + "…" : d.last_look.text); loadChat(false); }
      }
    }, 1200);
  });

  // -- photos: a filter, the shutter (with a timer), the ones taken -------------------------------
  const photoState = { filters: [], filter: "none", items: [], count: 0, busy: false, countdown: null, open: null };
  try { photoState.filter = localStorage.getItem("photoFilter") || "none"; } catch (e) { /* private mode */ }
  const cssOf = (f) => (f && f.css ? f.css.map(([fn, v]) => (fn === "hue-rotate" ? `${fn}(${v}deg)` : `${fn}(${v})`)).join(" ") : "");
  function currentFilter() {
    return photoState.filters.find((f) => f.id === photoState.filter) || { id: "none", label: "Original", css: [] };
  }
  const photoWhen = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  function renderFilters() {
    $("#photo-filters").innerHTML = photoState.filters.map((f) => `<button type="button" role="radio" data-id="${esc(f.id)}"`
      + ` aria-checked="${f.id === photoState.filter}" class="${f.id === photoState.filter ? "active" : ""}">${esc(f.label)}</button>`).join("");
    camImg.style.filter = cssOf(currentFilter());   // the live view shows the colour filters exactly as the photo will be
  }
  function renderStrip() {
    const strip = $("#photo-strip");
    strip.hidden = !photoState.items.length;
    strip.innerHTML = photoState.items.map((p) => `<button type="button" class="photo-thumb" data-name="${esc(p.name)}"`
      + ` title="${esc(photoWhen(p.ts))}${p.filter !== "none" ? ` · ${esc(p.label)}` : ""}"><img src="${esc(p.thumb)}" alt="" loading="lazy"></button>`).join("")
      + (photoState.count > photoState.items.length ? `<span class="photo-more">+${photoState.count - photoState.items.length} more</span>` : "");
  }
  async function loadPhotos() {
    try {
      const d = await api("/photos?limit=40");
      const wasDrawn = currentFilter().css === null;
      photoState.filters = d.filters || [];
      photoState.items = d.photos || [];
      photoState.count = d.count || 0;
      if (!photoState.filters.some((f) => f.id === photoState.filter)) photoState.filter = "none";
      renderFilters(); renderStrip();
      if (state.tab === "camera" && wasDrawn !== (currentFilter().css === null)) restartStream();
    } catch (e) { /* not there */ }
  }
  $("#photo-filters").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-id]"); if (!b || b.dataset.id === photoState.filter) return;
    const before = currentFilter();
    photoState.filter = b.dataset.id;
    try { localStorage.setItem("photoFilter", photoState.filter); } catch (err) { /* private mode */ }
    renderFilters();
    if (before.css === null || currentFilter().css === null) restartStream();   // a drawn one comes from the Pi
  });
  function setShutter(text, disabled) {
    $("#photo-take-label").textContent = text || "Take a picture";
    $("#photo-take").disabled = !!disabled;
  }
  async function shoot() {
    setShutter("Taking it…", true);
    try {
      const d = await api("/photos", { method: "POST", body: { filter: photoState.filter, rot: Number(camWrap.dataset.rot) || 0 } });
      photoState.items = [d.photo, ...photoState.items].slice(0, 40);
      photoState.count += 1;
      renderStrip();
      toast(d.photo.filter !== "none" ? `Saved — ${d.photo.label}` : "Saved");
    } catch (e) { toast(e.message); }
    photoState.busy = false;
    setShutter();
  }
  $("#photo-take").addEventListener("click", () => {
    if (photoState.countdown) {                                     // tapped during the countdown: don't
      clearInterval(photoState.countdown); photoState.countdown = null; photoState.busy = false;
      setShutter(); toast("Not taken");
      return;
    }
    if (photoState.busy) return;
    photoState.busy = true;
    let left = Number($("#photo-timer").value) || 0;
    if (!left) { shoot(); return; }
    setShutter(`${left}… tap to stop`);
    photoState.countdown = setInterval(() => {
      left -= 1;
      if (left > 0) { setShutter(`${left}… tap to stop`); return; }
      clearInterval(photoState.countdown); photoState.countdown = null;
      shoot();
    }, 1000);
  });
  function openPhoto(name) {
    const i = photoState.items.findIndex((x) => x.name === name);
    if (i < 0) return;
    const p = photoState.items[i];
    photoState.open = p;
    $("#photo-big").src = p.url;
    $("#photo-meta").textContent = [photoWhen(p.ts), p.filter !== "none" ? p.label : "", p.w ? `${p.w}×${p.h}` : "",
      p.bytes ? `${Math.max(1, Math.round(p.bytes / 1024))} KB` : ""].filter(Boolean).join(" · ");
    $("#photo-prev").hidden = i === 0;
    $("#photo-next").hidden = i >= photoState.items.length - 1;
    if ($("#photo-sheet").hidden) openSheet("photo-sheet");
  }
  const openAt = (step) => {
    const i = photoState.items.findIndex((x) => x === photoState.open);
    const p = photoState.items[i + step];
    if (p) openPhoto(p.name);
  };
  const photoPath = (tail = "") => `/photos/${encodeURIComponent(photoState.open.name)}${tail}`;
  $("#photo-strip").addEventListener("click", (e) => { const b = e.target.closest(".photo-thumb"); if (b) openPhoto(b.dataset.name); });
  $("#photo-prev").addEventListener("click", () => openAt(-1));
  $("#photo-next").addEventListener("click", () => openAt(1));
  $("#photo-close").addEventListener("click", closeSheets);
  $("#photo-download").addEventListener("click", () => { if (photoState.open) window.location.href = `${photoState.open.url}?download=1`; });
  $("#photo-show").addEventListener("click", async () => {
    try {
      await api(photoPath("/show"), { method: "POST", body: {} });
      toast("It's on the screen");
      await pollStatus(); updateButtons();
      setTimeout(refreshPreview, 1200);
    } catch (e) { toast(e.message); }
  });
  $("#photo-send").addEventListener("click", async () => {
    const btn = $("#photo-send");
    btn.disabled = true;
    try { await api(photoPath("/send"), { method: "POST", body: {} }); toast("Sent to Telegram"); } catch (e) { toast(e.message); }
    btn.disabled = false;
  });
  $("#photo-delete").addEventListener("click", async () => {
    if (!photoState.open || !confirm("Delete this photo?")) return;
    try {
      const d = await api(photoPath(), { method: "DELETE" });
      photoState.items = d.photos || []; photoState.count = d.count || 0;
      renderStrip(); closeSheets(); toast("Deleted");
    } catch (e) { toast(e.message); }
  });
  document.addEventListener("keydown", (e) => {                   // the photo sheet: arrows go through them
    if ($("#photo-sheet").hidden) return;
    if (e.key === "ArrowLeft") { e.preventDefault(); openAt(-1); }
    else if (e.key === "ArrowRight") { e.preventDefault(); openAt(1); }
    else if (e.key === "Escape") closeSheets();
  });

  // -- the little friend: on or off, its face, what it last said ----------------------------------
  let buddyBusy = false;
  const agoText = (sec) => (sec < 60 ? "just now" : sec < 3600 ? `${Math.round(sec / 60)} min ago` : `${Math.round(sec / 3600)} h ago`);
  const SEES = { model: "the vision model", basic: "OpenCV on the Pi", movement: "movement only (no OpenCV, no model)" };
  function showBuddy(d) {
    if (!buddyBusy) $("#buddy-on").checked = !!d.enabled;
    $("#buddy-face").textContent = d.face || "(-__-)";
    $("#buddy-caption").textContent = d.caption || "";
    $("#buddy-said").textContent = d.line && d.line_ago != null && d.line_ago < 3600 ? `"${d.line}" · ${agoText(d.line_ago)}` : "";
    $("#buddy-group").classList.toggle("on", !!d.enabled);
    const note = d.enabled ? `Sees with ${SEES[d.sees] || d.sees}` + (d.error ? ` · ${d.error}` : "")
      + (d.why && /resting/.test(d.why) ? ` · ${d.why}` : "")
      : "Looks around by itself, says hi, is surprised by what's new. Settings → Friend.";
    $("#buddy-note").textContent = note;
    const pane = $("#buddy-pane-note");
    if (pane) pane.textContent = d.enabled ? `${d.face}  ${d.caption}` + (d.line ? ` — last said "${d.line}"` : "") + ` · sees with ${SEES[d.sees] || d.sees}` : "";
  }
  async function loadBuddy() {
    try { showBuddy(await api("/buddy")); } catch (e) { /* not there */ }
  }
  $("#buddy-on").addEventListener("change", async (e) => {
    buddyBusy = true;
    e.target.disabled = true;
    try {
      const d = await api("/buddy", { method: "POST", body: { on: e.target.checked } });
      buddyBusy = false;
      showBuddy(d);
      await pollStatus();
    } catch (err) {
      buddyBusy = false;
      e.target.checked = !e.target.checked;
      toast(err.message);
    }
    e.target.disabled = false;
  });
  $("#buddy-show").addEventListener("click", async () => {
    try {
      await api("/mode", { method: "POST", body: { mode: "buddy" } });
      await pollStatus(); updateButtons();
      setTimeout(refreshPreview, 900);
    } catch (e) { toast(e.message); }
  });

  // The label over the picture: what the camera is and what it is sending, for a few seconds
  // when the tab opens or the camera changes, then it fades out of the way. A problem
  // (paused, stalled, no camera) stays up until it's over. A tap on the picture shows it again.
  const BADGE_FOR = 4000;
  let badgeKey = "", badgeText = "", badgeKeep = false, badgeTimer = null;
  function showBadge(text, key, keep, again) {
    const b = $("#cam-badge");
    badgeText = text;
    b.textContent = text;
    if (key === badgeKey && keep === badgeKeep && !again) return;   // the same thing (a new fps figure): stays as it is
    badgeKey = key; badgeKeep = keep;
    b.classList.remove("faded");
    clearTimeout(badgeTimer);
    badgeTimer = keep ? null : setTimeout(() => b.classList.add("faded"), BADGE_FOR);
  }
  camImg.addEventListener("click", () => { if (badgeText && !badgeKeep) showBadge(badgeText, badgeKey, false, true); });

  async function pollCamera() {
    if (ptzState.on && !ptzState.held && !steer.dragging) loadPtz();
    loadBuddy();
    try {
      const st = await api("/camera/status");
      // on the test pattern, say why the real cameras didn't answer; on a real one, what it is sending and how fast
      const rate = st.running && st.fps ? ` · ${st.fps} fps` : "";
      const again = st.restarts ? ` · reopened ${st.restarts}×` : "";
      const problem = st.paused_for ? `Paused for ${st.paused_for} s more (Settings → Sound)`
        : (st.error && !st.running) ? st.error
        : st.stalled ? `No picture for ${Math.round(st.age)} s — opening the camera again…`
        : (st.id === "mock" && st.error) ? `${st.source} — ${st.error}`
        : !st.running ? (st.source ? `${st.source} · starting…` : "starting…") : "";
      if (problem) showBadge(problem, "problem", true);
      else showBadge(st.source + (st.format ? ` · ${st.format}` : "") + rate + again, `${st.id}|${st.format}`, false);
      // a camera with two lenses sends both views side by side: which to show, and swapped
      $("#cam-lenses-row").hidden = !st.stereo;
      $("#cam-swap-row").hidden = !st.stereo;
      // the camera gave up (or stopped) while this page watched: ask for it again, now and then
      if (state.tab === "camera" && !st.running && Date.now() - camStreamAt > 10000) restartStream();
    } catch (e) { /* ignore */ }
  }
  new ResizeObserver(() => { if (state.tab === "camera") applyCamShape(); }).observe(camWrap);
  // one button for the screen: saves immediately and the panel follows on its next frame
  $("#cam-screen-rotate").addEventListener("click", async () => {
    const next = (Number(getSetting("camera.rotation")) + 90) % 360;
    try {
      await api("/settings", { method: "PUT", body: { camera: { rotation: next } } });
      delete state.draft["camera.rotation"];
      await pollStatus();
      toast(`Screen rotated ${next}°`);
      setTimeout(refreshPreview, 1500);
    } catch (e) { toast(e.message); }
  });
  // a camera with two lenses: which view, and the two swapped — saved at once, like the rotation,
  // since the Pi rearranges the picture and the live view shows it straight away
  async function saveLenses(key, value) {
    try {
      await api("/settings", { method: "PUT", body: { camera: { [key]: value } } });
      delete state.draft[`camera.${key}`];
      await pollStatus();
    } catch (e) { toast(e.message); }
  }
  $('[data-setting="camera.swap_lenses"]').addEventListener("change", (e) => saveLenses("swap_lenses", e.target.checked));
  $('[data-setting="camera.lens"]').addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) saveLenses("lens", b.dataset.value);
  });
  camImg.addEventListener("error", () => {
    if (state.tab === "camera" && camImg.getAttribute("src")) setTimeout(() => { if (state.tab === "camera") restartStream(); }, 2000);
  });

  // -- watchlist ------------------------------------------------------------------------

  let watch = [];
  // a price in its own money: $ for a coin, the exchange's currency for a share (£, €, ¥…)
  const fmtPrice = (p, money = "$") => {
    if (p == null) return "";
    const d = p >= 10000 ? 0 : p >= 1 ? 2 : Math.min(8, -Math.floor(Math.log10(p)) + 2);
    return money + p.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  };

  async function loadWatchlist() {
    try { watch = (await api("/watchlist")).items; renderWatch(); }
    catch (e) { toast(e.message); }
  }

  // -- your miners: Duino-Coin, Verus ---------------------------------------------------------
  let miningTimer = null;
  const POOL_WHO = { duco: "duco.username", verus: "verus.wallet" };
  const POOL_NAMES = { duco: "Duino-Coin", verus: "Verus" };
  // a pool you don't mine stays out of the page (setup.sh asks; Settings → You has the switches)
  function syncMining() {
    const show = { duco: getSetting("duco.show") !== false, verus: getSetting("verus.show") !== false };
    for (const el of $$("#mining-more .pool")) el.hidden = !show[el.dataset.pool];
    $("#mining-more .pool-split").hidden = !(show.duco && show.verus);
    $("#mining-group").hidden = !(show.duco || show.verus);
    const off = Object.entries(POOL_NAMES).filter(([p]) => show[p]).map(([, n]) => n).join(", ");
    const sum = $("#mining-sum");
    sum.dataset.off = off ? `${off} — off` : "";
    if (!sum.textContent || sum.textContent.endsWith("— off")) sum.textContent = sum.dataset.off;
  }
  // the miners fold away under one heading; how you left it is remembered
  const miningMore = $("#mining-more");
  try { miningMore.open = localStorage.getItem("pieink.miningOpen") === "1"; } catch (e) { /* no storage */ }
  miningMore.addEventListener("toggle", () => { try { localStorage.setItem("pieink.miningOpen", miningMore.open ? "1" : ""); } catch (e) { /* ignore */ } });
  async function loadMining(refresh = false) {
    clearInterval(miningTimer); miningTimer = null;
    if (state.tab !== "crypto") return;
    try {
      const d = await api(`/mining${refresh ? "?refresh=1" : ""}`);
      let any = false;
      const sum = [];
      for (const [pool, st] of Object.entries(d.pools || {})) {
        const note = $(`.mining-note[data-pool="${pool}"]`), list = $(`.mining-miners[data-pool="${pool}"]`);
        if (!note) continue;
        note.classList.remove("warn");
        if (!st.enabled) {
          note.textContent = getSetting(POOL_WHO[pool]) || hasDraft([pool]) ? "Off — turn it on and press the button above to save." : "Off.";
          list.hidden = true; continue;
        }
        any = true;
        const m = st.data;
        sum.push(`${POOL_NAMES[pool] || pool} ${m ? (m.hashrate > 0 ? m.hashrate_text : "nothing mining") : "…"}${m && m.alert ? " ⚠" : ""}`);
        if (!m) { note.textContent = st.error ? `${st.who}: ${st.error}` : `Looking ${st.who} up…`; list.hidden = true; continue; }
        const dec = m.decimals || 3;
        const bits = [`${m.who}: ${m.balance.toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec })} ${m.symbol} ${m.balance_label}`
          + (m.price ? ` ($${(m.balance * m.price).toFixed(2)})` : "")];
        if (m.paid != null) bits.push(`${m.paid.toLocaleString(undefined, { maximumFractionDigits: dec })} paid out`);
        bits.push(m.hashrate > 0 ? `${m.hashrate_text} from ${m.workers} worker${m.workers === 1 ? "" : "s"}` : "nothing mining");
        if (m.per_day != null) bits.push(`~${m.per_day.toFixed(Math.min(dec, 3))} ${m.symbol} a day`);
        if (m.price) bits.push(`$${m.price.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")} each`);
        if (m.luck) bits.push(`luck ${m.luck}`);
        if (m.net_hashrate) bits.push(`network ${m.net_hashrate}`);
        if (st.fetched_ago != null) bits.push(`${st.fetched_ago < 90 ? `${st.fetched_ago}s` : `${Math.round(st.fetched_ago / 60)} min`} ago`);
        note.textContent = bits.join(" · ") + (m.alert ? ` — UNDER THE LINE: ${m.alert.text}` : "");
        note.classList.toggle("warn", !!m.alert);
        list.hidden = !m.miners.length;
        list.innerHTML = m.miners.map((w) => {
          const sub = [w.software, w.pool !== "luckpool" ? w.pool : "", w.accepted ? `${w.accepted} accepted` : "", w.rejected ? `${w.rejected} rejected` : "",
            w.shares != null && pool === "verus" ? `${Math.round(w.shares)} shares` : "", w.status && w.status !== "on" ? w.status : ""].filter(Boolean).join(" · ");
          return `<li class="lv-${m.alert ? "alert" : "note"}"><time>${esc(hashText(w.hashrate))}</time><span>${esc(w.identifier)}${sub ? `<small>${esc(sub)}</small>` : ""}</span></li>`;
        }).join("");
      }
      $("#mining-sum").textContent = sum.length ? sum.join(" · ") : ($("#mining-sum").dataset.off || "Duino-Coin, Verus — off");
      if (any) miningTimer = setInterval(loadMining, 30000);
    } catch (e) { for (const n of $$(".mining-note")) n.textContent = e.message; }
  }
  for (const btn of $$(".mining-check")) btn.addEventListener("click", async () => {
    const pool = btn.dataset.pool, who = (getSetting(POOL_WHO[pool]) || "").trim();
    if (!who) { toast(pool === "duco" ? "Type your Duino-Coin username first" : "Paste the wallet address first"); return; }
    btn.disabled = true;
    try { const d = await api("/mining/check", { method: "POST", body: { pool, who } }); toast(d.words); }
    catch (e) { toast(e.message); }
    btn.disabled = false;
  });
  function hashText(h) {
    h = Number(h) || 0;
    if (h >= 1e9) return `${(h / 1e9).toFixed(2)} GH/s`;
    if (h >= 1e6) return `${(h / 1e6).toFixed(2)} MH/s`;
    if (h >= 1e3) return `${(h / 1e3).toFixed(2)} KH/s`;
    return h < 100 ? `${h.toFixed(1)} H/s` : `${h.toFixed(0)} H/s`;
  }
  const fmtMoney = (v) => {
    if (v == null) return "";
    const a = Math.abs(v), d = a >= 10000 ? 0 : a >= 1 ? 2 : 4;
    return (v < 0 ? "−" : "") + "$" + a.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
  };
  const fmtSigned = (v) => (v >= 0 ? "+" : "−") + fmtMoney(Math.abs(v));
  const fmtPct = (v) => (v == null ? "" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`);
  function worthLine(i) {
    // what this amount is worth, and what today did to it (a share in another currency: in that currency)
    if (!(i.amount > 0) || i.price == null) return i.amount > 0 ? "no price yet" : "";
    const v = i.amount * i.price, ch = i.change_1d, money = i.money || "$";
    const day = ch == null ? null : v - v / (1 + ch / 100);
    const worth = money === "$" ? fmtMoney(v) : fmtPrice(v, money);
    const moved = day == null ? "" : money === "$" ? fmtSigned(day) : (day >= 0 ? "+" : "−") + fmtPrice(Math.abs(day), money);
    return `${worth}${day == null ? "" : `<small class="${day >= 0 ? "up" : "down"}">${moved} today</small>`}`;
  }
  function renderWatch() {
    const on = watch.filter((i) => i.show).length;
    const shares = watch.filter((i) => i.kind === "stock").length;
    $("#watch-hint").textContent = on ? `${on} in rotation, ${watch.length - on} paused.${shares ? " Share prices are Yahoo's, a few minutes behind." : ""}`
      : "Nothing ticked, so Bitcoin will show.";
    $("#watch-list").innerHTML = watch.map((i) => {
      const ch = i.change_1d, stock = i.kind === "stock";
      const chTxt = ch == null ? "" : `<span class="${ch >= 0 ? "up" : "down"}">${ch >= 0 ? "+" : ""}${ch.toFixed(2)}%</span>`;
      const tag = stock ? `<span class="tag" title="A share${i.exchange ? ` on ${esc(i.exchange)}` : ""}">${esc(i.exchange || "share")}</span>` : "";
      return `<li class="${i.show ? "" : "off"}" data-id="${esc(i.id)}">
        <span class="name">${esc(i.name)}<span class="sym">${esc(i.symbol)}${tag}</span></span>
        <span class="price"><b>${fmtPrice(i.price, i.money || "$")}</b>${chTxt}</span>
        <input type="checkbox" ${i.show ? "checked" : ""} aria-label="Show ${esc(i.name)}">
        <button class="remove" aria-label="Remove ${esc(i.name)}">×</button>
        <label class="hold"><span>You hold</span>
          <input type="number" class="hold-amount" min="0" step="any" inputmode="decimal" placeholder="0" value="${i.amount > 0 ? esc(i.amount) : ""}" aria-label="How much ${esc(i.name)} you hold">
          <span class="sym">${stock ? "shares" : esc(i.symbol)}</span><b>${worthLine(i)}</b>
        </label>
      </li>`;
    }).join("");
  }
  $("#watch-list").addEventListener("change", async (e) => {
    const li = e.target.closest("li"); if (!li || e.target.type !== "checkbox") return;
    try { watch = (await api(`/watchlist/${encodeURIComponent(li.dataset.id)}`, { method: "PUT", body: { show: e.target.checked } })).items; renderWatch(); }
    catch (err) { toast(err.message); }
  });
  // how much you hold: saved as you type (a moment after, each coin on its own), the total follows
  const amountTimers = {};
  async function saveAmount(id, amount, input) {
    try {
      const items = (await api(`/watchlist/${encodeURIComponent(id)}`, { method: "PUT", body: { amount } })).items;
      watch = items;
      const i = items.find((x) => x.id === id), b = input.closest(".hold")?.querySelector("b");
      if (i && b) b.innerHTML = worthLine(i);
      loadHoldings();
    } catch (err) { toast(err.message); }
  }
  $("#watch-list").addEventListener("input", (e) => {
    const input = e.target.closest(".hold-amount"), li = e.target.closest("li");
    if (!input || !li) return;
    const id = li.dataset.id;
    clearTimeout(amountTimers[id]);
    amountTimers[id] = setTimeout(() => saveAmount(id, input.value === "" ? 0 : Number(input.value), input), 600);
  });

  // -- what it all adds up to ------------------------------------------------------------------
  let holdingsTimer = null;
  async function loadHoldings() {
    clearInterval(holdingsTimer); holdingsTimer = null;
    if (state.tab !== "crypto") return;
    try {
      const d = await api("/holdings");
      const v = d.value || {};
      const any = !!(v.items && v.items.length);
      $("#worth").hidden = !any;
      $("#worth-empty").hidden = any;
      if (any) {
        $("#worth-total").textContent = fmtMoney(v.total);
        const day = $("#worth-day");
        day.textContent = v.day_pct == null ? "no change figure yet" : `${fmtSigned(v.day)} today (${fmtPct(v.day_pct)})`;
        day.className = v.day_pct == null ? "" : v.day >= 0 ? "up" : "down";
        $("#worth-week").textContent = d.week ? `${fmtSigned(d.week.change)} on a week ago (${fmtPct(d.week.pct)})`
          : d.readings > 1 ? `${d.readings} readings so far — a week's graph builds up from here` : "";
        const bits = [];
        if (d.peak) bits.push(`Peak ${fmtMoney(d.peak.total)} on ${new Date(d.peak.ts * 1000).toLocaleDateString([], { month: "short", day: "numeric" })}`);
        if (d.line) bits.push(d.over ? `Past the ${fmtMoney(d.line)} line` : `${Math.round(d.progress)}% of the way to ${fmtMoney(d.line)}`);
        if (d.move) bits.push(`${d.move.kind === "crash" ? "A crash" : "A rally"} today`);
        if (d.slump) bits.push(`${Math.round(d.slump.pct)}% below the peak`);
        if (v.unknown && v.unknown.length) bits.push(`no price yet for ${v.unknown.join(", ")}`);
        if (v.foreign && v.foreign.length) bits.push(`${v.foreign.join(", ")} not in the total (another currency)`);
        $("#worth-note").textContent = bits.join(" · ");
        drawWorth(d.history || [], d.line);
      }
      holdingsTimer = setInterval(loadHoldings, 30000);
    } catch (e) { $("#worth-note").textContent = e.message; }
  }
  function drawWorth(hist, line) {
    const svg = $("#worth-graph");
    svg.toggleAttribute("hidden", hist.length < 2);          // (an SVG has no .hidden of its own)
    if (hist.length < 2) return;
    const vals = hist.map((h) => h[1]);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (line && line > hi && line < hi * 1.5) hi = line;                    // the line in sight: draw it
    if (hi - lo < hi * 0.002) { lo -= hi * 0.001 || 1; hi += hi * 0.001 || 1; }
    const W = 300, H = 60, pad = 3;
    const x = (i) => (i / (hist.length - 1)) * W;
    const y = (v) => H - pad - ((v - lo) / (hi - lo)) * (H - 2 * pad);
    const pts = vals.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const mark = line && line <= hi && line >= lo ? `<line class="mark" x1="0" x2="${W}" y1="${y(line).toFixed(1)}" y2="${y(line).toFixed(1)}"/>` : "";
    svg.innerHTML = `<polygon class="fill" points="0,${H} ${pts} ${W},${H}"/><polyline class="line" points="${pts}"/>${mark}`;
  }
  for (const btn of $$(".holdings-try")) btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      const d = await api("/holdings/try", { method: "POST", body: { kind: btn.dataset.kind } });
      toast(d.model ? "On its way — the bot is finding its words" : d.text);
    } catch (e) { toast(e.message); }
    btn.disabled = false;
  });
  $("#watch-list").addEventListener("click", async (e) => {
    const btn = e.target.closest(".remove"); if (!btn) return;
    const li = btn.closest("li");
    try { watch = (await api(`/watchlist/${encodeURIComponent(li.dataset.id)}`, { method: "DELETE" })).items; renderWatch(); }
    catch (err) { toast(err.message); }
  });

  const results = $("#watch-results");
  const search = debounce(async (q) => {
    if (!q) { results.hidden = true; return; }
    try {
      const { results: hits, note } = await api(`/coins/search?q=${encodeURIComponent(q)}`);
      // coins carry their CoinGecko rank; shares the exchange they trade on
      results.innerHTML = (hits.length
        ? hits.map((h) => `<li data-id="${esc(h.id)}" data-symbol="${esc(h.symbol)}" data-kind="${esc(h.kind || "crypto")}">
            <span class="sym">${esc(h.symbol)}</span><span class="name">${esc(h.name)}</span><span class="kind${h.kind === "stock" ? " ex" : ""}">${h.kind === "stock" ? esc(h.exchange || "share") : h.rank ? "#" + h.rank : "coin"}</span></li>`).join("")
        : `<li class="empty">Nothing found for “${esc(q)}”</li>`) + (note ? `<li class="empty">${esc(note)}</li>` : "");
      results.hidden = false;
    } catch (e) { results.innerHTML = `<li class="empty">${esc(e.message)}</li>`; results.hidden = false; }
  }, 300);
  $("#watch-search").addEventListener("input", (e) => search(e.target.value.trim()));
  $("#watch-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { const first = $("li[data-id]", results); if (first) first.click(); }
    if (e.key === "Escape") results.hidden = true;
  });
  results.addEventListener("click", async (e) => {
    const li = e.target.closest("li[data-id]"); if (!li) return;
    results.hidden = true;
    $("#watch-search").value = "";
    try {
      watch = (await api("/watchlist", { method: "POST", body: { id: li.dataset.id, kind: li.dataset.kind || "crypto" } })).items;
      renderWatch();
      toast(`Added ${li.dataset.symbol}`);
    } catch (err) { toast(err.message); }
  });
  document.addEventListener("click", (e) => { if (!e.target.closest(".search-wrap")) results.hidden = true; });

  // -- system -----------------------------------------------------------------------

  async function pollSystem() {
    if (state.tab !== "system" && !WIDE.matches) return;
    try {
      const s = await api("/system");
      const h = Math.floor(s.uptime / 3600), m = Math.floor((s.uptime % 3600) / 60);
      const up = h >= 48 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h ${m}m`;
      const temp = s.temp == null ? "—" : `${s.temp}°C`;
      $("#st-cpu").textContent = s.mhz ? `${s.cpu}% · ${(s.mhz / 1000).toFixed(1)} GHz` : `${s.cpu}%`;
      $("#st-mem").textContent = `${s.memory}%`;
      $("#st-temp").textContent = temp;
      $("#st-disk").textContent = `${Math.round(s.disk.used_gb)} of ${Math.round(s.disk.total_gb)} GB`;
      $("#st-ip").textContent = s.ip;
      $("#st-up").textContent = up;
      $("#sb-cpu").textContent = `${s.cpu}%`;
      $("#sb-temp").textContent = temp;
      $("#sb-mem").textContent = `${s.memory}%`;
      $("#sb-disk").textContent = `${Math.round(s.disk.used_gb)} / ${Math.round(s.disk.total_gb)} GB`;
      $("#sb-up").textContent = up;
      $("#sb-ip").textContent = s.ip;
    } catch (e) { /* ignore */ }
  }
  WIDE.addEventListener("change", (e) => { if (e.matches) { pollSystem(); pollNow(); } });

  // -- the side column: what is going on, the screens, the second screen ----------------------
  const NOW_EVERY = 15000;
  function nowRow(label, text, cls = "") {
    return `<li class="${cls}"><span>${esc(label)}</span><b>${esc(text)}</b></li>`;
  }
  async function pollNow() {
    try {
      const d = await api("/now");
      const rows = [];
      if (d.speech) rows.push(nowRow("The bot", d.speech, "long"));
      if (d.weather) rows.push(nowRow("Weather", `${d.weather.temp}°${d.weather.units} · ${d.weather.text}` + (d.weather.place ? ` · ${d.weather.place}` : "")));
      if (d.music) rows.push(nowRow(d.music.state === "playing" ? "Playing" : "Paused", d.music.title));
      if (d.watcher) rows.push(nowRow("Watching", d.watcher.state === "something" ? "something moved just now"
        : d.watcher.seen_ago != null ? `quiet for ${d.watcher.seen_ago < 90 ? `${d.watcher.seen_ago}s` : `${Math.round(d.watcher.seen_ago / 60)} min`}` : "all quiet"));
      if (d.room) rows.push(nowRow("The room", d.room));
      if (d.tv) rows.push(nowRow("TV", d.tv));
      for (const p of d.mining || []) rows.push(nowRow(p.symbol, p.hashrate > 0 ? `${p.hashrate_text} · ${p.workers} worker${p.workers === 1 ? "" : "s"}${p.alert ? " · under the line" : ""}` : "nothing mining", p.alert || p.hashrate <= 0 ? "warn" : ""));
      if (d.holdings) rows.push(nowRow(d.holdings.whose || "Your crypto", fmtMoney(d.holdings.total) + (d.holdings.day_pct != null ? ` · ${fmtPct(d.holdings.day_pct)} today` : "")
        + (d.holdings.move ? ` · a ${d.holdings.move}` : ""), d.holdings.move === "crash" ? "warn" : ""));
      if (d.reminder) rows.push(nowRow("Reminder", d.reminder.words + (d.reminder.more ? ` · +${d.reminder.more} more` : "")));
      if (d.rooms) rows.push(nowRow(d.rooms.count === 1 ? "Other room" : "Other rooms", d.rooms.labels.map((l, i) => l + (d.rooms.showing[i] ? ` (${d.rooms.showing[i]})` : "")).join(", ")
        + (d.rooms.count > d.rooms.labels.length ? ` +${d.rooms.count - d.rooms.labels.length}` : "") + " · the same bot, there too", "long"));
      if (d.pitato) rows.push(nowRow(d.pitato.excited ? "Pietato ★" : "Pietato", `${d.pitato.name}${d.pitato.here ? " · on this Pi" : ""}${d.pitato.more ? ` +${d.pitato.more}` : ""} · ${d.pitato.ago < 90 ? "just now" : d.pitato.ago < 5400 ? `${Math.round(d.pitato.ago / 60)} min ago` : `${Math.round(d.pitato.ago / 3600)} h ago`}`
        + (d.pitato.activity ? ` · ${d.pitato.activity}` : d.pitato.asks ? " · asks for the weather and prices" : "") + (d.pitato.cameras && d.pitato.cameras.length ? ` · camera ${d.pitato.cameras.join(", ")}` : ""), "long" + (d.pitato.excited ? " good" : "")));
      if (d.snake) rows.push(nowRow("Snake", d.snake.error ? d.snake.error : `score ${d.snake.score}` + (d.snake.best ? ` · best ${d.snake.best}` : "") + ` · ${d.snake.model}${d.snake.bored ? " · bored" : ""}`, d.snake.error ? "warn" : ""));
      if (d.event) rows.push(nowRow("Seen", `${d.event.text} · ${d.event.ago < 90 ? `${d.event.ago}s` : d.event.ago < 5400 ? `${Math.round(d.event.ago / 60)} min` : `${Math.round(d.event.ago / 3600)} h`} ago`, "long"));
      if (d.agent) rows.push(nowRow("Agent", d.agent.busy ? "checking now…" : d.agent.error ? d.agent.error : d.agent.thought
        ? `${d.agent.thought}${d.agent.ago != null ? ` · ${Math.round(d.agent.ago / 60)} min ago` : ""}` : "hasn't checked yet", "long"));
      $("#now-list").innerHTML = rows.length ? rows.join("")
        : `<li class="empty">Quiet. The panel is showing ${(MODE_LABELS[d.mode] || d.mode || "nothing").toLowerCase()}.</li>`;
      const lcdCard = $("#lcd-card");
      lcdCard.hidden = !d.lcd;
      if (d.lcd && WIDE.matches) {                 // its picture is only on show in the rail
        const sel = $("#sb-lcd-screen");
        if (!sel.options.length) {
          const L = await api("/lcd");
          for (const [value, label] of L.screens) sel.append(new Option(label, value));
        }
        if (document.activeElement !== sel) sel.value = d.lcd.screen || "system";
        const img = $("#sb-lcd");
        try {
          const res = await fetch(`/api/lcd/preview.png?t=${Date.now()}`);
          if (res.ok) { const url = URL.createObjectURL(await res.blob()); img.onload = () => { if (img.dataset.url) URL.revokeObjectURL(img.dataset.url); img.dataset.url = url; }; img.src = url; }
        } catch (e) { /* the preview is a nicety */ }
      }
    } catch (e) { /* the column is a nicety; nothing to do */ }
  }
  $("#sb-lcd-screen").addEventListener("change", async (e) => {
    try {
      await api("/settings", { method: "PUT", body: { lcd: { screen: e.target.value } } });
      if (state.settings && state.settings.lcd) state.settings.lcd.screen = e.target.value;
      setTimeout(pollNow, 600);
    } catch (err) { toast(err.message); }
  });
  // one dropdown puts any screen on the panel; it follows what is on it
  function renderScreenGrid() {
    const pick = $("#screen-pick");
    const modes = (state.modes || []).filter((m) => m.id !== "postcard");
    const items = modes.map((m) => [m.id, m.label]);
    if (!modes.some((m) => m.id === "off")) items.push(["off", "Off"]);
    const want = items.map(([id]) => id).join("|");
    if (pick.dataset.items !== want) {
      pick.innerHTML = items.map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join("");
      pick.dataset.items = want;
    }
    if (document.activeElement !== pick && items.some(([id]) => id === state.mode)) pick.value = state.mode;
  }
  $("#screen-pick").addEventListener("change", (e) => sendMode(e.target.value));

  // -- OS update (apt) -----------------------------------------------------------------

  let updTimer = null;
  function renderUpdate(u) {
    const log = $("#upd-log");
    log.hidden = !u.log.length; log.textContent = u.log.join("\n"); log.scrollTop = log.scrollHeight;
    $("#btn-update").disabled = u.running;
    let hint = "";
    if (u.running) hint = "Updating the Pi… this can take a while. Leave the page open or come back later.";
    else if (u.ok === true) hint = "Update finished." + (u.reboot ? " A reboot is needed to finish it." : "");
    else if (u.ok === false) hint = "Update failed — see the log.";
    $("#upd-hint").textContent = hint;
    if (u.running && !updTimer) updTimer = setInterval(pollUpdate, 3000);
    if (!u.running && updTimer) { clearInterval(updTimer); updTimer = null; }
  }
  async function pollUpdate() { try { renderUpdate(await api("/system/update")); } catch (e) { /* ignore */ } }
  $("#btn-update").addEventListener("click", async () => {
    if (!confirm("Run apt update and upgrade on the Pi? It can take several minutes.")) return;
    try { await api("/system/update", { method: "POST" }); pollUpdate(); } catch (e) { toast(e.message); }
  });

  async function power(action) {
    if (!confirm(`${action === "reboot" ? "Reboot" : "Shut down"} the Pi?`)) return;
    try { await api("/power", { method: "POST", body: { action } }); toast(action === "reboot" ? "Rebooting…" : "Shutting down…"); }
    catch (e) { toast(e.message); }
  }

  // -- theme --------------------------------------------------------------------

  function applyTheme(dark) {
    document.documentElement.dataset.theme = dark ? "dark" : "";
    localStorage.setItem("theme", dark ? "dark" : "light");
  }
  const savedTheme = localStorage.getItem("theme");
  applyTheme(savedTheme ? savedTheme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches);

  // -- wire up ------------------------------------------------------------------------

  bindInputs();
  $("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) showTab(b.dataset.tab); });
  $("#action").addEventListener("click", () => {
    if (state.tab === "draw") sendDrawing(); else sendMode(TAB_MODE[state.tab]);
  });
  $("#keys").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-key]"); if (!b) return;
    e.preventDefault();
    try { await api("/button", { method: "POST", body: { key: Number(b.dataset.key) } }); await pollStatus(); setTimeout(refreshPreview, 700); }
    catch (err) { toast(err.message); }
  });
  $("#btn-rotate").addEventListener("click", async () => {
    // an e-ink turns round (0/180); a colour panel as the main screen has four ways (the LCD's landscape too)
    const step = state.panel.colour && !/gpicase/.test(state.panel.driver || "") ? 90 : 180;   // (the GPi Case's screen: upside down at most)
    const next = ((Number(getSetting("display.rotation")) || 0) + step) % 360;
    try {
      await api("/settings", { method: "PUT", body: { display: { rotation: next } } });
      delete state.draft["display.rotation"];
      await pollStatus(); renderInputs(); updateButtons();
      flashPanel(); toast(step === 180 ? (next === 180 ? "Turned upside down" : "The right way up") : `Turned to ${next}°`);
      setTimeout(refreshPreview, 900);
    } catch (e) { toast(e.message); }
  });
  $("#btn-refresh").addEventListener("click", async () => { await api("/refresh", { method: "POST" }); flashPanel(); toast("Refreshing"); });
  $("#btn-off").addEventListener("click", () => sendMode("off"));
  $("#btn-reboot").addEventListener("click", () => power("reboot"));
  $("#btn-shutdown").addEventListener("click", () => power("shutdown"));
  function renderTheme() {
    const dark = document.documentElement.dataset.theme === "dark";
    for (const b of $$("#theme-pick button")) b.classList.toggle("active", (b.dataset.value === "dark") === dark);
  }
  $("#theme-pick").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-value]"); if (!b) return;
    applyTheme(b.dataset.value === "dark");
    renderTheme();
  });
  renderTheme();

  pollStatus().then(() => {
    // the tab for what's on the panel
    const tab = Object.keys(TAB_MODE).find((t) => TAB_MODE[t] === state.mode) || "clock";
    showTab(tab);
  });
  setInterval(pollStatus, 10000);
  setInterval(() => { if (!hasDraft([...tabSections(), "display"])) refreshPreview(); }, 4000);
  pollSystem();
  setInterval(pollSystem, 10000);
  pollNow();
  setInterval(pollNow, NOW_EVERY);
  loadPitatoScreens();
  setInterval(loadPitatoScreens, 20000);
  setInterval(() => { if (state.tab === "read" && ((reading && reading.book && !reading.ready) || jobsWere)) loadBooks(); }, 1500);
})();
