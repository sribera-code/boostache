"""
search.py – Recherche dans tout Boostache (Ctrl+K) : conversations, notes,
presse-papiers, transcriptions, calculs, messages arrivés, documents indexés
et tâches.

Sans accents ni majuscules, et tous les mots cherchés doivent se trouver dans
l'élément (dans n'importe quel ordre). Chaque résultat porte son extrait (mots
trouvés repérés) et sa cible : la section, et ce que sa vue doit montrer
(reveal, côté interface). Les documents indexés sont cherchés par les mots
seulement (index plein texte) : pas d'appel à Ollama à chaque frappe.
"""

import re
import threading
import unicodedata

import custom_tasks
from chat import _ui_message
from engine import logger
from storage import clipboard_history

MAX_PER_GROUP = 5
MAX_TEXT = 400_000             # caractères lus par élément (un très long texte : son début)
SNIPPET = 150                  # longueur d'un extrait
BEFORE = 45                    # … dont avant le premier mot trouvé
TITLE_LEN = 70
_WORD = re.compile(r"\w+")


def _fold_table() -> dict:
    """Majuscules et lettres accentuées → minuscule sans accent, un caractère pour un :
    le texte replié garde la longueur de l'original (positions des extraits)."""
    table = {}
    for code in range(0x41, 0x250):
        c = chr(code)
        base = "".join(ch for ch in unicodedata.normalize("NFKD", c.lower()) if not unicodedata.combining(ch))
        if len(base) == 1 and base != c:
            table[code] = base
    # Apostrophes typographiques, espaces insécables, sauts de ligne (extraits sur une ligne)
    for code in (0x2019, 0x2018, 0x02BC):
        table[code] = "'"
    for code in (0x00A0, 0x202F, 0x2009, 0x0A, 0x0D, 0x09):
        table[code] = " "
    return table


_FOLD = _fold_table()


def fold(text: str) -> str:
    return text.translate(_FOLD)


def words_of(query: str) -> list[str]:
    """Mots de la recherche, repliés ; une lettre seule ne compte que s'il n'y a qu'elle."""
    words = list(dict.fromkeys(_WORD.findall(fold(query))))
    longer = [w for w in words if len(w) > 1]
    return (longer or words)[:8]


