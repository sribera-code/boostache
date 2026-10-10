// Petits utilitaires DOM.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/**
 * Crée un élément : h("button", { class: "btn", onClick: fn }, "Texte", enfant…)
 * Attributs spéciaux : class, text, html, style (objet), dataset (objet), on<Event>.
 */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "html") el.innerHTML = value;
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key === "dataset") {
      for (const [k, v] of Object.entries(value)) if (v != null) el.dataset[k] = v;
    }
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

/** Élément à partir d'un fragment HTML de confiance (icônes). */
export function frag(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
}

export function debounce(fn, ms) {
  let timer = null;
  let lastArgs = null;
  const wrapped = (...args) => {
    lastArgs = args;
    clearTimeout(timer);
    timer = setTimeout(() => { timer = null; fn(...lastArgs); }, ms);
  };
  wrapped.flush = () => {
    if (timer) { clearTimeout(timer); timer = null; fn(...lastArgs); }
  };
  wrapped.cancel = () => { clearTimeout(timer); timer = null; };
  wrapped.pending = () => timer !== null;
  return wrapped;
}

/** Limite un rendu à une fois par intervalle (dernier appel garanti). */
export function throttle(fn, ms) {
  let last = 0;
  let timer = null;
  return (...args) => {
    const wait = ms - (Date.now() - last);
    clearTimeout(timer);
    if (wait <= 0) { last = Date.now(); fn(...args); }
    else timer = setTimeout(() => { last = Date.now(); fn(...args); }, wait);
  };
}

export function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

const pad = (n) => String(n).padStart(2, "0");

/** "14:32:05" aujourd'hui, "27/09 14:32" sinon. */
export function formatTimestamp(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso || "";
  const now = new Date();
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  if (d.toDateString() === now.toDateString()) return time;
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Date d'un horodatage ISO, ou null s'il manque ou est illisible. */
export function parseDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

const sameDay = (a, b) => a.toDateString() === b.toDateString();

/** "14:32". */
export const formatHour = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** "14:32" aujourd'hui, "27/09 14:32" sinon. */
export const formatShort = (d) => (sameDay(d, new Date()) ? formatHour(d)
  : `${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${formatHour(d)}`);

/** "Aujourd'hui", "Hier", "Lundi 6 octobre", "6 octobre 2025" les autres années. */
export function formatDay(d) {
  const today = new Date();
  if (sameDay(d, today)) return "Aujourd'hui";
  if (sameDay(d, new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1))) return "Hier";
  const text = d.toLocaleDateString("fr-FR", d.getFullYear() === today.getFullYear()
    ? { weekday: "long", day: "numeric", month: "long" }
    : { day: "numeric", month: "long", year: "numeric" });
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "mercredi 8 octobre 2026 à 14:32:05". */
export function formatFullDate(d) {
  const day = d.toLocaleDateString("fr-FR", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  return `${day} à ${formatHour(d)}:${pad(d.getSeconds())}`;
}

export function plural(n, one, many) {
  return `${n.toLocaleString("fr-FR")} ${n > 1 ? many : one}`;
}

/** Vrai si l'utilisateur est en train de saisir du texte dans un champ. */
export function isTyping(target = document.activeElement) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === "TEXTAREA" || (tag === "INPUT" && !["checkbox", "radio", "button"].includes(target.type))
    || target.isContentEditable;
}
