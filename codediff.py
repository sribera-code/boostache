"""
codediff.py – Comparer, mode « par symbole » : le code découpé en symboles et
ses différences symbole à symbole.

Un symbole : mot-clé, nom, nombre, chaîne, commentaire ou opérateur entier
(« <<= », « -> », « :: »). Les espaces et les retours à la ligne ne comptent pas
— un reformatage n'est pas une différence —, sauf en Python, où chaque ligne
logique commence par un symbole « niveau d'indentation ».

Comparaison en deux temps : difflib sur les lignes réduites à leurs symboles
(rapide ; l'ordre des lignes guide l'alignement), puis symbole à symbole dans
chaque zone changée. Une petite partie commune coincée entre deux changements
plus longs est absorbée : un passage réécrit donne une différence, pas des
confettis. Chaque différence porte la plage du texte de gauche qu'elle remplace
dans la fusion et le texte de droite qui y va ; autour d'un ajout ou d'une
suppression, les espaces et la fin de ligne suivent les symboles.
"""

import difflib
import hashlib
import os
import re
from bisect import bisect_left, bisect_right

AUTOJUNK_LINES = 20_000       # au-delà, difflib écarte les lignes très fréquentes (plus rapide)
SMALL_PAIRS    = 250_000      # zone comparée d'un coup par difflib (symboles gauche × droite) ; au-delà,
                              # découpée d'abord aux symboles uniques des deux côtés (comme « patience diff »)
MAX_DEPTH      = 40           # découpages successifs au plus ; au-delà, le reste est une seule différence
REWRITTEN      = 0.4          # zone changée qui se ressemble moins (part commune) : réécrite, une seule différence
SPLIT_CUTOFF   = 0.6          # passage sur plusieurs lignes : ressemblance de deux lignes pour les mettre en regard
SPLIT_REWRITTEN = 0.8         # … dans une zone réécrite (seules des lignes presque identiques restent à part)
ALIGN_GROUPS   = 400          # lignes d'un passage comparées au plus (gauche × droite) pour les mettre en regard
CONTEXT_TOKENS = 6            # symboles qui précèdent une différence, dans sa signature

KIND_NAMES = {"comment": "commentaire", "string": "chaîne", "number": "nombre", "keyword": "mot-clé",
              "identifier": "nom", "punct": "opérateur", "indent": "indentation"}

# ─────────────────────────────────────────────
#  Langages
# ─────────────────────────────────────────────
_DQ = r'"(?:\\.|[^"\\\n])*"'                 # chaîne sur une ligne, échappements « \ »
_SQ = r"'(?:\\.|[^'\\\n])*'"
_DQ_ML = r'"(?:\\[\s\S]|[^"\\])*"'           # sur plusieurs lignes
_SQ_ML = r"'(?:\\[\s\S]|[^'\\])*'"
_CHAR = r"'(?:\\.|[^'\\\n]){1,10}'"          # caractère : court, pour ne pas lire « 1'000 » comme une chaîne
_TRIPLE = [r'"""[\s\S]*?"""', r"'''[\s\S]*?'''"]
_SLASH = [r"//[^\n]*", r"/\*[\s\S]*?(?:\*/|\Z)"]
_BLOCK = [r"/\*[\s\S]*?(?:\*/|\Z)"]
_HASH = r"(?<![^\s;(|&{])#[^\n]*"            # « # » en début de mot (pas « $# »)
_CPP_PREFIX = r"(?:u8|[uUL])?"
_CPP_STRINGS = [_CPP_PREFIX + r'R"(?P<rd>[^ ()\\\t\n]{0,16})\([\s\S]*?\)(?P=rd)"', _CPP_PREFIX + _DQ, _CPP_PREFIX + _CHAR]

_PUNCT = """>>>= <<= >>= >>> <=> ... === !== **= ??= &&= ||= :: -> => ++ -- << >> <= >= == != && || ?? ?.
            += -= *= /= %= &= |= ^= ** ..""".split()

_KW_C = """auto break case char const continue default do double else enum extern float for goto if inline int long
register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while _Bool
bool true false NULL"""
_KW_CPP = _KW_C + """ alignas alignof and asm catch char8_t char16_t char32_t class co_await co_return co_yield concept
const_cast consteval constexpr constinit decltype delete dynamic_cast explicit export final friend import module
mutable namespace new noexcept not nullptr operator or override private protected public reinterpret_cast requires
static_assert static_cast template this thread_local throw try typeid typename using virtual wchar_t xor"""
_KW_CS = """abstract as base bool break byte case catch char checked class const continue decimal default delegate do
double else enum event explicit extern false finally fixed float for foreach goto if implicit in int interface internal
is lock long namespace new null object operator out override params private protected public readonly record ref
return sbyte sealed short sizeof stackalloc static string struct switch this throw true try typeof uint ulong unchecked
unsafe ushort using var virtual void volatile while async await get set init yield when where dynamic nameof"""
_KW_JAVA = """abstract assert boolean break byte case catch char class const continue default do double else enum
extends final finally float for goto if implements import instanceof int interface long native new package private
protected public return short static strictfp super switch synchronized this throw throws transient try void volatile
while true false null var record sealed permits yield"""
_KW_JVM = """val var fun class object interface if else when for while do return break continue try catch finally throw
import package is as in null true false this super override open data sealed private public protected internal func
let struct enum protocol extension guard self Self nil static def trait match case new extends with yield lazy final
async await void int double bool dynamic"""
_KW_JS = """break case catch class const continue debugger default delete do else export extends false finally for
function if import in instanceof let new null return super switch this throw true try typeof undefined var void while
with yield async await of static get set interface type enum implements namespace declare abstract readonly public
private protected keyof as any never unknown number string boolean"""
_KW_GO = """break case chan const continue default defer else fallthrough for func go goto if import interface map package
range return select struct switch type var nil true false iota"""
_KW_RUST = """as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod
move mut pub ref return self Self static struct super trait true type unsafe use where while"""
_KW_PHP = """abstract and array as break callable case catch class clone const continue declare default do echo else
elseif empty extends final finally fn for foreach function global goto if implements include include_once instanceof
interface isset list match namespace new or print private protected public readonly require require_once return static
switch throw trait try unset use var while xor yield true false null"""
_KW_PY = """False None True and as assert async await break class continue def del elif else except finally for from
global if import in is lambda nonlocal not or pass raise return try while with yield match case self"""
_KW_PS = """begin break catch class continue data do dynamicparam else elseif end enum exit filter finally for foreach
from function if in param process return switch throw trap try until using var while"""
_KW_SH = "if then else elif fi case esac for select while until do done in function local export readonly return exit"
_KW_RUBY = """alias and begin break case class def do else elsif end ensure false for if in module next nil not or redo
rescue retry return self super then true undef unless until when while yield"""
_KW_SQL = """select from where and or not insert into values update set delete create table alter drop index view join
inner left right outer full cross on as group by order having limit offset union all distinct case when then else end
null is in exists between like primary key foreign references default constraint unique begin commit rollback declare
procedure function returns return if while top with"""
_KW_LUA = "and break do else elseif end false for function goto if in local nil not or repeat return then true until while"

