"""
gmail.py – Gmail intégré (voir webpane.py).

Première ouverture : connexion au compte Google, la session est ensuite
conservée dans DATA_DIR/gmail.
"""

import json
import re
import threading
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

# Boutons utiles (WATCH_JS) : un e-mail ouvert (message déplié affiché) pour
# « Suggérer », un brouillon écrit (avant signature et citation) pour « Améliorer »
CONTEXT_JS = "(() => {" + HELPERS_JS + r"""
  const shown = (e) => e.offsetParent !== null;
  return { reply: [...document.querySelectorAll('div[role="main"] div.adn')].some(shown),
    improve: [...document.querySelectorAll('div[contenteditable="true"][g_editable="true"]')]
      .some((e) => shown(e) && !!draftOf(e)) };
})"""

# Fils de la liste affichée dans Gmail (boîte de réception, libellé, recherche… ;
# tr.zA, dans l'ordre de la liste). Pas de liste (fil ouvert, paramètres) :
# list: false, ou avec go la boîte de réception est ouverte (rows: null, pas
# encore affichée). zE = non lu, x7 ou case cochée = sélectionné, .yW [email] =
# participants, .bog = objet, .y2 = extrait, .xW = date, .Dj = « 1–50 sur 312 ».
LIST_JS = r"""
((go) => {
  const h = location.hash;
  if (!h || /^#settings/.test(h) || /\/([0-9a-f]{16}|[A-Za-z0-9]{30,})$/.test(h)) {
    if (!go) return { ok: true, list: false };
    location.hash = "#inbox";
    return { ok: true, list: true, rows: null };
  }
  const shown = (e) => e && e.offsetParent !== null;
  const text = (e) => (e?.textContent || "").trim();
  const rows = [...document.querySelectorAll('div[role="main"] tr.zA')].filter(shown).map((r) => {
    const ids = r.querySelector("[data-legacy-thread-id]");
    const when = r.querySelector(".xW [title]") || r.querySelector(".xW");
    const names = [...r.querySelectorAll(".yW [email]")]
      .map((e) => (e.getAttribute("name") || e.textContent || e.getAttribute("email") || "").trim());
    return {
      id: ids?.getAttribute("data-legacy-thread-id") || "",
      last: ids?.getAttribute("data-legacy-last-message-id") || "",
      unread: r.classList.contains("zE"),
      selected: r.classList.contains("x7") || r.querySelector('[role="checkbox"]')?.getAttribute("aria-checked") === "true",
      from: [...new Set(names.filter(Boolean))].join(", "),
      subject: text(r.querySelector(".bog")),
      snippet: text(r.querySelector(".y2")).replace(/^-\s*/, ""),
      date: (when?.getAttribute("title") || "").trim(),
      when: text(when),
    };
  }).filter((r) => r.id);
  return { ok: true, list: true, rows, base: h, pager: text([...document.querySelectorAll(".Dj")].find(shown)),
    view: document.title.split(" - ")[0].replace(/\s*\(\d[\d\s.,]*\)$/, "").trim(),
    search: (/^#(spam|trash)(\/|$)/.exec(h) || [])[1] || "all" };   // vue « imprimer » : où chercher le fil
})
"""

# E-mail ouvert, résumé seul : fil = h2.hP[data-legacy-thread-id] (objet), dernier
# message déplié = div.adn[data-legacy-message-id] ; base = la liste d'où il est ouvert
CURRENT_JS = r"""
(() => {
  const shown = (e) => !!e && e.offsetParent !== null;
  const head = [...document.querySelectorAll("h2.hP[data-legacy-thread-id]")].find(shown);
  if (!head) return { ok: false, error: "Ouvrez d'abord un e-mail dans Gmail." };
  const msgs = [...document.querySelectorAll('div[role="main"] div.adn')].filter(shown);
  const last = msgs[msgs.length - 1];
  const when = last?.querySelector(".g3");
  const names = msgs.map((m) => m.querySelector(".gD")).filter(Boolean)
    .map((e) => (e.getAttribute("name") || e.textContent || e.getAttribute("email") || "").trim());
  const h = location.hash;
  return { ok: true, base: h.replace(/\/[^\/]*$/, "") || "#inbox",
    search: (/^#(spam|trash)(\/|$)/.exec(h) || [])[1] || "all",
    row: { id: head.getAttribute("data-legacy-thread-id"),
      last: last?.getAttribute("data-legacy-message-id") || String(msgs.length), unread: false,
      from: [...new Set(names.filter(Boolean))].join(", "), subject: (head.textContent || "").trim(),
      snippet: (last?.querySelector(".a3s")?.textContent || "").trim().slice(0, 200),
      date: (when?.getAttribute("title") || "").trim(), when: (when?.textContent || "").trim() } };
})()
"""

