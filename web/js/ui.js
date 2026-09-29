// Composants partagés : notifications, menus contextuels, modales, onglets, infobulles.
import { h, frag } from "./dom.js";
import { icon } from "./icons.js";

/** Élément <span> contenant une icône SVG. */
export function ico(name, size = 16, stroke) {
  return frag(icon(name, size, stroke));
}

export function iconButton(name, tip, onClick, { size = 16, cls = "", kbd = "" } = {}) {
  const btn = h("button", { class: `icon-btn ${cls}`.trim(), type: "button", "aria-label": tip,
    dataset: { tip, ...(kbd ? { kbd } : {}) }, onClick });
  btn.append(ico(name, size));
  return btn;
}

// ─────────────────────────────────────────────
//  Notifications
// ─────────────────────────────────────────────
let toastHost = null;

export function toast(text, kind = "success", ms = 2600) {
  if (!text) return;
  if (!toastHost) {
    toastHost = h("div", { class: "toasts", role: "status", "aria-live": "polite" });
    document.body.append(toastHost);
  }
  const name = kind === "error" ? "triangle-alert" : kind === "info" ? "info" : "check";
  const el = h("div", { class: `toast ${kind}` }, ico(name, 15), h("span", { text }));
  toastHost.append(el);
  while (toastHost.children.length > 4) toastHost.firstElementChild.remove();
  setTimeout(() => {
    el.classList.add("out");
    setTimeout(() => el.remove(), 220);
  }, kind === "error" ? Math.max(ms, 5000) : ms);
}

// ─────────────────────────────────────────────
//  Menus contextuels
//  items : { label, icon, hint, checked, disabled, danger, onSelect, submenu }
//          | "-" (séparateur) | { section: "Titre" }
// ─────────────────────────────────────────────
const openMenus = [];

export function closeMenus(fromLevel = 0) {
  while (openMenus.length > fromLevel) openMenus.pop().el.remove();
}

function buildMenu(items, level) {
  const el = h("div", { class: "menu", role: "menu" });
  const entries = [];
  let subTimer = null;

  const focusEntry = (entry) => {
    for (const e of entries) e.el.classList.toggle("focus", e === entry);
  };

  for (const item of items) {
    if (!item) continue;
    if (item === "-") { el.append(h("div", { class: "menu-sep" })); continue; }
    if (item.section) { el.append(h("div", { class: "menu-section", text: item.section })); continue; }
    const row = h("div", {
      class: `menu-item${item.disabled ? " disabled" : ""}${item.danger ? " danger" : ""}`,
      role: "menuitem",
    });
    if (item.checked !== undefined) {
      row.append(h("span", { class: "mi-check" }, item.checked ? ico("check", 14) : null));
    } else if (item.icon) {
      row.append(ico(item.icon, 15));
    }
    row.append(h("span", { class: "mi-label", text: item.label }));
    if (item.hint) row.append(h("span", { class: "mi-hint", text: item.hint }));
    if (item.submenu) row.append(ico("chevron-right", 14));
    el.append(row);
    const entry = { el: row, item };
    entries.push(entry);

    row.addEventListener("mouseenter", () => {
      focusEntry(entry);
      clearTimeout(subTimer);
      subTimer = setTimeout(() => {
        closeMenus(level + 1);
        if (item.submenu && !item.disabled) openSub(entry);
      }, item.submenu ? 90 : 160);
    });
    row.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (item.disabled) return;
      if (item.submenu) { clearTimeout(subTimer); closeMenus(level + 1); openSub(entry); return; }
      closeMenus();
      item.onSelect?.();
    });
  }

  function openSub(entry) {
    const r = entry.el.getBoundingClientRect();
    showMenu(entry.item.submenu, r.right + 2, r.top - 6, level + 1, r.left - 2);
  }

  return { el, entries, focusEntry, openSub };
}

function place(el, x, y, altX) {
  const { innerWidth: W, innerHeight: H } = window;
  const r = el.getBoundingClientRect();
  let left = x;
  if (left + r.width > W - 6) left = altX !== undefined ? altX - r.width : W - r.width - 6;
  let top = y;
  if (top + r.height > H - 6) top = Math.max(6, H - r.height - 6);
  el.style.left = `${Math.max(6, left)}px`;
  el.style.top = `${Math.max(6, top)}px`;
}

