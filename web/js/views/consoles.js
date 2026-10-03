// Vue Consoles : vrais terminaux (ConPTY côté Python, xterm.js ici).
import { h, debounce, throttle } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow, TabStrip, toast } from "../ui.js";

const TITLE_MARK = "boostache-cwd:";

// « black » = fond du terminal : PSReadLine peint le fond de la saisie en noir ANSI
const THEME = {
  background: "#0f0f0f", foreground: "#d6d6d6",
  cursor: "#ececec", cursorAccent: "#0f0f0f",
  selectionBackground: "rgba(255, 255, 255, 0.22)",
  selectionInactiveBackground: "rgba(255, 255, 255, 0.12)",
  black: "#0f0f0f", red: "#ee7d7d", green: "#7cc48f", yellow: "#e7c07b",
  blue: "#82aaff", magenta: "#c3a6ff", cyan: "#7fd1d8", white: "#d6d6d6",
  brightBlack: "#707070", brightRed: "#ff9d9d", brightGreen: "#9fe0ae", brightYellow: "#f6d68f",
  brightBlue: "#a9c4ff", brightMagenta: "#d9c4ff", brightCyan: "#a4e7ed", brightWhite: "#ffffff",
};

const quotePath = (p) => (/\s/.test(p) ? `"${p}"` : p);

