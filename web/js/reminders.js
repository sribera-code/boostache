// Rappels (reminders.py) : menu « Me le rappeler » (moments proposés, dont la veille et le
// jour d'une échéance trouvée dans le texte) et fenêtre pour choisir le texte, le jour et
// l'heure. Partagés par l'Assistant (messages arrivés) et la section Tâches.
import { h } from "./dom.js";
import { openMenuBelow, openModal, toast } from "./ui.js";

const pad = (n) => String(n).padStart(2, "0");
const isoDay = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const isoTime = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** Ajoute le rappel (ou le remet à plus tard : id) ; message de confirmation. */
async function save(api, { id, text, details, source }, at) {
  const res = id ? await api.reminder_snooze(id, at) : await api.reminder_add(text, at, details || "", source || null);
  if (!res?.ok) { toast(res?.error || "Rappel impossible.", "error"); return false; }
  toast(`Rappel ${id ? "remis à" : "prévu"} ${res.when || res.reminder?.when}`);
  return true;
}

/**
 * Menu sous anchor : moments proposés, puis « Choisir le jour et l'heure… ».
 * r : { text, details, source, hint } (hint : texte où chercher une échéance) ou { id, text } (remettre).
 */
export async function reminderMenu(ctx, anchor, r) {
  const choices = await ctx.api.reminder_suggestions(r.hint || `${r.text || ""} ${r.details || ""}`);
  // Bouton dans la moitié droite : menu aligné sur sa droite (les libellés sont longs)
  const align = anchor.getBoundingClientRect().left > window.innerWidth / 2 ? "right" : "left";
  openMenuBelow([
    { section: r.id ? "Remettre à plus tard" : "Me le rappeler" },
    ...(choices || []).map((c) => ({ label: c.label, icon: "alarm-clock", onSelect: () => save(ctx.api, r, c.at) })),
    "-",
    { label: "Choisir le jour et l'heure…", icon: "calendar-clock", onSelect: () => reminderDialog(ctx, r) },
  ], anchor, align);
}

/** Fenêtre : texte (nouveau rappel), jour et heure, moments proposés en raccourci. */
export async function reminderDialog(ctx, r = {}) {
  const { api } = ctx;
  const start = new Date(Date.now() + 3600 * 1000);
  start.setMinutes(Math.ceil(start.getMinutes() / 5) * 5, 0, 0);
  const text = h("input", { class: "field", value: r.text || "", placeholder: "Appeler le garage, payer la facture…",
    spellcheck: "true", autofocus: !r.id });
  const day = h("input", { class: "field", type: "date", value: isoDay(start), min: isoDay(new Date()) });
  const time = h("input", { class: "field", type: "time", value: isoTime(start), step: "300" });
  const chips = h("div", { class: "rem-choices" });
  const choices = await api.reminder_suggestions(r.hint || `${r.text || ""} ${r.details || ""}`);
  for (const c of choices || []) {
    chips.append(h("button", { class: "btn ghost sm", type: "button", text: c.label, onClick: () => {
      const d = new Date(c.at);
      day.value = isoDay(d);
      time.value = isoTime(d);
    } }));
  }
  const body = h("div", { class: "rem-form" },
    r.id ? h("div", { class: "rem-text", text: r.text }) :
      h("div", {}, h("label", { class: "label", text: "Me rappeler de" }), text),
    r.details && !r.id ? h("div", { class: "hint-text", text: r.details }) : null,
    h("div", { class: "rem-fields" },
      h("div", {}, h("label", { class: "label", text: "Le" }), day),
      h("div", {}, h("label", { class: "label", text: "À" }), time)),
    chips.children.length ? chips : null,
    h("div", { class: "hint-text", text: "Dit à voix haute et affiché en notification à ce moment-là ; "
      + "si Boostache est fermé, au lancement suivant." }));
  const modal = openModal({
    title: r.id ? "Remettre le rappel" : "Nouveau rappel",
    subtitle: "Ctrl+Entrée pour enregistrer",
    body,
    width: 520,
    actions: [
      { label: "Annuler", kind: "ghost" },
      { label: r.id ? "Remettre" : "Créer le rappel", kind: "primary", onClick: async () => {
        if (!r.id && !text.value.trim()) { modal.setError("Écrivez de quoi vous rappeler."); return false; }
        if (!day.value || !time.value) { modal.setError("Choisissez le jour et l'heure."); return false; }
        return save(api, { ...r, text: r.id ? r.text : text.value }, `${day.value}T${time.value}`);
      } },
    ],
  });
  if (r.id) setTimeout(() => time.focus(), 40);
}
