"""
gmail.py – Gmail intégré (voir webpane.py).

Première ouverture : connexion au compte Google, la session est ensuite
conservée dans DATA_DIR/gmail.
"""

import json
import re
import time

from engine import logger
from webpane import IMPROVE_SCHEMA, STYLE_RULES, SUGGEST_SCHEMA, WebPane, clean, same_text

UNREAD_RE = re.compile(r"\((\d[\d\s  .,]*)\)")
INBOX_TITLES = ("Boîte de réception", "Inbox")
GOOGLE_COUNTRY_RE = re.compile(r"(.+\.)?google\.(com?\.)?[a-z]{2,3}")

# Repères de Gmail, stables depuis des années (utilisés par les extensions) :
# h2.hP (objet), div.adn (message déplié), .gD[email][name] (expéditeur),
# .g3[title] (date), .a3s (corps), div[g_editable] (éditeur de réponse).
# Le brouillon est la partie de l'éditeur avant la signature et la citation,
# qui sont conservées quand on le remplace.
HELPERS_JS = r"""
  const SKIP = ".gmail_quote, .gmail_extra, blockquote, .adm, .h5, style, script";
  const BLOCKS = new Set(["P", "DIV", "LI", "TR", "H1", "H2", "H3", "H4"]);
  const textOf = (el) => {
    let s = "";
    for (const n of el.childNodes) {
      if (n.nodeType === 3) s += n.nodeValue;
      else if (n.nodeName === "BR") s += "\n";
      else if (n.nodeType === 1 && !n.matches(SKIP) && !n.hidden) {
        s += textOf(n) + (BLOCKS.has(n.nodeName) ? "\n" : "");
      }
    }
    return s;
  };
  const tidy = (s) => s.replace(/[ \t ]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
  const findEditor = () => {
    const all = [...document.querySelectorAll('div[contenteditable="true"][g_editable="true"]')];
    return all.find((e) => e.contains(document.activeElement))
      || all.find((e) => e.closest('div[role="main"]')) || all[all.length - 1] || null;
  };
  const TAIL = ".gmail_signature, .gmail_signature_prefix, .gmail_quote, .gmail_quote_container, [data-smartmail]";
  const tailOf = (box) => [...box.children].find((c) => c.matches(TAIL) || c.querySelector(TAIL)) || null;
  const draftOf = (box) => {
    const stop = tailOf(box);
    let s = "";
    for (const c of box.childNodes) {
      if (c === stop) break;
      s += c.nodeType === 3 ? c.nodeValue : c.nodeName === "BR" ? "\n" : textOf(c) + "\n";
    }
    return tidy(s);
  };
"""

READ_THREAD_JS = "(() => {" + HELPERS_JS + r"""
  const account = ((document.title.match(/[\w.+-]+@[\w-]+(\.[\w-]+)+/) || [""])[0]).toLowerCase();
  const subject = (document.querySelector("h2.hP")?.textContent || "").trim();
  const messages = [];
  for (const msg of document.querySelectorAll('div[role="main"] div.adn')) {
    const from = msg.querySelector(".gD");
    const email = (from?.getAttribute("email") || "").toLowerCase();
    const author = (from?.getAttribute("name") || from?.textContent || email).trim();
    const when = msg.querySelector(".g3");
    const body = msg.querySelector(".a3s");
    const text = body ? tidy(textOf(body)) : "";
    if (text) messages.push({ mine: !!account && email === account, author, email,
      date: (when?.getAttribute("title") || when?.textContent || "").trim(), text });
  }
  const box = findEditor();
  const composeSubject = box?.closest("form, [role=dialog], table")?.querySelector('input[name="subjectbox"]')?.value || "";
  if (!messages.length && !box) return { ok: false, error: "Ouvrez d'abord un e-mail (ou un brouillon) dans Gmail." };
  return { ok: true, subject: subject || composeSubject, messages: messages.slice(-20),
    draft: box ? draftOf(box) : null };
})()
"""

