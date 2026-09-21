"""Configuration de l'A400M : emplacements de palettes et passagers.

Table « Version A400M » (versions cargo & pax) affichée à l'ETAA :
54 passagers et 9 palettes, 64 et 7, 74 et 6, 84 et 5, 94 et 4, 104 et 3,
116 et 2. Au-delà de 54 passagers (sièges latéraux), des bancs centraux
remplacent des emplacements de palettes.
"""

from __future__ import annotations

from .modeles import Parametres, Resultat

VERSIONS = [
    (54, 9), (64, 7), (74, 6), (84, 5), (94, 4), (104, 3), (116, 2),
]


def positions_disponibles(passagers: int, params: Parametres | None = None) -> int:
    """Nombre d'emplacements de palettes disponibles pour ce nombre de passagers."""
    total = (params.positions_soute + params.positions_rampe) if params else 9
    for pax_max, palettes in VERSIONS:
        if passagers <= pax_max:
            return min(total, palettes)
    return 0


def bilan(resultat: Resultat) -> dict:
    params = resultat.parametres
    palettes = resultat.palettes
    tare = params.tare_palette
    masse_fret = sum(p.masse for p in palettes)
    masse_palettes = sum(p.type.positions * tare for p in palettes)
    masse_pax = params.passagers * (params.masse_passager + params.masse_bagages)
    return {
        "passagers": params.passagers,
        "positions_disponibles": positions_disponibles(params.passagers, params),
        "positions_utilisees": resultat.positions_utilisees,
        "nombre_palettes": len(palettes),
        "palettes_soute": sum(1 for p in palettes if p.zone == "soute"),
        "palettes_rampe": sum(1 for p in palettes if p.zone == "rampe"),
        "masse_fret": round(masse_fret, 1),
        "masse_palettes": round(masse_palettes, 1),
        "masse_passagers": round(masse_pax, 1),
        "masse_totale_brute": round(masse_fret + masse_palettes + masse_pax, 1),
        "capacite_avion": params.capacite_avion,
        "colis_places": sum(len(p.placements) for p in palettes),
        "colis_non_places": len(resultat.non_places),
        "somme_hauteurs": round(resultat.somme_hauteurs, 1),
    }
