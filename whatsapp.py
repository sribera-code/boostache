"""
whatsapp.py – WhatsApp Web intégré (voir webpane.py).

Première ouverture : QR code à scanner avec le téléphone, la session est
ensuite conservée dans DATA_DIR/whatsapp.
"""

import json
import re
import time

from engine import logger
from webpane import IMPROVE_SCHEMA, STYLE_RULES, SUGGEST_SCHEMA, WebPane, clean, same_text

UNREAD_RE = re.compile(r"^\((\d+)\)")

# Discussion ouverte : titre, derniers messages affichés, brouillon.
# Repères de WhatsApp Web : #main (discussion), [data-pre-plain-text]
# (« [12:34, 29/09/2026] Nom: » sur chaque message texte), data-id (true_/false_).
READ_CHAT_JS = r"""
(() => {
  const main = document.querySelector("#main");
  if (!main) return { ok: false, error: "Ouvrez d'abord une discussion dans WhatsApp." };
  const BLOCKS = new Set(["P", "DIV", "LI"]);
  const textOf = (el) => {
    let s = "";
    for (const n of el.childNodes) {
      if (n.nodeType === 3) s += n.nodeValue;
      else if (n.nodeName === "IMG") s += n.alt || "";
      else if (n.nodeName === "BR") s += "\n";
      else if (n.nodeType === 1) s += textOf(n) + (BLOCKS.has(n.nodeName) ? "\n" : "");
    }
    return s;
  };
  const header = main.querySelector("header");
  const chat = (header?.querySelector("span[dir='auto']")?.textContent
    || header?.querySelector("[title]")?.getAttribute("title") || "").trim();
  const messages = [];
  for (const node of main.querySelectorAll("[data-pre-plain-text]")) {
    if (node.closest("footer") || node.parentElement.closest("[data-pre-plain-text]")) continue;
    const meta = /^\[([^\]]*)\]\s*([\s\S]*?):\s*$/.exec(node.getAttribute("data-pre-plain-text") || "");
    // data-id : « true_… » pour mes messages, « false_… » pour les autres
    const id = node.closest("[data-id]")?.getAttribute("data-id") || "";
    const mine = /^(true|false)_/.test(id) ? id.startsWith("true_")
      : !!node.closest(".message-out");
    const parts = [...node.querySelectorAll(".selectable-text")]
      .filter((e) => !e.parentElement.closest(".selectable-text") && !e.closest(".quoted-mention"));
    const text = (parts.length ? parts.map(textOf).join("\n") : textOf(node)).trim();
    if (text) messages.push({ mine, author: meta ? meta[2].trim() : "", time: meta ? meta[1] : "", text });
  }
  const box = main.querySelector("footer [contenteditable='true']");
  return { ok: true, chat, messages: messages.slice(-60), draft: box ? textOf(box).trim() : null };
})()
"""

# Remplacement du contenu de la zone de saisie, en plusieurs appels : l'éditeur
# de WhatsApp (Lexical) ne prend la sélection en compte qu'à l'événement
# selectionchange, qui arrive après le script qui l'a faite, et applique ses
# modifications de façon différée. Une ligne : execCommand (actif seulement si
# la page a le focus clavier) ; plusieurs lignes : un collage, traité d'un bloc.
SELECT_ALL_JS = r"""
(() => {
  const box = document.querySelector("#main footer [contenteditable='true']");
  if (!box) return { ok: false, error: "Zone de saisie introuvable : ouvrez une discussion." };
  box.focus();
  window.getSelection().selectAllChildren(box);
  return { ok: true };
})()
"""
TYPE_JS = r"""
((text) => ({ ok: document.execCommand("insertText", false, text) }))
"""
PASTE_JS = r"""
((text) => {
  const box = document.querySelector("#main footer [contenteditable='true']");
  const data = new DataTransfer();
  data.setData("text/plain", text);
  box.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  return { ok: true };
})
"""
COMPOSER_JS = r"""
(() => {
  const box = document.querySelector("#main footer [contenteditable='true']");
  return { ok: !!box, text: box ? box.innerText : "" };
})()
"""

