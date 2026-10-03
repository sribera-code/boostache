// Vue Raccourcis : raccourcis clavier globaux (bindings.py).
import { h } from "../dom.js";
import { ico, iconButton, openMenu, toast, kbdCombo } from "../ui.js";

export function createHotkeysView(ctx, state) {
  const { api, on } = ctx;
  let rows = state.hotkeys || [];

  const tbody = h("tbody");
  const el = h("section", {},
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Raccourcis" }),
      h("span", { class: "view-sub", text: "globaux" })),
    h("div", { class: "table-wrap" },
      h("table", { class: "grid" },
        h("thead", {}, h("tr", {}, h("th", { text: "Raccourci" }), h("th", { text: "Action" }), h("th", { "aria-label": "Actions" }))),
        tbody)),
    h("div", { class: "foot-note" }, ico("info", 13),
      h("span", {}, "Définis dans ", h("code", { text: "bindings.py" }),
        " (Impr. écran et Ctrl+Impr. écran : Paramètres → Captures) — actifs même quand la fenêtre est masquée (lancer en administrateur).")));

  function run(r) {
    api.hotkey_run(r.idx);
    toast(`${r.label} — déclenché.`);
  }

  function render() {
    if (!rows.length) {
      tbody.replaceChildren(h("tr", {}, h("td", { colspan: "3" },
        h("div", { class: "empty" }, h("div", { class: "glyph" }, ico("keyboard", 22)),
          h("h3", { text: "Aucun raccourci" }), h("p", { text: "Ajoutez-en dans bindings.py puis rechargez Boostache." })))));
      return;
    }
    tbody.replaceChildren(...rows.map((r) => {
      const tr = h("tr", {},
        h("td", { style: { width: "260px" } }, ...kbdCombo(r.combo)),
        h("td", { text: r.label }),
        h("td", { class: "actions" }, h("div", { class: "row-actions" },
          iconButton("play", "Exécuter maintenant", () => run(r), { size: 15, cls: "sm" }))));
      tr.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        openMenu([{ label: "Exécuter maintenant", icon: "play", onSelect: () => run(r) }], ev.clientX, ev.clientY);
      });
      return tr;
    }));
  }

  async function refresh() {
    const res = await api.hotkeys_list();
    if (res) { rows = res; render(); }
  }

  on("window:shown", refresh);
  render();

  return { el, onShow: refresh };
}