function showMenu(items, x, y, level = 0, altX) {
  closeMenus(level);
  const menu = buildMenu(items, level);
  menu.el.style.left = "-9999px";
  document.body.append(menu.el);
  place(menu.el, x, y, altX);
  openMenus.push(menu);
  return menu;
}

/** Ouvre un menu contextuel à la position (x, y). */
export function openMenu(items, x, y) {
  closeTooltip();
  return showMenu(items, x, y, 0);
}

/** Ouvre un menu sous un élément (bouton), ou au-dessus s'il n'y a pas la place. */
export function openMenuBelow(items, anchor, align = "left") {
  const r = anchor.getBoundingClientRect();
  const menu = openMenu(items, r.left, r.bottom + 6);
  const { offsetWidth: w, offsetHeight: hgt } = menu.el;
  const fitsBelow = r.bottom + 6 + hgt <= window.innerHeight - 6;
  place(menu.el, align === "right" ? r.right - w : r.left, fitsBelow ? r.bottom + 6 : r.top - hgt - 6);
  return menu;
}

document.addEventListener("mousedown", (ev) => {
  if (openMenus.length && !ev.target.closest(".menu")) closeMenus();
}, true);
window.addEventListener("blur", () => closeMenus());
window.addEventListener("resize", () => closeMenus());
document.addEventListener("wheel", (ev) => {
  if (openMenus.length && !ev.target.closest(".menu")) closeMenus();
}, { passive: true });

document.addEventListener("keydown", (ev) => {
  if (!openMenus.length) return;
  const menu = openMenus[openMenus.length - 1];
  const enabled = menu.entries.filter((e) => !e.item.disabled);
  const current = enabled.findIndex((e) => e.el.classList.contains("focus"));
  if (ev.key === "Escape") { closeMenus(openMenus.length - 1); }
  else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
    const step = ev.key === "ArrowDown" ? 1 : -1;
    const next = enabled[(current + step + enabled.length) % enabled.length];
    if (next) menu.focusEntry(next);
  } else if (ev.key === "ArrowRight") {
    const cur = enabled[current];
    if (cur?.item.submenu) {
      menu.openSub(cur);
      const sub = openMenus[openMenus.length - 1];
      sub.focusEntry(sub.entries.find((e) => !e.item.disabled));
    }
  } else if (ev.key === "ArrowLeft") {
    if (openMenus.length > 1) closeMenus(openMenus.length - 1);
  } else if (ev.key === "Enter") {
    enabled[current]?.el.click();
  } else return;
  ev.preventDefault();
  ev.stopPropagation();
}, true);

// ─────────────────────────────────────────────
//  Modales
// ─────────────────────────────────────────────
/**
 * openModal({ title, subtitle, body, actions, width, onClose })
 * actions : [{ label, kind: "primary" | "ghost" | "danger", left, onClick }]
 * onClick peut retourner false (ou une promesse de false) pour garder la modale ouverte.
 */
export function openModal({ title, subtitle, body, actions = [], width, onClose }) {
  closeMenus();
  const backdrop = h("div", { class: "modal-backdrop" });
  const box = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-label": title });
  if (width) box.style.width = `min(${width}px, 100%)`;
  const errorEl = h("div", { class: "error-text", hidden: true });
  const head = h("div", { class: "modal-head" },
    h("div", {}, h("h2", { text: title }), subtitle ? h("div", { class: "sub", text: subtitle }) : null),
    iconButton("x", "Fermer", () => close()));
  const bodyEl = h("div", { class: "modal-body" }, body, errorEl);
  const foot = h("div", { class: "modal-foot" });
  box.append(head, bodyEl);
  if (actions.length) box.append(foot);

  let closed = false;
  const previousFocus = document.activeElement;
  function close() {
    if (closed) return;
    closed = true;
    backdrop.remove();
    document.removeEventListener("keydown", onKey, true);
    onClose?.();
    previousFocus?.focus?.();
  }

  for (const action of actions) {
    const btn = h("button", {
      class: `btn ${action.kind || ""} ${action.left ? "left" : ""}`.trim(),
      type: "button",
    }, action.icon ? ico(action.icon, 15) : null, action.label);
    btn.addEventListener("click", async () => {
      if (!action.onClick) return close();
      btn.disabled = true;
      try {
        const keep = await action.onClick();
        if (keep !== false) close();
      } finally {
        btn.disabled = false;
      }
    });
    foot.append(btn);
  }

  function onKey(ev) {
    if (openMenus.length) return;
    if (ev.key === "Escape") { ev.preventDefault(); ev.stopPropagation(); close(); }
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      const primary = foot.querySelector(".btn.primary");
      if (primary) { ev.preventDefault(); primary.click(); }
    }
  }
  document.addEventListener("keydown", onKey, true);
  backdrop.addEventListener("mousedown", (ev) => { if (ev.target === backdrop) close(); });
  backdrop.append(box);
  document.body.append(backdrop);

  const autofocus = box.querySelector("[autofocus]") || foot.querySelector(".btn.primary");
  setTimeout(() => autofocus?.focus(), 30);

  return {
    el: box,
    close,
    setError(msg) {
      errorEl.textContent = msg || "";
      errorEl.hidden = !msg;
    },
  };
}

