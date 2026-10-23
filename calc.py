"""
calc.py – Section Calculatrice : console Python pour les calculs, avec un
assistant (Ollama) en conversation.

Les calculs s'exécutent dans un processus à part (calc_worker.py), gardé d'un
calcul à l'autre (variables, fonctions définies) : un calcul trop long
s'interrompt en le terminant, puis un nouveau démarre (variables effacées).
Historique des calculs gardé sur disque (DATA_DIR/calc.json) ; chaque
lancement de l'interpréteur ouvre une « session » : les calculs d'une session
passée peuvent être rejoués pour retrouver leurs variables.

Un calcul précédent se modifie (par l'utilisateur ou l'assistant) : il est
recalculé avec ceux qui le suivent dans sa session, dans un interpréteur neuf
qui rejoue d'abord ceux d'avant — comme si le calcul avait été écrit ainsi
dès le départ. Les variables de la session en cours sont gardées (ses calculs
sont rejoués à la suite).

Assistant : conversation en bas de la section avec un modèle Ollama qui
connaît la calculatrice (fonctions prêtes, variables, derniers calculs). Il
fait les calculs demandés en langage courant, modifie les calculs précédents,
explique un calcul ou une erreur — outils calculer et modifier_calcul : ses
calculs vont dans l'historique, comme ceux de l'utilisateur. Le code qu'il
écrit ne peut ni importer de module hors calcul, ni ouvrir de fichier.
"""

import ast
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from chat import _ollama, keep_alive, num_ctx
from engine import logger
from storage import DATA_DIR, read_json, settings, write_json

WORKER = Path(__file__).resolve().parent / "calc_worker.py"
HISTORY_FILE = DATA_DIR / "calc.json"
MAX_ENTRIES = 300            # calculs gardés dans l'historique
MAX_CODE = 20_000
CREATE_NO_WINDOW = 0x08000000
INTERRUPTED = "Calcul interrompu : les variables ont été effacées."

# ── Assistant ─────────────────────────────────
MAX_TURNS = 40               # questions gardées dans la conversation
HISTORY_TURNS = 4            # questions précédentes transmises au modèle
CONTEXT_ENTRIES = 25         # derniers calculs transmis au modèle
CONTEXT_CHARS = 7000         # … au plus (caractères)
MAX_ROUNDS = 5               # allers-retours outils → modèle par question
MAX_CALLS = 6                # appels d'outils traités par aller-retour
AI_WAIT = 30                 # un calcul de l'utilisateur en cours : attente (s)
AI_TIMEOUT = 30              # calcul de l'assistant interrompu au-delà (s)
TOOL_TEXT = 1500             # résultat renvoyé au modèle (caractères)
DELTA_INTERVAL = 0.05        # regroupement des tokens envoyés à l'interface (s)
QA_BUSY = ("thinking", "running")

# Modules que le code de l'assistant peut importer ; noms et attributs refusés (fichiers, système)
SAFE_MODULES = {"math", "cmath", "statistics", "fractions", "decimal", "datetime", "random", "itertools",
                "functools", "operator", "re", "json", "numpy", "calendar", "collections", "string", "zoneinfo"}
BLOCKED_NAMES = {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "globals", "locals",
                 "vars", "getattr", "setattr", "delattr", "exit", "quit", "help", "memoryview"}
BLOCKED_ATTRS = {"load", "save", "savez", "savetxt", "loadtxt", "genfromtxt", "fromfile", "tofile", "memmap",
                 "system", "popen"}

