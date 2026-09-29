// Vue Historique : journal de l'application.
import { h, plural } from "../dom.js";
import { ico, iconButton, openMenu } from "../ui.js";

const MAX_LINES = 3000;

export function createLogsView(ctx, state) {
  const { api, on } = ctx;
  let lastSeq = 0;
  let count = 0;

  const sub = h("span", { class: "view-sub" });
  const box = h("div", { class: "logs", tabindex: "0" });
  const el = h("section", {},
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Historique" }), sub,
      h("div", { class: "head-spacer" }),
      iconButton("copy", "Tout copier", copyAll),
      iconButton("eraser", "Effacer l'historique", clear)),
    box);

  function atBottom() {
    return box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  }

  function lineEl(line) {
    const m = /^\[(\d{2}:\d{2}:\d{2})\]\s?(.*)$/s.exec(line);
    const ts = m ? m[1] : "";
    const msg = m ? m[2] : line;
    const kind = /⚠|erreur|impossible|échec|indisponible/i.test(msg) ? " warn" : "";
    return h("div", { class: `log-line${kind}`, dataset: { line } },
      ts ? h("span", { class: "log-ts", text: `${ts} ` }) : null,
      h("span", { class: "log-msg", text: msg }));
  }

  function append(entries) {
    const stick = atBottom();
    const frag = document.createDocumentFragment();
    for (const [seq, line] of entries) {
      if (seq <= lastSeq) continue;
      lastSeq = seq;
      frag.append(lineEl(line));
      count += 1;
    }
    box.append(frag);
    while (box.childElementCount > MAX_LINES) box.firstElementChild.remove();
    sub.textContent = count ? plural(count, "ligne", "lignes") : "";
    if (stick) box.scrollTop = box.scrollHeight;
  }

  function text() {
    return [...box.querySelectorAll(".log-line")].map((n) => n.dataset.line).join("\n");
  }

  function copyAll() {
    const t = text();
    if (t) ctx.copy(t, "Historique copié");
  }

  function clear() {
    api.logs_clear();
    box.replaceChildren();
    count = 0;
    sub.textContent = "";
  }

  box.addEventListener("contextmenu", (ev) => {
    ev.preventDefault();
    const sel = window.getSelection().toString();
    openMenu([
      { label: "Copier la sélection", icon: "copy", hint: "Ctrl+C", disabled: !sel, onSelect: () => ctx.copy(sel) },
      { label: "Tout copier", icon: "copy", disabled: !count, onSelect: copyAll },
      "-",
      { label: "Effacer", icon: "eraser", disabled: !count, onSelect: clear },
    ], ev.clientX, ev.clientY);
  });

  on("log", ({ seq, line }) => append([[seq, line]]));
  append(state.logs || []);

  return {
    el,
    onShow() { box.scrollTop = box.scrollHeight; },
  };
}
