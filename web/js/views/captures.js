// Vue Captures : captures d'une zone de l'écran (Impr. écran) et éditeur d'image façon Paint.
//
// Chaque onglet a trois canvas : l'image (retouches appliquées directement dessus), un calque
// d'aperçu (tracé en cours, sélection) et la capture d'origine, que la gomme fait réapparaître.
// Annuler/Rétablir mémorise seulement les pixels de la zone modifiée par chaque opération.
import { h, debounce, isTyping } from "../dom.js";
import { ico, iconButton, openMenu, openMenuBelow, TabStrip, toast, kbdCombo } from "../ui.js";

const TOOLS = [
  { id: "select",      label: "Sélection (copier, rogner…)", icon: "square-dashed", key: "s" },
  "-",
  { id: "pen",         label: "Crayon",              icon: "pencil-line",   key: "p" },
  { id: "highlighter", label: "Surligneur",          icon: "highlighter",   key: "h" },
  { id: "eraser",      label: "Gomme (fait réapparaître la capture d'origine)", icon: "eraser", key: "e" },
  "-",
  { id: "line",        label: "Ligne",               icon: "slash",         key: "l" },
  { id: "arrow",       label: "Flèche",              icon: "move-up-right", key: "a" },
  { id: "rect",        label: "Rectangle",           icon: "square",        key: "r" },
  { id: "ellipse",     label: "Ellipse",             icon: "circle",        key: "o" },
  { id: "text",        label: "Texte",               icon: "type",          key: "t" },
  "-",
  { id: "fill",        label: "Remplissage",         icon: "paint-bucket",  key: "f" },
  { id: "picker",      label: "Pipette",             icon: "pipette",       key: "i" },
  { id: "pixelate",    label: "Pixelliser une zone", icon: "grid-3x3",      key: "m" },
];
const COLORS = [
  ["#e5484d", "Rouge"], ["#f76b15", "Orange"], ["#ffc53d", "Jaune"], ["#30a46c", "Vert"],
  ["#0090ff", "Bleu"], ["#8e4ec6", "Violet"], ["#111111", "Noir"], ["#ffffff", "Blanc"],
];
// Tailles en pixels d'écran : multipliées par devicePixelRatio, une capture étant en pixels physiques
const SIZES = [
  { label: "Fin",        stroke: 2,  text: 14, block: 6 },
  { label: "Moyen",      stroke: 4,  text: 20, block: 10 },
  { label: "Épais",      stroke: 8,  text: 28, block: 16 },
  { label: "Très épais", stroke: 14, text: 40, block: 24 },
];
const FREEHAND = new Set(["pen", "highlighter", "eraser"]);
const USES_COLOR = new Set(["pen", "highlighter", "line", "arrow", "rect", "ellipse", "text", "fill"]);
const USES_SIZE = new Set(["pen", "highlighter", "eraser", "line", "arrow", "rect", "ellipse", "text", "pixelate"]);
const USES_FILL = new Set(["rect", "ellipse", "text"]);
const ZOOM_STEPS = [0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.25, 1.5, 2, 3, 4, 6, 8, 12, 16];
const FONT = '"Inter", "Segoe UI", system-ui, sans-serif';
const UNDO_BUDGET = 150 * 1024 * 1024;   // octets de pixels gardés par onglet pour Annuler
const FILL_TOLERANCE = 40;
const BLANK = "new";          // onglet vierge : même contenu que la vue sans capture
const HANDLE_CURSORS = {
  n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize",
  nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize", move: "move",
};

const dpr = () => window.devicePixelRatio || 1;
const font = (px) => `600 ${px}px ${FONT}`;
const normRect = (a, b) => ({ x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), w: Math.abs(b.x - a.x), h: Math.abs(b.y - a.y) });

function hexToRgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const rgbToHex = (r, g, b) => `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;

/** Noir ou blanc, selon ce qui se lit le mieux sur `hex`. */
function contrast(hex) {
  const [r, g, b] = hexToRgb(hex);
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "#111111" : "#ffffff";
}

function copyCanvas(src) {
  const c = document.createElement("canvas");
  c.width = src.width;
  c.height = src.height;
  c.getContext("2d").drawImage(src, 0, 0);
  return c;
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("image illisible"));
    img.src = src;
  });
}

function canvasData(canvas) {
  return new Promise((resolve) => canvas.toBlob((blob) => {
    if (!blob) { resolve(null); return; }
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(blob);
  }, "image/png"));
}

let scratchCanvas = null;
/** Canvas de travail réutilisé (pixellisation, gomme). */
function scratch(w, h) {
  if (!scratchCanvas) scratchCanvas = document.createElement("canvas");
  if (scratchCanvas.width < w || scratchCanvas.height < h) {
    scratchCanvas.width = Math.max(scratchCanvas.width, w);
    scratchCanvas.height = Math.max(scratchCanvas.height, h);
  }
  const c = scratchCanvas.getContext("2d");
  c.clearRect(0, 0, w, h);
  return scratchCanvas;
}

// ─────────────────────────────────────────────
//  Dessin
// ─────────────────────────────────────────────
function strokePoints(c, pts) {
  if (pts.length === 1) {
    c.beginPath();
    c.arc(pts[0].x, pts[0].y, c.lineWidth / 2, 0, Math.PI * 2);
    c.fill();
    return;
  }
  c.beginPath();
  c.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i];
    const q = pts[i + 1];
    c.quadraticCurveTo(p.x, p.y, (p.x + q.x) / 2, (p.y + q.y) / 2);
  }
  const last = pts[pts.length - 1];
  c.lineTo(last.x, last.y);
  c.stroke();
}

const arrowHead = (width) => Math.max(width * 3.2, 9);

function drawArrow(c, a, b, width) {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy);
  if (len < 1) return;
  const ux = dx / len;
  const uy = dy / len;
  const head = Math.min(len, arrowHead(width));
  const half = head * 0.55;
  const bx = b.x - ux * head;
  const by = b.y - uy * head;
  c.beginPath();
  c.moveTo(a.x, a.y);
  c.lineTo(b.x - ux * head * 0.5, b.y - uy * head * 0.5);   // la pointe recouvre le bout arrondi
  c.stroke();
  c.beginPath();
  c.moveTo(b.x, b.y);
  c.lineTo(bx - uy * half, by + ux * half);
  c.lineTo(bx + uy * half, by - ux * half);
  c.closePath();
  c.fill();
}

function pixelate(c, src, r, block) {
  const sw = Math.max(1, Math.round(r.w / block));
  const sh = Math.max(1, Math.round(r.h / block));
  const tmp = scratch(sw, sh);
  const tc = tmp.getContext("2d");
  tc.imageSmoothingEnabled = true;
  tc.imageSmoothingQuality = "high";
  tc.drawImage(src, r.x, r.y, r.w, r.h, 0, 0, sw, sh);
  c.save();
  c.imageSmoothingEnabled = false;
  c.drawImage(tmp, 0, 0, sw, sh, r.x, r.y, r.w, r.h);
  c.restore();
}

export function createCapturesView(ctx, state) {
  const { api, on } = ctx;
  const S = { tabs: new Map(), order: [], active: null, blank: false };
  const E = { tool: "pen", prevTool: "pen", color: COLORS[0][0], size: 1, fill: false };
  let drag = null;          // { t, id, op } tracé en cours, { t, id, sel } cadre modifié, { t, id, pan } défilement
  let textBox = null;       // saisie de texte en cours
  let spaceDown = false;
  let rafPending = null;

  // ── Structure ───────────────────────────────
  const strip = new TabStrip({
    onSelect: (id) => (id === BLANK ? selectBlank() : select(id)),
    onClose: (id) => (id === BLANK ? closeBlank() : closeTab(id)),
    onNew: newBlank,
    onRename: (id, label) => (id === BLANK ? renderStrip() : rename(id, label)),
    onMenu: tabMenu,
    newLabel: "Nouvel onglet",
    placeholder: "Capture",
  });

  const toolBtns = new Map();
  const toolbox = h("div", { class: "paint-tools", role: "toolbar", "aria-label": "Outils" },
    TOOLS.map((tool) => {
      if (tool === "-") return h("div", { class: "paint-sep" });
      const btn = iconButton(tool.icon, tool.label, () => setTool(tool.id), { kbd: tool.key.toUpperCase() });
      toolBtns.set(tool.id, btn);
      return btn;
    }));

  const swatches = COLORS.map(([color, name]) => {
    const btn = h("button", { class: "swatch", type: "button", "aria-label": name,
      dataset: { tip: name, color }, onClick: () => setColor(color) });
    btn.style.setProperty("--c", color);
    return btn;
  });
  const customInput = h("input", { type: "color", tabindex: "-1", "aria-label": "Autre couleur" });
  customInput.addEventListener("input", () => setColor(customInput.value));
  const customSwatch = h("label", { class: "swatch custom", dataset: { tip: "Autre couleur…" } }, customInput);
  const colorGroup = h("div", { class: "swatches" }, swatches, customSwatch);

  const sizeBtns = SIZES.map((size, i) => h("button", { class: "size-btn", type: "button",
    "aria-label": size.label, dataset: { tip: size.label }, onClick: () => setSize(i) },
  h("span", { class: "dot", style: { width: `${3 + i * 3}px`, height: `${3 + i * 3}px` } })));
  const sizeGroup = h("div", { class: "sizes" }, sizeBtns);
  const fillBtn = iconButton("square", "Formes et texte pleins", () => setFill(!E.fill));
  fillBtn.classList.add("fill-toggle");

  const undoBtn = iconButton("undo-2", "Annuler", () => undo(activeTab()), { kbd: "Ctrl+Z" });
  const redoBtn = iconButton("redo-2", "Rétablir", () => redo(activeTab()), { kbd: "Ctrl+Y" });
  const chatBtn = iconButton("message-square", "Joindre à une conversation",
    (ev) => ctx.pickDestination("chat", ev.currentTarget, (tab) => toChat(activeTab(), tab)));
  chatBtn.dataset.ollama = "";      // masqué tant qu'Ollama ne répond pas
  const bar = h("div", { class: "paint-bar" },
    colorGroup,
    h("div", { class: "paint-vsep" }),
    sizeGroup, fillBtn,
    h("div", { class: "paint-vsep" }),
    undoBtn, redoBtn,
    h("div", { class: "paint-actions" },
      chatBtn,
      iconButton("copy", "Copier (la sélection, sinon l'image)", () => copy(activeTab()), { kbd: "Ctrl+C" }),
      iconButton("download", "Enregistrer sous…", () => saveAs(activeTab()), { kbd: "Ctrl+S" }),
      iconButton("ellipsis", "Plus d'actions", (ev) => openMenuBelow(moreItems(activeTab()), ev.currentTarget, "right"))));

  const brush = h("div", { class: "paint-brush", hidden: true });
  const stage = h("div", { class: "paint-stage" }, brush);

  const dims = h("span", { class: "paint-dims" });
  const pos = h("span", { class: "paint-pos" });
  const saveState = h("span", { class: "save-state" });
  const zoomLabel = h("button", { class: "zoom-label", type: "button", dataset: { tip: "Zoom" },
    onClick: (ev) => openMenuBelow(zoomItems(activeTab()), ev.currentTarget, "right") });
  const foot = h("footer", { class: "paint-foot" },
    dims, pos, h("div", { class: "head-spacer" }), saveState,
    iconButton("zoom-out", "Zoom arrière", () => zoomStep(activeTab(), -1), { cls: "sm", kbd: "Ctrl+-" }),
    zoomLabel,
    iconButton("zoom-in", "Zoom avant", () => zoomStep(activeTab(), 1), { cls: "sm", kbd: "Ctrl++" }),
    iconButton("maximize", "Ajuster à la fenêtre", () => fitZoom(activeTab()), { cls: "sm", kbd: "Ctrl+0" }));

  const editor = h("div", { class: "paint-editor" }, bar, h("div", { class: "paint-work" }, toolbox, stage), foot);
  const emptyHint = h("p");
  const empty = h("div", { class: "empty" },
    h("div", { class: "glyph" }, ico("camera", 22)),
    h("h3", { text: "Nouvelle capture" }),
    emptyHint,
    h("div", { class: "empty-actions" },
      h("button", { class: "btn primary", type: "button", onClick: snip }, ico("scan", 15), "Capturer une zone"),
      h("button", { class: "btn", type: "button", onClick: paste }, ico("clipboard-paste", 15), "Coller une image"),
      h("button", { class: "btn ghost", type: "button", onClick: openDialog }, ico("folder-open", 15), "Ouvrir…")));
  const dropOverlay = h("div", { class: "drop-overlay" },
    h("div", { class: "inner" }, ico("image-plus", 22), "Déposez des images pour les ouvrir"));

  const el = h("section", { class: "paint-view" },
    h("header", { class: "view-head" }, strip.el), editor, empty, dropOverlay);

  const activeTab = () => S.tabs.get(S.active);

  // ── Onglets ─────────────────────────────────
  function makeTab(data) {
    const canvas = h("canvas", { class: "paint-canvas" });
    const overlay = h("canvas", { class: "paint-overlay" });
    const wrap = h("div", { class: "paint-wrap" }, canvas, overlay);
    const scroller = h("div", { class: "paint-scroll" }, wrap);
    stage.insertBefore(scroller, brush);
    const t = {
      slot: data.slot, data, canvas, overlay, wrap, scroller,
      orig: document.createElement("canvas"),
      ctx: canvas.getContext("2d", { willReadFrequently: true }),
      octx: overlay.getContext("2d"),
      loaded: false, loading: null, s: 1, fit: "auto",   // fit : "auto", "fill" ou false (zoom libre)
      undo: [], redo: [], dirty: false, origDirty: false,
      sel: null, selBar: null,
    };
    t.save = debounce(() => persist(t), 800);
    scroller.addEventListener("pointerdown", (ev) => onPointerDown(t, ev));
    scroller.addEventListener("pointermove", (ev) => onPointerMove(t, ev));
    scroller.addEventListener("pointerup", (ev) => onPointerUp(t, ev));
    for (const type of ["pointercancel", "lostpointercapture"]) {
      scroller.addEventListener(type, (ev) => { if (drag?.t === t && drag.id === ev.pointerId) cancelDrag(); });
    }
    scroller.addEventListener("pointerleave", () => { brush.hidden = true; if (!drag) pos.textContent = ""; });
    scroller.addEventListener("mousedown", (ev) => { if (ev.button === 1) ev.preventDefault(); });   // pas de défilement auto
    scroller.addEventListener("wheel", (ev) => onWheel(t, ev), { passive: false });
    scroller.addEventListener("contextmenu", (ev) => canvasMenu(t, ev));
    S.tabs.set(t.slot, t);
    return t;
  }

  /** Charge l'image d'un onglet (à sa première ouverture, ou fournie avec une nouvelle capture). */
  function ensureLoaded(t, images = null) {
    if (!t.loading) {
      t.loading = (async () => {
        try {
          const res = images || await api.capture_load(t.slot);
          if (!res?.image) throw new Error("fichier introuvable");
          const img = await loadImage(res.image);
          const orig = res.orig && res.orig !== res.image ? await loadImage(res.orig) : img;
          setCanvasSize(t, img.naturalWidth, img.naturalHeight);
          t.ctx.drawImage(img, 0, 0);
          const same = orig.naturalWidth === img.naturalWidth && orig.naturalHeight === img.naturalHeight;
          t.orig.getContext("2d").drawImage(same ? orig : img, 0, 0);
          t.loaded = true;
        } catch (err) {
          t.loading = null;
          toast(`Capture illisible : ${err.message || err}`, "error");
        }
      })();
    }
    return t.loading;
  }

  function setCanvasSize(t, w, hgt) {
    t.canvas.width = w;
    t.canvas.height = hgt;
    t.orig.width = w;
    t.orig.height = hgt;
    sizeOverlay(t);
  }

  /** Le calque d'aperçu n'occupe de la mémoire que pour l'onglet affiché. */
  function sizeOverlay(t) {
    const active = t.slot === S.active;
    t.overlay.width = active ? t.canvas.width : 0;
    t.overlay.height = active ? t.canvas.height : 0;
  }

  function renderStrip() {
    const tabs = S.order.map((slot) => {
      const t = S.tabs.get(slot);
      return { id: slot, title: t.data.title, label: t.data.label };
    });
    if (S.blank) tabs.push({ id: BLANK, title: "Nouvel onglet" });
    strip.render(tabs, S.active);
    const showEditor = !!activeTab();
    editor.hidden = !showEditor;
    empty.hidden = showEditor;
  }

  function renderEmptyHint() {
    emptyHint.replaceChildren(...(ctx.store.settings.print_screen_capture
      ? ["Appuyez sur ", ...kbdCombo("print screen"),
        " n'importe où dans Windows : la zone sélectionnée s'ouvre ici, prête à être annotée."]
      : ["Capturez une zone de l'écran, collez une image (Ctrl+V) ou glissez-déposez un fichier."]));
  }

  /** Quitte l'onglet affiché : texte validé, tracé et sélection abandonnés. */
  function leaveTab() {
    commitText();
    cancelDrag();
    const prev = activeTab();
    if (prev) clearSelection(prev);
    pos.textContent = "";
    brush.hidden = true;
  }

  function showScroller(t) {
    for (const x of S.tabs.values()) {
      x.scroller.classList.toggle("active", x === t);
      if (x.loaded) sizeOverlay(x);
    }
  }

  function select(slot) {
    const t = S.tabs.get(slot);
    if (!t) return;
    if (S.active !== slot) leaveTab();
    else commitText();
    S.active = slot;
    showScroller(t);
    renderStrip();
    refreshUi();
    ensureLoaded(t).then(() => {
      if (S.active !== slot || !t.loaded) return;
      sizeOverlay(t);
      if (t.fit) refit(t);
      else applyScale(t);
      refreshUi();
    });
  }

  /** Onglet vierge (bouton +) : propose de capturer, coller ou ouvrir une image, qui
   *  prend alors sa place. Un seul à la fois, toujours en dernier. */
  function newBlank() {
    S.blank = true;
    selectBlank();
  }

  function selectBlank() {
    if (!S.blank) return;
    if (S.active !== BLANK) leaveTab();
    S.active = BLANK;
    showScroller(null);
    renderEmptyHint();
    renderStrip();
    refreshUi();
  }

  function closeBlank() {
    if (!S.blank) return;
    S.blank = false;
    if (S.active === BLANK) {
      S.active = null;
      const last = S.order[S.order.length - 1];
      if (last !== undefined) { select(last); return; }
    }
    renderStrip();
  }

  function addTab(data, images = null) {
    const t = makeTab(data);
    S.order.push(t.slot);
    if (images) ensureLoaded(t, images);
    select(t.slot);
    return t;
  }

  async function closeTab(slot) {
    const t = S.tabs.get(slot);
    if (!t) return;
    if (textBox?.t === t) commitText();
    if (drag?.t === t) cancelDrag();
    t.save.cancel();
    const res = await api.capture_close(slot);
    if (!res?.removed) return;
    const idx = S.order.indexOf(slot);
    S.order.splice(idx, 1);
    S.tabs.delete(slot);
    t.scroller.remove();
    if (S.active === slot) {
      S.active = null;
      const next = S.order[Math.max(0, idx - 1)];
      if (next !== undefined) select(next);
      else if (S.blank) selectBlank();
    }
    renderStrip();
    refreshUi();
  }

  async function rename(slot, label) {
    const data = await api.capture_rename(slot, label);
    const t = S.tabs.get(slot);
    if (data && t) t.data = data;
    renderStrip();
  }

  function tabMenu(slot, ev) {
    if (slot === BLANK) {
      openMenu([
        { label: "Capturer une zone", icon: "scan", onSelect: snip },
        { label: "Coller une image", icon: "clipboard-paste", hint: "Ctrl+V", onSelect: paste },
        { label: "Ouvrir une image…", icon: "folder-open", onSelect: openDialog },
        "-",
        { label: "Fermer l'onglet", icon: "x", onSelect: closeBlank },
      ], ev.clientX, ev.clientY);
      return;
    }
    const t = S.tabs.get(slot);
    openMenu([
      { label: "Capturer une zone", icon: "scan", onSelect: snip },
      { label: "Renommer…", icon: "pencil", hint: "F2", onSelect: () => strip.rename(slot) },
      "-",
      { label: "Copier l'image", icon: "copy", onSelect: () => copyImage(t) },
      { label: "Enregistrer sous…", icon: "download", onSelect: () => saveAs(t) },
      ctx.store.ollama ? ctx.destinationMenuItem("chat", { label: "Joindre à une conversation",
        newLabel: "Joindre à une nouvelle conversation", icon: "message-square" }, (tab) => toChat(t, tab)) : null,
      "-",
      { label: "Fermer la capture", icon: "x", onSelect: () => closeTab(slot) },
    ], ev.clientX, ev.clientY);
  }

  // ── Barre d'options ─────────────────────────
  function setTool(id) {
    commitText();
    const t = activeTab();
    if (t && id !== "select") clearSelection(t);
    if (id !== "picker") E.prevTool = id;
    E.tool = id;
    for (const [key, btn] of toolBtns) {
      btn.classList.toggle("on", key === id);
      btn.setAttribute("aria-pressed", String(key === id));
    }
    stage.dataset.tool = id;
    brush.hidden = true;
    colorGroup.classList.toggle("off", !USES_COLOR.has(id));
    sizeGroup.classList.toggle("off", !USES_SIZE.has(id));
    fillBtn.classList.toggle("off", !USES_FILL.has(id));
  }

  function setColor(color) {
    E.color = color.toLowerCase();
    const preset = COLORS.some(([c]) => c === E.color);
    for (const sw of swatches) sw.classList.toggle("on", sw.dataset.color === E.color);
    customSwatch.classList.toggle("on", !preset);
    if (preset) customSwatch.style.removeProperty("--c");
    else customSwatch.style.setProperty("--c", E.color);
    customInput.value = E.color;
    if (textBox) styleText(textBox, { color: E.color });
  }

  function setSize(i) {
    E.size = i;
    sizeBtns.forEach((b, j) => b.classList.toggle("on", j === i));
    if (textBox) styleText(textBox, { fontPx: textSize() });
  }

  function setFill(value) {
    E.fill = value;
    fillBtn.classList.toggle("on", value);
    fillBtn.setAttribute("aria-pressed", String(value));
    if (textBox) styleText(textBox, { fill: value });
  }

  const strokeWidth = (tool = E.tool) => {
    const base = SIZES[E.size].stroke * dpr();
    return tool === "highlighter" || tool === "eraser" ? base * 4 : base;
  };
  const textSize = () => Math.round(SIZES[E.size].text * dpr());
  const blockSize = () => Math.round(SIZES[E.size].block * dpr());

  function refreshUi() {
    const t = activeTab();
    undoBtn.disabled = !t?.undo.length;
    redoBtn.disabled = !t?.redo.length;
    dims.textContent = t?.loaded ? `${t.canvas.width} × ${t.canvas.height} px` : "";
    zoomLabel.textContent = t?.loaded ? `${Math.round(t.s * dpr() * 100)} %` : "—";
    setSaveState(t && (t.dirty || t.origDirty || t.save.pending()) ? "editing" : "");
  }

  function setSaveState(kind) {
    if (kind === "saved") saveState.replaceChildren(ico("check", 13), "Enregistré");
    else if (kind === "editing") saveState.replaceChildren("Modification…");
    else saveState.replaceChildren();
  }

  // ── Zoom ────────────────────────────────────
  function applyScale(t) {
    t.wrap.style.width = `${t.canvas.width * t.s}px`;
    t.wrap.style.height = `${t.canvas.height * t.s}px`;
    t.wrap.classList.toggle("pixelated", t.s * dpr() >= 2);
    if (textBox?.t === t) styleText(textBox);
    if (t.sel) { renderOverlay(t); positionSelBar(t); }
    if (t.slot === S.active) refreshUi();
  }

  /** Ajuste l'image à la zone visible. fill (bouton « Ajuster », Ctrl+0) : agrandit
   *  aussi les petites images ; sinon (ouverture d'une capture) au plus 100 %. */
  function fitZoom(t, fill = true) {
    if (!t?.loaded) return;
    const pad = 48;
    const w = stage.clientWidth - pad;
    const hgt = stage.clientHeight - pad;
    t.fit = fill ? "fill" : "auto";
    if (w <= 0 || hgt <= 0) return;   // vue masquée : recalculé à l'affichage
    const scale = Math.min(w / t.canvas.width, hgt / t.canvas.height);
    t.s = fill ? scale : Math.min(scale, 1 / dpr());
    applyScale(t);
  }

  /** Réajuste après un redimensionnement, en gardant le mode d'ajustement en cours. */
  function refit(t) {
    if (t?.fit) fitZoom(t, t.fit === "fill");
  }

  /** z : zoom en pixels physiques (1 = 100 %). anchor : point de l'écran qui reste fixe. */
  function setZoom(t, z, anchor = null) {
    if (!t?.loaded) return;
    const next = Math.min(16, Math.max(0.05, z)) / dpr();
    const sr = t.scroller.getBoundingClientRect();
    const ax = anchor ? anchor.x : sr.left + sr.width / 2;
    const ay = anchor ? anchor.y : sr.top + sr.height / 2;
    const wr = t.wrap.getBoundingClientRect();
    const ix = (ax - wr.left) / t.s;
    const iy = (ay - wr.top) / t.s;
    t.s = next;
    t.fit = false;
    applyScale(t);
    const wr2 = t.wrap.getBoundingClientRect();
    t.scroller.scrollLeft += wr2.left + ix * next - ax;
    t.scroller.scrollTop += wr2.top + iy * next - ay;
  }

  function zoomStep(t, dir, anchor = null) {
    if (!t?.loaded) return;
    const cur = t.s * dpr();
    const next = dir > 0 ? ZOOM_STEPS.find((z) => z > cur + 1e-3)
      : [...ZOOM_STEPS].reverse().find((z) => z < cur - 1e-3);
    if (next) setZoom(t, next, anchor);
  }

  function onWheel(t, ev) {
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    setZoom(t, t.s * dpr() * Math.exp(-ev.deltaY * 0.0015), { x: ev.clientX, y: ev.clientY });
  }

  function zoomItems(t) {
    const cur = t ? Math.round(t.s * dpr() * 100) : 0;
    return [
      { label: "Ajuster à la fenêtre", checked: t?.fit === "fill", onSelect: () => fitZoom(t) },
      "-",
      ...[0.5, 1, 2, 4].map((z) => ({ label: `${z * 100} %`, checked: t?.fit !== "fill" && cur === z * 100,
        onSelect: () => setZoom(t, z) })),
    ];
  }

  // ── Souris ──────────────────────────────────
  function toImage(t, ev) {
    const r = t.wrap.getBoundingClientRect();
    return { x: (ev.clientX - r.left) / t.s, y: (ev.clientY - r.top) / t.s };
  }

  function onPointerDown(t, ev) {
    if (!t.loaded || drag) return;
    if (ev.target.closest(".paint-text, .sel-bar")) return;
    // Clic sur une barre de défilement
    if (ev.target === t.scroller && (ev.offsetX >= t.scroller.clientWidth || ev.offsetY >= t.scroller.clientHeight)) return;
    if (ev.button === 1 || (ev.button === 0 && spaceDown)) { startPan(t, ev); return; }
    if (ev.button !== 0) return;
    ev.preventDefault();
    if (textBox) { commitText(); return; }   // comme Paint : cliquer ailleurs valide le texte
    const p = toImage(t, ev);
    if (E.tool === "picker") { pickColor(t, p); return; }
    if (E.tool === "fill") { floodFill(t, p); return; }
    if (E.tool === "text") { openText(t, p); return; }
    if (E.tool === "select") {
      const hit = hitSelection(t, p);
      if (hit) {   // poignée, bord ou intérieur du cadre : on le redimensionne ou le déplace
        drag = { t, id: ev.pointerId, sel: { mode: hit, start: p, from: { ...t.sel } } };
        if (t.selBar) t.selBar.hidden = true;
        capturePointer(t, ev);
        return;
      }
      clearSelection(t);
    }
    drag = { t, id: ev.pointerId, op: {
      tool: E.tool, color: E.color, width: strokeWidth(), block: blockSize(), fill: E.fill,
      points: [p], a: p, b: p, shift: ev.shiftKey,
    } };
    capturePointer(t, ev);
    schedulePreview(t);
  }

  function onPointerMove(t, ev) {
    moveBrush(t, ev);
    if (!drag || drag.t !== t || drag.id !== ev.pointerId) {
      showPosition(t, ev);
      const hit = E.tool === "select" && !spaceDown && t.loaded ? hitSelection(t, toImage(t, ev)) : null;
      t.scroller.style.cursor = hit ? HANDLE_CURSORS[hit] : "";
      return;
    }
    if (drag.pan) {
      t.scroller.scrollLeft = drag.sl - (ev.clientX - drag.x);
      t.scroller.scrollTop = drag.st - (ev.clientY - drag.y);
      return;
    }
    if (drag.sel) {
      const p = toImage(t, ev);
      t.sel = reshapeSelection(t, drag.sel, p.x - drag.sel.start.x, p.y - drag.sel.start.y);
      pos.textContent = `${t.sel.w} × ${t.sel.h} px`;
      schedulePreview(t);
      return;
    }
    const op = drag.op;
    if (FREEHAND.has(op.tool)) {
      const events = ev.getCoalescedEvents?.() || [];
      for (const e of events.length ? events : [ev]) {
        const p = toImage(t, e);
        const last = op.points[op.points.length - 1];
        if (Math.abs(p.x - last.x) + Math.abs(p.y - last.y) >= 0.75) op.points.push(p);
      }
    }
    op.b = toImage(t, ev);
    op.shift = ev.shiftKey;
    showPosition(t, ev);
    schedulePreview(t);
  }

  function onPointerUp(t, ev) {
    if (!drag || drag.t !== t || drag.id !== ev.pointerId) return;
    const d = drag;
    drag = null;
    try { t.scroller.releasePointerCapture(ev.pointerId); } catch { /* déjà relâché */ }
    if (d.pan) { stage.classList.remove("panning"); return; }
    if (d.sel) { renderOverlay(t); showSelBar(t); return; }
    d.op.b = toImage(t, ev);
    d.op.shift = ev.shiftKey;
    finishOp(t, d.op);
    showPosition(t, ev);
  }

  function startPan(t, ev) {
    ev.preventDefault();
    drag = { t, id: ev.pointerId, pan: true, x: ev.clientX, y: ev.clientY,
      sl: t.scroller.scrollLeft, st: t.scroller.scrollTop };
    capturePointer(t, ev);
    stage.classList.add("panning");
    brush.hidden = true;
  }

  /** Garde les évènements du pointeur même quand il sort de la zone. */
  function capturePointer(t, ev) {
    try { t.scroller.setPointerCapture(ev.pointerId); } catch { /* pointeur déjà relâché */ }
  }

  function cancelDrag() {
    if (!drag) return;
    const d = drag;
    drag = null;
    stage.classList.remove("panning");
    if (d.sel) {
      d.t.sel = d.sel.from;
      showSelBar(d.t);
    }
    renderOverlay(d.t);
  }

  function moveBrush(t, ev) {
    if (!FREEHAND.has(E.tool) || spaceDown || drag?.pan || !t.loaded) { brush.hidden = true; return; }
    const sr = stage.getBoundingClientRect();
    const d = Math.max(4, strokeWidth() * t.s);
    Object.assign(brush.style, { left: `${ev.clientX - sr.left}px`, top: `${ev.clientY - sr.top}px`,
      width: `${d}px`, height: `${d}px` });
    brush.hidden = false;
  }

  function showPosition(t, ev) {
    if (!t.loaded) return;
    const op = drag?.t === t ? drag.op : null;
    if (op && !FREEHAND.has(op.tool)) {
      const r = normRect(op.a, endPoint(op));
      pos.textContent = `${Math.round(r.w)} × ${Math.round(r.h)} px`;
      return;
    }
    const p = toImage(t, ev);
    const inside = p.x >= 0 && p.y >= 0 && p.x < t.canvas.width && p.y < t.canvas.height;
    pos.textContent = inside ? `${Math.floor(p.x)}, ${Math.floor(p.y)} px` : "";
  }

  // ── Opérations ──────────────────────────────
  /** Point d'arrivée, contraint avec Maj (angles de 45°, carré, cercle). */
  function endPoint(op) {
    if (!op.shift) return op.b;
    const dx = op.b.x - op.a.x;
    const dy = op.b.y - op.a.y;
    if (op.tool === "line" || op.tool === "arrow") {
      const angle = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
      const len = Math.hypot(dx, dy);
      return { x: op.a.x + Math.cos(angle) * len, y: op.a.y + Math.sin(angle) * len };
    }
    const m = Math.max(Math.abs(dx), Math.abs(dy));
    return { x: op.a.x + Math.sign(dx || 1) * m, y: op.a.y + Math.sign(dy || 1) * m };
  }

  function opBounds(op) {
    const pts = FREEHAND.has(op.tool) ? op.points : [op.a, endPoint(op)];
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of pts) {
      x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y);
      x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y);
    }
    const pad = op.width / 2 + (op.tool === "arrow" ? arrowHead(op.width) : 0) + 2;
    return { x: x0 - pad, y: y0 - pad, w: x1 - x0 + pad * 2, h: y1 - y0 + pad * 2 };
  }

  /** Rectangle en pixels entiers, limité à l'image (null s'il est vide). */
  function clampRect(r, t) {
    const x0 = Math.max(0, Math.floor(r.x));
    const y0 = Math.max(0, Math.floor(r.y));
    const x1 = Math.min(t.canvas.width, Math.ceil(r.x + r.w));
    const y1 = Math.min(t.canvas.height, Math.ceil(r.y + r.h));
    return x1 > x0 && y1 > y0 ? { x: x0, y: y0, w: x1 - x0, h: y1 - y0 } : null;
  }

  function drawOp(c, op, t) {
    const b = endPoint(op);
    c.save();
    c.strokeStyle = op.color;
    c.fillStyle = op.color;
    c.lineWidth = op.width;
    c.lineCap = "round";
    c.lineJoin = "round";
    switch (op.tool) {
      case "pen":
        strokePoints(c, op.points);
        break;
      case "highlighter":
        c.globalAlpha = 0.4;   // un seul tracé : pas de surépaisseur là où il se recoupe
        c.lineCap = "square";
        strokePoints(c, op.points);
        break;
      case "eraser":
        eraseStroke(c, op, t);
        break;
      case "line":
        c.beginPath();
        c.moveTo(op.a.x, op.a.y);
        c.lineTo(b.x, b.y);
        c.stroke();
        break;
      case "arrow":
        drawArrow(c, op.a, b, op.width);
        break;
      case "rect": {
        const r = normRect(op.a, b);
        if (op.fill) c.fillRect(r.x, r.y, r.w, r.h);
        else c.strokeRect(r.x, r.y, r.w, r.h);
        break;
      }
      case "ellipse": {
        const r = normRect(op.a, b);
        c.beginPath();
        c.ellipse(r.x + r.w / 2, r.y + r.h / 2, r.w / 2, r.h / 2, 0, 0, Math.PI * 2);
        if (op.fill) c.fill();
        else c.stroke();
        break;
      }
      case "pixelate": {
        const r = clampRect(normRect(op.a, b), t);
        if (r) pixelate(c, t.canvas, r, op.block);
        break;
      }
      case "select": {
        const r = clampRect(normRect(op.a, b), t);
        if (r) drawSelection(c, t, r, false);
        break;
      }
    }
    c.restore();
  }

  /** Gomme : fait réapparaître la capture d'origine sous le tracé. */
  function eraseStroke(c, op, t) {
    const r = clampRect(opBounds(op), t);
    if (!r) return;
    const mask = scratch(r.w, r.h);
    const mc = mask.getContext("2d");
    mc.save();
    mc.translate(-r.x, -r.y);
    mc.lineWidth = op.width;
    mc.lineCap = "round";
    mc.lineJoin = "round";
    mc.strokeStyle = "#000";
    mc.fillStyle = "#000";
    strokePoints(mc, op.points);
    mc.restore();
    mc.save();
    mc.globalCompositeOperation = "source-in";
    mc.drawImage(t.orig, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
    mc.restore();
    c.drawImage(mask, 0, 0, r.w, r.h, r.x, r.y, r.w, r.h);
  }

  /** Cadre de sélection : extérieur assombri, pointillés, poignées de redimensionnement. */
  function drawSelection(c, t, r, handles = true) {
    const px = 1 / t.s;   // un pixel d'écran, quel que soit le zoom
    c.save();
    c.fillStyle = "rgba(0, 0, 0, .4)";
    c.beginPath();
    c.rect(0, 0, t.canvas.width, t.canvas.height);
    c.rect(r.x, r.y, r.w, r.h);
    c.fill("evenodd");
    c.lineWidth = px;
    c.strokeStyle = "rgba(0, 0, 0, .8)";
    c.strokeRect(r.x + px / 2, r.y + px / 2, r.w - px, r.h - px);
    c.strokeStyle = "#ffffff";
    c.setLineDash([4 * px, 4 * px]);
    c.strokeRect(r.x + px / 2, r.y + px / 2, r.w - px, r.h - px);
    if (handles) {
      c.setLineDash([]);
      const size = 8 * px;
      const xs = [r.x, r.x + r.w / 2, r.x + r.w];
      const ys = [r.y, r.y + r.h / 2, r.y + r.h];
      for (const [i, x] of xs.entries()) {
        for (const [j, y] of ys.entries()) {
          if (i === 1 && j === 1) continue;
          c.fillStyle = "#ffffff";
          c.fillRect(x - size / 2, y - size / 2, size, size);
          c.strokeStyle = "rgba(0, 0, 0, .75)";
          c.strokeRect(x - size / 2, y - size / 2, size, size);
        }
      }
    }
    c.restore();
  }

  function schedulePreview(t) {
    if (rafPending === t) return;
    rafPending = t;
    requestAnimationFrame(() => {
      rafPending = null;
      renderOverlay(t);
    });
  }

  function renderOverlay(t) {
    if (!t.overlay.width) return;
    t.octx.clearRect(0, 0, t.overlay.width, t.overlay.height);
    if (drag?.t === t && drag.op) drawOp(t.octx, drag.op, t);
    else if (t.sel) drawSelection(t.octx, t, t.sel);
  }

  function finishOp(t, op) {
    renderOverlay(t);
    const end = endPoint(op);
    const tiny = Math.abs(end.x - op.a.x) < 2 && Math.abs(end.y - op.a.y) < 2;
    if (op.tool === "select") {
      setSelection(t, tiny ? null : clampRect(normRect(op.a, end), t));
      return;
    }
    if (tiny && !FREEHAND.has(op.tool)) return;   // simple clic : rien à dessiner
    if (op.tool === "pixelate") {
      const r = clampRect(normRect(op.a, end), t);
      if (r) commit(t, r, (c) => pixelate(c, t.canvas, r, op.block));
      return;
    }
    commit(t, opBounds(op), (c) => drawOp(c, op, t));
  }

  /** Applique un dessin sur l'image en mémorisant les pixels qu'il recouvre. */
  function commit(t, bounds, draw) {
    const r = clampRect(bounds, t);
    if (!r) return false;
    const before = t.ctx.getImageData(r.x, r.y, r.w, r.h);
    draw(t.ctx);
    pushUndo(t, { kind: "region", x: r.x, y: r.y, data: before });
    changed(t);
    return true;
  }

  function pickColor(t, p) {
    const x = Math.min(t.canvas.width - 1, Math.max(0, Math.floor(p.x)));
    const y = Math.min(t.canvas.height - 1, Math.max(0, Math.floor(p.y)));
    const [r, g, b] = t.ctx.getImageData(x, y, 1, 1).data;
    setColor(rgbToHex(r, g, b));
    setTool(E.prevTool);
  }

  /** Pot de peinture : remplit la zone de couleur proche autour du point cliqué. */
  function floodFill(t, p) {
    const W = t.canvas.width;
    const H = t.canvas.height;
    const x0 = Math.floor(p.x);
    const y0 = Math.floor(p.y);
    if (x0 < 0 || y0 < 0 || x0 >= W || y0 >= H) return;
    const img = t.ctx.getImageData(0, 0, W, H);
    const d = img.data;
    const [fr, fg, fb] = hexToRgb(E.color);
    const i0 = (y0 * W + x0) * 4;
    const [tr, tg, tb, ta] = [d[i0], d[i0 + 1], d[i0 + 2], d[i0 + 3]];
    if (tr === fr && tg === fg && tb === fb && ta === 255) return;
    const tol = FILL_TOLERANCE;
    const match = (i) => Math.abs(d[i] - tr) <= tol && Math.abs(d[i + 1] - tg) <= tol
      && Math.abs(d[i + 2] - tb) <= tol && Math.abs(d[i + 3] - ta) <= tol;
    const mask = new Uint8Array(W * H);
    const stack = [x0, y0];
    let minX = x0, maxX = x0, minY = y0, maxY = y0;
    while (stack.length) {
      const y = stack.pop();
      let x = stack.pop();
      let idx = y * W + x;
      if (mask[idx] || !match(idx * 4)) continue;
      while (x > 0 && !mask[idx - 1] && match((idx - 1) * 4)) { x--; idx--; }
      const startX = x;
      let up = false;
      let down = false;
      while (x < W && !mask[idx] && match(idx * 4)) {
        mask[idx] = 1;
        if (y > 0) {
          const ok = !mask[idx - W] && match((idx - W) * 4);
          if (ok && !up) stack.push(x, y - 1);
          up = ok;
        }
        if (y < H - 1) {
          const ok = !mask[idx + W] && match((idx + W) * 4);
          if (ok && !down) stack.push(x, y + 1);
          down = ok;
        }
        x++;
        idx++;
      }
      minX = Math.min(minX, startX);
      maxX = Math.max(maxX, x - 1);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
    }
    const r = { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
    const before = t.ctx.getImageData(r.x, r.y, r.w, r.h);
    for (let y = r.y; y < r.y + r.h; y++) {
      for (let x = r.x, idx = y * W + r.x; x < r.x + r.w; x++, idx++) {
        if (!mask[idx]) continue;
        const i = idx * 4;
        d[i] = fr; d[i + 1] = fg; d[i + 2] = fb; d[i + 3] = 255;
      }
    }
    t.ctx.putImageData(img, 0, 0, r.x, r.y, r.w, r.h);
    pushUndo(t, { kind: "region", x: r.x, y: r.y, data: before });
    changed(t);
  }

  // ── Texte ───────────────────────────────────
  function openText(t, p) {
    const el = h("textarea", { class: "paint-text", spellcheck: "false", rows: "1", wrap: "off",
      "aria-label": "Texte" });
    const box = { t, p, el, fontPx: textSize(), color: E.color, fill: E.fill };
    el.addEventListener("input", () => sizeText(box));
    el.addEventListener("keydown", (ev) => {
      ev.stopPropagation();
      if (ev.key === "Escape" || (ev.key === "Enter" && ev.ctrlKey)) { ev.preventDefault(); commitText(); }
    });
    el.addEventListener("blur", () => { if (textBox === box) commitText(); });
    t.wrap.append(el);
    textBox = box;
    styleText(box);
    setTimeout(() => el.focus(), 0);
  }

  const textPad = (box) => (box.fill ? Math.round(box.fontPx * 0.3) : 0);

  function styleText(box, changes = null) {
    if (changes) Object.assign(box, changes);
    const { t, p, el } = box;
    const pad = textPad(box) * t.s;
    Object.assign(el.style, {
      left: `${p.x * t.s - pad}px`,
      top: `${p.y * t.s - pad}px`,
      padding: `${pad}px`,
      fontSize: `${box.fontPx * t.s}px`,
      color: box.fill ? contrast(box.color) : box.color,
      background: box.fill ? box.color : "transparent",
      borderRadius: `${pad * 0.8}px`,
    });
    sizeText(box);
  }

  function sizeText(box) {
    const { el } = box;
    el.style.width = "0px";
    el.style.height = "0px";
    el.style.width = `${Math.max(el.scrollWidth, box.fontPx * box.t.s * 0.6) + 2}px`;
    el.style.height = `${el.scrollHeight}px`;
  }

  function commitText() {
    const box = textBox;
    if (!box) return;
    textBox = null;
    const text = box.el.value.replace(/\s+$/, "");
    box.el.remove();
    if (!text.trim()) return;
    const { t, p, fontPx } = box;
    const lines = text.split("\n");
    const lineH = fontPx * 1.25;
    const pad = textPad(box);
    t.ctx.save();
    t.ctx.font = font(fontPx);
    const width = Math.max(...lines.map((line) => t.ctx.measureText(line).width));
    t.ctx.restore();
    const height = lines.length * lineH;
    const bounds = { x: p.x - pad - 2, y: p.y - pad - 2, w: width + pad * 2 + 4, h: height + pad * 2 + 4 };
    commit(t, bounds, (c) => {
      c.save();
      if (box.fill) {
        c.fillStyle = box.color;
        c.beginPath();
        c.roundRect(p.x - pad, p.y - pad, width + pad * 2, height + pad * 2, pad * 0.8);
        c.fill();
      }
      c.fillStyle = box.fill ? contrast(box.color) : box.color;
      c.font = font(fontPx);
      c.textBaseline = "middle";
      lines.forEach((line, i) => c.fillText(line, p.x, p.y + i * lineH + lineH / 2));
      c.restore();
    });
  }

  // ── Sélection ───────────────────────────────
  function setSelection(t, r) {
    if (!r || r.w < 2 || r.h < 2) { clearSelection(t); return; }
    t.sel = r;
    renderOverlay(t);
    showSelBar(t);
  }

  function clearSelection(t) {
    if (!t.sel && !t.selBar) return;
    t.sel = null;
    t.selBar?.remove();
    t.selBar = null;
    t.scroller.style.cursor = "";
    renderOverlay(t);
  }

  function selectAll(t) {
    if (!t?.loaded) return;
    if (E.tool !== "select") setTool("select");
    setSelection(t, { x: 0, y: 0, w: t.canvas.width, h: t.canvas.height });
  }

  /** Partie du cadre sous le pointeur : poignée ou bord (n, se…), intérieur (move), ou null. */
  function hitSelection(t, p) {
    const r = t.sel;
    if (!r) return null;
    const tol = 8 / t.s;
    const x1 = r.x + r.w;
    const y1 = r.y + r.h;
    if (p.x < r.x - tol || p.x > x1 + tol || p.y < r.y - tol || p.y > y1 + tol) return null;
    const v = Math.abs(p.y - r.y) <= tol ? "n" : Math.abs(p.y - y1) <= tol ? "s" : "";
    const hz = Math.abs(p.x - r.x) <= tol ? "w" : Math.abs(p.x - x1) <= tol ? "e" : "";
    return v + hz || "move";
  }

  /** Cadre déplacé (move) ou redimensionné par un bord, dans les limites de l'image. */
  function reshapeSelection(t, { mode, from: o }, dx, dy) {
    const W = t.canvas.width;
    const H = t.canvas.height;
    if (mode === "move") {
      return { x: Math.round(Math.min(W - o.w, Math.max(0, o.x + dx))),
        y: Math.round(Math.min(H - o.h, Math.max(0, o.y + dy))), w: o.w, h: o.h };
    }
    let x0 = o.x, y0 = o.y, x1 = o.x + o.w, y1 = o.y + o.h;
    if (mode.includes("w")) x0 += dx;
    if (mode.includes("e")) x1 += dx;
    if (mode.includes("n")) y0 += dy;
    if (mode.includes("s")) y1 += dy;
    const clamp = (v, max) => Math.round(Math.min(max, Math.max(0, v)));
    const ax = clamp(Math.min(x0, x1), W);
    const bx = clamp(Math.max(x0, x1), W);
    const ay = clamp(Math.min(y0, y1), H);
    const by = clamp(Math.max(y0, y1), H);
    return { x: ax, y: ay, w: Math.max(1, bx - ax), h: Math.max(1, by - ay) };
  }

  function nudgeSelection(t, dx, dy) {
    t.sel = reshapeSelection(t, { mode: "move", from: t.sel }, dx, dy);
    renderOverlay(t);
    showSelBar(t);
  }

  function showSelBar(t) {
    if (!t.sel) return;
    if (!t.selBar) {
      t.selBar = h("div", { class: "sel-bar" },
        h("span", { class: "sel-size" }),
        iconButton("copy", "Copier la sélection", () => copySelection(t), { size: 15, cls: "sm", kbd: "Ctrl+C" }),
        h("button", { class: "btn primary sm", type: "button",
          dataset: { tip: "Rogner l'image à la sélection", kbd: "Ctrl+Maj+X" }, onClick: () => cropToSelection(t) },
        ico("crop", 14), "Rogner"),
        iconButton("ellipsis", "Plus d'actions", (ev) => openMenuBelow(selectionItems(t), ev.currentTarget, "right"),
          { size: 15, cls: "sm" }),
        iconButton("x", "Désélectionner", () => clearSelection(t), { size: 14, cls: "sm", kbd: "Échap" }));
      t.wrap.append(t.selBar);
    }
    t.selBar.hidden = false;
    t.selBar.querySelector(".sel-size").textContent = `${t.sel.w} × ${t.sel.h}`;
    positionSelBar(t);
  }

  /** Barre sous le cadre, alignée à droite ; remontée dans le cadre s'il touche le bas de l'image. */
  function positionSelBar(t) {
    const { selBar: barEl, sel: r } = t;
    if (!barEl || !r) return;
    const right = (r.x + r.w) * t.s;
    let top = (r.y + r.h) * t.s + 8;
    if (top + 40 > t.canvas.height * t.s) top = Math.max(0, (r.y + r.h) * t.s - 46);
    barEl.style.left = `${Math.max(right, barEl.offsetWidth)}px`;
    barEl.style.top = `${top}px`;
  }

  function selectionItems(t) {
    return [
      { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", onSelect: () => copySelection(t) },
      { label: "Rogner", icon: "crop", hint: "Ctrl+Maj+X", onSelect: () => cropToSelection(t) },
      { label: "Enregistrer la sélection sous…", icon: "download", onSelect: () => saveSelectionAs(t) },
      "-",
      { label: "Pixelliser la zone", icon: "grid-3x3", onSelect: () => pixelateSelection(t) },
      { label: "Effacer la zone (blanc)", icon: "eraser", hint: "Suppr", onSelect: () => eraseSelection(t) },
      "-",
      { label: "Tout sélectionner", icon: "square-dashed", hint: "Ctrl+A", onSelect: () => selectAll(t) },
      { label: "Désélectionner", icon: "x", hint: "Échap", onSelect: () => clearSelection(t) },
    ];
  }

  const selRect = (t) => (t?.sel ? clampRect(t.sel, t) : null);

  /** PNG (data URL) d'une zone de l'image. */
  function regionData(t, r) {
    const c = document.createElement("canvas");
    c.width = r.w;
    c.height = r.h;
    c.getContext("2d").drawImage(t.canvas, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
    return canvasData(c);
  }

  async function copySelection(t) {
    const r = selRect(t);
    if (!r) return;
    commitText();
    const data = await regionData(t, r);
    if (!data) return;
    if (await api.capture_copy(data)) toast("Sélection copiée dans le presse-papiers");
    else toast("Copie impossible : le presse-papiers est occupé.", "error");
  }

  async function saveSelectionAs(t) {
    const r = selRect(t);
    if (!r) return;
    const data = await regionData(t, r);
    const path = data && await api.capture_save_as(t.slot, data);
    if (path) toast(`Enregistrée : ${path.split(/[\\/]/).pop()}`);
  }

  function pixelateSelection(t) {
    const r = selRect(t);
    if (r) commit(t, r, (c) => pixelate(c, t.canvas, r, blockSize()));
  }

  function eraseSelection(t) {
    const r = selRect(t);
    if (!r) return;
    commit(t, r, (c) => {
      c.fillStyle = "#ffffff";
      c.fillRect(r.x, r.y, r.w, r.h);
    });
  }

  /** Rogne l'image (et la capture d'origine) au cadre sélectionné. */
  function cropToSelection(t) {
    const r = selRect(t);
    if (!r) return;
    commitText();
    clearSelection(t);
    if (r.w === t.canvas.width && r.h === t.canvas.height) return;
    const main = copyCanvas(t.canvas);
    const orig = copyCanvas(t.orig);
    pushUndo(t, { kind: "full", main, orig });
    setCanvasSize(t, r.w, r.h);
    t.ctx.drawImage(main, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
    t.orig.getContext("2d").drawImage(orig, r.x, r.y, r.w, r.h, 0, 0, r.w, r.h);
    t.origDirty = true;
    changed(t);
    fitZoom(t, t.fit === "fill");
  }

  // ── Annuler / Rétablir ──────────────────────
  const entryBytes = (e) => (e.kind === "full"
    ? (e.main.width * e.main.height + e.orig.width * e.orig.height) * 4
    : e.data.data.length);

  function pushUndo(t, entry) {
    t.undo.push(entry);
    t.redo = [];
    let total = t.undo.reduce((sum, e) => sum + entryBytes(e), 0);
    while (total > UNDO_BUDGET && t.undo.length > 1) total -= entryBytes(t.undo.shift());
    refreshUi();
  }

  /** Applique une entrée d'historique et y range l'état qu'elle remplace. */
  function swap(t, e) {
    if (e.kind === "region") {
      const current = t.ctx.getImageData(e.x, e.y, e.data.width, e.data.height);
      t.ctx.putImageData(e.data, e.x, e.y);
      e.data = current;
      return;
    }
    const main = copyCanvas(t.canvas);
    const orig = copyCanvas(t.orig);
    setCanvasSize(t, e.main.width, e.main.height);
    t.ctx.drawImage(e.main, 0, 0);
    t.orig.getContext("2d").drawImage(e.orig, 0, 0);
    e.main = main;
    e.orig = orig;
    t.origDirty = true;
    if (t.fit) refit(t);
    else applyScale(t);
  }

  function undo(t) {
    if (!t?.undo.length) return;
    commitText();
    cancelDrag();
    clearSelection(t);
    const e = t.undo.pop();
    swap(t, e);
    t.redo.push(e);
    changed(t);
  }

  function redo(t) {
    if (!t?.redo.length) return;
    commitText();
    cancelDrag();
    clearSelection(t);
    const e = t.redo.pop();
    swap(t, e);
    t.undo.push(e);
    changed(t);
  }

  function restoreOriginal(t) {
    if (!t?.loaded) return;
    commit(t, { x: 0, y: 0, w: t.canvas.width, h: t.canvas.height }, (c) => {
      c.clearRect(0, 0, t.canvas.width, t.canvas.height);
      c.drawImage(t.orig, 0, 0);
    });
  }

  // ── Enregistrement ──────────────────────────
  function changed(t) {
    t.dirty = true;
    t.save();
    if (t.slot === S.active) refreshUi();
  }

  async function persist(t) {
    if (!t.loaded || (!t.dirty && !t.origDirty) || !S.tabs.has(t.slot)) return;
    const wantImage = t.dirty;
    const wantOrig = t.origDirty;
    t.dirty = false;
    t.origDirty = false;
    const [image, orig] = await Promise.all([
      wantImage ? canvasData(t.canvas) : null,
      wantOrig ? canvasData(t.orig) : null,
    ]);
    if (!S.tabs.has(t.slot)) return;   // fermée entre-temps
    const ok = await api.capture_save(t.slot, image, orig);
    if (!ok) {
      t.dirty ||= wantImage;
      t.origDirty ||= wantOrig;
      return;
    }
    if (t.slot === S.active && !t.dirty && !t.origDirty && !t.save.pending()) setSaveState("saved");
  }

  // ── Actions ─────────────────────────────────
  /** PNG de l'image affichée (data URL), texte en cours compris. */
  async function imageData(t) {
    if (!t) return null;
    if (textBox?.t === t) commitText();
    await ensureLoaded(t);
    return t.loaded ? canvasData(t.canvas) : null;
  }

  /** Copier : la sélection s'il y en a une, sinon toute l'image. */
  function copy(t) {
    if (t?.sel) copySelection(t);
    else copyImage(t);
  }

  async function copyImage(t) {
    const data = await imageData(t);
    if (!data) return;
    if (await api.capture_copy(data)) toast("Image copiée dans le presse-papiers");
    else toast("Copie impossible : le presse-papiers est occupé.", "error");
  }

  async function saveAs(t) {
    const data = await imageData(t);
    if (!data) return;
    const path = await api.capture_save_as(t.slot, data);
    if (path) toast(`Enregistrée : ${path.split(/[\\/]/).pop()}`);
  }

  /** tab : conversation choisie, ou "new" (ctx.pickDestination). */
  async function toChat(t, tab = "new") {
    const data = await imageData(t);
    if (!data) return;
    const path = await api.capture_temp_file(t.slot, data);
    if (!path) return;
    ctx.emit("chat:attach", { paths: [path], tab });
  }

  function snip() {
    commitText();
    api.capture_snip();
  }

  async function paste() {
    const count = await api.capture_paste();
    if (count === 0) toast("Aucune image dans le presse-papiers.", "info");
  }

  async function openDialog() {
    const res = await api.capture_open_dialog();
    for (const err of res?.errors || []) toast(err, "error");
  }

  function moreItems(t) {
    return [
      { label: "Capturer une zone", icon: "scan",
        hint: ctx.store.settings.print_screen_capture ? "Impr. écran" : "", onSelect: snip },
      { label: "Coller une image", icon: "clipboard-paste", hint: "Ctrl+V", onSelect: paste },
      { label: "Ouvrir une image…", icon: "folder-open", onSelect: openDialog },
      "-",
      { label: "Rétablir la capture d'origine", icon: "rotate-ccw", disabled: !t?.loaded,
        onSelect: () => restoreOriginal(t) },
      "-",
      { label: "Fermer la capture", icon: "x", disabled: !t, onSelect: () => closeTab(t.slot) },
    ];
  }

  function canvasMenu(t, ev) {
    ev.preventDefault();
    if (!t.loaded || ev.target.closest(".paint-text, .sel-bar")) return;
    commitText();
    openMenu([
      ...(t.sel ? [...selectionItems(t).slice(0, 2), "-"] : []),
      { label: "Annuler", icon: "undo-2", hint: "Ctrl+Z", disabled: !t.undo.length, onSelect: () => undo(t) },
      { label: "Rétablir", icon: "redo-2", hint: "Ctrl+Y", disabled: !t.redo.length, onSelect: () => redo(t) },
      "-",
      { label: "Copier l'image", icon: "copy", hint: t.sel ? "" : "Ctrl+C", onSelect: () => copyImage(t) },
      { label: "Enregistrer sous…", icon: "download", hint: "Ctrl+S", onSelect: () => saveAs(t) },
      ctx.store.ollama ? ctx.destinationMenuItem("chat", { label: "Joindre à une conversation",
        newLabel: "Joindre à une nouvelle conversation", icon: "message-square" }, (tab) => toChat(t, tab)) : null,
      { label: "Tout sélectionner", icon: "square-dashed", hint: "Ctrl+A", onSelect: () => selectAll(t) },
      "-",
      { label: "Ajuster à la fenêtre", icon: "maximize", hint: "Ctrl+0", onSelect: () => fitZoom(t) },
      { label: "Taille réelle (100 %)", icon: "search", onSelect: () => setZoom(t, 1, { x: ev.clientX, y: ev.clientY }) },
    ], ev.clientX, ev.clientY);
  }

  // ── Clavier ─────────────────────────────────
  document.addEventListener("keydown", (ev) => {
    if (!ctx.isFocused("captures") || isTyping(ev.target) || document.querySelector(".modal-backdrop")) return;
    const t = activeTab();
    const key = ev.key.toLowerCase();
    if (ev.ctrlKey && !ev.altKey) {
      if (key === "z" && !ev.shiftKey) undo(t);
      else if (key === "y" || (key === "z" && ev.shiftKey)) redo(t);
      else if (key === "c") copy(t);
      else if (key === "a") selectAll(t);
      else if (key === "x" && ev.shiftKey) cropToSelection(t);
      else if (key === "s") saveAs(t);
      else if (key === "v") paste();
      else if (key === "0") fitZoom(t);
      else if (key === "=" || key === "+") zoomStep(t, 1);
      else if (key === "-") zoomStep(t, -1);
      else return;
      ev.preventDefault();
      return;
    }
    if (ev.altKey || ev.metaKey || !t) return;
    if (ev.key === " ") {
      ev.preventDefault();
      spaceDown = true;
      stage.classList.add("can-pan");
      brush.hidden = true;
    } else if (ev.key === "Escape") {
      if (drag) cancelDrag();
      else clearSelection(t);
    } else if (ev.key === "Delete" && t.sel && !drag) {
      ev.preventDefault();
      eraseSelection(t);
    } else if (ev.key.startsWith("Arrow") && t.sel && !drag) {
      ev.preventDefault();
      const step = ev.shiftKey ? 10 : 1;
      nudgeSelection(t, { ArrowLeft: -step, ArrowRight: step }[ev.key] || 0,
        { ArrowUp: -step, ArrowDown: step }[ev.key] || 0);
    } else if (!ev.ctrlKey && !ev.repeat) {
      const tool = TOOLS.find((x) => x !== "-" && x.key === key);
      if (tool) { ev.preventDefault(); setTool(tool.id); }
    }
  });
  document.addEventListener("keyup", (ev) => {
    if (ev.key === " ") { spaceDown = false; stage.classList.remove("can-pan"); }
  });
  window.addEventListener("blur", () => { spaceDown = false; stage.classList.remove("can-pan"); });

  new ResizeObserver(() => {
    const t = activeTab();
    if (t?.loaded && !textBox) refit(t);
  }).observe(stage);

  ctx.onCollect((st) => {
    commitText();
    for (const t of S.tabs.values()) {
      if (!t.loaded || (!t.dirty && !t.origDirty)) continue;
      st.captures[t.slot] = {
        image: t.dirty ? t.canvas.toDataURL("image/png") : null,
        orig: t.origDirty ? t.orig.toDataURL("image/png") : null,
      };
    }
  });

  // ── Évènements Python ───────────────────────
  on("capture:new", ({ tab, image }) => {
    if (S.tabs.has(tab.slot)) return;
    commitText();
    S.blank = false;   // la capture prend la place de l'onglet vierge
    ctx.navigate("captures");
    addTab(tab, { image, orig: null });
  });

  // ── Initialisation ──────────────────────────
  for (const data of state.captures?.tabs || []) {
    const t = makeTab(data);
    S.order.push(t.slot);
  }
  setTool(E.tool);
  setColor(E.color);
  setSize(E.size);
  setFill(E.fill);
  renderEmptyHint();
  if (S.order.length) select(S.order[S.order.length - 1]);
  else renderStrip();

  return {
    el,
    onShow() {
      renderEmptyHint();
      const t = activeTab();
      if (t?.loaded) refit(t);
    },
    onHide() {
      commitText();
      cancelDrag();
      for (const t of S.tabs.values()) t.save.flush();
    },
    onDrop(paths) {
      api.capture_open_paths(paths).then((res) => {
        for (const err of res?.errors || []) toast(err, "error");
      });
    },
  };
}
