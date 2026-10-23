"""
calc_worker.py – Interpréteur de la Calculatrice, lancé à part par calc.py :
un calcul trop long ou qui consomme toute la mémoire s'interrompt en
terminant ce processus, sans toucher à Boostache.

Une requête JSON par ligne sur l'entrée standard ({"id", "code"} ; "quiet" :
sans résultat, pour rejouer des calculs), une réponse JSON par ligne sur la
sortie : texte affiché par print, valeur de la dernière expression (comme la
console Python), erreur, variables. S'arrête quand l'entrée se ferme.
"""

import ast
import contextlib
import io
import json
import math
import sys
import traceback
from decimal import Decimal
from fractions import Fraction

MAX_REPR = 20_000            # résultat affiché (caractères)
MAX_OUTPUT = 50_000          # texte des print
VAR_REPR = 80
FILE = "<calcul>"

PRELUDE = '''
from math import *
import math, cmath, statistics, random, itertools, functools, operator, re, json
from statistics import mean, median, mode, stdev, pstdev, variance
from fractions import Fraction
from decimal import Decimal
from datetime import date, datetime, time, timedelta
try:
    import numpy as np
except ImportError:
    pass


def ttc(ht, tva=20):
    """Prix TTC d'un prix hors taxes (TVA en %)."""
    return ht * (1 + tva / 100)


def ht(ttc, tva=20):
    """Prix hors taxes d'un prix TTC (TVA en %)."""
    return ttc / (1 + tva / 100)


def variation(avant, apres):
    """Évolution en % de avant à apres."""
    return (apres - avant) / avant * 100


def pourcentage(part, total):
    """Part de total, en %."""
    return part / total * 100


def mensualite(capital, taux, annees):
    """Mensualité d'un prêt à taux fixe (taux annuel en %)."""
    t = taux / 100 / 12
    n = annees * 12
    return capital / n if t == 0 else capital * t / (1 - (1 + t) ** -n)


def interets(capital, taux, annees, par_an=1):
    """Capital au bout de `annees` avec intérêts composés (taux annuel en %)."""
    return capital * (1 + taux / 100 / par_an) ** (annees * par_an)


JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def jour(d):
    """Jour de la semaine d'une date, en français."""
    return JOURS[d.weekday()]


def input(*_):
    raise RuntimeError("input() n'est pas disponible dans la calculatrice : mettez la valeur dans une variable.")
'''


def _namespace() -> tuple[dict, dict]:
    """Espace des calculs, et ce que le prélude y a mis (nom → identité de la valeur)."""
    ns = {"__name__": "__calc__"}
    exec(PRELUDE, ns)
    return ns, {name: id(value) for name, value in ns.items()}


def _short(value) -> str:
    try:
        text = repr(value)
    except Exception as e:
        text = f"<repr impossible : {e}>"
    return text if len(text) <= VAR_REPR else text[:VAR_REPR - 1] + "…"


def _plain(value):
    """Scalaire numpy → nombre Python (np.float64(2.0) s'affiche 2.0)."""
    item = getattr(value, "item", None)
    if item is not None and type(value).__module__ == "numpy" and getattr(value, "shape", None) == ():
        try:
            return item()
        except Exception:
            pass
    return value


def _number(value):
    """Valeur numérique à montrer en notation française (texte), ou None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (float, Fraction, Decimal)):
        try:
            f = float(value)
        except (OverflowError, ValueError):
            return None
        return repr(f) if math.isfinite(f) else None
    return None


def _variables(ns: dict, base: dict) -> list[dict]:
    """Variables de l'utilisateur (un nom du prélude réaffecté compte aussi)."""
    out = []
    for name, value in ns.items():
        if name.startswith("_") or base.get(name) == id(value) or name == "ans":
            continue
        if type(value).__name__ == "module":
            continue
        if callable(value) and hasattr(value, "__code__"):
            try:
                import inspect
                signature = str(inspect.signature(value))
            except (TypeError, ValueError):
                signature = "(…)"
            out.append({"name": name, "type": "fonction", "value": f"{name}{signature}"})
        else:
            out.append({"name": name, "type": type(value).__name__, "value": _short(_plain(value))})
    return out[:60]


def _error(exc: BaseException) -> str:
    """Erreur sans les lignes de cet interpréteur : seulement celles du calcul."""
    if isinstance(exc, SyntaxError):
        return "".join(traceback.format_exception_only(type(exc), exc)).rstrip()
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == FILE]
    lines = []
    if frames:
        lines.append("Traceback (most recent call last):")
        lines += [line.rstrip() for line in traceback.format_list(frames)]
    lines.append("".join(traceback.format_exception_only(type(exc), exc)).rstrip())
    return "\n".join(lines)


def run(code: str, ns: dict, quiet: bool = False) -> dict:
    out = io.StringIO()
    result = {"output": "", "result": None, "number": None, "error": None}
    try:
        tree = ast.parse(code, FILE, "exec")
        last = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = ast.Expression(tree.body.pop().value)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            if tree.body:
                exec(compile(tree, FILE, "exec"), ns)
            value = eval(compile(last, FILE, "eval"), ns) if last is not None else None
        if value is not None:
            ns["_"] = ns["ans"] = value
            if not quiet:
                value = _plain(value)
                text = repr(value)
                result["result"] = text if len(text) <= MAX_REPR else (
                    text[:MAX_REPR] + f"… ({len(text) - MAX_REPR:,} caractères de plus)".replace(",", " "))
                result["type"] = type(value).__name__
                result["number"] = _number(value)
    except SystemExit:
        result["error"] = "exit() ne ferme pas la calculatrice : « Réinitialiser » efface les variables."
    except BaseException as e:            # MemoryError, RecursionError… : rapportées comme les autres
        result["error"] = _error(e)
    text = out.getvalue()
    result["output"] = text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + "\n…"
    return result


def main():
    sys.set_int_max_str_digits(0)     # grands entiers : affichage tronqué par MAX_REPR, pas d'erreur
    sys.setrecursionlimit(10_000)
    ns, base = _namespace()
    stdin, stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO()
    stdout.write(json.dumps({"ready": True}) + "\n")
    stdout.flush()
    for line in stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        res = run(str(req.get("code", "")), ns, bool(req.get("quiet")))
        res["id"] = req.get("id")
        res["vars"] = _variables(ns, base)
        stdout.write(json.dumps(res) + "\n")
        stdout.flush()


if __name__ == "__main__":
    main()