# Pas d'éditeur ouvert : clic sur « Répondre » sous le dernier message
# (Gmail réagit à mousedown/mouseup plutôt qu'à click)
OPEN_REPLY_JS = "(() => {" + HELPERS_JS + r"""
  if (findEditor()) return { ok: true, opened: false };
  const btn = document.querySelector('div[role="main"] .ams.bkH');
  if (!btn) return { ok: false, error: "Ouvrez un e-mail puis cliquez sur « Répondre » dans Gmail." };
  for (const type of ["mousedown", "mouseup", "click"]) {
    btn.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
  }
  return { ok: true, opened: true };
})()
"""

# Éditeur classique (contenteditable, lu tel quel à l'envoi) : le brouillon est
# remplacé directement dans le DOM, une ligne par <div> comme Gmail. execCommand
# fusionnerait le texte avec la signature qui suit.
INSERT_JS = "((text) => {" + HELPERS_JS + r"""
  const box = findEditor();
  if (!box) return { ok: false, error: "Éditeur de réponse introuvable." };
  const stop = tailOf(box);
  for (const n of [...box.childNodes]) {
    if (n === stop) break;
    n.remove();
  }
  const line = (content) => {
    const div = document.createElement("div");
    if (content) div.textContent = content; else div.append(document.createElement("br"));
    return div;
  };
  const lines = text.split("\n").map(line);
  if (stop) lines.push(line(""));          // ligne vide avant la signature
  box.insertBefore(lines.reduce((f, d) => (f.append(d), f), document.createDocumentFragment()), stop);
  box.focus();
  const caret = document.createRange();
  caret.selectNodeContents(lines.filter((d) => d.textContent).pop() || box);
  caret.collapse(false);
  window.getSelection().removeAllRanges();
  window.getSelection().addRange(caret);
  box.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText" }));
  return { ok: true };
})
"""

DRAFT_JS = "(() => {" + HELPERS_JS + r"""
  const box = findEditor();
  return { ok: !!box, text: box ? draftOf(box) : "" };
})()
"""

SUGGEST_SYSTEM = (
    "Tu aides l'utilisateur à répondre à un e-mail ; ses propres messages sont marqués « Moi ». "
    "Propose trois réponses différentes au dernier message du fil (par exemple accepter, demander une "
    "précision, décliner poliment — selon ce qui a du sens). Chaque réponse est le corps complet de "
    "l'e-mail : formule d'appel, un à trois courts paragraphes séparés par une ligne vide, puis une "
    "formule de politesse seule (« Bonne journée, », « Cordialement, »…). N'écris ni prénom, ni nom, ni "
    "signature après la formule de politesse : Gmail ajoute la signature. Pas d'objet. Même langue et "
    "même registre que le fil (tutoiement ou vouvoiement). N'invente pas de faits précis (dates, lieux, "
    "chiffres, engagements) absents du fil. " + STYLE_RULES + "Si une consigne est donnée, respecte-la. "
    'Réponds uniquement en JSON : {"replies": ["…", "…", "…"]}.'
)
IMPROVE_SYSTEM = (
    "Tu améliores le brouillon d'un e-mail que l'utilisateur (« Moi ») s'apprête à envoyer. Corrige "
    "l'orthographe, la grammaire et la ponctuation, et rends le texte clair, fluide et adapté à un e-mail, "
    "en gardant son sens, sa langue, son registre (tutoiement ou vouvoiement) et ses paragraphes (séparés "
    "par une ligne vide). N'ajoute aucune information, ni signature ou prénom à la fin. " + STYLE_RULES +
    "Si une consigne est donnée, applique-la. "
    'Réponds uniquement en JSON : {"text": "…"}.'
)
CONTEXT_CHARS = 8000
MESSAGE_CHARS = 3000


def _transcript(subject: str, messages: list[dict]) -> str:
    """Derniers e-mails du fil ; les plus anciens sont coupés au-delà de CONTEXT_CHARS."""
    blocks, size = [], 0
    for m in reversed(messages):
        if m.get("mine"):
            who = "Moi"
        elif m.get("email") and m.get("author") and m["author"].lower() != m["email"]:
            who = f"{m['author']} <{m['email']}>"
        else:
            who = m.get("author") or m.get("email") or "Expéditeur"
        date = f" — {m['date']}" if m.get("date") else ""
        block = f"--- De : {who}{date}\n{m['text'][:MESSAGE_CHARS]}"
        size += len(block)
        if blocks and size > CONTEXT_CHARS:
            break
        blocks.append(block)
    head = f"Fil d'e-mails « {subject} »" if subject else "Fil d'e-mails"
    return f"{head} (du plus ancien au plus récent) :\n\n" + "\n\n".join(reversed(blocks))