CALC_SYSTEM = """Tu es l'assistant de la Calculatrice de Boostache : une console Python 3 où l'utilisateur fait ses calculs. Tu l'aides en français : tu fais les calculs qu'il demande en langage courant, tu modifies ses calculs précédents, tu expliques un calcul ou une erreur, tu montres comment écrire un calcul.

Règles :
- Ne calcule jamais de tête, même un calcul simple, une date ou un nombre de jours : toute réponse qui contient un nombre passe d'abord par l'outil calculer (ou modifier_calcul), et tu donnes le résultat qu'il renvoie. Pour les dates, calcule avec date.today(), date(…) et timedelta.
- calculer ajoute un calcul à l'historique de l'utilisateur, dans la même console que lui : ses variables et ses fonctions sont disponibles, celles que tu crées restent. Écris un code court et lisible, comme l'utilisateur l'écrirait, avec des noms de variables parlants en français (capital = 200000) ; la valeur de la dernière ligne est le résultat. Un seul calcul suffit le plus souvent : regroupe les étapes.
- L'utilisateur veut changer un calcul déjà fait (« change le taux à 4 % », « mets 25 ans au lieu de 20 », « corrige le calcul 3 ») : utilise modifier_calcul avec le numéro du calcul (#n) et son code complet corrigé ; ce calcul et ceux qui le suivent sont recalculés. Ne fais pas de nouveau calcul dans ce cas.
- Dans les fonctions prêtes, les taux et la TVA s'écrivent en pourcentage : 3.5 pour 3,5 %, tva=5.5 pour 5,5 %.
- Ta réponse : courte, en français, le résultat en notation française (1 159,92 €, espace entre les milliers, virgule décimale), arrondi utilement, avec son unité seulement si la demande en donne une (15 % de 340 → 51, sans €). Ne recopie pas le code (il s'affiche dans l'historique), sauf si on te demande comment faire : alors un court exemple en Python. Pour une erreur, dis simplement ce qui ne va pas et comment corriger.
- Rien d'autre que des calculs : pas de fichiers, d'internet ni de programmes du système. Pour une telle demande, dis-le en une phrase, sans rien proposer d'autre.

Prêt à l'emploi, sans import :
- tout le module math : sqrt, cbrt, exp, log(x, base), log10, log2, sin, cos, tan (en radians), radians, degrees, factorial, comb, perm, gcd, lcm, floor, ceil, trunc, hypot, pi, e, inf ; et round(x, 2), abs, sum, min, max
- statistics : mean, median, mode, stdev, pstdev, variance
- Fraction (fractions exactes : Fraction(1, 3)), Decimal (décimaux exacts, argent : Decimal("0.1"))
- date, datetime, time, timedelta : date.today(), datetime.now(), (date(2026, 12, 25) - date.today()).days, date.today() + timedelta(days=90)
- jour(d) : jour de la semaine d'une date, en français (jour(date(2027, 7, 14)) → 'mercredi')
- np (numpy, s'il est installé), random, itertools, functools, operator, cmath, re, json
- ttc(ht, tva=20) : prix TTC d'un prix hors taxes ; ht(ttc, tva=20) : prix hors taxes d'un prix TTC
- variation(avant, apres) : évolution en % ; pourcentage(part, total) : part en % du total
- mensualite(capital, taux, annees) : mensualité d'un prêt à taux fixe (taux annuel en %)
- interets(capital, taux, annees, par_an=1) : capital après intérêts composés (taux annuel en %)
- _ ou ans : le dernier résultat"""

TOOLS = [
    {"type": "function", "function": {
        "name": "calculer",
        "description": "Exécute du code Python dans la calculatrice de l'utilisateur (sa console : ses variables "
                       "sont disponibles et les nouvelles restent) et renvoie le résultat. Le calcul s'ajoute à "
                       "l'historique, sous un nouveau numéro.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "Code Python ; la valeur de la dernière ligne est le résultat."}},
            "required": ["code"]}}},
    {"type": "function", "function": {
        "name": "modifier_calcul",
        "description": "Remplace le code d'un calcul de l'historique (numéro #n) puis le recalcule, avec tous ceux "
                       "qui le suivent ; renvoie les nouveaux résultats.",
        "parameters": {"type": "object", "properties": {
            "numero": {"type": "integer", "description": "Numéro du calcul à modifier (#n)."},
            "code": {"type": "string", "description": "Nouveau code complet du calcul."}},
            "required": ["numero", "code"]}}},
]


