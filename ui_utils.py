"""
ui_utils.py – Helpers UI partagés
Détection des dépendances optionnelles, classe racine CTk+DnD,
et utilitaires de parsing.
"""

import re

import customtkinter as ctk

# ── Dépendances optionnelles ──────────────────────────────────────────────────
try:
    import ollama as _ollama
    OLLAMA_OK = True
except ImportError:
    _ollama = None
    OLLAMA_OK = False

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_OK = True

    class CTkRoot(ctk.CTk, TkinterDnD.DnDWrapper):
        """Fenêtre racine CustomTkinter avec support drag & drop."""
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    DND_OK = False
    CTkRoot = ctk.CTk


# ── Apparence globale CustomTkinter ──────────────────────────────────────────
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


# ── Helpers ───────────────────────────────────────────────────────────────────
def strip_markdown(text: str) -> str:
    """Supprime les balises markdown avant la lecture TTS."""
    text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    text = re.sub(r'`[^`]+`', '', text)
    text = re.sub(r'\*\*\*([^*]+)\*\*\*', r'\1', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'─+', '', text)
    return text.strip()


def parse_drop_data(data: str) -> list[str]:
    """Parse la chaîne de données issue d'un événement Drag & Drop tkinterdnd2."""
    paths = []
    braced = re.findall(r'\{([^}]+)\}', data)
    paths.extend(braced)
    remainder = re.sub(r'\{[^}]+\}', '', data).strip()
    if remainder:
        for part in re.split(r'(?<![:\\])\s+', remainder):
            part = part.strip()
            if part:
                paths.append(part)
    return [p for p in paths if p]