class GmailPane(WebPane):
    key = "gmail"
    name = "Gmail"
    url = "https://mail.google.com/mail/"
    hosts = ("google.com", "gstatic.com", "googleusercontent.com", "googleapis.com", "youtube.com")
    read_js = READ_THREAD_JS

    def allowed_host(self, host: str) -> bool:
        # La connexion passe par les domaines Google nationaux (google.fr…)
        return super().allowed_host(host) or bool(GOOGLE_COUNTRY_RE.fullmatch(host))

    def unread_from_title(self, title: str) -> int:
        # « Boîte de réception (3) - moi@gmail.com - Gmail » ; en lecture d'un
        # e-mail le titre est son objet : on garde le dernier compte connu
        m = UNREAD_RE.search(title)
        if m:
            return int(re.sub(r"\D", "", m.group(1)) or 0)
        return 0 if title.startswith(INBOX_TITLES) else self._unread

    def insert(self, text: str) -> dict:
        """Remplace le brouillon de la réponse (sans l'envoyer), en gardant signature
        et citation ; ouvre la réponse au dernier message si besoin."""
        text = str(text or "").strip()
        if not text:
            return {"ok": False, "error": "Rien à insérer."}
        self._focus_page()
        res = self._eval(OPEN_REPLY_JS)
        if not res.get("ok"):
            return res
        before = {}
        for _ in range(40 if res.get("opened") else 1):
            before = self._eval(DRAFT_JS)
            if before.get("ok"):
                break
            time.sleep(0.1)
        res = self._eval(f"{INSERT_JS}({json.dumps(text)})")
        if not res.get("ok"):
            return res
        after = self._eval(DRAFT_JS)
        if same_text(after.get("text"), text):
            return {"ok": True, "previous": str(before.get("text") or "")}
        logger.log(f"Gmail : insertion refusée par l'éditeur ({after!r})")
        return {"ok": False, "error": "Gmail n'a pas accepté le texte : cliquez dans la réponse puis réessayez."}

    def _suggest(self, model: str, page: dict, hint: str) -> dict:
        messages = page.get("messages") or []
        if not messages:
            return {"ok": False, "error": "Aucun e-mail lisible : ouvrez le message auquel répondre."}
        prompt = _transcript(page.get("subject", ""), messages)
        if hint:
            prompt += f"\n\nConsigne de l'utilisateur : {hint}"
        data = self._ask(model, SUGGEST_SYSTEM, prompt, SUGGEST_SCHEMA)
        replies = [r for r in (clean(x) for x in data.get("replies") or []) if r][:3]
        if not replies:
            return {"ok": False, "error": "Le modèle n'a proposé aucune réponse."}
        return {"ok": True, "title": page.get("subject", ""), "replies": replies, "model": model}

    def _improve(self, model: str, page: dict, hint: str) -> dict:
        draft = page.get("draft")
        if draft is None:
            return {"ok": False, "error": "Aucune réponse en cours : cliquez sur « Répondre » ou « Nouveau message »."}
        if not draft:
            return {"ok": False, "error": "Le brouillon est vide : écrivez d'abord votre message dans Gmail."}
        messages = page.get("messages") or []
        prompt = (_transcript(page.get("subject", ""), messages[-3:]) + "\n\n") if messages else (
            f"Nouvel e-mail, objet : « {page['subject']} »\n\n" if page.get("subject") else "")
        prompt += f"Brouillon à améliorer :\n{draft}"
        if hint:
            prompt += f"\n\nConsigne de l'utilisateur : {hint}"
        return self._improved(model, draft, self._ask(model, IMPROVE_SYSTEM, prompt, IMPROVE_SCHEMA).get("text"))