# clé : nom affiché, extensions, commentaires, chaînes, et au besoin : mots-clés (kw), mots-clés sans
# casse (ci), noms entre guillemets (names), opérateurs en plus (punct), séparateurs de chiffres (digits),
# indentation qui compte (indent), forme des noms (ident)
_C_LIKE = {"comments": _SLASH}
_SPECS = {
    "c": {**_C_LIKE, "name": "C", "ext": ".c", "strings": _CPP_STRINGS, "kw": _KW_C, "digits": True},
    "cpp": {**_C_LIKE, "name": "C++", "ext": ".cpp .cc .cxx .c++ .hpp .hh .hxx .h++ .h .inl .ipp .tpp .ixx .cppm .cu "
                                             ".cuh .ino", "strings": _CPP_STRINGS, "kw": _KW_CPP, "digits": True,
            "punct": ["->*", ".*"]},
    "objc": {**_C_LIKE, "name": "Objective-C", "ext": ".m .mm", "strings": ["@?" + _DQ, _CHAR], "kw": _KW_CPP},
    "cs": {**_C_LIKE, "name": "C#", "ext": ".cs .csx", "kw": _KW_CS,
           "strings": [r'\$*"""[\s\S]*?"""', r'(?:\$@|@\$|@)"(?:[^"]|"")*"', r"\$" + _DQ, _DQ, _CHAR]},
    "java": {**_C_LIKE, "name": "Java", "ext": ".java", "strings": [_TRIPLE[0], _DQ, _CHAR], "kw": _KW_JAVA},
    "kotlin": {**_C_LIKE, "name": "Kotlin", "ext": ".kt .kts", "strings": [_TRIPLE[0], _DQ, _CHAR], "kw": _KW_JVM},
    "scala": {**_C_LIKE, "name": "Scala", "ext": ".scala .sc", "strings": [_TRIPLE[0], _DQ, _CHAR], "kw": _KW_JVM},
    "groovy": {**_C_LIKE, "name": "Groovy", "ext": ".groovy .gradle", "strings": [*_TRIPLE, _DQ, _SQ], "kw": _KW_JVM},
    "swift": {**_C_LIKE, "name": "Swift", "ext": ".swift", "strings": [_TRIPLE[0], _DQ], "kw": _KW_JVM},
    "dart": {**_C_LIKE, "name": "Dart", "ext": ".dart", "strings": [*_TRIPLE, _DQ, _SQ], "kw": _KW_JVM},
    "js": {**_C_LIKE, "name": "JavaScript", "ext": ".js .mjs .cjs .jsx", "kw": _KW_JS,
           "strings": [r"`(?:\\[\s\S]|[^`\\])*`", _DQ, _SQ]},
    "ts": {**_C_LIKE, "name": "TypeScript", "ext": ".ts .mts .cts .tsx", "kw": _KW_JS,
           "strings": [r"`(?:\\[\s\S]|[^`\\])*`", _DQ, _SQ]},
    "go": {**_C_LIKE, "name": "Go", "ext": ".go", "strings": [r"`[^`]*`", _DQ, _CHAR], "kw": _KW_GO,
           "punct": [":=", "<-"]},
    "rust": {**_C_LIKE, "name": "Rust", "ext": ".rs", "kw": _KW_RUST, "punct": ["..="],
             "strings": [r'b?r(?P<rh>#*)"[\s\S]*?"(?P=rh)', r'b?"(?:\\[\s\S]|[^"\\])*"',
                         r"b?'(?:\\(?:x[0-9a-fA-F]{2}|u\{[0-9a-fA-F]{1,6}\}|.)|[^'\\\n])'"]},
    "php": {"name": "PHP", "ext": ".php", "comments": [*_SLASH, r"#(?!\[)[^\n]*"], "strings": [_DQ_ML, _SQ_ML],
            "kw": _KW_PHP},
    "shader": {**_C_LIKE, "name": "Shader", "ext": ".glsl .vert .frag .geom .comp .hlsl .hlsli .fx .fxh .shader .metal "
                                                   ".wgsl", "strings": [_DQ], "kw": _KW_C},
    "proto": {**_C_LIKE, "name": "Protobuf", "ext": ".proto", "strings": [_DQ, _SQ],
              "kw": "syntax package import option message enum service rpc returns repeated optional required oneof map"},
    "css": {"name": "CSS", "ext": ".css", "comments": _BLOCK, "strings": [_DQ, _SQ]},
    "scss": {**_C_LIKE, "name": "SCSS", "ext": ".scss .less", "strings": [_DQ, _SQ]},
    "json": {**_C_LIKE, "name": "JSON", "ext": ".json .jsonc .json5", "strings": [_DQ, _SQ], "kw": "true false null"},
    "python": {"name": "Python", "ext": ".py .pyw .pyi", "comments": [r"#[^\n]*"], "kw": _KW_PY, "indent": True,
               "strings": [r"(?:[rRbBuUfF]{1,2})?(?:" + "|".join([*_TRIPLE, _DQ, _SQ]) + ")"],
               "punct": ["//=", "//", "@=", ":="]},
    "powershell": {"name": "PowerShell", "ext": ".ps1 .psm1 .psd1", "kw": _KW_PS, "ci": True,
                   "comments": [r"<#[\s\S]*?(?:#>|\Z)", _HASH],
                   "strings": [r'@"[\s\S]*?\n"@', r"@'[\s\S]*?\n'@", r'"(?:`[\s\S]|""|[^"`])*"', r"'(?:''|[^'])*'"]},
    "shell": {"name": "Shell", "ext": ".sh .bash .zsh", "comments": [_HASH], "strings": [_DQ_ML, r"'[^']*'"],
              "kw": _KW_SH},
    "ruby": {"name": "Ruby", "ext": ".rb", "comments": [_HASH], "strings": [_DQ_ML, _SQ_ML], "kw": _KW_RUBY},
    "perl": {"name": "Perl", "ext": ".pl .pm", "comments": [_HASH], "strings": [_DQ_ML, _SQ_ML]},
    "r": {"name": "R", "ext": ".r", "comments": [_HASH], "strings": [_DQ_ML, _SQ_ML]},
    "cmake": {"name": "CMake", "ext": ".cmake", "comments": [r"#\[(?P<cc>=*)\[[\s\S]*?\](?P=cc)\]", r"#[^\n]*"],
              "strings": [r"\[(?P<cb>=*)\[[\s\S]*?\](?P=cb)\]", _DQ_ML], "ci": True,
              "kw": "if elseif else endif foreach endforeach while endwhile function endfunction macro endmacro"},
    "toml": {"name": "TOML", "ext": ".toml", "comments": [_HASH], "strings": [*_TRIPLE, _DQ, r"'[^'\n]*'"],
             "kw": "true false"},
    "sql": {"name": "SQL", "ext": ".sql", "comments": [r"--[^\n]*", *_BLOCK], "strings": [r"[NnEe]?'(?:''|[^'])*'"],
            "names": [r'"(?:""|[^"])*"', r"`[^`]*`", r"\[[^\]\n]*\]"], "kw": _KW_SQL, "ci": True, "punct": ["<>"]},
    "lua": {"name": "Lua", "ext": ".lua", "comments": [r"--\[(?P<lc>=*)\[[\s\S]*?\](?P=lc)\]", r"--[^\n]*"],
            "strings": [r"\[(?P<ls>=*)\[[\s\S]*?\](?P=ls)\]", _DQ, _SQ], "kw": _KW_LUA, "punct": ["~="]},
    "xml": {"name": "XML", "ext": ".xml .xsd .xsl .xslt .svg .xaml .csproj .vcxproj .vbproj .fsproj .props .targets "
                                 ".resx .config .manifest .plist .nuspec .wxs", "comments": [r"<!--[\s\S]*?(?:-->|\Z)"],
            "strings": [r"<!\[CDATA\[[\s\S]*?\]\]>", r'(?<==)"[^"]*"', r"(?<==)'[^']*'"],
            "ident": r"[^\W\d][\w.:-]*", "punct": ["</", "/>", "<?", "?>"]},
    "html": {"name": "HTML", "ext": ".html .htm .xhtml .vue", "comments": [r"<!--[\s\S]*?(?:-->|\Z)"],
             "strings": [r'(?<==)"[^"]*"', r"(?<==)'[^']*'"], "ident": r"[^\W\d][\w.:-]*", "punct": ["</", "/>"]},
    # Code collé, langage inconnu : les formes les plus courantes (famille du C)
    "code": {**_C_LIKE, "name": "code", "ext": "", "strings": [_CPP_STRINGS[0], r"`(?:\\[\s\S]|[^`\\])*`", _DQ, _SQ],
             "kw": " ".join({*_KW_CPP.split(), *_KW_CS.split(), *_KW_JAVA.split(), *_KW_JS.split()})},
}
_EXT = {e: key for key, spec in _SPECS.items() for e in spec["ext"].split()}
_NAMES = {"cmakelists.txt": "cmake", "makefile": "", "dockerfile": ""}


