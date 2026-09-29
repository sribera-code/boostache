// Vue Tâches : tâches planifiées (tasks.py + tâches créées ici).
import { h } from "../dom.js";
import { ico, iconButton, openMenu, openModal, toast } from "../ui.js";

export function createTasksView(ctx, state) {
  const { api } = ctx;
  let rows = state.tasks || [];
  let timer = null;

  const tbody = h("tbody");
  const table = h("table", { class: "grid" },
    h("thead", {}, h("tr", {},
      h("th", { text: "Tâche" }), h("th", { text: "Périodicité" }),
      h("th", { text: "Prochaine exécution" }), h("th", { "aria-label": "Actions" }))),
    tbody);
  const wrap = h("div", { class: "table-wrap" }, table);
  const el = h("section", {},
    h("header", { class: "view-head" },
      h("span", { class: "view-title", text: "Tâches planifiées" }),
      h("div", { class: "head-spacer" }),
      h("button", { class: "btn primary sm", type: "button", onClick: () => editor() },
        ico("plus", 15), "Nouvelle tâche")),
    wrap,
    h("div", { class: "foot-note" }, ico("info", 13),
      h("span", {}, "Les tâches de ", h("code", { text: "tasks.py" }),
        " se modifient dans le code. Le code des tâches perso a accès à ", h("code", { text: "logger" }), ".")));

  wrap.addEventListener("contextmenu", (ev) => {
    if (ev.target.closest("tbody tr")) return;
    ev.preventDefault();
    openMenu([{ label: "Nouvelle tâche…", icon: "plus", onSelect: () => editor() }], ev.clientX, ev.clientY);
  });

  function render() {
    if (!rows.length) {
      tbody.replaceChildren(h("tr", {}, h("td", { colspan: "4" },
        h("div", { class: "empty" }, h("div", { class: "glyph" }, ico("calendar-clock", 22)),
          h("h3", { text: "Aucune tâche" }), h("p", { text: "Créez une tâche pour exécuter du code Python à intervalle régulier." })))));
      return;
    }
    tbody.replaceChildren(...rows.map((r) => {
      const tr = h("tr", {},
        h("td", {}, r.label, r.custom ? h("span", { class: "pill", text: "perso" }) : null),
        h("td", { class: "num", text: r.period }),
        h("td", { class: "num", text: r.next_run }),
        h("td", { class: "actions" }, h("div", { class: "row-actions" },
          iconButton("play", "Exécuter maintenant", () => run(r), { size: 15, cls: "sm" }),
          r.custom ? iconButton("pencil", "Modifier", () => editor(r.idx), { size: 15, cls: "sm" }) : null,
          r.custom ? iconButton("trash-2", "Supprimer", () => remove(r), { size: 15, cls: "sm" }) : null)));
      tr.addEventListener("dblclick", () => { if (r.custom) editor(r.idx); });
      tr.addEventListener("contextmenu", (ev) => {
        ev.preventDefault();
        openMenu([
          { label: "Nouvelle tâche…", icon: "plus", onSelect: () => editor(null, r.idx) },
          "-",
          { label: "Exécuter maintenant", icon: "play", onSelect: () => run(r) },
          { label: "Modifier…", icon: "pencil", disabled: !r.custom, onSelect: () => editor(r.idx) },
          { label: "Supprimer", icon: "trash-2", danger: true, disabled: !r.custom, onSelect: () => remove(r) },
        ], ev.clientX, ev.clientY);
      });
      return tr;
    }));
  }

  async function refresh() {
    const res = await api.tasks_list();
    if (res) { rows = res; render(); }
  }

  function run(r) {
    api.task_run(r.idx);
    toast(`« ${r.label} » exécutée.`);
  }

  async function remove(r) {
    const res = await api.task_delete(r.idx);
    if (res?.ok) { rows = res.tasks; render(); toast("Tâche supprimée."); }
    else if (res?.error) toast(res.error, "error");
  }

  /** Création (editIdx null) ou modification d'une tâche perso. */
  function editor(editIdx = null, insertAfter = null) {
    const r = editIdx !== null ? rows.find((x) => x.idx === editIdx) : null;
    const v = r || { label: "Ma tâche", sched_type: "interval", interval_value: 30,
      interval_unit: "minutes", at_time: "09:00", action_code: 'logger.log("Ma tâche s\'exécute ✓")' };

    const label = h("input", { class: "field", value: v.label, autofocus: true, spellcheck: "false" });
    let kind = v.sched_type === "fixed" ? "fixed" : "interval";
    const segInterval = h("button", { type: "button", text: "Intervalle" });
    const segFixed = h("button", { type: "button", text: "Heure fixe" });
    const amount = h("input", { class: "field", type: "number", min: "1", value: String(v.interval_value) });
    const unit = h("select", { class: "field" },
      ...["secondes", "minutes", "heures"].map((u) => h("option", { value: u, text: u, selected: u === v.interval_unit })));
    const time = h("input", { class: "field", type: "time", value: v.at_time });
    const intervalRow = h("div", { class: "row", style: { justifyContent: "flex-start", gap: "10px" } },
      h("span", { class: "muted", text: "Toutes les" }), amount, unit);
    const fixedRow = h("div", { class: "row", style: { justifyContent: "flex-start", gap: "10px" } },
      h("span", { class: "muted", text: "Chaque jour à" }), time);
    const code = h("textarea", { class: "field mono", rows: "7", spellcheck: "false" });
    code.value = v.action_code;
    code.addEventListener("keydown", (ev) => {
      if (ev.key === "Tab") { ev.preventDefault(); document.execCommand("insertText", false, "    "); }
    });

    const setKind = (k) => {
      kind = k;
      segInterval.classList.toggle("active", k === "interval");
      segFixed.classList.toggle("active", k === "fixed");
      intervalRow.hidden = k !== "interval";
      fixedRow.hidden = k !== "fixed";
    };
    segInterval.addEventListener("click", () => setKind("interval"));
    segFixed.addEventListener("click", () => setKind("fixed"));
    setKind(kind);

    const body = h("div", { style: { display: "flex", flexDirection: "column", gap: "16px" } },
      h("div", {}, h("label", { class: "label", text: "Titre" }), label),
      h("div", {}, h("label", { class: "label", text: "Périodicité" }),
        h("div", { class: "segmented" }, segInterval, segFixed),
        h("div", { style: { marginTop: "10px" } }, intervalRow, fixedRow)),
      h("div", {}, h("label", { class: "label", text: "Action (Python)" }), code,
        h("div", { class: "hint-text", text: "Exécuté à chaque déclenchement. Variable disponible : logger (logger.log(\"…\"))." })));

    const modal = openModal({
      title: r ? "Modifier la tâche" : "Nouvelle tâche planifiée",
      subtitle: "Ctrl+Entrée pour enregistrer",
      body,
      width: 580,
      actions: [
        { label: "Annuler", kind: "ghost" },
        { label: r ? "Enregistrer" : "Créer", kind: "primary", onClick: async () => {
          const res = await api.task_save({
            label: label.value, sched_type: kind, interval_value: amount.value,
            interval_unit: unit.value, at_time: time.value, action_code: code.value,
          }, editIdx, insertAfter);
          if (!res?.ok) { modal.setError(res?.error || "Enregistrement impossible."); return false; }
          rows = res.tasks;
          render();
          toast(r ? "Tâche modifiée." : "Tâche créée.");
          return true;
        } },
      ],
    });
  }

  render();

  return {
    el,
    onShow() { refresh(); clearInterval(timer); timer = setInterval(refresh, 5000); },
    onHide() { clearInterval(timer); timer = null; },
  };
}