def unsafe(code: str) -> str:
    """Ce qui est refusé dans le code de l'assistant (fichiers, système), ou "" ."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""                     # l'interpréteur rapportera l'erreur
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if name.split(".")[0] not in SAFE_MODULES:
                    return f"import {name}"
        elif isinstance(node, ast.Name) and node.id in BLOCKED_NAMES:
            return node.id
        elif isinstance(node, ast.Attribute) and (node.attr.startswith("__") or node.attr in BLOCKED_ATTRS):
            return node.attr
    return ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class CalcService:
    def __init__(self, bridge=None, chat=None):
        self._bridge = bridge
        self._chat = chat
        self._lock = threading.Lock()           # processus (démarrage, arrêt)
        self._run_lock = threading.Lock()       # un calcul à la fois
        self._proc: subprocess.Popen | None = None
        self._expired = 0                       # calcul interrompu car trop long : sa limite (s)
        self.session = ""                       # session de l'interpréteur en cours
        self.vars: list[dict] = []
        data = read_json(HISTORY_FILE)
        data = data if isinstance(data, dict) else {}
        self.entries: list[dict] = [e for e in data.get("entries", []) if isinstance(e, dict) and "code" in e][-MAX_ENTRIES:]
        self._counter = max((e.get("n", 0) for e in self.entries), default=0)
        # Conversation avec l'assistant ; réponses interrompues par un arrêt de Boostache : arrêtées
        self.convo: list[dict] = [t for t in data.get("conversation", []) if isinstance(t, dict) and "id" in t][-MAX_TURNS:]
        for t in self.convo:
            if t.get("state") in QA_BUSY:
                t.update(state="stopped", phase="")
        self._qa_lock = threading.Lock()
        self._job = None                        # (tour, arrêt) de la réponse en cours

    def _emit(self, event: str, data: dict):
        if self._bridge is not None:
            self._bridge.emit(event, data)

    # ── Interpréteur ──────────────────────────
    def _start(self):
        """Nouvel interpréteur (nouvelle session, variables vides). Sous self._lock."""
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self._proc = subprocess.Popen([sys.executable, "-u", str(WORKER)], stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                                      cwd=str(Path.home()), creationflags=CREATE_NO_WINDOW,
                                      encoding="utf-8", errors="replace")
        self.session = uuid.uuid4().hex[:8]
        self.vars = []
        line = self._proc.stdout.readline()      # {"ready": true} une fois le prélude chargé
        if not line:
            self._kill()
            raise OSError("l'interpréteur Python ne démarre pas")

    def _kill(self):
        proc, self._proc = self._proc, None
        self.session = ""                        # variables perdues
        self.vars = []
        if proc and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

    def warm_up(self):
        """Démarre l'interpréteur à l'avance (la section est affichée)."""
        threading.Thread(target=self._ensure, daemon=True, name="calc-start").start()

    def _ensure(self):
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                try:
                    self._start()
                except OSError as e:
                    logger.log(f"Calculatrice : {e}.", level="warning")

    def _send(self, code: str, quiet: bool = False, timeout: float = 0) -> dict | None:
        """Calcul envoyé à l'interpréteur ; None s'il a été interrompu (ou s'est arrêté).
        timeout : interrompu au-delà (s)."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            proc = self._proc
        waiting = threading.Event()
        timer = threading.Timer(timeout, lambda: waiting.is_set() and self._expire(proc, timeout)) if timeout else None
        try:
            proc.stdin.write(json.dumps({"code": code, "quiet": quiet}) + "\n")
            proc.stdin.flush()
            waiting.set()
            if timer:
                timer.start()
            line = proc.stdout.readline()
        except (OSError, ValueError):
            line = ""
        finally:
            waiting.clear()
            if timer:
                timer.cancel()
        if not line:
            with self._lock:
                if self._proc is proc:
                    self._kill()
            return None
        res = json.loads(line)
        self.vars = res.get("vars") or []
        return res

    def _expire(self, proc, timeout: float):
        with self._lock:
            if self._proc is proc:
                self._expired = timeout
                self._kill()

    def _acquire(self, wait: float = 0) -> bool:
        """Un calcul à la fois ; wait : attente d'un calcul en cours (s)."""
        if not (self._run_lock.acquire(timeout=wait) if wait else self._run_lock.acquire(blocking=False)):
            return False
        self._expired = 0
        self._emit("calc:running", {"on": True})
        return True

    def _release(self):
        self._run_lock.release()
        self._emit("calc:running", {"on": False})

    def _fill(self, entry: dict, res: dict | None):
        """Résultat d'un calcul (None : interrompu) écrit dans son entrée d'historique."""
        for k in ("output", "result", "number", "type", "error", "interrupted"):
            entry.pop(k, None)
        if res is None:
            entry.update(error=(f"Calcul interrompu au bout de {self._expired:g} s (trop long) : les variables ont "
                                "été effacées.") if self._expired else INTERRUPTED, interrupted=True, session="")
        else:
            entry.update({k: res.get(k) for k in ("output", "result", "number", "type", "error") if res.get(k)})

    # ── Pour l'interface ──────────────────────
    def state(self) -> dict:
        return {"entries": list(self.entries), "vars": self.vars, "session": self.session,
                "running": self._run_lock.locked(), "conversation": self.conversation(),
                "model": self.model()}

    def _emit_state(self):
        self._emit("calc:state", {"entries": list(self.entries), "vars": self.vars, "session": self.session})

    def run(self, code: str, by: str = "", wait: float = 0, timeout: float = 0) -> dict:
        """Nouveau calcul ; by : "ia" pour un calcul de l'assistant (l'interface l'apprend par calc:state)."""
        code = str(code or "").rstrip()[:MAX_CODE]
        if not code.strip():
            return {"ok": False, "error": "Rien à calculer."}
        if not self._acquire(wait):
            return {"ok": False, "error": "Un calcul est déjà en cours."}
        try:
            try:
                res = self._send(code, timeout=timeout)
            except OSError as e:
                return {"ok": False, "error": f"Calcul impossible : {e}."}
            self._counter += 1
            entry = {"n": self._counter, "code": code, "session": self.session, "at": _now()}
            if by:
                entry["by"] = by
            self._fill(entry, res)
            self.entries.append(entry)
            del self.entries[:-MAX_ENTRIES]
            self._save()
            result = {"ok": True, "entry": entry, "vars": self.vars, "session": self.session}
        finally:
            self._release()
        if by:
            self._emit_state()
        return result

    def recompute(self, n: int, code: str, by: str = "", wait: float = 0, timeout: float = 0) -> dict:
        """Modifie le calcul n puis le recalcule, avec ceux qui le suivent dans sa session, dans un
        interpréteur neuf (ceux d'avant rejoués sans résultat) ; la session en cours est rejouée à la
        suite si c'en était une autre. → {ok, changed: [n…], entries, vars, session}"""
        code = str(code or "").rstrip()[:MAX_CODE]
        if not code.strip():
            return {"ok": False, "error": "Rien à calculer."}
        target = next((e for e in self.entries if e.get("n") == n), None)
        if target is None:
            return {"ok": False, "error": f"Le calcul #{n} n'est plus dans l'historique."}
        if not self._acquire(wait):
            return {"ok": False, "error": "Un calcul est déjà en cours."}
        try:
            result = self._recompute(target, code, by, timeout)
        finally:
            self._release()
        if by:
            self._emit_state()
        return result

    def _recompute(self, target: dict, code: str, by: str, timeout: float) -> dict:
        old = target.get("session") or ""
        chain = [e for e in self.entries if old and e.get("session") == old and not e.get("interrupted")]
        if target not in chain:
            chain = [target]              # calcul interrompu : seul
        current = self.session
        tail = [e for e in self.entries if current and current != old and e.get("session") == current
                and not e.get("interrupted") and e not in chain]
        try:
            with self._lock:
                self._kill()
                self._start()
        except OSError as e:
            return {"ok": False, "error": f"Calcul impossible : {e}."}
        new = self.session
        at = chain.index(target)
        changed = []

        def done(ok: bool, error: str = "") -> dict:
            self._save()
            out = {"ok": ok, "changed": [e["n"] for e in changed], "entries": list(self.entries),
                   "vars": self.vars, "session": self.session}
            if error:
                out["error"] = error
            return out

        for i, e in enumerate(chain):
            if i < at:
                res = self._send(e["code"], quiet=True, timeout=timeout)
                if res is None:
                    return done(False, INTERRUPTED)
            else:
                if e is target:
                    e.update(code=code, edited=_now())
                    if by:
                        e["edited_by"] = by
                res = self._send(e["code"], timeout=timeout)
                self._fill(e, res)
                changed.append(e)
                if res is None:
                    return done(False, e["error"])
            e["session"] = new
        for e in tail:                    # variables de la session en cours retrouvées
            if self._send(e["code"], quiet=True, timeout=timeout) is None:
                return done(False, INTERRUPTED)
            e["session"] = new
        return done(True)

    def interrupt(self) -> dict:
        """Calcul trop long : l'interpréteur est terminé (le calcul en cours revient interrompu)."""
        with self._lock:
            running = self._run_lock.locked()
            self._kill()
        return {"ok": True, "running": running}

    def reset(self) -> dict:
        """Variables effacées : nouvel interpréteur."""
        with self._lock:
            self._kill()
            try:
                self._start()
            except OSError as e:
                return {"ok": False, "error": str(e)}
        return {"ok": True, "vars": self.vars, "session": self.session}

    def replay(self, session: str) -> dict:
        """Rejoue les calculs d'une session passée (sans les ajouter à l'historique) :
        leurs variables reviennent, ils rejoignent la session en cours. S'arrête à la
        première erreur."""
        todo = [e for e in self.entries if e.get("session") == session and not e.get("error")]
        if not todo:
            return {"ok": False, "error": "Aucun calcul à rejouer."}
        if not self._acquire():
            return {"ok": False, "error": "Un calcul est déjà en cours."}
        try:
            done = 0
            for e in todo:
                res = self._send(e["code"], quiet=True)
                if res is None:
                    return {"ok": False, "error": "Calcul interrompu.", "vars": self.vars, "session": self.session}
                if res.get("error"):
                    break
                e["session"] = self.session
                done += 1
            self._save()
            return {"ok": True, "done": done, "total": len(todo), "entries": list(self.entries),
                    "vars": self.vars, "session": self.session}
        except OSError as e:
            return {"ok": False, "error": f"Calcul impossible : {e}."}
        finally:
            self._release()

    def delete(self, n: int) -> dict:
        self.entries = [e for e in self.entries if e.get("n") != n]
        self._save()
        return {"ok": True}

    def clear(self) -> dict:
        self.entries = []
        self._save()
        return {"ok": True}

    def _save(self):
        with self._qa_lock:
            convo = [{k: v for k, v in t.items() if k != "phase"} for t in self.convo]
        write_json(HISTORY_FILE, {"entries": self.entries, "conversation": convo})

    def shutdown(self):
        self.stop()
        with self._lock:
            self._kill()

    # ── Assistant ─────────────────────────────
    def model(self) -> str:
        """Modèle de l'assistant : celui choisi, sinon celui des fichiers, des documents, de l'assistant ou des conversations."""
        models = self._chat.models if self._chat else []
        wanted = (settings.get("calc_model", ""), settings.get("files_model", ""), settings.get("rag_model", ""),
                  settings.get("assistant_model", ""), settings.get("last_model", ""))
        return next((m for m in wanted if m and m in models), models[0] if models else "")

    def conversation(self) -> list[dict]:
        with self._qa_lock:
            return [dict(t, calcs=list(t.get("calcs", []))) for t in self.convo]

    def _check(self) -> str:
        chat = self._chat
        if chat is None or _ollama is None:
            return "La librairie « ollama » est absente (pip install ollama)."
        if not chat.available:
            return "Ollama ne répond pas : il est nécessaire pour l'assistant."
        if not self.model():
            chat.check()
            return chat.models_error or "Aucun modèle Ollama installé."
        return ""

    def ask(self, question: str) -> dict:
        question = str(question or "").strip()[:2000]
        if not question:
            return {"ok": False, "error": "Écrivez d'abord votre demande."}
        error = self._check()
        if error:
            return {"ok": False, "error": error}
        turn = {"id": uuid.uuid4().hex[:10], "question": question, "answer": "", "calcs": [],
                "state": "thinking", "phase": "", "error": "", "model": self.model(), "at": _now()}
        stop = threading.Event()
        with self._qa_lock:
            if self._job:                  # une réponse à la fois : la nouvelle arrête celle en cours
                self._job[1].set()
            self.convo.append(turn)
            del self.convo[:-MAX_TURNS]
            self._job = (turn, stop)
        self._emit_turn(turn)
        threading.Thread(target=self._answer, args=(turn, stop), daemon=True, name="calc-assistant").start()
        return {"ok": True, "id": turn["id"]}

    def stop(self):
        with self._qa_lock:
            job = self._job
        if job:
            job[1].set()

    def delete_turn(self, turn_id: str):
        with self._qa_lock:
            if self._job and self._job[0]["id"] == turn_id:
                self._job[1].set()
            self.convo = [t for t in self.convo if t["id"] != turn_id]
        self._save()
        self._emit("calc:conversation", {"items": self.conversation()})

    def clear_conversation(self):
        with self._qa_lock:
            if self._job:
                self._job[1].set()
            self.convo = []
        self._save()
        self._emit("calc:conversation", {"items": []})

    def _emit_turn(self, turn: dict):
        with self._qa_lock:
            data = dict(turn, calcs=list(turn["calcs"]))
        self._emit("calc:qa", {"item": data})

    def _phase(self, turn: dict, state: str, phase: str):
        turn.update(state=state, phase=phase)
        self._emit_turn(turn)

    def _context(self) -> str:
        """Ce que le modèle sait de la calculatrice : date, variables, derniers calculs."""
        now = datetime.now()
        lines = [f"Aujourd'hui : {now:%d/%m/%Y}, {now:%H:%M}."]
        if self.vars:
            lines.append("\nVariables actuelles de la console :")
            for v in self.vars:
                lines.append(f"- {v['value']} (fonction)" if v["type"] == "fonction"
                             else f"- {v['name']} ({v['type']}) = {v['value']}")
        else:
            lines.append("\nAucune variable dans la console pour l'instant.")
        recent = self.entries[-CONTEXT_ENTRIES:]
        if recent:
            lines.append("\nDerniers calculs de l'historique (#numéro › code, puis le résultat) :")
            block = []
            for e in recent:
                code = e["code"].replace("\n", "\n    ")
                past = "" if e.get("session") and e.get("session") == self.session else \
                    "  [session terminée : ses variables n'existent plus]"
                item = [f"#{e['n']} › {code}{past}"]
                if e.get("output"):
                    item.append("  affiche : " + e["output"].strip()[:300].replace("\n", " / "))
                if e.get("result") is not None:
                    item.append(f"  = {e['result'][:300]}")
                if e.get("error"):
                    item.append("  erreur : " + e["error"].strip().splitlines()[-1][:300])
                block.append("\n".join(item))
            while len(block) > 1 and sum(len(b) + 1 for b in block) > CONTEXT_CHARS:
                block.pop(0)              # trop long : les plus anciens d'abord
            lines += block
        else:
            lines.append("\nL'historique des calculs est vide.")
        return "\n".join(lines)

    def _messages(self, turn: dict) -> list[dict]:
        messages = [{"role": "system", "content": f"{CALC_SYSTEM}\n\n{self._context()}"}]
        with self._qa_lock:
            before = [t for t in self.convo if t is not turn and t.get("answer") and t.get("state") == "done"]
        for t in before[-HISTORY_TURNS:]:
            messages += [{"role": "user", "content": t["question"]}, {"role": "assistant", "content": t["answer"]}]
        messages.append({"role": "user", "content": turn["question"]})
        return messages

    def _answer(self, turn: dict, stop: threading.Event):
        try:
            messages = self._messages(turn)
            for rnd in range(MAX_ROUNDS):
                last = rnd == MAX_ROUNDS - 1           # dernier aller-retour : répondre, sans outil
                self._phase(turn, "thinking", f"Réflexion avec {turn['model']}…" if rnd == 0 else "Réponse…")
                text, calls, reason = self._stream(turn, messages, None if last else TOOLS, stop)
                if stop.is_set() or not calls:
                    if reason == "length" and not stop.is_set():
                        turn["error"] = ("Réponse coupée : le contexte du modèle est plein "
                                         "(agrandissez-le dans la section Ollama).")
                    break
                messages.append({"role": "assistant", "content": text, "tool_calls": calls})
                for call in calls[:MAX_CALLS]:
                    if stop.is_set():
                        break
                    messages.append({"role": "tool", "tool_name": call.function.name,
                                     "content": self._tool(turn, call)})
            if stop.is_set():
                turn["state"] = "stopped"
            elif turn["error"]:
                turn["state"] = "error"
            elif not turn["answer"].strip():
                turn.update(state="error", error="Le modèle n'a rien répondu.")
            else:
                turn["state"] = "done"
        except Exception as e:
            error = str(e)
            if "does not support tools" in error:
                error = (f"{turn['model']} ne sait pas se servir de la calculatrice (pas d'outils) : "
                         "choisissez un autre modèle, qwen3.5 ou gemma4 par exemple.")
            elif self._chat:
                error = self._chat.failure(e)
            logger.log(f"Calculatrice, assistant ({turn['model']}) : {error}")
            turn.update(state="error", error=error)
        finally:
            turn["phase"] = ""
            with self._qa_lock:
                if self._job and self._job[0] is turn:
                    self._job = None
            self._emit_turn(turn)
            self._save()

    def _stream(self, turn: dict, messages: list[dict], tools, stop: threading.Event):
        """Un appel au modèle : texte (ajouté à la réponse au fil de l'eau), appels d'outils, fin."""
        stream, buf, last, reason, text, calls = None, "", time.monotonic(), None, "", []
        # Texte d'un aller-retour précédent (« Je calcule… ») : la suite commence un paragraphe
        sep = "\n\n" if turn["answer"].strip() else ""
        try:
            stream = _ollama.chat(model=turn["model"], messages=messages, tools=tools, stream=True, think=False,
                                  options={"temperature": 0.2, "num_ctx": num_ctx()}, keep_alive=keep_alive())
            for chunk in stream:
                if stop.is_set():
                    break
                reason = chunk.done_reason or reason
                if chunk.message.tool_calls:
                    calls += chunk.message.tool_calls
                delta = chunk.message.content or ""
                if not delta:
                    continue
                if not text and sep:
                    delta = sep + delta.lstrip()
                text += delta
                turn["answer"] += delta
                buf += delta
                if time.monotonic() - last >= DELTA_INTERVAL:
                    self._emit("calc:delta", {"id": turn["id"], "text": buf})
                    buf, last = "", time.monotonic()
        finally:
            if buf:
                self._emit("calc:delta", {"id": turn["id"], "text": buf})
            if stream is not None and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
        return text, calls, reason

    def _tool(self, turn: dict, call) -> str:
        """Exécute un appel d'outil du modèle ; renvoie le texte de son résultat."""
        name = call.function.name
        args = call.function.arguments or {}
        code = str(args.get("code") or "").strip()
        if name not in ("calculer", "modifier_calcul"):
            return f"Outil inconnu : {name}. Outils : calculer, modifier_calcul."
        if not code:
            return "Erreur : code vide."
        bad = unsafe(code)
        if bad:
            turn["calcs"].append({"op": "refused", "code": code, "error": f"{bad} : refusé"})
            self._emit_turn(turn)
            return (f"Refusé : « {bad} » n'est pas permis ici, la calculatrice ne fait que des calculs "
                    "(pas de fichiers ni de système).")
        if name == "calculer":
            self._phase(turn, "running", "Calcul…")
            res = self.run(code, by="ia", wait=AI_WAIT, timeout=AI_TIMEOUT)
            if not res["ok"]:
                return f"Erreur : {res['error']}"
            e = res["entry"]
            turn["calcs"].append(self._ref(e, "run"))
            self._emit_turn(turn)
            return self._describe([e])
        try:
            n = int(args.get("numero"))
        except (TypeError, ValueError):
            return "Erreur : numéro de calcul manquant (#n)."
        self._phase(turn, "running", f"Modification du calcul #{n}…")
        res = self.recompute(n, code, by="ia", wait=AI_WAIT, timeout=AI_TIMEOUT)
        changed = [e for e in self.entries if e.get("n") in res.get("changed", [])]
        if changed:
            ref = self._ref(changed[0], "edit")
            ref["count"] = len(changed)
            turn["calcs"].append(ref)
            self._emit_turn(turn)
        if not res["ok"]:
            return f"Erreur : {res['error']}" + (f"\n{self._describe(changed)}" if changed else "")
        return self._describe(changed)

    @staticmethod
    def _ref(e: dict, op: str) -> dict:
        """Calcul fait par l'assistant, montré sous sa réponse."""
        ref = {"op": op, "n": e["n"], "code": e["code"]}
        if e.get("result") is not None:
            ref["result"] = e["result"][:200]
            if e.get("number"):
                ref["number"] = e["number"]
        if e.get("error"):
            ref["error"] = e["error"].strip().splitlines()[-1][:200]
        return ref

    @staticmethod
    def _describe(entries: list[dict]) -> str:
        """Résultats de calculs, en texte pour le modèle."""
        out = []
        for e in entries:
            lines = [f"#{e['n']} › {e['code']}"]
            if e.get("output"):
                lines.append("affiche : " + e["output"].strip())
            if e.get("result") is not None:
                lines.append(f"= {e['result']}")
            if e.get("error"):
                lines.append("erreur : " + e["error"].strip())
            if len(lines) == 1:
                lines.append("(fait, pas de résultat à afficher)")
            out.append("\n".join(lines))
        return "\n\n".join(out)[:TOOL_TEXT]