// ─────────────────────────────────────────────
//  Infobulles (attribut data-tip, raccourci optionnel data-kbd)
// ─────────────────────────────────────────────
let tipEl = null;
let tipTimer = null;
let tipTarget = null;

export function closeTooltip() {
  clearTimeout(tipTimer);
  tipTimer = null;
  tipTarget = null;
  tipEl?.remove();
  tipEl = null;
}

document.addEventListener("mouseover", (ev) => {
  const target = ev.target.closest?.("[data-tip]");
  if (target === tipTarget) return;
  closeTooltip();
  if (!target || openMenus.length) return;
  tipTarget = target;
  tipTimer = setTimeout(() => {
    if (!document.body.contains(target)) return;
    tipEl = h("div", { class: "tooltip" }, target.dataset.tip,
      target.dataset.kbd ? h("span", { class: "tip-kbd", text: target.dataset.kbd }) : null);
    document.body.append(tipEl);
    const r = target.getBoundingClientRect();
    const t = tipEl.getBoundingClientRect();
    let top = r.bottom + 7;
    if (top + t.height > window.innerHeight - 4) top = r.top - t.height - 7;
    let left = r.left + r.width / 2 - t.width / 2;
    left = Math.min(Math.max(6, left), window.innerWidth - t.width - 6);
    tipEl.style.top = `${top}px`;
    tipEl.style.left = `${left}px`;
  }, 480);
});
document.addEventListener("mousedown", closeTooltip, true);
document.addEventListener("wheel", closeTooltip, { passive: true });

// ─────────────────────────────────────────────
//  Barre d'onglets (conversations, consoles, notes, captures)
// ─────────────────────────────────────────────
export class TabStrip {
  /**
   * @param {object} o
   * @param {(id) => void} o.onSelect
   * @param {(id) => void} o.onClose
   * @param {() => void} o.onNew
   * @param {(id, label) => void} o.onRename
   * @param {(id, event) => void} o.onMenu
   * @param {string} o.newLabel   infobulle du bouton +
   * @param {string} o.placeholder titre affiché pour un onglet sans nom
   */
  constructor(o) {
    this.o = o;
    this.tabs = [];
    this.activeId = null;
    this.el = h("div", { class: "tabs", role: "tablist" });
    this.addBtn = iconButton("plus", o.newLabel || "Nouvel onglet", () => o.onNew(), { cls: "tab-add" });
    this.el.addEventListener("wheel", (ev) => {
      if (Math.abs(ev.deltaY) > Math.abs(ev.deltaX)) this.el.scrollLeft += ev.deltaY;
    }, { passive: true });
  }

