"""
docreader.py – Texte des fichiers indexés par la section Documents (rag.py).

Texte et code (encodage deviné), PDF (PyMuPDF, sinon pypdf), Word (.docx),
PowerPoint (.pptx), Excel (.xlsx, openpyxl), OpenDocument (.odt, .ods, .odp),
RTF, HTML, e-mails (.eml), EPUB et notebooks Jupyter. Rien à installer pour la
plupart des formats : les fichiers Office et OpenDocument sont des archives ZIP
de XML, lues directement.

read() rend une liste de segments (texte, page) : la page (diapositive,
chapitre, feuille…) sert à citer la source, None quand le format n'en a pas.
"""

import codecs
import email
import html.parser
import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from email import policy

# Extension → genre (icône et libellé de la page dans l'interface)
TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".properties", ".xml", ".tex", ".bib",
    ".srt", ".vtt", ".org", ".adoc", ".textile", ".nfo",
}
CODE_EXTS = {
    ".py", ".pyw", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss",
    ".less", ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".cs", ".java", ".kt", ".kts", ".go", ".rs",
    ".rb", ".php", ".pl", ".lua", ".r", ".m", ".swift", ".scala", ".dart", ".sql", ".sh", ".bash",
    ".zsh", ".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".vb", ".vbs", ".fs", ".groovy", ".gradle",
    ".cmake", ".mk", ".dockerfile", ".tf", ".hcl", ".proto", ".graphql", ".asm", ".ahk", ".au3",
}
WEB_EXTS = {".html", ".htm", ".xhtml"}
DOC_KINDS = {
    ".pdf": "pdf",
    ".docx": "word", ".docm": "word", ".dotx": "word", ".odt": "word", ".rtf": "word",
    ".xlsx": "sheet", ".xlsm": "sheet", ".ods": "sheet",
    ".pptx": "slides", ".pptm": "slides", ".ppsx": "slides", ".odp": "slides",
    ".eml": "mail", ".epub": "book", ".ipynb": "code",
}
# Anciens formats binaires d'Office : non lus (message affiché)
LEGACY = {".doc": ".docx", ".xls": ".xlsx", ".ppt": ".pptx"}

BINARY_PROBE = 8192           # octets examinés pour reconnaître un fichier binaire
ODF_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
ODF_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
ODF_DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
NBSP = chr(0xA0)
_CONTROL = re.compile("[" + "".join(chr(c) for c in range(32) if c not in (9, 10)) + chr(0x7F) + "]")


class Unreadable(Exception):
    """Fichier pris en charge mais illisible : le message est montré tel quel."""


class NoText(Unreadable):
    """Fichier lisible, mais sans texte (PDF scanné, fichier binaire…)."""


def kind(ext: str) -> str:
    """Genre d'un fichier d'après son extension ("" : non pris en charge)."""
    ext = ext.lower()
    if ext in DOC_KINDS:
        return DOC_KINDS[ext]
    if ext in CODE_EXTS:
        return "code"
    if ext in WEB_EXTS:
        return "web"
    if ext in TEXT_EXTS:
        return "text"
    return ""


def supported(name: str) -> bool:
    return bool(kind(os.path.splitext(name)[1]))