def language(name: str) -> str:
    """Langage d'un fichier d'après son nom ("" : pas du code reconnu)."""
    base = os.path.basename(str(name or "")).lower()
    if base in _NAMES:
        return _NAMES[base]
    return _EXT.get(os.path.splitext(base)[1], "")


def label(lang: str) -> str:
    return (_SPECS.get(lang) or _SPECS["code"])["name"]


def guess(text: str) -> str:
    """Langage d'un texte collé, s'il ressemble nettement à du code ("" sinon)."""
    sample = (text or "")[:20_000]
    lines = [line.strip() for line in sample.split("\n") if line.strip()]
    if len(lines) < 2:
        return ""
    semis = sum(line.endswith(";") for line in lines)
    if semis <= len(lines) * 0.1 and (re.search(r"^[ \t]*(?:async\s+)?(?:def|class)\s+\w+[^\n]*:[ \t]*(?:#[^\n]*)?$", sample, re.M)
                                      or re.search(r"^(?:from\s+[\w.]+\s+)?import\s+[\w.]+(?:\s+as\s+\w+)?[ \t]*$", sample, re.M)):
        return "python"
    if re.search(r"^[ \t]*#[ \t]*(?:include|define|pragma|ifn?def)\b", sample, re.M) or "std::" in sample:
        return "cpp"
    code = sum(line.endswith((";", "{", "}", "};", ");")) for line in lines)
    return "code" if code >= 2 and code >= len(lines) * 0.3 else ""


