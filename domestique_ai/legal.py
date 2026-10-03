"""Documents légaux — version courante des CGU/confidentialité et contact public.

La version est exposée par ``GET /api/auth/config`` et stockée à l'inscription /
l'acceptation d'invitation (colonnes ``users.*_version`` de ``platform.db``), ce
qui permet de prouver quel texte chaque compte a accepté et à quelle date.
"""

from __future__ import annotations

# À incrémenter à chaque modification substantielle des CGU ou de la politique
# de confidentialité (format AAAA-MM[-suffixe]). Les textes publiés vivent côté
# frontend (``frontend/src/legal/*.md``) : garder les deux en phase.
LEGAL_VERSION = "2026-10-beta"

# Adresse de contact publiée (mentions légales, confidentialité, droits RGPD).
CONTACT_EMAIL = "contact@domestique-ai.com"
