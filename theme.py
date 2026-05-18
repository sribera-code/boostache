"""
theme.py – Palette Boostache (dark monochrome)
Gris purs pour le chrome, accent blanc cassé, GREEN/RED conservés pour
les états sémantiques (success / erreur).
"""

# ── Background (du plus sombre au plus clair, gris neutres) ──────────────────
BG        = "#1E1E1E"   # fond principal (contenu, chat)
BG2       = "#171717"   # fond fenêtre / barres / status
BG3       = "#2A2A2A"   # fond input, cards
BG4       = "#3A3A3A"   # fond hover / sélection
BG_LOG    = "#121212"   # fond zone log / console output
BG_CHAT   = "#1E1E1E"   # fond zone chat (= BG)
BORDER    = "#0A0A0A"   # bordures

# ── Foreground (blanc / gris) ────────────────────────────────────────────────
FG        = "#E5E5E5"   # texte principal
FG_DIM    = "#808080"   # texte secondaire
FG_LOG    = "#A8A8A8"   # texte log
FG_HEAD   = "#D0D0D0"   # texte headers

# ── Accents (boutons primaires : blanc cassé, texte sombre) ──────────────────
ACCENT          = "#E0E0E0"
ACCENT_HOVER    = "#FFFFFF"
ACCENT_DIM      = "#5A5A5A"

# ── Sémantique (conservés en couleur car fonctionnels) ───────────────────────
GREEN           = "#7AAF7A"   # success / actif / utilisateur chat
GREEN_HOVER     = "#5F8F5F"
RED             = "#D47070"   # erreur / stop
PURPLE          = "#A8A8A8"   # neutralisé en gris
YELLOW          = "#C8C8C8"   # neutralisé en gris
CYAN            = "#B0B0B0"   # neutralisé en gris

# ── Couleurs sémantiques chat ────────────────────────────────────────────────
USER_COLOR  = "#9EC89E"   # vert doux pour utilisateur (non-bleu)
BOT_COLOR   = "#D0D0D0"   # gris clair pour bot
CODE_COLOR  = "#C8C8C8"   # gris clair pour code