# ─────────────────────────────────────────────
#  Découpage en symboles
# ─────────────────────────────────────────────
class _Lexer:
    def __init__(self, key: str):
        spec = _SPECS.get(key) or _SPECS["code"]
        self.indent = bool(spec.get("indent"))
        self.ci = bool(spec.get("ci"))
        self.keywords = frozenset(w.casefold() if self.ci else w for w in spec.get("kw", "").split())
        punct = sorted({*_PUNCT, *spec.get("punct", [])}, key=len, reverse=True)
        digits = r"'(?=[0-9A-Za-z])|" if spec.get("digits") else ""
        parts = [("cont", r"\\\r?\n")] if self.indent else []
        parts.append(("space", r"\s+"))
        if spec.get("comments"):
            parts.append(("comment", "|".join(spec["comments"])))
        if spec.get("names"):
            parts.append(("name", "|".join(spec["names"])))
        if spec.get("strings"):
            parts.append(("string", "|".join(spec["strings"])))
        parts += [("number", r"\.?\d(?:[eEpP][+-]|" + digits + r"[\w.])*"),
                  ("word", spec.get("ident", r"(?:[^\W\d]|\$)[\w$]*")),
                  ("punct", "|".join(map(re.escape, punct))),
                  ("other", r"[\s\S]")]
        self.regex = re.compile("|".join(f"(?P<{n}>{p})" for n, p in parts))

    def tokenize(self, text: str) -> list[tuple]:
        """Symboles du texte : (genre, début, fin, clé). En Python, chaque ligne logique
        commence par un symbole « indent » : le retour à la ligne qui la précède et son
        indentation (sa clé : le niveau d'indentation)."""
        out = []
        indent, depth, pending, levels = self.indent, 0, 0, [0]
        for m in self.regex.finditer(text):
            kind = m.lastgroup
            if kind == "space":
                if indent and not depth:
                    nl = m.group().rfind("\n")
                    if nl >= 0:
                        pending = m.start() + nl + 1
                continue
            if kind == "cont":
                continue
            start, end = m.span()
            tok = m.group()
            if kind == "comment":
                out.append(("comment", start, end, tok))
                continue
            if indent and pending is not None:
                width = len(text[pending:start].expandtabs(8))
                if width > levels[-1]:
                    levels.append(width)
                else:
                    while len(levels) > 1 and width < levels[-1]:
                        levels.pop()
                    if width > levels[-1]:
                        levels.append(width)
                begin = pending - 1 if pending and text[pending - 1] == "\n" else pending
                out.append(("indent", begin, start, f"\0{len(levels) - 1}"))
                pending = None
            if kind == "word":
                kind = "keyword" if (tok.casefold() if self.ci else tok) in self.keywords else "identifier"
            elif kind == "name":
                kind = "identifier"
            elif kind in ("punct", "other"):
                kind = "punct"
                if indent:
                    if tok in ("(", "[", "{"):
                        depth += 1
                    elif tok in (")", "]", "}"):
                        depth = max(0, depth - 1)
            out.append((kind, start, end, tok))
        return out


_LEXERS: dict[str, _Lexer] = {}


def lexer(lang: str) -> _Lexer:
    key = lang if lang in _SPECS else "code"
    if key not in _LEXERS:
        _LEXERS[key] = _Lexer(key)
    return _LEXERS[key]


def tokenize(text: str, lang: str) -> list[tuple]:
    return lexer(lang).tokenize(text)