class _Folded:
    """Textes repliés gardés tant qu'ils ne changent pas (même chaîne)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cache: dict = {}

    def get(self, key, text: str) -> str:
        with self._lock:
            hit = self._cache.get(key)
        if hit is not None and hit[0] == text:
            return hit[1]
        folded = fold(text)
        with self._lock:
            if len(self._cache) > 2000:
                self._cache.clear()
            self._cache[key] = (text, folded)
        return folded


def snippet(text: str, folded: str, words: list[str], length: int = SNIPPET) -> dict:
    """Extrait autour du premier mot trouvé, sur une ligne, et ses repères [début, fin]."""
    hits = [i for i in (folded.find(w) for w in words) if i >= 0]
    pos = min(hits) if hits else 0
    start = max(0, pos - BEFORE)
    if start:
        space = folded.find(" ", start, pos)
        start = space + 1 if space >= 0 else start
    end = min(len(text), start + length)
    if end < len(text):
        space = folded.rfind(" ", pos, end)
        end = space if space > pos else end
    raw = " ".join(text[start:end].split())
    head, tail = ("…" if start else ""), ("…" if end < len(text) else "")
    out = head + raw + tail
    return {"text": out, "marks": marks(out, words)}


def marks(text: str, words: list[str]) -> list[list[int]]:
    """Positions des mots cherchés dans text (repliés), fusionnées."""
    folded = fold(text)
    spans = []
    for w in words:
        i = folded.find(w)
        while i >= 0:
            spans.append([i, i + len(w)])
            i = folded.find(w, i + len(w))
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def _short(text: str, n: int = TITLE_LEN) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


class SearchService:
    def __init__(self, app):
        self._app = app
        self._folded = _Folded()
        self._transcripts: dict[str, tuple[float, str]] = {}     # chemin → (date, texte)

    def search(self, query: str) -> dict:
        query = " ".join(str(query or "").split())[:200]
        words = words_of(query)
        if not words:
            return {"query": query, "words": [], "groups": []}
        groups = []
        for kind, fn in (("chat", self._chat), ("notes", self._notes), ("assistant", self._feed),
                         ("recorder", self._recordings), ("clipboard", self._clipboard), ("calc", self._calc),
                         ("rag", self._documents), ("tasks", self._tasks)):
            try:
                items = fn(query, words)
            except Exception as e:          # une source en échec n'empêche pas les autres
                logger.log(f"Recherche ({kind}) en échec : {e}")
                items = []
            if items:
                items.sort(key=lambda it: -it.pop("_score", 0))
                groups.append({"section": kind, "items": items[:MAX_PER_GROUP], "more": max(0, len(items) - MAX_PER_GROUP)})
        return {"query": query, "words": words, "groups": groups}

    # ── Recherche dans un élément ─────────────
    def _match(self, key, title: str, body: str, words: list[str], query: str, rank: int = 0) -> dict | None:
        """Résultat si tous les mots sont dans le titre ou le texte, None sinon."""
        body = body[:MAX_TEXT]
        ft = fold(title)
        fb = self._folded.get(key, body)
        if not all(w in ft or w in fb for w in words):
            return None
        score = 3 * sum(w in ft for w in words) + (2 if fold(query) in fb else 0) - rank * 0.01
        part = snippet(body, fb, words) if any(w in fb for w in words) else snippet(body, fb, [])
        return {"title": title, "title_marks": marks(title, words), "snippet": part["text"],
                "marks": part["marks"], "_score": score, "_pos": _first(fb, words)}

    # ── Sources ───────────────────────────────
    def _chat(self, query, words):
        chat = self._app.chat
        if chat is None:
            return []
        with chat._lock:
            tabs = [(t.id, t.title, [u for u in (_ui_message(m) for m in t.messages) if u]) for t in chat.tabs]
        out = []
        for rank, (tid, title, messages) in enumerate(tabs):
            texts = [m["text"] for m in messages]
            hit = self._match(("chat", tid), title, "\n".join(texts), words, query, rank)
            if not hit:
                continue
            # Message montré : celui qui contient le plus de mots cherchés (le premier à égalité)
            best, index = -1, None
            for i, text in enumerate(texts):
                f = fold(text)
                n = sum(w in f for w in words)
                if n > best:
                    best, index = n, i
            if best > 0:
                part = snippet(texts[index], fold(texts[index]), words)
                hit["snippet"], hit["marks"] = part["text"], part["marks"]
            hit["target"] = {"tab": tid, "message": index if best > 0 else None}
            hit["when"] = messages[-1].get("time", "") if messages else ""
            out.append(hit)
        return out

    def _notes(self, query, words):
        notes = self._app.notes
        with notes._lock:
            items = [(n.slot, n.title, n.content) for n in notes._notes]
        out = []
        for rank, (slot, title, content) in enumerate(items):
            hit = self._match(("note", slot), title or "Note", content, words, query, rank)
            if hit:
                hit["target"] = {"slot": slot, "pos": hit["_pos"]}
                out.append(hit)
        return out

    def _clipboard(self, query, words):
        out = []
        for rank, it in enumerate(clipboard_history.all()):
            text = it.get("text") or ""
            hit = self._match(("clip", it["id"]), _short(text.strip().split("\n")[0]), text, words, query, rank)
            if hit:
                hit["target"] = {"id": it["id"]}
                hit["when"] = it.get("ts", "")
                out.append(hit)
        return out

    def _recordings(self, query, words):
        rec = self._app.recorder
        out = []
        for rank, f in enumerate(rec.files()):
            if not f.get("transcript"):
                continue
            text = self._transcript(rec, f["name"])
            hit = self._match(("rec", f["name"]), f["title"], text, words, query, rank)
            if hit:
                hit["target"] = {"name": f["name"]}
                hit["when"] = f.get("mtime", "")
                out.append(hit)
        return out

    def _transcript(self, rec, name: str) -> str:
        path = rec._file(name)
        txt = path.with_suffix(".txt") if path else None
        try:
            mtime = txt.stat().st_mtime
        except (OSError, AttributeError):
            return ""
        cached = self._transcripts.get(str(txt))
        if cached and cached[0] == mtime:
            return cached[1]
        info = rec.transcript(name)
        text = info["text"] if info else ""
        self._transcripts[str(txt)] = (mtime, text)
        return text

    def _calc(self, query, words):
        out = []
        for rank, e in enumerate(reversed(list(self._app.calc.entries))):
            result = e.get("result") or e.get("error") or ""
            body = "\n".join(x for x in (e.get("code", ""), e.get("output", ""), result) if x)
            hit = self._match(("calc", e["n"], len(body)), f"Calcul #{e['n']}", body, words, query, rank)
            if hit:
                hit["title"] = f"#{e['n']} · {_short(e.get('code', ''), 50)}"
                hit["title_marks"] = marks(hit["title"], words)
                hit["target"] = {"n": e["n"]}
                hit["when"] = e.get("at", "")
                out.append(hit)
        return out

    def _feed(self, query, words):
        assistant = self._app.assistant
        with assistant._lock:
            feed = [dict(i) for i in assistant.feed]
        out = []
        for rank, it in enumerate(feed):
            body = "\n".join(x for x in (it.get("summary", ""), it.get("action", ""), it.get("text", ""),
                                         it.get("snippet", "")) if x)
            name = "Gmail" if it.get("source") == "gmail" else "WhatsApp"
            hit = self._match(("feed", it["id"], len(body)), f"{name} · {it.get('title', '')}", body, words, query, rank)
            if hit:
                hit["target"] = {"id": it["id"]}
                hit["when"] = it.get("at", "")
                out.append(hit)
        return out

    def _documents(self, query, words):
        rag = self._app.rag
        res = rag.search(query, None, semantic=False)
        if not res.get("ok"):
            return []
        out = []
        for f in res.get("files", []):
            out.append({"title": f["name"], "title_marks": marks(f["name"], words), "snippet": f["path"],
                        "marks": [], "_score": 10, "target": {"path": f["path"]}})
        for p in res.get("passages", []):
            text = p.get("text", "")
            part = snippet(text, fold(text), words)
            title = f"{p['name']} · {p['loc']}" if p.get("loc") else p["name"]
            out.append({"title": title, "title_marks": marks(title, words), "snippet": part["text"],
                        "marks": part["marks"], "_score": 5, "target": {"passage": p}})
        return out

    def _tasks(self, query, words):
        out = []
        for rank, row in enumerate(custom_tasks.rows()):
            body = f"{row.get('period', '')}\n{row.get('action_code', '')}"
            hit = self._match(("task", row["idx"], len(body)), row["label"], body, words, query, rank)
            if hit:
                hit["target"] = {"idx": row["idx"]}
                out.append(hit)
        return out


def _first(folded: str, words: list[str]) -> int:
    hits = [i for i in (folded.find(w) for w in words) if i >= 0]
    return min(hits) if hits else 0
