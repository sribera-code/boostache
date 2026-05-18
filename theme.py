"""
theme.py – Palette Boostache (dark moderne, Tokyo Night-ish)
Centralise les couleurs utilisées par l'UI et les widgets CustomTkinter.
"""

# ── Background (du plus sombre au plus clair) ────────────────────────────────
BG        = "#1A1B26"   # fond principal (contenu, chat)
BG2       = "#16161E"   # fond fenêtre / barres / status
BG3       = "#24283B"   # fond input, cards
BG4       = "#2F334D"   # fond hover / sélection
BG_LOG    = "#13141A"   # fond zone log / console output
BG_CHAT   = "#1A1B26"   # fond zone chat (= BG)
BORDER    = "#0F0F13"   # bordures

# ── Foreground ───────────────────────────────────────────────────────────────
FG        = "#C0CAF5"   # texte principal
FG_DIM    = "#7A88AF"   # texte secondaire
FG_LOG    = "#A9B1D6"   # texte log
FG_HEAD   = "#C0CAF5"   # texte headers

# ── Accents ──────────────────────────────────────────────────────────────────
ACCENT          = "#7AA2F7"   # bleu accent (boutons primaires, sélection)
ACCENT_HOVER    = "#5E81E8"   # bleu accent hover
ACCENT_DIM      = "#3D5A99"   # bleu accent désaturé (états désactivés)
GREEN           = "#9ECE6A"   # success / utilisateur chat
GREEN_HOVER     = "#7FAF4C"
RED             = "#F7768E"   # erreur / stop
PURPLE          = "#BB9AF7"
YELLOW          = "#E0AF68"
CYAN            = "#7DCFFF"

# ── Couleurs sémantiques chat ────────────────────────────────────────────────
USER_COLOR  = "#9ECE6A"
BOT_COLOR   = "#7AA2F7"
CODE_COLOR  = "#7DCFFF"