# ─────────────────────────────────────────────
#  Différences
# ─────────────────────────────────────────────
def line_regions(ka: list, kb: list) -> list[tuple]:
    """Plages de lignes qui diffèrent : [(a0, a1, b0, b1)] (difflib, début et fin communs écartés d'abord)."""
    n = min(len(ka), len(kb))
    pre = 0
    while pre < n and ka[pre] == kb[pre]:
        pre += 1
    suf = 0
    while suf < n - pre and ka[-1 - suf] == kb[-1 - suf]:
        suf += 1
    sa, sb = ka[pre:len(ka) - suf], kb[pre:len(kb) - suf]
    matcher = difflib.SequenceMatcher(None, sa, sb, autojunk=len(sb) > AUTOJUNK_LINES)
    return [(i1 + pre, i2 + pre, j1 + pre, j2 + pre)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


class _Side:
    """Un côté : texte, symboles (tous, et ceux qui comptent), lignes."""

    def __init__(self, text: str, tokens: list[tuple], options: dict):
        self.text = text
        self.all = tokens
        skip = "comment" if options.get("comments") else None
        self.idx = [n for n, t in enumerate(tokens) if t[0] != skip]
        self.sig = [tokens[n] for n in self.idx]
        self.keys = [_key(t, options) for t in self.sig]
        self.starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.nlines = text.count("\n") + (0 if not text or text.endswith("\n") else 1)
        # Ligne de chaque symbole ; clé de chaque ligne : ses symboles (un symbole sur plusieurs
        # lignes marque chacune, pour qu'un changement à l'intérieur les rende toutes différentes)
        parts = [[] for _ in range(self.nlines + 1)]
        self.line = []
        line, starts = 0, self.starts
        for tok, key in zip(self.sig, self.keys):
            at = tok[2] if tok[0] == "indent" else tok[1]      # (l'indentation : sur la ligne qu'elle ouvre)
            while line + 1 < len(starts) and starts[line + 1] <= at:
                line += 1
            self.line.append(line)
            last = self.line_of(tok[2] - 1) if (tok[0] != "indent" and tok[2] - tok[1] > 1
                                                and "\n" in text[tok[1]:tok[2]]) else line
            if last == line:
                parts[line].append(key)
            else:
                mark = f"\1{hashlib.sha1(key.encode('utf-8', 'surrogatepass')).hexdigest()[:12]}"
                for n in range(line, last + 1):
                    parts[n].append(mark)
        self.line_keys = ["\x1f".join(p) for p in parts[:self.nlines]]

    def line_of(self, pos: int) -> int:
        return bisect_right(self.starts, pos) - 1

    def col(self, pos: int) -> int:
        return pos - self.starts[self.line_of(pos)]

    def shares_line(self, i: int, j: int) -> bool:
        """Les symboles i et j existent et sont sur la même ligne."""
        return 0 <= i < len(self.sig) and 0 <= j < len(self.sig) and self.line[i] == self.line[j]

    def tokens_in(self, l0: int, l1: int) -> tuple[int, int]:
        """Symboles qui commencent sur les lignes [l0, l1)."""
        return bisect_left(self.line, l0), bisect_left(self.line, l1)

    def prev_end(self, i: int) -> int | None:
        """Fin du symbole (commentaire compris) juste avant le symbole i (len : la fin du texte)."""
        n = self.idx[i] - 1 if i < len(self.sig) else len(self.all) - 1
        return self.all[n][2] if n >= 0 else None

    def next_start(self, i: int) -> int | None:
        """Début du symbole (commentaire compris) juste après le symbole i."""
        n = self.idx[i] + 1
        return self.all[n][1] if n < len(self.all) else None

    def marks(self, c0: int, c1: int) -> list[list[int]]:
        """Plage [c0, c1) → [[ligne, début, fin]] par ligne, sans les espaces de part et d'autre ;
        une plage faite d'espaces (indentation) : marquée telle quelle sur sa dernière ligne."""
        out = []
        for line in range(self.line_of(c0), self.line_of(max(c0, c1 - 1)) + 1):
            ls = self.starts[line]
            s, e = max(c0, ls), min(c1, self.line_end(line))
            seg = self.text[s:e]
            lead, keep = len(seg) - len(seg.lstrip()), len(seg.rstrip())
            if keep > lead:
                out.append([line, s - ls + lead, s - ls + keep])
        if not out:
            line = self.line_of(c1)
            ls = self.starts[line]
            out.append([line, max(c0, ls) - ls, c1 - ls])
        return out

    def line_end(self, line: int) -> int:
        return self.starts[line + 1] - 1 if line + 1 < len(self.starts) else len(self.text)

    def content(self, l0: int, l1: int) -> tuple[int, int]:
        """Lignes [l0, l1) sans l'indentation de la première ni les espaces de fin de la dernière."""
        s, e = self.starts[l0], self.line_end(l1 - 1)
        seg = self.text[s:e]
        return s + len(seg) - len(seg.lstrip()), s + len(seg.rstrip())


def _key(tok: tuple, options: dict) -> str:
    kind, _, _, key = tok
    if kind == "comment" or (kind == "string" and options.get("space")):
        key = " ".join(key.split())
    if options.get("case") and kind != "indent":
        key = key.casefold()
    return key


def _match(a: list, b: list, alo: int, ahi: int, blo: int, bhi: int, out: list, depth: int = 0):
    """Parties communes de a[alo:ahi] et b[blo:bhi] → out [(i, j, longueur)], dans l'ordre."""
    n = 0
    while alo + n < ahi and blo + n < bhi and a[alo + n] == b[blo + n]:
        n += 1
    if n:
        out.append((alo, blo, n))
        alo, blo = alo + n, blo + n
    n = 0
    while ahi - n > alo and bhi - n > blo and a[ahi - 1 - n] == b[bhi - 1 - n]:
        n += 1
    tail = (ahi - n, bhi - n, n) if n else None
    ahi, bhi = ahi - n, bhi - n
    if alo < ahi and blo < bhi:
        if (ahi - alo) * (bhi - blo) <= SMALL_PAIRS:
            matcher = difflib.SequenceMatcher(None, a[alo:ahi], b[blo:bhi], autojunk=False)
            out.extend((alo + i, blo + j, size) for i, j, size in matcher.get_matching_blocks() if size)
        elif depth < MAX_DEPTH:
            anchors = _anchors(a, b, alo, ahi, blo, bhi)
            i0, j0 = alo, blo
            for i, j in anchors:
                _match(a, b, i0, i, j0, j, out, depth + 1)
                out.append((i, j, 1))
                i0, j0 = i + 1, j + 1
            if anchors:
                _match(a, b, i0, ahi, j0, bhi, out, depth + 1)
    if tail:
        out.append(tail)


def _anchors(a: list, b: list, alo: int, ahi: int, blo: int, bhi: int) -> list[tuple]:
    """Symboles présents une seule fois de chaque côté, gardés dans le même ordre des deux côtés
    (plus longue sous-suite croissante) : [(i, j)]."""
    count = {}
    for i in range(alo, ahi):
        count[a[i]] = count.get(a[i], 0) + 1
    where = {}
    for j in range(blo, bhi):
        if count.get(b[j]) == 1:
            where[b[j]] = -1 if b[j] in where else j
    cand = [(i, where[a[i]]) for i in range(alo, ahi) if count[a[i]] == 1 and where.get(a[i], -1) >= 0]
    tails, ends, prev = [], [], [-1] * len(cand)
    for n, (_, j) in enumerate(cand):
        k = bisect_left(tails, j)
        if k == len(tails):
            tails.append(j)
            ends.append(n)
        else:
            tails[k], ends[k] = j, n
        prev[n] = ends[k - 1] if k else -1
    out, n = [], ends[-1] if ends else -1
    while n >= 0:
        out.append(cand[n])
        n = prev[n]
    return out[::-1]


def _edits(ka: list[str], kb: list[str], la: list[int], lb: list[int]) -> tuple[list[tuple], bool]:
    """Différences entre deux suites de symboles (la, lb : leurs lignes) : ([(i1, i2, j1, j2)],
    réécrit ?), petites parties communes coincées entre deux changements absorbées — sauf si elles
    séparent deux lignes des deux côtés (deux changements sur deux lignes restent deux
    différences) ; une zone qui se ressemble trop peu : une seule différence (réécrite)."""
    blocks = []
    _match(ka, kb, 0, len(ka), 0, len(kb), blocks)
    # Segments : [commun ?, i1, i2, j1, j2]
    segs, i, j = [], 0, 0
    for bi, bj, size in blocks + [(len(ka), len(kb), 0)]:
        if bi > i or bj > j:
            segs.append([False, i, bi, j, bj])
        if size:
            if segs and segs[-1][0] and segs[-1][2] == bi and segs[-1][4] == bj:
                segs[-1][2], segs[-1][4] = bi + size, bj + size
            else:
                segs.append([True, bi, bi + size, bj, bj + size])
        i, j = bi + size, bj + size
    wa, wb = _widths(ka), _widths(kb)
    edits = [s for s in segs if not s[0]]
    same = sum(wa[s[2]] - wa[s[1]] for s in segs if s[0])
    if edits and 2 * same < REWRITTEN * (wa[-1] + wb[-1]):
        return [(edits[0][1], edits[-1][2], edits[0][3], edits[-1][4])], True

    def size(seg):
        return max(wa[seg[2]] - wa[seg[1]], wb[seg[4]] - wb[seg[3]])

    def crosses(lines, s, e):
        """Les symboles de part et d'autre de [s, e) sont sur deux lignes différentes."""
        return s == 0 or e >= len(lines) or lines[s - 1] != lines[e]

    k = 1
    while k < len(segs) - 1:
        mid = segs[k]
        if mid[0] and not segs[k - 1][0] and not segs[k + 1][0]:
            width = wa[mid[2]] - wa[mid[1]]
            if (width <= size(segs[k - 1]) and width <= size(segs[k + 1])
                    and not (crosses(la, mid[1], mid[2]) and crosses(lb, mid[3], mid[4]))):
                before, after = segs[k - 1], segs[k + 1]
                segs[k - 1:k + 2] = [[False, before[1], after[2], before[3], after[4]]]
                k = max(1, k - 2)        # la partie commune d'avant peut maintenant être absorbée
                continue
        k += 1
    return [tuple(s[1:]) for s in segs if not s[0]], False


def _widths(keys: list[str]) -> list[int]:
    """Longueurs cumulées des symboles (en caractères)."""
    out = [0]
    for key in keys:
        out.append(out[-1] + len(key))
    return out


def _sha(*parts: str) -> str:
    return hashlib.sha1("\0".join(parts).encode("utf-8", "surrogatepass")).hexdigest()[:16]


def diff(left: str, right: str, lang: str, options: dict | None = None) -> dict:
    """Différences symbole à symbole → {regions, hunks, same, total}.
    regions : plages de lignes changées [{a0, a1, b0, b1, hunks}] (indices des différences) ;
    hunks : les différences, dans l'ordre (voir _hunk) ; same : symboles identiques ;
    total : symboles des deux côtés."""
    options = options or {}
    lex = lexer(lang)
    A, B = _Side(left, lex.tokenize(left), options), _Side(right, lex.tokenize(right), options)
    regions, hunks, changed = [], [], 0
    for a0, a1, b0, b1 in line_regions(A.line_keys, B.line_keys):
        i_lo, i_hi = A.tokens_in(a0, a1)
        j_lo, j_hi = B.tokens_in(b0, b1)
        region = {"a0": a0, "a1": a1, "b0": b0, "b1": b1, "hunks": []}
        edits, rewritten = _edits(A.keys[i_lo:i_hi], B.keys[j_lo:j_hi], A.line[i_lo:i_hi], B.line[j_lo:j_hi])
        for edit in edits:
            edit = (i_lo + edit[0], i_lo + edit[1], j_lo + edit[2], j_lo + edit[3])
            for i1, i2, j1, j2 in _by_lines(A, B, *edit, rewritten):
                region["hunks"].append(len(hunks))
                hunks.append(_hunk(A, B, i1, i2, j1, j2, region))
                changed += i2 - i1
        _both(A, B, [hunks[k] for k in region["hunks"]])
        regions.append(region)
    return {"regions": regions, "hunks": hunks, "same": len(A.sig) - changed, "total": len(A.sig) + len(B.sig)}


def _by_lines(A: _Side, B: _Side, i1: int, i2: int, j1: int, j2: int, rewritten: bool = False) -> list[tuple]:
    """Un passage remplacé qui court sur plusieurs lignes est coupé ligne à ligne là où ses lignes
    se correspondent : la fin d'une ligne changée et la ligne ajoutée juste après restent deux
    différences, une ligne semblable de chaque côté en fait une chacune (en Python, l'indentation
    changée d'une ligne reste à part du changement de la ligne d'avant). Des lignes réécrites, qui
    ne se ressemblent pas, restent une seule différence (rewritten : zone réécrite, seules des lignes
    presque identiques sont mises à part)."""
    if i1 == i2 or j1 == j2:
        return [(i1, i2, j1, j2)]
    ga, gb = _groups(A, i1, i2), _groups(B, j1, j2)
    if len(ga) == 1 and len(gb) == 1:
        return [(i1, i2, j1, j2)]
    out, lo_a, hi_a, lo_b, hi_b = [], 0, len(ga), 0, len(gb)
    cutoff = SPLIT_REWRITTEN if rewritten else SPLIT_CUTOFF
    head = not rewritten and A.shares_line(ga[0][0] - 1, ga[0][0]) and B.shares_line(gb[0][0] - 1, gb[0][0])
    if head:                    # fin de la ligne qui précède, de chaque côté : ensemble
        lo_a = lo_b = 1
    tail = (not rewritten and hi_a > lo_a and hi_b > lo_b and A.shares_line(ga[-1][1] - 1, ga[-1][1])
            and B.shares_line(gb[-1][1] - 1, gb[-1][1]))
    if tail:                    # début de la ligne qui suit, de chaque côté : ensemble
        hi_a, hi_b = hi_a - 1, hi_b - 1
    if head:
        out.append((*ga[0], *gb[0]))
    pa = [ga[k][0] for k in range(lo_a, hi_a)] + [ga[hi_a][0] if hi_a < len(ga) else i2]
    pb = [gb[k][0] for k in range(lo_b, hi_b)] + [gb[hi_b][0] if hi_b < len(gb) else j2]
    text_a = ["".join(A.keys[s:e]) for s, e in ga[lo_a:hi_a]]
    text_b = ["".join(B.keys[s:e]) for s, e in gb[lo_b:hi_b]]
    matcher = difflib.SequenceMatcher(None, autojunk=False)

    def walk(alo, ahi, blo, bhi):
        best, bi, bj = cutoff, -1, -1
        if alo < ahi and blo < bhi and (ahi - alo) * (bhi - blo) <= ALIGN_GROUPS:
            for j in range(blo, bhi):
                matcher.set_seq2(text_b[j])
                for i in range(alo, ahi):
                    matcher.set_seq1(text_a[i])
                    if matcher.real_quick_ratio() > best and matcher.quick_ratio() > best and matcher.ratio() > best:
                        best, bi, bj = matcher.ratio(), i, j
        if bi < 0:
            if alo < ahi or blo < bhi:
                out.append((pa[alo], pa[ahi], pb[blo], pb[bhi]))
            return
        walk(alo, bi, blo, bj)
        out.append((pa[bi], pa[bi + 1], pb[bj], pb[bj + 1]))
        walk(bi + 1, ahi, bj + 1, bhi)

    walk(0, hi_a - lo_a, 0, hi_b - lo_b)
    if tail:
        out.append((*ga[-1], *gb[-1]))
    return [(a0, a1, b0, b1) for a0, a1, b0, b1 in out if A.keys[a0:a1] != B.keys[b0:b1]]


def _groups(side: "_Side", i1: int, i2: int) -> list[tuple[int, int]]:
    """Symboles [i1, i2) regroupés par ligne : [(début, fin)]."""
    out = []
    for i in range(i1, i2):
        if out and side.line[i] == side.line[i - 1]:
            out[-1] = (out[-1][0], i + 1)
        else:
            out.append((i, i + 1))
    return out


def _hunk(A: _Side, B: _Side, i1: int, i2: int, j1: int, j2: int, region: dict) -> dict:
    """Une différence : symboles [i1, i2) à gauche remplacés par [j1, j2) à droite.
    tag : replace | insert | delete ; left, right : les passages (des symboles, sans les espaces
    autour) ; m0, m1 : plage du texte de gauche remplacée dans la fusion ; rv : le texte de
    droite qui la remplace (avec, autour d'un ajout, ses espaces et sa fin de ligne) ; pre,
    post : espaces gardés autour d'un texte écrit à la main ; a0…b1 : lignes concernées
    (affichage) ; lmarks, rmarks : parties marquées [[ligne, début, fin]] ; sig : signature
    (le choix suit la différence), twin : la même, gauche et droite échangées."""
    L, R = A.text, B.text
    lc = (A.sig[i1][1], A.sig[i2 - 1][2]) if i1 < i2 else None
    rc = (B.sig[j1][1], B.sig[j2 - 1][2]) if j1 < j2 else None
    pre = post = ""
    at = {}                     # côté vide : là où le passage est (ou serait)
    if lc and rc:
        tag = "replace"
        m0, m1 = lc
        rv = R[rc[0]:rc[1]]
        # Python : une ligne logique de plus ou de moins (« a; b » ↔ deux lignes) — l'indentation
        # qui finit un côté prend avec elle les espaces qui suivent l'autre
        ends_l, ends_r = A.sig[i2 - 1][0] == "indent", B.sig[j2 - 1][0] == "indent"
        if ends_r and not ends_l:
            m1 = A.next_start(i2 - 1) or m1
        elif ends_l and not ends_r:
            rn = B.next_start(j2 - 1)
            rv = R[rc[0]:rn if rn is not None else rc[1]]
    elif rc:
        tag = "insert"
        lp, rp = A.prev_end(i1), B.prev_end(j1)
        if lp is not None and (rp is not None or i1 == len(A.sig)):
            pre = R[rp:rc[0]] if rp is not None else "\n"
            m0 = m1 = lp
            rv = pre + R[rc[0]:rc[1]]
        else:
            m0 = m1 = A.sig[i1][1] if i1 < len(A.sig) else len(L)
            rn = B.next_start(j2 - 1)
            post = R[rc[1]:rn] if rn is not None else "\n"
            rv = R[rc[0]:rc[1]] + post
        at["a"] = m0
    else:
        tag = "delete"
        lp, rp = A.prev_end(i1), B.prev_end(j1)
        if lp is not None:
            m0, m1 = lp, lc[1]
            pre = L[lp:lc[0]]
            at["b"] = rp if rp is not None else 0
        else:
            ln = A.next_start(i2 - 1)
            m0, m1 = lc[0], len(L) if ln is None else ln
            post = L[lc[1]:m1]
            at["b"] = B.sig[j1][1] if j1 < len(B.sig) else len(R)
        rv = ""
    hk = {"unit": "symbol", "tag": tag, "left": L[lc[0]:lc[1]] if lc else "", "right": R[rc[0]:rc[1]] if rc else "",
          "m0": m0, "m1": m1, "rv": rv, "pre": pre, "post": post,
          "kinds": sorted({t[0] for t in A.sig[i1:i2] + B.sig[j1:j2]})}
    # Lignes et marques : le passage de chaque côté ; du côté vide, l'endroit de l'ajout — entre
    # deux lignes si l'ajout fait des lignes entières, sinon un repère dans la ligne
    moved = rv if tag == "insert" else L[m0:m1]
    lead, trail = moved[:len(moved) - len(moved.lstrip())], moved[len(moved.rstrip()):]
    for side, core, key in ((A, lc, "a"), (B, rc, "b")):
        lo, hi = region[f"{key}0"], region[f"{key}1"]
        if core:
            marks = [m for m in side.marks(*core) if lo <= m[0] < hi]
            lines = [m[0] for m in marks] or [lo]
            l0, l1 = min(lines), max(lines) + 1
        else:
            line = side.line_of(at[key])
            if "\n" in lead or "\n" in trail:
                l0 = l1 = line + 1 if "\n" in lead else line
                marks = []
            else:
                l0, l1 = line, line + 1
                marks = [[line, side.col(at[key]), side.col(at[key])]]
            l0, l1 = min(max(l0, lo), hi), min(max(l1, lo), hi)
            marks = [m for m in marks if lo <= m[0] < hi]
        hk[f"{key}0"], hk[f"{key}1"] = l0, max(l0, l1)
        hk["lmarks" if key == "a" else "rmarks"] = marks
    lk, rk = "\x1f".join(A.keys[i1:i2]), "\x1f".join(B.keys[j1:j2])
    hk["sig"] = _sha("\x1f".join(A.keys[max(0, i1 - CONTEXT_TOKENS):i1]), lk, rk)
    hk["twin"] = _sha("\x1f".join(B.keys[max(0, j1 - CONTEXT_TOKENS):j1]), rk, lk)
    hk["same_key"] = f"{lk}\0{rk}"
    return hk


def _both(A: _Side, B: _Side, hunks: list[dict]):
    """« Garder les deux » : les lignes entières des deux côtés, l'une après l'autre — pour une
    différence seule sur ses lignes (au milieu d'une ligne, deux passages mis bout à bout ne
    donneraient rien de sensé). both : [début, fin, texte de droite, séparateur] (ou absent)."""
    for hk in hunks:
        if hk["tag"] != "replace" or "indent" in hk["kinds"] or hk["a1"] <= hk["a0"] or hk["b1"] <= hk["b0"]:
            continue
        if any(o is not hk and (o["a0"] < hk["a1"] and hk["a0"] < o["a1"] or o["b0"] < hk["b1"] and hk["b0"] < o["b1"])
               for o in hunks):
            continue
        s0, s1 = A.content(hk["a0"], hk["a1"])
        r0, r1 = B.content(hk["b0"], hk["b1"])
        if s0 > hk["m0"] or s1 < hk["m1"]:
            continue                     # (passage qui déborde de ses lignes : indentation de Python)
        hk["both"] = [s0, s1, B.text[r0:r1], "\n" + A.text[A.starts[hk["a0"]]:s0]]


# ─────────────────────────────────────────────
#  Fusion
# ─────────────────────────────────────────────
def span(hunk: dict, choice: dict | None) -> tuple[int, int]:
    """Plage du texte de gauche que le choix remplace (les deux : les lignes entières)."""
    if (choice or {}).get("choice") in ("left_right", "right_left") and hunk.get("both"):
        return hunk["both"][0], hunk["both"][1]
    return hunk["m0"], hunk["m1"]


def value(left: str, hunk: dict, choice: dict | None) -> str | None:
    """Ce que le choix d'une différence met à sa place dans le résultat (None : pas de choix)."""
    kind = (choice or {}).get("choice")
    if kind == "left":
        return left[hunk["m0"]:hunk["m1"]]
    if kind == "right":
        return hunk["rv"]
    if kind in ("left_right", "right_left"):
        if hunk.get("both"):
            s0, s1, right, sep = hunk["both"]
            first, second = (left[s0:s1], right) if kind == "left_right" else (right, left[s0:s1])
            return first + sep + second
        lv, rv = left[hunk["m0"]:hunk["m1"]], hunk["rv"]
        return lv + rv if kind == "left_right" else rv + lv
    if kind == "text":
        text = (choice.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
        return hunk["pre"] + text + hunk["post"] if text else ""
    return None


def merge(left: str, hunks: list[dict], choices: list[dict | None], default: str = "left") -> tuple[str, int]:
    """Texte fusionné : le texte de gauche, chaque différence remplacée selon son choix ; une
    différence sans choix prend le côté default. → (texte, nombre de différences sans choix)."""
    out, pos, unresolved = [], 0, 0
    for hunk, choice in zip(hunks, choices):
        if value(left, hunk, choice) is None:
            unresolved += 1
            choice = {"choice": default}
        s0, s1 = span(hunk, choice)
        out += [left[pos:s0], value(left, hunk, choice)]
        pos = s1
    out.append(left[pos:])
    return "".join(out), unresolved