def unsupported_reason(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in LEGACY:
        return f"Ancien format Office : enregistrez-le en {LEGACY[ext]} pour l'indexer"
    return "Format non pris en charge"


def read(path: str, max_chars: int) -> tuple[list[tuple[str, int | None]], bool]:
    """Texte du fichier → (segments [(texte, page)], tronqué). Lève Unreadable
    (message pour l'utilisateur) ou une autre exception si la lecture échoue."""
    ext = os.path.splitext(path)[1].lower()
    genre = kind(ext)
    if genre in ("text", "code") and ext != ".ipynb":
        segments = [(_text_file(path), None)]
    elif genre == "web":
        segments = [(html_to_text(_decode(_read_bytes(path))), None)]
    elif ext == ".pdf":
        segments = _pdf(path)
    elif ext in (".docx", ".docm", ".dotx"):
        segments = _docx(path)
    elif ext in (".pptx", ".pptm", ".ppsx"):
        segments = _pptx(path)
    elif ext in (".xlsx", ".xlsm"):
        segments = _xlsx(path, max_chars)
    elif ext in (".odt", ".ods", ".odp"):
        segments = _odf(path, ext)
    elif ext == ".rtf":
        segments = [(rtf_to_text(_read_bytes(path).decode("latin-1")), None)]
    elif ext == ".eml":
        segments = [(_eml(path), None)]
    elif ext == ".epub":
        segments = _epub(path)
    elif ext == ".ipynb":
        segments = [(_notebook(path), None)]
    else:
        raise Unreadable(unsupported_reason(path))
    out, total, truncated = [], 0, False
    for text, page in segments:
        text = clean(text, keep_indent=genre == "code")
        if not text.strip():
            continue
        if total + len(text) > max_chars:
            text = text[:max(0, max_chars - total)]
            truncated = True
        out.append((text, page))
        total += len(text)
        if truncated:
            break
    return out, truncated


def clean(text: str, keep_indent: bool = False) -> str:
    """Fins de ligne normalisées, caractères de contrôle retirés, espaces superflus réduits."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(NBSP, " ")
    text = _CONTROL.sub("", text)
    if not keep_indent:
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"[ \t]{3,}", "  ", text)
    return re.sub(r"\n{4,}", "\n\n\n", text)


# ─────────────────────────────────────────────
#  Texte brut
# ─────────────────────────────────────────────
def _read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def decode(data: bytes) -> tuple[str, str]:
    """Octets → (texte, encodage) : BOM, sinon UTF-8, sinon Windows-1252 (les anciens
    fichiers texte de Windows en français), sinon l'encodage le plus probable
    (charset_normalizer s'il est installé, peu fiable sur un texte court).
    L'encodage permet de réécrire le texte à l'identique ("utf-8-sig" : avec BOM)."""
    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16", "replace"), "utf-16"
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    try:
        from charset_normalizer import from_bytes
        best = from_bytes(data[:200_000]).best()
        if best is not None and best.encoding:
            return data.decode(best.encoding, "replace"), best.encoding
    except Exception:
        pass
    return data.decode("latin-1"), "latin-1"


def _decode(data: bytes) -> str:
    return decode(data)[0]


def binary(data: bytes) -> bool:
    """Fichier binaire (octets nuls au début, hors UTF-16) : pas de texte à lire."""
    head = data[:BINARY_PROBE]
    return not head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)) and b"\x00" in head


def _text_file(path: str) -> str:
    data = _read_bytes(path)
    if binary(data):
        raise NoText("Fichier binaire : pas de texte à lire")
    return _decode(data)


class _HTMLText(html.parser.HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article",
             "table", "ul", "ol", "pre", "blockquote", "header", "footer", "title", "dt", "dd", "hr",
             "main", "aside", "nav", "figure", "figcaption", "form", "fieldset", "details", "summary"}
    CELL = {"td", "th"}
    SKIP = {"script", "style", "noscript", "template", "svg", "math", "iframe", "object"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in self.CELL:
            self.parts.append("\t")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        pass
    lines = (" ".join(line.split()) for line in "".join(parser.parts).split("\n"))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ─────────────────────────────────────────────
#  PDF
# ─────────────────────────────────────────────
def _pdf(path: str) -> list[tuple[str, int]]:
    try:
        import fitz     # PyMuPDF : rapide et fidèle
    except ImportError:
        fitz = None
    if fitz is not None:
        try:
            fitz.TOOLS.mupdf_display_errors(False)
        except Exception:
            pass
        try:
            doc = fitz.open(path)
        except Exception:
            raise Unreadable("PDF illisible : fichier endommagé ou d'un autre format")
        try:
            if doc.needs_pass:
                raise Unreadable("PDF protégé par un mot de passe")
            pages = [(page.get_text("text") or "", i) for i, page in enumerate(doc, 1)]
        finally:
            doc.close()
    else:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise Unreadable("Lecture des PDF indisponible (pip install pypdf)")
        try:
            reader = PdfReader(path)
            if reader.is_encrypted and not reader.decrypt(""):
                raise Unreadable("PDF protégé par un mot de passe")
            pages = [(page.extract_text() or "", i) for i, page in enumerate(reader.pages, 1)]
        except Unreadable:
            raise
        except Exception:
            raise Unreadable("PDF illisible : fichier endommagé ou d'un autre format")
    if pages and not any(text.strip() for text, _ in pages):
        raise NoText("PDF sans texte (document scanné ?) : rien à lire")
    return pages


# ─────────────────────────────────────────────
#  Office (OOXML) et OpenDocument : XML dans une archive ZIP
# ─────────────────────────────────────────────
def _zip_member(z: zipfile.ZipFile, name: str) -> bytes:
    try:
        return z.read(name)
    except KeyError:
        raise Unreadable("Document incomplet ou d'un autre format")


def _open_zip(path: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise Unreadable("Document illisible (protégé par un mot de passe ou endommagé ?)")


def _docx(path: str) -> list[tuple[str, None]]:
    with _open_zip(path) as z:
        data = _zip_member(z, "word/document.xml")
    w = "{" + W_NS + "}"
    paras, buf = [], []
    cell_start: list[int] = []        # tableaux (imbriqués) : premier paragraphe de la cellule en cours
    rows: list[list[str]] = []        # cellules de la ligne en cours
    for event, node in ET.iterparse(io.BytesIO(data), events=("start", "end")):
        tag = node.tag
        if event == "start":
            if tag == w + "tr":
                rows.append([])
            elif tag == w + "tc":
                cell_start.append(len(paras))
            continue
        if tag == w + "t":
            buf.append(node.text or "")
        elif tag == w + "tab":
            buf.append("\t")
        elif tag in (w + "br", w + "cr"):
            buf.append("\n")
        elif tag == w + "p":
            paras.append("".join(buf))
            buf = []
            node.clear()
        elif tag == w + "tc":
            # Une ligne de tableau devient une ligne de texte : « cellule | cellule »
            start = cell_start.pop() if cell_start else len(paras)
            text = " ".join(p.strip() for p in paras[start:] if p.strip())
            del paras[start:]
            if rows:
                rows[-1].append(text)
        elif tag == w + "tr":
            cells = rows.pop() if rows else []
            if any(cells):
                paras.append(" | ".join(cells))
    return [("\n".join(paras), None)]


def _rels(z: zipfile.ZipFile, part: str) -> dict[str, tuple[str, str]]:
    """Relations d'une partie OOXML : id → (type, chemin dans l'archive)."""
    folder, name = part.rsplit("/", 1)
    try:
        root = ET.fromstring(z.read(f"{folder}/_rels/{name}.rels"))
    except KeyError:
        return {}
    out = {}
    for rel in root:
        target = rel.get("Target") or ""
        path = os.path.normpath(os.path.join(folder, target)).replace(os.sep, "/")
        out[rel.get("Id")] = (rel.get("Type") or "", path)
    return out


def _pptx(path: str) -> list[tuple[str, int]]:
    a = "{" + A_NS + "}"
    r_id = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"

    def lines_of(xml: bytes) -> list[str]:
        root = ET.fromstring(xml)
        return ["".join(t.text or "" for t in p.iter(a + "t")) for p in root.iter(a + "p")]

    out = []
    with _open_zip(path) as z:
        names = set(z.namelist())
        # Ordre de la présentation (sldIdLst), sinon celui des numéros de fichier
        rels = _rels(z, "ppt/presentation.xml")
        try:
            pres = ET.fromstring(z.read("ppt/presentation.xml"))
            slides = [rels[el.get(r_id)][1] for el in pres.iter() if el.tag.endswith("}sldId")
                      and el.get(r_id) in rels]
        except (KeyError, ET.ParseError):
            slides = []
        if not slides:
            slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                            key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)))
        for number, name in enumerate((s for s in slides if s in names), 1):
            lines = lines_of(z.read(name))
            notes = next((p for kind_, p in _rels(z, name).values() if kind_.endswith("/notesSlide")), None)
            if notes in names:
                # Sans le numéro de diapositive que montrent les pages de notes
                note_lines = [ln for ln in lines_of(z.read(notes)) if ln.strip() and not ln.strip().isdigit()]
                if note_lines:
                    lines += ["", "Notes :"] + note_lines
            out.append(("\n".join(lines), number))
    return out


def _xlsx(path: str, max_chars: int) -> list[tuple[str, int]]:
    try:
        import openpyxl
    except ImportError:
        raise Unreadable("Lecture des classeurs Excel indisponible (pip install openpyxl)")
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        raise Unreadable(f"Classeur illisible ({e})")
    out, total = [], 0
    try:
        for i, ws in enumerate(wb.worksheets, 1):
            lines = [f"Feuille « {ws.title} »"]
            for row in ws.iter_rows(values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                while cells and not cells[-1]:
                    cells.pop()
                if any(c.strip() for c in cells):
                    line = "\t".join(cells)
                    lines.append(line)
                    total += len(line) + 1
                if total > max_chars:
                    break
            out.append(("\n".join(lines), i))
            if total > max_chars:
                break
    finally:
        wb.close()
    return out


def _odf(path: str, ext: str) -> list[tuple[str, int | None]]:
    with _open_zip(path) as z:
        data = _zip_member(z, "content.xml")
    t, tb, d = "{" + ODF_TEXT + "}", "{" + ODF_TABLE + "}", "{" + ODF_DRAW + "}"

    def text_of(node) -> str:
        parts = []

        def walk(n):
            if n.text:
                parts.append(n.text)
            for child in n:
                if child.tag == t + "s":
                    parts.append(" " * int(child.get(t + "c", "1") or 1))
                elif child.tag == t + "tab":
                    parts.append("\t")
                elif child.tag == t + "line-break":
                    parts.append("\n")
                else:
                    walk(child)
                if child.tail:
                    parts.append(child.tail)

        walk(node)
        return "".join(parts)

    root = ET.fromstring(data)
    if ext == ".ods":
        out = []
        for i, table in enumerate(root.iter(tb + "table"), 1):
            lines = [f"Feuille « {table.get(tb + 'name', str(i))} »"]
            for row in table.iter(tb + "table-row"):
                cells = [" ".join(text_of(p) for p in cell.iter(t + "p")) for cell in row
                         if cell.tag in (tb + "table-cell", tb + "covered-table-cell")]
                while cells and not cells[-1].strip():
                    cells.pop()
                if cells:
                    lines.append("\t".join(cells))
            out.append(("\n".join(lines), i))
        return out
    if ext == ".odp":
        return [("\n".join(text_of(p) for p in page.iter() if p.tag in (t + "p", t + "h")), i)
                for i, page in enumerate(root.iter(d + "page"), 1)]
    return [("\n".join(text_of(p) for p in root.iter() if p.tag in (t + "p", t + "h")), None)]


# ─────────────────────────────────────────────
#  RTF : mots de contrôle retirés, groupes de mise en forme ignorés
# ─────────────────────────────────────────────
_RTF_TOKEN = re.compile(r"\\([a-z]{1,32})(-?\d{1,10})?[ ]?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|[\r\n]+|(.)",
                        re.I | re.S)
_RTF_SKIP = {
    "aftncn", "aftnsep", "aftnsepc", "annotation", "atnauthor", "atndate", "atnicn", "atnid", "atnparent",
    "atnref", "atntime", "atrfend", "atrfstart", "author", "background", "bkmkend", "bkmkstart", "blipuid",
    "buptim", "category", "colorschememapping", "colortbl", "comment", "company", "creatim", "datafield",
    "datastore", "defchp", "defpap", "do", "doccomm", "docvar", "dptxbxtext", "ebcend", "ebcstart",
    "factoidname", "falt", "fchars", "ffdeftext", "ffentrymcr", "ffexitmcr", "ffformat", "ffhelptext",
    "ffl", "ffname", "ffstattext", "file", "filetbl", "fldinst", "fldtype", "fname",
    "fontemb", "fontfile", "fonttbl", "footer", "footerf", "footerl", "footerr", "footnote", "formfield",
    "ftncn", "ftnsep", "ftnsepc", "g", "generator", "gridtbl", "header", "headerf", "headerl", "headerr",
    "hl", "hlfr", "hlinkbase", "hlloc", "hlsrc", "hsv", "htmltag", "info", "keycode", "keywords",
    "latentstyles", "lchars", "levelnumbers", "leveltext", "lfolevel", "linkval", "list", "listlevel",
    "listname", "listoverride", "listoverridetable", "listpicture", "liststylename", "listtable",
    "listtext", "lsdlockedexcept", "macc", "maccPr", "mailmerge", "maln", "malnScr", "manager", "margPr",
    "mbar", "mbarPr", "mbaseJc", "mbegChr", "mborderBox", "mborderBoxPr", "mbox", "mboxPr", "mchr",
    "mcount", "mctrlPr", "md", "mdeg", "mdegHide", "mden", "mdiff", "mdPr", "me", "mendChr", "meqArr",
    "meqArrPr", "mf", "mfName", "mfPr", "mfunc", "mfuncPr", "mgroupChr", "mgroupChrPr", "mgrow",
    "mhideBot", "mhideLeft", "mhideRight", "mhideTop", "mhtmltag", "mlim", "mlimloc", "mlimlow",
    "mlimlowPr", "mlimupp", "mlimuppPr", "mm", "mmaddfieldname", "mmath", "mmathPict", "mmathPr",
    "mmaxdist", "mmc", "mmcJc", "mmconnectstr", "mmconnectstrdata", "mmcPr", "mmcs", "mmdatasource",
    "mmheadersource", "mmmailsubject", "mmodso", "mmodsofilter", "mmodsofldmpdata", "mmodsomappedname",
    "mmodsoname", "mmodsorecipdata", "mmodsosort", "mmodsosrc", "mmodsotable", "mmodsoudl",
    "mmodsoudldata", "mmodsouniquetag", "mmPr", "mmquery", "mmr", "mnary", "mnaryPr", "mnoBreak",
    "mnum", "mobjDist", "moMath", "moMathPara", "moMathParaPr", "mopEmu", "mphant", "mphantPr",
    "mplcHide", "mpos", "mr", "mrad", "mradPr", "mrPr", "msepChr", "mshow", "mshp", "msPre",
    "msPrePr", "msSub", "msSubPr", "msSubSup", "msSubSupPr", "msSup", "msSupPr", "mstrikeBLTR",
    "mstrikeH", "mstrikeTLBR", "mstrikeV", "msub", "msubHide", "msup", "msupHide", "mtransp", "mtype",
    "mvertJc", "mvfmf", "mvfml", "mvtof", "mvtol", "mzeroAsc", "mzeroDesc", "mzeroWid", "nesttableprops",
    "nextfile", "nonesttables", "objalias", "objclass", "objdata", "object", "objname", "objsect",
    "objtime", "oldcprops", "oldpprops", "oldsprops", "oldtprops", "oleclsid", "operator", "panose",
    "password", "passwordhash", "pgp", "pgptbl", "picprop", "pict", "pn", "pnseclvl", "pntext",
    "pntxta", "pntxtb", "printim", "private", "propname", "protend", "protstart", "protusertbl", "pxe",
    "result", "revtbl", "revtim", "rsidtbl", "rxe", "shp", "shpgrp", "shpinst", "shppict", "shprslt",
    "shptxt", "sn", "sp", "staticval", "stylesheet", "subject", "sv", "svb", "tc", "template",
    "themedata", "title", "txe", "ud", "upr", "userprops", "wgrffmtfilter", "windowcaption", "writereservation",
    "writereservhash", "xe", "xform", "xmlattrname", "xmlattrvalue", "xmlclose", "xmlname", "xmlnstbl",
    "xmlopen",
}
_RTF_SPECIAL = {"par": "\n", "sect": "\n\n", "page": "\n\n", "line": "\n", "tab": "\t", "emdash": "—",
                "endash": "–", "emspace": " ", "enspace": " ", "qmspace": " ", "bullet": "•",
                "lquote": "‘", "rquote": "’", "ldblquote": "“", "rdblquote": "”", "row": "\n", "cell": "\t"}


def rtf_to_text(rtf: str) -> str:
    stack: list[tuple[int, bool]] = []
    ignorable = False
    ucskip, curskip = 1, 0
    out: list[str] = []
    for m in _RTF_TOKEN.finditer(rtf):
        word, arg, hexcode, char, brace, tchar = m.groups()
        if brace:
            curskip = 0
            if brace == "{":
                stack.append((ucskip, ignorable))
            elif stack:
                ucskip, ignorable = stack.pop()
        elif char:
            curskip = 0
            if char == "~":
                if not ignorable:
                    out.append(" ")
            elif char in "{}\\":
                if not ignorable:
                    out.append(char)
            elif char == "*":
                ignorable = True
        elif word:
            curskip = 0
            if word in _RTF_SKIP:
                ignorable = True
            elif ignorable:
                pass
            elif word in _RTF_SPECIAL:
                out.append(_RTF_SPECIAL[word])
            elif word == "uc":
                ucskip = int(arg or 1)
            elif word == "u" and arg:
                code = int(arg)
                if code < 0:
                    code += 0x10000
                out.append(chr(code))
                curskip = ucskip
        elif hexcode:
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                out.append(bytes([int(hexcode, 16)]).decode("cp1252", "replace"))
        elif tchar:
            if curskip > 0:
                curskip -= 1
            elif not ignorable:
                out.append(tchar)
    return "".join(out)


# ─────────────────────────────────────────────
#  E-mails, livres, notebooks
# ─────────────────────────────────────────────
def _eml(path: str) -> str:
    msg = email.message_from_bytes(_read_bytes(path), policy=policy.default)
    head = [f"{label} : {msg[key]}" for key, label in (("subject", "Objet"), ("from", "De"), ("to", "À"),
                                                       ("cc", "Cc"), ("date", "Date")) if msg[key]]
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        try:
            text = body.get_content()
        except Exception:
            text = _decode(body.get_payload(decode=True) or b"")
        if body.get_content_type() == "text/html":
            text = html_to_text(text)
    attached = [part.get_filename() for part in msg.iter_attachments() if part.get_filename()]
    if attached:
        head.append("Pièces jointes : " + ", ".join(attached))
    return "\n".join(head) + "\n\n" + text


def _epub(path: str) -> list[tuple[str, int]]:
    with _open_zip(path) as z:
        names = set(z.namelist())
        order: list[str] = []
        try:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            rootfile = next(el.get("full-path") for el in container.iter() if el.tag.endswith("rootfile"))
            opf = ET.fromstring(z.read(rootfile))
            base = os.path.dirname(rootfile)
            items = {el.get("id"): el.get("href") for el in opf.iter() if el.tag.endswith("item")}
            for el in opf.iter():
                if el.tag.endswith("itemref") and items.get(el.get("idref")):
                    name = os.path.normpath(os.path.join(base, items[el.get("idref")])).replace(os.sep, "/")
                    if name in names:
                        order.append(name)
        except Exception:
            order = []
        if not order:
            order = sorted(n for n in names if n.lower().endswith((".xhtml", ".html", ".htm")))
        return [(html_to_text(_decode(z.read(name))), i) for i, name in enumerate(order, 1)]


def _notebook(path: str) -> str:
    try:
        nb = json.loads(_decode(_read_bytes(path)))
    except ValueError:
        raise Unreadable("Notebook illisible")
    parts = []
    for cell in nb.get("cells") or []:
        source = cell.get("source") or ""
        if isinstance(source, list):
            source = "".join(source)
        if source.strip():
            parts.append(source.strip())
    return "\n\n".join(parts)
