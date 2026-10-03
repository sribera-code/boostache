// Zone principale : une section, ou deux côte à côte (écran partagé).
// Le volet actif — le dernier utilisé, souligné en haut — reçoit les sections
// choisies dans la barre latérale ; Ctrl+clic (ou clic du milieu) sur une
// section l'ouvre dans l'autre volet. Une vue n'existe qu'en un exemplaire :
// l'ouvrir dans l'autre volet l'y déplace.
import { h, debounce, throttle } from "./dom.js";
import { ico, openMenu, closeMenus } from "./ui.js";

const MIN_PANE = 320;                           // largeur minimale d'un volet (px)
const DRAG_TYPE = "application/x-boostache-view";

export function createLayout(ctx, views, main) {
  const { api, on, emit, store } = ctx;
  const L = { panes: [], focus: 0, ratio: 0.5, recent: [] };
  const layout = { onChange: null };

  const isSplit = () => L.panes.length === 2;
  const focused = () => L.panes[L.focus] ?? null;
  const save = debounce(() => ctx.saveSetting("layout", { panes: [...L.panes], ratio: L.ratio }), 400);
  const layoutChanged = throttle(() => emit("layout"), 40);

  const handle = h("div", { class: "split-handle", role: "separator", tabindex: "0",
    "aria-orientation": "vertical", "aria-label": "Séparateur des volets" });
  main.append(handle);

  // ── État → DOM ──────────────────────────────
  function apply() {
    const split = isSplit();
    main.classList.toggle("split", split);
    main.style.setProperty("--pane-a", String(L.ratio * 100));
    main.style.setProperty("--pane-b", String((1 - L.ratio) * 100));
    for (const [id, entry] of views) {
      const i = L.panes.indexOf(id);
      const el = entry.view.el;
      el.classList.toggle("active", i >= 0);
      el.classList.toggle("focused", split && i === L.focus);
      if (i >= 0) el.dataset.pane = String(i);
      else delete el.dataset.pane;
      if (!entry.nav) continue;
      entry.nav.classList.toggle("active", i >= 0 && i === L.focus);
      entry.nav.classList.toggle("open", split && i >= 0 && i !== L.focus);
      entry.nav.setAttribute("aria-current", i >= 0 && i === L.focus ? "page" : "false");
    }
    store.active = focused();
    layout.onChange?.(split);
  }

  /** Sections récemment utilisées : la plus récente accompagne la section affichée. */
  function touch(id) {
    if (id) L.recent = [id, ...L.recent.filter((x) => x !== id)].slice(0, 12);
  }

  function companion(exclude) {
    const ok = (id) => views.has(id) && !exclude.includes(id) && id !== "settings";
    return L.recent.find(ok) || [...views.keys()].find(ok);
  }

  /** Affiche `panes` (une ou deux sections, de gauche à droite), `focus` = volet actif. */
  function show(panes, focus = 0, { persist = true, giveFocus = true } = {}) {
    panes = panes.filter((id, i) => views.has(id) && panes.indexOf(id) === i).slice(0, 2);
    if (!panes.length) return;
    closeMenus();
    const before = L.panes;
    const wasSplit = isSplit();
    L.panes = panes;
    L.focus = Math.min(Math.max(0, focus), panes.length - 1);
    apply();
    for (const id of before) if (!panes.includes(id)) views.get(id).view.onHide?.();
    for (const id of panes) if (!before.includes(id)) views.get(id).view.onShow?.();
    touch(panes[1 - L.focus]);
    touch(focused());
    if (giveFocus) focusView(focused());
    emit("layout");
    if (persist && (wasSplit || isSplit())) save();
  }

  function setFocus(i) {
    if (!isSplit() || i < 0 || i > 1 || i === L.focus) return;
    L.focus = i;
    apply();
    touch(focused());
  }

  /** Donne le clavier à une section (son champ principal, ou rien d'une autre section). */
  function focusView(id) {
    const view = views.get(id)?.view;
    if (!view) return;
    // Un site intégré garde le clavier tant qu'on ne le rend pas à l'interface
    if (!view.native && !document.hasFocus()) api.focus_ui();
    if (view.focus) view.focus();
    else if (!view.el.contains(document.activeElement)) document.activeElement?.blur?.();
  }

  // ── Actions ─────────────────────────────────
  /** Barre latérale, Ctrl+1…9 : dans le volet actif (ou simple bascule si déjà affichée). */
  function navigate(id) {
    if (!views.has(id)) return;
    const i = L.panes.indexOf(id);
    if (i < 0) {
      const panes = [...L.panes];
      panes[L.focus] = id;
      show(panes, L.focus);
      return;
    }
    closeMenus();
    setFocus(i);
    focusView(id);
  }

  /** Ctrl+clic : dans l'autre volet (ouvre l'écran partagé au besoin). */
  function openBeside(id) {
    if (!views.has(id)) return;
    if (!isSplit()) {
      if (id === focused()) toggle();
      else show([focused(), id], 1);
      return;
    }
    const other = 1 - L.focus;
    if (L.panes[L.focus] === id) return;
    if (L.panes[other] === id) { navigate(id); return; }
    const panes = [...L.panes];
    panes[other] = id;
    show(panes, other);
  }

  /** Place une section à gauche (0) ou à droite (1). */
  function openIn(id, side) {
    if (!views.has(id)) return;
    let panes = [...L.panes];
    if (!isSplit()) {
      const current = panes[0];
      const mate = current === id ? companion([id]) : current;
      panes = side === 0 ? [id, mate] : [mate, id];
    } else {
      const at = panes.indexOf(id);
      if (at === side) { navigate(id); return; }
      if (at >= 0) panes.reverse();
      else panes[side] = id;
    }
    show(panes, side);
  }

  function closePane(side) {
    if (isSplit()) show([L.panes[1 - side]], 0);
  }

  /** Bouton et Ctrl+Maj+S : ouvre l'écran partagé (section récente à droite) ou le referme. */
  function toggle() {
    if (isSplit()) closePane(1 - L.focus);
    else show([L.panes[0], companion(L.panes)], 1);
  }

  function swap() {
    if (isSplit()) show([L.panes[1], L.panes[0]], 1 - L.focus);
  }

  /** F6 : passe au volet voisin. */
  function focusOther() {
    if (!isSplit()) return;
    setFocus(1 - L.focus);
    focusView(focused());
  }

  /** Retire une section de l'écran (fenêtre masquée : presse-papiers). */
  function evict(id) {
    const i = L.panes.indexOf(id);
    if (i < 0) return;
    const panes = [...L.panes];
    panes[i] = [...views.keys()].find((v) => !panes.includes(v) && v !== "clipboard" && v !== "settings");
    show(panes, L.focus, { giveFocus: false });
  }

  /** Démarrage : écran partagé de la session précédente, sinon la première section.
   *  Fenêtre masquée : le clavier sera donné à son affichage (window:shown). */
  function restore(saved, visible) {
    const ratio = Number(saved?.ratio);
    if (ratio >= 0.1 && ratio <= 0.9) L.ratio = ratio;
    const wanted = Array.isArray(saved?.panes) ? saved.panes : [];
    const panes = wanted.filter((id) => views.has(id) && id !== "clipboard").slice(0, 2);
    if (wanted.length === 2 && panes.length === 1) panes.push(companion(panes));
    show(panes.length === 2 ? panes : [[...views.keys()][0]], 0, { persist: false, giveFocus: !!visible });
  }

  // ── Volet actif ─────────────────────────────
  const paneOf = (target) => L.panes.indexOf(target?.closest?.(".view")?.dataset.view);
  main.addEventListener("pointerdown", (ev) => {
    setFocus(paneOf(ev.target));
    // Un pointerdown annulé (canevas de Captures) ne déplace pas le focus :
    // le champ de l'autre volet garderait le clavier
    const view = ev.target.closest?.(".view");
    const active = document.activeElement;
    if (view && active && active !== document.body && !view.contains(active)) active.blur();
  }, true);
  main.addEventListener("focusin", (ev) => setFocus(paneOf(ev.target)));
  // Clic dans un site intégré (contrôle natif : la page n'en voit rien)
  on("webpane:focus", ({ pane }) => {
    if (!document.hasFocus()) setFocus(L.panes.indexOf(pane));
  });

  // ── Séparateur ──────────────────────────────
  function setRatio(r) {
    const width = main.clientWidth - handle.offsetWidth;
    const min = width > 0 ? Math.min(0.5, MIN_PANE / width) : 0.15;
    L.ratio = Math.min(1 - min, Math.max(min, r));
    main.style.setProperty("--pane-a", String(L.ratio * 100));
    main.style.setProperty("--pane-b", String((1 - L.ratio) * 100));
    layoutChanged();
  }

  handle.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0) return;
    ev.preventDefault();
    closeMenus();
    try { handle.setPointerCapture(ev.pointerId); } catch { /* pointeur déjà relâché */ }
    handle.classList.add("dragging");
    document.body.classList.add("split-resizing");
    const box = main.getBoundingClientRect();
    const start = L.ratio;
    let done = false;
    const move = (e) => {
      if (!(e.buttons & 1)) { end(); return; }   // relâché hors de la page
      setRatio((e.clientX - box.left - handle.offsetWidth / 2) / Math.max(1, box.width - handle.offsetWidth));
    };
    const end = () => {
      if (done) return;
      done = true;
      handle.removeEventListener("pointermove", move);
      handle.classList.remove("dragging");
      document.body.classList.remove("split-resizing");
      emit("layout");
      if (L.ratio !== start) save();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end, { once: true });
    handle.addEventListener("lostpointercapture", end, { once: true });
  });
  handle.addEventListener("dblclick", () => { setRatio(0.5); save(); });
  handle.addEventListener("keydown", (ev) => {
    const step = { ArrowLeft: -0.02, ArrowRight: 0.02 }[ev.key];
    if (!step) return;
    ev.preventDefault();
    setRatio(L.ratio + step);
    save();
  });
  handle.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    openMenu([
      { label: "Inverser les volets", icon: "arrow-left-right", onSelect: swap },
      { label: "Parts égales", icon: "columns-2", hint: "Double-clic", onSelect: () => { setRatio(0.5); save(); } },
      "-",
      { label: "Fermer le volet gauche", icon: "panel-left-close", onSelect: () => closePane(0) },
      { label: "Fermer le volet droit", icon: "panel-right-close", onSelect: () => closePane(1) },
    ], ev.clientX, ev.clientY);
  });

  // ── Sections de la barre latérale ───────────
  function navMenu(id, ev) {
    const side = isSplit() ? L.panes.indexOf(id) : -1;
    openMenu([
      { section: "Écran partagé" },
      { label: "Ouvrir à gauche", checked: side === 0, onSelect: () => openIn(id, 0) },
      { label: "Ouvrir à droite", checked: side === 1, onSelect: () => openIn(id, 1) },
      side >= 0 ? "-" : null,
      side >= 0 ? { label: "Fermer ce volet", icon: "x", onSelect: () => closePane(side) } : null,
    ], ev.clientX, ev.clientY);
  }

  /** Glisser une section vers une moitié de la zone principale. */
  let dropEl = null;

  function hideDropZones() {
    dropEl?.remove();
    dropEl = null;
  }

  function showDropZones(id) {
    hideDropZones();
    const box = main.getBoundingClientRect();
    const label = views.get(id).item.label;
    const zones = [0, 1].map((side) => h("div", { class: "split-zone", style: { flexGrow: String(side ? 1 - L.ratio : L.ratio) } },
      h("div", { class: "inner" }, ico(side ? "panel-right" : "panel-left", 22),
        h("span", { text: `${label} ${side ? "à droite" : "à gauche"}` }))));
    const sideAt = (x) => (x < box.left + box.width * L.ratio ? 0 : 1);
    const el = h("div", { class: "split-drop", style: {
      left: `${box.left}px`, top: `${box.top}px`, width: `${box.width}px`, height: `${box.height}px` } }, zones);
    el.addEventListener("dragover", (ev) => {
      if (!ev.dataTransfer.types.includes(DRAG_TYPE)) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = "move";
      const side = sideAt(ev.clientX);
      zones.forEach((z, i) => z.classList.toggle("over", i === side));
    });
    el.addEventListener("dragleave", (ev) => {
      if (!el.contains(ev.relatedTarget)) zones.forEach((z) => z.classList.remove("over"));
    });
    el.addEventListener("drop", (ev) => {
      if (!ev.dataTransfer.types.includes(DRAG_TYPE)) return;
      ev.preventDefault();
      hideDropZones();
      openIn(id, sideAt(ev.clientX));
    });
    document.body.append(el);
    dropEl = el;
  }

  function bindNav(el, id) {
    el.draggable = true;
    el.addEventListener("contextmenu", (ev) => { ev.preventDefault(); navMenu(id, ev); });
    el.addEventListener("mousedown", (ev) => { if (ev.button === 1) ev.preventDefault(); });
    el.addEventListener("auxclick", (ev) => { if (ev.button === 1) openBeside(id); });
    el.addEventListener("dragstart", (ev) => {
      closeMenus();
      ev.dataTransfer.setData(DRAG_TYPE, id);
      ev.dataTransfer.effectAllowed = "move";
      // Pas dans dragstart même : Chrome peut annuler le glisser si le DOM change
      setTimeout(() => showDropZones(id), 0);
    });
    el.addEventListener("dragend", () => setTimeout(hideDropZones, 0));
  }

  return Object.assign(layout, {
    navigate, openBeside, openIn, closePane, toggle, swap, focusOther, evict, restore, bindNav, focusView,
    isSplit,
    focused,
    panes: () => [...L.panes],
    isVisible: (id) => L.panes.includes(id),
    isFocused: (id) => focused() === id,
    /** Section sous un élément (glisser-déposer de fichiers), à défaut celle du volet actif. */
    viewAt: (target) => target?.closest?.(".view.active")?.dataset.view || focused(),
    windowShown() {
      for (const id of L.panes) views.get(id).view.onWindowShown?.();
      focusView(focused());
    },
  });
}