SUGGEST_SYSTEM = (
    "Tu aides l'utilisateur à répondre sur WhatsApp ; il est « Moi » dans la discussion. "
    "Propose trois réponses qu'il pourrait envoyer maintenant, en réponse aux derniers messages. "
    "Elles doivent être différentes les unes des autres (par exemple accepter, nuancer ou poser une "
    "question, décliner poliment — selon ce qui a du sens), courtes (une ou deux phrases), naturelles, "
    "écrites dans la langue de la discussion et sur le même ton (tutoiement ou vouvoiement, emojis "
    "seulement si la discussion en contient). N'invente pas de faits précis (dates, lieux, chiffres) "
    "absents de la discussion. " + STYLE_RULES + "Si une consigne est donnée, respecte-la. "
    'Réponds uniquement en JSON : {"replies": ["…", "…", "…"]}.'
)
IMPROVE_SYSTEM = (
    "Tu améliores le brouillon d'un message WhatsApp que l'utilisateur (« Moi ») s'apprête à envoyer. "
    "Corrige l'orthographe, la grammaire et la ponctuation, et rends le message clair et naturel, en "
    "gardant son sens, sa langue, son ton (tutoiement ou vouvoiement) et ses emojis. N'ajoute aucune "
    "information. " + STYLE_RULES + "Si une consigne est donnée, applique-la. "
    'Réponds uniquement en JSON : {"text": "…"}.'
)
CONTEXT_MESSAGES = 30
CONTEXT_CHARS = 6000


def _transcript(chat: str, messages: list[dict], limit: int = CONTEXT_MESSAGES) -> str:
    """Derniers messages en texte ; les plus anciens sont coupés au-delà de CONTEXT_CHARS."""
    lines, size = [], 0
    for m in reversed(messages[-limit:]):
        who = "Moi" if m.get("mine") else (m.get("author") or chat or "Contact")
        hour = (m.get("time") or "").split(",")[0].strip()
        line = f"[{hour}] {who} : {m['text'][:800]}" if hour else f"{who} : {m['text'][:800]}"
        size += len(line)
        if lines and size > CONTEXT_CHARS:
            break
        lines.append(line)
    head = f"Discussion WhatsApp avec « {chat} »" if chat else "Discussion WhatsApp"
    return f"{head} (du plus ancien au plus récent) :\n" + "\n".join(reversed(lines))


class WhatsAppPane(WebPane):
    key = "whatsapp"
    name = "WhatsApp"
    url = "https://web.whatsapp.com/"
    hosts = ("whatsapp.com", "whatsapp.net")
    read_js = READ_CHAT_JS

    def unread_from_title(self, title: str) -> int:
        m = UNREAD_RE.match(title)
        return int(m.group(1)) if m else 0

    def insert(self, text: str) -> dict:
        """Remplace le contenu de la zone de saisie (sans l'envoyer) et donne le clavier à WhatsApp."""
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "Rien à insérer."}
        self._focus_page()
        previous = str(self._eval(COMPOSER_JS).get("text") or "").strip()
        attempts = [(PASTE_JS, text), (TYPE_JS, " ".join(text.split()))] if "\n" in text else [(TYPE_JS, text)]
        after = {}
        for script, value in attempts:
            res = self._eval(SELECT_ALL_JS)
            if not res.get("ok"):
                return res
            time.sleep(0.05)
            self._eval(f"{script}({json.dumps(value)})")
            after = self._eval(COMPOSER_JS)
            if same_text(after.get("text"), value):
                return {"ok": True, "previous": previous}
        logger.log(f"WhatsApp : insertion refusée par la zone de saisie ({after!r})")
        return {"ok": False, "error": "WhatsApp n'a pas accepté le texte : cliquez dans la zone de saisie puis réessayez."}

    def _suggest(self, model: str, page: dict, hint: str) -> dict:
        messages = page.get("messages") or []
        if not messages:
            return {"ok": False, "error": "Aucun message texte lisible dans cette discussion."}
        prompt = _transcript(page.get("chat", ""), messages)
        if hint:
            prompt += f"\n\nConsigne de l'utilisateur : {hint}"
        data = self._ask(model, SUGGEST_SYSTEM, prompt, SUGGEST_SCHEMA)
        replies = [r for r in (clean(x) for x in data.get("replies") or []) if r][:3]
        if not replies:
            return {"ok": False, "error": "Le modèle n'a proposé aucune réponse."}
        return {"ok": True, "title": page.get("chat", ""), "replies": replies, "model": model}

    def _improve(self, model: str, page: dict, hint: str) -> dict:
        draft = page.get("draft")
        if draft is None:
            return {"ok": False, "error": "Zone de saisie introuvable : ouvrez une discussion."}
        if not draft:
            return {"ok": False, "error": "Le brouillon est vide : écrivez d'abord votre message dans WhatsApp."}
        prompt = _transcript(page.get("chat", ""), page.get("messages") or [], limit=10)
        prompt += f"\n\nBrouillon à améliorer :\n{draft}"
        if hint:
            prompt += f"\n\nConsigne de l'utilisateur : {hint}"
        return self._improved(model, draft, self._ask(model, IMPROVE_SYSTEM, prompt, IMPROVE_SCHEMA).get("text"))