  render(tabs, activeId) {
    this.tabs = tabs;
    this.activeId = activeId;
    if (this.renaming) return;
    this.el.replaceChildren(...tabs.map((t) => this._tab(t)), this.addBtn);
    const active = this.el.querySelector(".tab.active");
    // Dernier onglet actif : on montre aussi le bouton + qui le suit
    const target = active?.nextElementSibling === this.addBtn ? this.addBtn : active;
    target?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  _tab(t) {
    const label = t.title || this.o.placeholder || "Sans titre";
    const el = h("div", {
      class: `tab${t.id === this.activeId ? " active" : ""}`,
      role: "tab",
      tabindex: t.id === this.activeId ? "0" : "-1",
      "aria-selected": String(t.id === this.activeId),
      dataset: { id: String(t.id), tip: label.length > 22 ? label : undefined },
    });
    if (t.busy) el.append(h("span", { class: "tab-dot" }));
    else if (t.dim) el.append(h("span", { class: "tab-dot dim" }));
    el.append(h("span", { class: `tab-label${t.title ? "" : " placeholder"}`, text: label }));
    const close = h("span", { class: "tab-close", role: "button", "aria-label": "Fermer",
      dataset: { tip: "Fermer" } }, ico("x", 13));
    el.append(close);

    el.addEventListener("click", (ev) => {
      if (close.contains(ev.target)) { this.o.onClose(t.id); return; }
      this.o.onSelect(t.id);
    });
    el.addEventListener("mousedown", (ev) => { if (ev.button === 1) ev.preventDefault(); });
    el.addEventListener("auxclick", (ev) => { if (ev.button === 1) this.o.onClose(t.id); });
    el.addEventListener("dblclick", (ev) => {
      if (!close.contains(ev.target)) this.rename(t.id);
    });
    el.addEventListener("contextmenu", (ev) => {
      ev.preventDefault();
      this.o.onMenu?.(t.id, ev);
    });
    el.addEventListener("keydown", (ev) => {
      if (ev.key === "F2") { ev.preventDefault(); this.rename(t.id); }
      if (ev.key === "ArrowRight" || ev.key === "ArrowLeft") {
        const idx = this.tabs.findIndex((x) => x.id === t.id);
        const next = this.tabs[idx + (ev.key === "ArrowRight" ? 1 : -1)];
        if (next) {
          this.o.onSelect(next.id);
          this.el.querySelector(`.tab[data-id="${CSS.escape(String(next.id))}"]`)?.focus();
        }
      }
    });
    return el;
  }

  /** Renommage en place (double-clic, F2 ou menu). */
  rename(id) {
    const el = this.el.querySelector(`.tab[data-id="${CSS.escape(String(id))}"]`);
    const tab = this.tabs.find((t) => t.id === id);
    if (!el || !tab) return;
    this.renaming = true;
    const input = h("input", { class: "tab-rename", value: tab.label || tab.title || "",
      placeholder: "Nom de l'onglet", spellcheck: "false" });
    const labelEl = el.querySelector(".tab-label");
    labelEl.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const finish = (commit) => {
      if (done) return;
      done = true;
      this.renaming = false;
      if (commit) this.o.onRename(id, input.value.trim());
      else this.render(this.tabs, this.activeId);
    };
    input.addEventListener("keydown", (ev) => {
      ev.stopPropagation();
      if (ev.key === "Enter") finish(true);
      if (ev.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
    input.addEventListener("click", (ev) => ev.stopPropagation());
    input.addEventListener("dblclick", (ev) => ev.stopPropagation());
  }
}

// ─────────────────────────────────────────────
//  Divers
// ─────────────────────────────────────────────
/** Affiche une combinaison "ctrl+shift+d" sous forme de touches. */
export function kbdCombo(combo) {
  const names = { ctrl: "Ctrl", shift: "Maj", alt: "Alt", win: "Win", windows: "Win", cmd: "Cmd",
    enter: "Entrée", space: "Espace", esc: "Échap", "print screen": "Impr. écran" };
  const parts = String(combo).split("+").map((p) => p.trim()).filter(Boolean);
  const out = [];
  parts.forEach((p, i) => {
    if (i) out.push(h("span", { class: "kbd-plus", text: "+" }));
    const key = names[p.toLowerCase()] || (p.length === 1 ? p.toUpperCase() : p.toUpperCase());
    out.push(h("kbd", { text: key }));
  });
  return out;
}

/** Bouton de lecture TTS : met à jour son apparence selon l'état global. */
export function setSpeakingButton(btn, speaking) {
  btn.classList.toggle("speaking", speaking);
  btn.replaceChildren(ico(speaking ? "square" : "volume-2", speaking ? 14 : 16));
  btn.dataset.tip = speaking ? "Arrêter la lecture" : btn.dataset.tipIdle || btn.dataset.tip;
}
