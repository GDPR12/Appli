import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Règles du projet qui s'ajoutent à celles du notebook (désactivées par défaut) : couloir de 14 cm par côté, colis IATA sur
# le bord, aucun colis lourd sur un colis plus léger.
REGLES_PROJET = dict(reduction_couloir=14.0, iata_au_bord=True, facteur_masse=1.0)