# Fil complet par la vue « imprimer » de Gmail, chargée en arrière-plan : la page
# affichée ne change pas et le fil n'est pas marqué comme lu. La réponse est lue
# par XHR « document » (DOMParser est refusé par la politique Trusted Types de
# Gmail). Un message = table.message : rows[0] expéditeur + date, dernière ligne
# le corps. Les classes des e-mails y sont préfixées (m_123gmail_quote).
THREAD_JS = r"""
(async (id, search) => {
  const ik = window.GLOBALS && GLOBALS[9];
  if (!ik) return { ok: false, error: "Gmail n'est pas prêt." };
  const doc = await new Promise((resolve) => {
    const x = new XMLHttpRequest();
    x.open("GET", `${location.pathname}?ik=${ik}&view=pt&search=${search}&th=${id}`);
    x.responseType = "document";
    x.timeout = 15000;
    x.onload = () => resolve(x.status === 200 ? x.response : null);
    x.onerror = x.ontimeout = () => resolve(null);
    x.send();
  });
  const main = doc?.querySelector(".maincontent");
  if (!main) return { ok: false, error: "Fil illisible." };
  const SKIP = 'style, script, title, blockquote, [class*="gmail_quote"], [class*="gmail_signature"],'
    + ' [style*="display:none" i], [style*="display: none" i]';
  const BLOCKS = new Set(["P", "DIV", "LI", "TR", "TABLE", "UL", "OL", "H1", "H2", "H3", "H4", "H5", "H6", "HR"]);
  const flat = (el) => {
    let s = "";
    for (const n of el.childNodes) {
      if (n.nodeType === 3) s += n.nodeValue.replace(/\s+/g, " ");
      else if (n.nodeName === "BR") s += "\n";
      else if (n.nodeType === 1 && !n.matches(SKIP)) {
        // Un bloc commence et finit une ligne ; les cellules sont séparées par une espace
        if (!BLOCKS.has(n.nodeName)) s += flat(n) + (n.nodeName === "TD" ? " " : "");
        else s += (s && !s.endsWith("\n") ? "\n" : "") + flat(n) + "\n";
      }
    }
    return s;
  };
  // Citation en texte brut : lignes « > » et tout ce qui suit l'en-tête de réponse
  const REPLY = /^(Le .{4,160} a écrit ?:|On .{4,160} wrote:|-{3,} ?(Original Message|Message d'origine)|(De|From) ?: .+\n(Envoyé|Sent|Date) ?: )/m;
  const account = ((document.title.match(/[\w.+-]+@[\w-]+(\.[\w-]+)+/) || [""])[0]).toLowerCase();
  const messages = [...main.querySelectorAll("table.message")].map((m) => {
    const head = m.rows[0]?.cells || [];
    const from = (head[0]?.textContent || "").replace(/\s+/g, " ").trim();
    const [, author = from, email = ""] = from.match(/^(.*?)\s*<([^<>]+)>$/) || [];
    let text = flat(m.rows[m.rows.length - 1]).split("\n").map((l) => l.trim())
      .filter((l) => !l.startsWith(">")).join("\n").replace(/\n{3,}/g, "\n\n").trim();
    const cut = text.search(REPLY);
    if (cut > 0) text = text.slice(0, cut).trim();
    return { mine: !!account && email.toLowerCase() === account, author: author.trim(),
      email: email.toLowerCase(), date: (head[1]?.textContent || "").trim(), text: text.slice(0, 4000) };
  }).filter((m) => m.text);
  return { ok: true, subject: (main.querySelector("font b")?.textContent || "").trim(), messages: messages.slice(-20) };
})
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
DIGEST_ME = "Vous (l'utilisateur)"
DIGEST_SYSTEM = (
    "Tu résumes pour l'utilisateur un fil d'e-mails de sa boîte de réception ; les messages qu'il a "
    "lui-même envoyés sont marqués « Vous (l'utilisateur) ». Désigne-le par « vous ». summary : une ou "
    "deux phrases courtes en français : qui écrit et ce qu'il dit ou demande, avec les dates, montants et "
    "noms importants, sans recopier le texte et sans commencer par « Ce fil » ni « Cet e-mail ». "
    "action : ce que le fil attend de l'utilisateur, en quelques mots, avec l'échéance s'il y en a une — "
    "par exemple répondre à une question qu'on lui pose, choisir un créneau, payer une facture, confirmer "
    "sa présence, envoyer un document. S'il n'attend rien de lui (publicité, lettre d'information, avis "
    "d'expédition, simple information), action est vide. N'invente rien. "
    'Réponds uniquement en JSON : {"summary": "…", "action": "…"}.'
)
# « Aucune », « Rien »… : pas d'action
NO_ACTION_RE = re.compile(r"(aucune?( action)?|rien( à faire)?|néant|n/?a|none|-+)\.?", re.IGNORECASE)
DIGEST_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "action": {"type": "string"}},
    "required": ["summary", "action"],
}
OVERVIEW_SYSTEM = (
    "Tu fais le point sur des e-mails de l'utilisateur à partir du résumé de chacun. "
    "overview : 2 à 4 phrases en français où tu t'adresses à l'utilisateur comme son assistant "
    "(« Vous avez… », « Pensez à… »), jamais au nom des expéditeurs : d'abord ce qui demande une action "
    "ou semble urgent, puis le reste, en regroupant les e-mails semblables (publicités, notifications, "
    "lettres d'information…) s'il y en a. Ne parle que des e-mails de la liste, ne la recopie pas et "
    "n'invente rien. "
    "Si une consigne est donnée, respecte-la. "
    'Réponds uniquement en JSON : {"overview": "…"}.'
)
OVERVIEW_SCHEMA = {"type": "object", "properties": {"overview": {"type": "string"}}, "required": ["overview"]}
DIGEST_MAX = 50            # fils résumés au plus : une page de Gmail
DIGEST_SCOPES = ("current", "selection", "unread", "all")    # current : l'e-mail ouvert
CURRENT_VIEW = "E-mail ouvert"
LIST_WAIT = 8              # attente de la liste à l'ouverture de la boîte (secondes)
LIST_HASH_RE = re.compile(r"#[^\"'\\<>\s]{1,200}")
DIGEST_FAILURES = 3        # échecs du modèle d'affilée avant abandon
SUMMARY_CACHE = 500        # résumés gardés (fil, dernier message, modèle)
THREAD_ID_RE = re.compile(r"[0-9a-f]{6,24}")
PAGER_TOTAL_RE = re.compile(r"(?:sur|of)\s+([\d\s.,]+)$")
CONTEXT_CHARS = 8000
MESSAGE_CHARS = 3000


def _transcript(subject: str, messages: list[dict], me: str = "Moi") -> str:
    """Derniers e-mails du fil ; les plus anciens sont coupés au-delà de CONTEXT_CHARS."""
    blocks, size = [], 0
    for m in reversed(messages):
        if m.get("mine"):
            who = me
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
    context_js = CONTEXT_JS

    def __init__(self, app):
        super().__init__(app)
        self._digest = {"state": "idle"}      # résumé de la boîte (voir digest)
        self._digest_stop = threading.Event()
        self._summaries = {}                  # (fil, dernier message, modèle) → {summary, action}

    def snapshot(self) -> dict:
        return {**super().snapshot(), "digest": self._digest_view()}

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

    # ─────────────────────────────────────────
    #  Résumé des e-mails (liste affichée dans Gmail)
    # ─────────────────────────────────────────
    def digest_scopes(self) -> dict:
        """Ce que « Résumer » peut proposer pour la liste affichée dans Gmail : nombre
        d'e-mails sélectionnés, non lus et en tout. list: false quand aucune liste
        n'est affichée (fil ouvert…) : le résumé portera sur la boîte de réception."""
        res = self._eval(f"{LIST_JS}(false)")
        if not res.get("ok") or not res.get("list"):
            return {**res, "view": "Boîte de réception"} if res.get("ok") else res
        rows = res.get("rows") or []
        return {"ok": True, "list": True, "view": res.get("view") or "Gmail", "all": len(rows),
                "unread": sum(1 for r in rows if r.get("unread")),
                "selection": sum(1 for r in rows if r.get("selected"))}

    def digest(self, model: str, hint: str = "", scope: str = "all") -> dict:
        """Lit chaque fil de la liste affichée dans Gmail — tous, les non lus ou les
        sélectionnés — et le résume, puis fait le point ; ou (current) seulement
        l'e-mail ouvert. En arrière-plan : progression par l'événement gmail:digest."""
        model, error = self._model(model)
        if error:
            return error
        if not self._busy.acquire(blocking=False):
            return {"ok": False, "error": "Une génération est déjà en cours."}
        scope = scope if scope in DIGEST_SCOPES else "all"
        self._digest_stop.clear()
        self._digest = {"state": "listing", "model": model, "scope": scope, "view": "", "base": "#inbox",
                        "total": 0, "listed": 0, "items": [], "overview": "", "error": ""}
        self._emit_digest()
        threading.Thread(target=self._run_digest, args=(model, (hint or "").strip()[:500], scope),
                         daemon=True, name="gmail-digest").start()
        return {"ok": True}

    def digest_stop(self):
        self._digest_stop.set()

    def open_thread(self, thread_id: str) -> dict:
        """Affiche dans Gmail un fil du dernier résumé (dans sa liste : boîte, libellé…)."""
        base = self._digest.get("base") or "#inbox"
        if not THREAD_ID_RE.fullmatch(thread_id or "") or not LIST_HASH_RE.fullmatch(base):
            return {"ok": False, "error": "E-mail introuvable."}
        return self._eval(f"(() => {{ location.hash = {json.dumps(f'{base}/{thread_id}')}; return {{ ok: true }}; }})()")

    def _digest_view(self) -> dict:
        """Copie de l'état du résumé (envoyée à l'interface pendant que le travail continue)."""
        return {**self._digest, "items": [dict(i) for i in self._digest.get("items", [])]}

    def _emit_digest(self):
        self._app.bridge.emit("gmail:digest", self._digest_view())

    def _run_digest(self, model: str, hint: str, scope: str):
        d = self._digest
        try:
            if scope == "current":
                listing = self._eval(CURRENT_JS)
                if listing.get("ok"):
                    listing = {**listing, "rows": [listing["row"]], "view": CURRENT_VIEW}
            else:
                listing = self._list()
            if not listing.get("ok"):
                d["state"], d["error"] = ("cancelled", "") if listing.get("cancelled") else ("error", listing.get("error", ""))
                return
            rows = [r for r in listing["rows"] if scope in ("all", "current")
                    or (scope == "unread" and r.get("unread")) or (scope == "selection" and r.get("selected"))]
            m = PAGER_TOTAL_RE.search(listing.get("pager") or "")
            d["view"] = listing.get("view") or "Gmail"
            base = re.sub(r"/p\d+$", "", listing.get("base") or "")
            d["base"] = base if LIST_HASH_RE.fullmatch(base) else "#inbox"
            d["listed"] = max(len(rows), int(re.sub(r"\D", "", m.group(1)) or 0) if m and scope == "all" else 0)
            d["total"] = min(len(rows), DIGEST_MAX)
            d["state"] = "reading"
            self._emit_digest()
            search = listing.get("search") if listing.get("search") in ("spam", "trash") else "all"
            failures, failure = 0, ""
            for row in rows[:DIGEST_MAX]:
                if self._digest_stop.is_set():
                    break
                item = {k: row.get(k) or "" for k in ("id", "from", "subject", "date", "when")}
                item["unread"] = bool(row.get("unread"))
                key = (row["id"], row.get("last", ""), model)
                found = self._summaries.get(key)
                if found is None:
                    found, failure = self._summarize(model, row, search)
                    failures = 0 if found else failures + 1
                    if found:
                        self._summaries[key] = found
                        while len(self._summaries) > SUMMARY_CACHE:
                            self._summaries.pop(next(iter(self._summaries)))
                # Fil illisible ou échec du modèle : l'extrait de la liste
                item.update(found or {"summary": row.get("snippet") or "", "action": "", "failed": True})
                d["items"].append(item)
                self._emit_digest()
                if failures >= DIGEST_FAILURES:
                    d["state"], d["error"] = "error", failure
                    return
            if self._digest_stop.is_set():
                d["state"] = "cancelled"
                return
            if d["items"] and all(i.get("failed") for i in d["items"]):
                d["state"], d["error"] = "error", failure
                return
            if len(d["items"]) > 1:      # un seul e-mail : son résumé suffit
                d["state"] = "overview"
                self._emit_digest()
                prompt = self._overview_prompt(d["items"], d["view"], scope, d["listed"])
                if hint:
                    prompt += f"\n\nConsigne de l'utilisateur : {hint}"
                d["overview"] = clean(self._ask(model, OVERVIEW_SYSTEM, prompt, OVERVIEW_SCHEMA, 0.3).get("overview"))
            d["state"] = "done"
        except Exception as e:
            logger.log(f"Gmail : résumé des e-mails ({model}) en échec : {e}")
            d["state"], d["error"] = "error", f"Échec du résumé : {self._app.chat.failure(e)}"
        finally:
            self._busy.release()
            self._emit_digest()

    def _list(self) -> dict:
        """Fils de la liste affichée dans Gmail (page affichée) ; aucune liste (fil
        ouvert…) : la boîte de réception est ouverte, le temps qu'elle s'affiche."""
        deadline = time.monotonic() + LIST_WAIT
        while True:
            res = self._eval(f"{LIST_JS}(true)")
            if not res.get("ok"):
                return res
            rows = res.get("rows")
            if rows or (rows is not None and time.monotonic() > deadline):
                return {**res, "rows": rows, "base": (res.get("base") or "")}
            if time.monotonic() > deadline:
                return {"ok": False, "error": "La boîte de réception ne s'affiche pas dans Gmail."}
            if self._digest_stop.wait(0.3):
                return {"ok": False, "cancelled": True}

    def _summarize(self, model: str, row: dict, search: str = "all") -> tuple[dict | None, str]:
        """Résumé d'un fil : ({summary, action}, "") ou (None, raison de l'échec)."""
        if not THREAD_ID_RE.fullmatch(row.get("id") or ""):
            return None, "E-mail illisible."
        thread = self._eval(f"{THREAD_JS}({json.dumps(row['id'])}, {json.dumps(search)})", timeout=20, awaits=True)
        messages = thread.get("messages") or []
        if not messages:
            logger.log(f"Gmail : fil {row['id']} illisible ({thread.get('error') or 'vide'})")
            return None, thread.get("error") or "E-mail illisible."
        try:
            found = self._summary(model, thread.get("subject") or row.get("subject", ""), messages)
        except Exception as e:
            logger.log(f"Gmail : résumé du fil {row['id']} ({model}) en échec : {e}")
            return None, f"Échec du résumé : {self._app.chat.failure(e)}"
        return (found, "") if found else (None, "Le modèle n'a rien résumé.")

    @classmethod
    def _summary(cls, model: str, subject: str, messages: list[dict]) -> dict | None:
        data = cls._ask(model, DIGEST_SYSTEM, _transcript(subject, messages, DIGEST_ME), DIGEST_SCHEMA, 0.3)
        summary, action = clean(data.get("summary")), clean(data.get("action"))
        if not summary:
            return None
        # Dernier message envoyé par l'utilisateur : la balle est dans l'autre camp
        if messages[-1].get("mine") or NO_ACTION_RE.fullmatch(action):
            action = ""
        return {"summary": summary, "action": action[:1].upper() + action[1:]}

    @staticmethod
    def _overview_prompt(items: list[dict], view: str, scope: str = "all", listed: int = 0) -> str:
        lines = []
        for n, item in enumerate(items, 1):
            head = " — ".join(x for x in (item["from"], f"« {item['subject']} »" if item["subject"] else "", item["when"]) if x)
            lines.append(f"{n}. {'[non lu] ' if item['unread'] else ''}{head}\n   {item['summary']}"
                         + (f"\n   À faire : {item['action']}" if item.get("action") else ""))
        which = {"unread": "Les e-mails non lus de", "selection": "Les e-mails choisis par l'utilisateur dans"}
        more = f" (les {len(items)} plus récents sur {listed})" if listed > len(items) else ""
        return (f"{which.get(scope, 'Les e-mails de')} « {view} »{more}, du plus récent au plus ancien :\n\n"
                + "\n".join(lines))