export function createConsolesView(ctx, state) {
  const { api, on } = ctx;
  const S = {
    tabs: new Map(),
    order: [],
    active: null,
    shells: state.consoles.shells || {},
    available: state.consoles.available,
  };

  // ── Structure ───────────────────────────────
  const strip = new TabStrip({
    onSelect: select,
    onClose: closeTab,
    onNew: () => newTab(),
    onRename: rename,
    onMenu: tabMenu,
    newLabel: "Nouvelle console",
    placeholder: "Console",
  });
  const cwdPath = h("span", { class: "path" });
  const cwdBtn = h("button", { class: "cwd", type: "button", dataset: { tip: "Changer de dossier (ou déposez un dossier ici)", drop: "cwd" },
    onClick: () => { const t = activeTab(); if (t) pickDir(t); } }, ico("folder", 14), cwdPath);
  const shellBtn = h("button", { class: "shell-badge", type: "button", dataset: { tip: "Shell de cette console" },
    onClick: (ev) => openMenuBelow(shellItems(activeTab()), ev.currentTarget) });
  const ttsBtn = iconButton("volume-2", "Lire la sortie", () => speak(activeTab(), ctx.store.settings.tts_mode_console));
  ttsBtn.dataset.tipIdle = "Lire la sortie (clic droit : mode)";
  ttsBtn.addEventListener("contextmenu", (ev) => ctx.ttsModeMenu(ev, "tts_mode_console",
    [["last", "Lire la dernière sortie"], ["all", "Tout lire"]]));
  const stack = h("div", { class: "term-stack" });

  const el = h("section", {},
    h("header", { class: "view-head" }, strip.el),
    h("div", { class: "term-bar" },
      cwdBtn, shellBtn, h("div", { class: "head-spacer" }),
      ttsBtn,
      iconButton("external-link", "Ouvrir dans un terminal externe", () => { const t = activeTab(); if (t) api.term_open_external(t.slot); }, { kbd: "Ctrl+T" }),
      iconButton("eraser", "Effacer", () => clear(activeTab()))),
    stack);

  if (!S.available) {
    stack.append(h("div", { class: "empty" },
      h("div", { class: "glyph" }, ico("triangle-alert", 22)),
      h("h3", { text: "Terminal indisponible" }),
      h("p", { text: "Installez pywinpty : pip install pywinpty" })));
  }

  // ── Onglets ─────────────────────────────────
  const activeTab = () => S.tabs.get(S.active);

  function makeTab(data) {
    const host = h("div", { class: "term-host", dataset: { drop: "terminal" } });
    stack.append(host);
    const t = {
      slot: data.slot, data, host, term: null, fit: null, ser: null,
      session: 0, starting: false, early: [], exited: false, opening: null,
      outbox: "", sending: false, pendingSize: null, resizing: false,
      enterMarker: null, promptSinceEnter: true,
    };
    t.save = debounce(() => saveBuffer(t), 2500);
    S.tabs.set(t.slot, t);
    return t;
  }

  function renderStrip() {
    strip.render(S.order.map((slot) => {
      const t = S.tabs.get(slot);
      return { id: slot, title: t.data.title, label: t.data.label, dim: t.exited };
    }), S.active);
  }

  function renderBar() {
    const t = activeTab();
    if (!t) return;
    // Conteneur RTL (ellipse à gauche, fin du chemin visible) + contenu isolé en LTR
    cwdPath.replaceChildren(h("bdi", { dir: "ltr", text: t.data.cwd }));
    shellBtn.textContent = S.shells[t.data.shell] || t.data.shell;
    ttsBtn.dataset.speakSource = `console:${t.slot}`;
    ctx.emit("speak-sources-changed");
  }

  async function select(slot) {
    const t = S.tabs.get(slot);
    if (!t) return;
    S.active = slot;
    for (const x of S.tabs.values()) x.host.classList.toggle("active", x === t);
    renderStrip();
    renderBar();
    if (ctx.isActive("consoles")) await open(t);
  }

  async function newTab(afterSlot = null) {
    const data = await api.term_new(afterSlot);
    if (!data) return;
    const t = makeTab(data);
    const idx = afterSlot !== null ? S.order.indexOf(afterSlot) : -1;
    if (idx >= 0) S.order.splice(idx + 1, 0, t.slot);
    else S.order.push(t.slot);
    select(t.slot);
  }

  async function closeTab(slot) {
    const t = S.tabs.get(slot);
    const res = await api.term_close(slot);
    if (!res || !t) return;
    if (res.reset) {
      t.data = res.reset;
      if (t.term) {
        t.term.reset();
        t.exited = false;
        t.session = 0;
        await start(t);
      }
    } else if (res.removed) {
      const idx = S.order.indexOf(slot);
      S.order.splice(idx, 1);
      S.tabs.delete(slot);
      t.save.cancel();
      t.term?.dispose();
      t.host.remove();
      if (S.active === slot) await select(S.order[Math.max(0, idx - 1)]);
    }
    renderStrip();
    renderBar();
  }

  async function rename(slot, label) {
    const data = await api.term_rename(slot, label);
    const t = S.tabs.get(slot);
    if (data && t) t.data = data;
    renderStrip();
  }

  function tabMenu(slot, ev) {
    const t = S.tabs.get(slot);
    openMenu([
      { label: "Nouvelle console", icon: "plus", onSelect: () => newTab(slot) },
      { label: "Renommer…", icon: "pencil", hint: "F2", onSelect: () => strip.rename(slot) },
      "-",
      { label: "Ouvrir dans un terminal externe", icon: "external-link", onSelect: () => api.term_open_external(slot) },
      { label: "Redémarrer le shell", icon: "rotate-ccw", disabled: !t?.term, onSelect: () => restart(t, true) },
      "-",
      { label: "Fermer la console", icon: "x", onSelect: () => closeTab(slot) },
    ], ev.clientX, ev.clientY);
  }

  function shellItems(t) {
    if (!t) return [];
    return [{ section: "Shell" }, ...Object.entries(S.shells).map(([id, label]) => ({
      label, checked: t.data.shell === id,
      onSelect: async () => {
        if (t.data.shell === id) return;
        const data = await api.term_set_shell(t.slot, id);
        if (!data) return;
        t.data = data;
        renderBar();
        if (t.term) restart(t, true);
      },
    }))];
  }

  // ── Terminal ────────────────────────────────
  function fitTerm(t) {
    if (!t.term || !t.host.offsetWidth || !t.host.offsetHeight) return;
    try { t.fit.fit(); } catch { /* conteneur masqué */ }
  }

  /** Crée le terminal au premier affichage de l'onglet, puis démarre le shell. */
  function open(t) {
    if (t.opening) return t.opening.then(() => { fitTerm(t); termFocus(t); });
    t.opening = (async () => {
      const term = new window.Terminal({
        fontFamily: '"JetBrains Mono", "Cascadia Mono", Consolas, monospace',
        fontSize: 13,
        lineHeight: 1.22,
        cursorBlink: true,
        cursorStyle: "bar",
        cursorWidth: 2,
        scrollback: 5000,
        theme: THEME,
        windowsPty: { backend: "conpty", buildNumber: ctx.store.winBuild || 19045 },
        allowProposedApi: true,
      });
      t.fit = new window.FitAddon.FitAddon();
      t.ser = new window.SerializeAddon.SerializeAddon();
      term.loadAddon(t.fit);
      term.loadAddon(t.ser);
      term.loadAddon(new window.WebLinksAddon.WebLinksAddon((_ev, uri) => api.open_url(uri)));
      term.open(t.host);
      t.term = term;
      fitTerm(t);

      const saved = await api.term_buffer(t.slot);
      if (saved) {
        // L'ancien contenu passe dans l'historique : ConPTY suppose un écran vierge
        term.write(`${saved}\x1b[0m\r\n\x1b[2m── session précédente ──\x1b[0m\r\n${"\r\n".repeat(term.rows)}\x1b[H`);
      }

      term.onData((d) => sendInput(t, d));
      term.onBinary((d) => sendInput(t, d));
      term.onResize(({ cols, rows }) => { if (t.session) queueResize(t, cols, rows); });
      term.onTitleChange((title) => onTitle(t, title));
      term.attachCustomKeyEventHandler((ev) => keyHandler(t, ev));
      term.element.addEventListener("contextmenu", (ev) => termMenu(t, ev));
      await start(t);
    })();
    return t.opening.then(() => termFocus(t));
  }

  function termFocus(t) {
    if (S.active === t.slot && ctx.isFocused("consoles")) t.term?.focus();
  }

  async function start(t) {
    t.starting = true;
    t.early = [];
    fitTerm(t);
    const res = await api.term_start(t.slot, t.term.cols, t.term.rows);
    t.starting = false;
    if (!res?.ok) {
      t.exited = true;
      t.term.write(`\r\n\x1b[91mImpossible de démarrer le shell : ${res?.error || "erreur inconnue"}\x1b[0m\r\n`);
      renderStrip();
      return;
    }
    t.session = res.session;
    t.exited = false;
    for (const ev of t.early) if (ev.session === t.session) write(t, ev.data);
    t.early = [];
    renderStrip();
  }

  async function restart(t, force = false) {
    if (!t?.term) return;
    if (force && !t.exited) await api.term_stop(t.slot);
    t.session = 0;
    t.exited = false;
    t.term.write(`\x1b[0m${"\r\n".repeat(t.term.rows)}\x1b[H`);
    await start(t);
    termFocus(t);
  }

  function write(t, data) {
    t.term.write(data);
    t.save();
  }

  /** Envoi ordonné : pywebview traite chaque appel dans un thread séparé. */
  function sendInput(t, data) {
    if (t.exited) {
      if (data === "\r") restart(t);
      return;
    }
    if (data.includes("\r") && t.term.buffer.active.type === "normal") {
      t.enterMarker?.dispose();
      t.enterMarker = t.term.registerMarker(0);
      t.promptSinceEnter = false;
    }
    t.outbox += data;
    if (!t.sending) pump(t);
  }

  async function pump(t) {
    t.sending = true;
    while (t.outbox) {
      const chunk = t.outbox;
      t.outbox = "";
      await api.term_input(t.slot, chunk);
    }
    t.sending = false;
  }

  function queueResize(t, cols, rows) {
    t.pendingSize = [cols, rows];
    if (t.resizing) return;
    t.resizing = true;
    (async () => {
      while (t.pendingSize) {
        const [c, r] = t.pendingSize;
        t.pendingSize = null;
        await api.term_resize(t.slot, c, r);
      }
      t.resizing = false;
    })();
  }

  function onTitle(t, title) {
    if (!title.includes(TITLE_MARK)) return;
    // cmd ajoute « - commande » au titre pendant l'exécution
    if (!title.slice(title.indexOf(TITLE_MARK)).includes(" - ")) t.promptSinceEnter = true;
    api.term_title(t.slot, title);
  }

  function keyHandler(t, ev) {
    if (ev.type !== "keydown" || !ev.ctrlKey || ev.altKey) return true;
    const key = ev.key.toLowerCase();
    if (key === "c" && (ev.shiftKey || t.term.hasSelection())) {
      if (t.term.hasSelection()) ctx.copy(t.term.getSelection(), null);
      t.term.clearSelection();
      return false;
    }
    if (key === "v") {
      if (ev.shiftKey) pasteClipboard(t);
      return false;   // Ctrl+V : collage natif du navigateur, géré par xterm
    }
    if (key === "t" && !ev.shiftKey) {
      api.term_open_external(t.slot);
      return false;
    }
    return true;
  }

  async function pasteClipboard(t) {
    const text = await api.read_clipboard();
    if (text) t.term.paste(text);
    t.term.focus();
  }

  function saveBuffer(t) {
    if (t.term && t.ser) api.term_save(t.slot, t.ser.serialize({ scrollback: 1000 }));
  }

  function clear(t) {
    if (!t?.term) return;
    const sent = t.exited ? Promise.resolve() : api.term_submit(t.slot, "cls");
    sent.then(() => setTimeout(() => { t.term.clear(); t.save(); }, 250));
    t.term.focus();
  }

  async function pickDir(t) {
    const ok = await api.term_pick_dir(t.slot);
    if (ok) t.term?.focus();
  }

  // ── Lecture vocale ──────────────────────────
  function readLines(buf, start, end) {
    const out = [];
    for (let i = Math.max(0, start); i <= end && i < buf.length; i++) {
      const line = buf.getLine(i);
      if (!line) continue;
      const text = line.translateToString(true);
      if (line.isWrapped && out.length) out[out.length - 1] += text;
      else out.push(text);
    }
    return out.join("\n").trim();
  }

  function outputText(t, mode) {
    if (!t?.term) return "";
    const buf = t.term.buffer.active;
    const cursorLine = buf.baseY + buf.cursorY;
    if (mode === "all") return readLines(buf, 0, cursorLine);
    const marker = t.enterMarker;
    if (!marker || marker.isDisposed || marker.line < 0) return "";
    return readLines(buf, marker.line + 1, t.promptSinceEnter ? cursorLine - 1 : cursorLine);
  }

  function speak(t, mode) {
    if (!t) return;
    const source = `console:${t.slot}`;
    if (ctx.speaking(source)) { api.tts_stop(); return; }
    ctx.speak(outputText(t, mode === "all" ? "all" : "last"), source);
  }

  function termMenu(t, ev) {
    ev.preventDefault();
    const sel = t.term.getSelection();
    openMenu([
      { label: "Copier", icon: "copy", hint: "Ctrl+C", disabled: !sel, onSelect: () => ctx.copy(sel, null) },
      { label: "Coller", icon: "clipboard-paste", hint: "Ctrl+V", onSelect: () => pasteClipboard(t) },
      "-",
      { label: "Lire la sélection", icon: "volume-2", disabled: !sel, onSelect: () => ctx.speak(sel, `console:${t.slot}`) },
      { label: "Lire la dernière sortie", icon: "volume-2", onSelect: () => speak(t, "last") },
      { label: "Tout lire", icon: "volume-2", onSelect: () => speak(t, "all") },
      "-",
      { label: "Changer de dossier…", icon: "folder-open", onSelect: () => pickDir(t) },
      { label: "Ouvrir dans un terminal externe", icon: "external-link", hint: "Ctrl+T", onSelect: () => api.term_open_external(t.slot) },
      { label: "Effacer", icon: "eraser", onSelect: () => clear(t) },
      { label: "Redémarrer le shell", icon: "rotate-ccw", onSelect: () => restart(t, true) },
    ], ev.clientX, ev.clientY);
  }

  // ── Évènements Python ───────────────────────
  on("term:data", ({ slot, session, data }) => {
    const t = S.tabs.get(slot);
    if (!t?.term) return;
    if (t.starting) { t.early.push({ session, data }); return; }
    if (session === t.session) write(t, data);
  });

  on("term:exit", ({ slot, session, code }) => {
    const t = S.tabs.get(slot);
    if (!t?.term || session !== t.session) return;
    t.exited = true;
    const codeText = code !== null && code !== undefined ? ` (code ${code})` : "";
    t.term.write(`\r\n\x1b[0m\x1b[2m[Processus terminé${codeText} — Entrée pour relancer]\x1b[0m\r\n`);
    renderStrip();
  });

  on("term:tab", (data) => {
    const t = S.tabs.get(data.slot);
    if (!t) return;
    t.data = data;
    renderStrip();
    if (t.slot === S.active) renderBar();
  });

  const refit = throttle(() => { const t = activeTab(); if (t) fitTerm(t); }, 60);
  new ResizeObserver(refit).observe(stack);
  on("layout", refit);

  ctx.onCollect((st) => {
    for (const t of S.tabs.values()) {
      if (t.term && t.ser) st.terminals[t.slot] = t.ser.serialize({ scrollback: 1000 });
    }
  });

  // ── Initialisation ──────────────────────────
  if (S.available) {
    for (const data of state.consoles.tabs) {
      const t = makeTab(data);
      S.order.push(t.slot);
    }
    S.active = S.order[0];
    renderStrip();
    renderBar();
    for (const x of S.tabs.values()) x.host.classList.toggle("active", x.slot === S.active);
  }

  return {
    el,
    onShow() {
      const t = activeTab();
      if (t) open(t);
    },
    onWindowShown() {
      const t = activeTab();
      if (t?.term) fitTerm(t);
    },
    focus() { activeTab()?.term?.focus(); },
    onDrop(paths, zone) {
      const t = activeTab();
      if (!t) return;
      if (zone === "cwd") {
        api.term_cd(t.slot, paths[0]);
        return;
      }
      if (!t.term) return;
      t.term.paste(paths.map(quotePath).join(" "));
      t.term.focus();
    },
  };
}
