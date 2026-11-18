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


def limite_passagers(passagers: int, total: int = 9) -> int:
    """Nombre maximal d'emplacements de palettes avec ce nombre de passagers (table des versions de l'A400M)."""
    for pax_max, palettes in VERSIONS:
        if passagers <= pax_max:
            return min(total, palettes)
    return 0


def positions_disponibles(passagers: int, params: Parametres | None = None) -> int:
    """Nombre d'emplacements de palettes utilisables : ni déclarés indisponibles, ni pris par les sièges."""
    if params is None:
        return limite_passagers(passagers)
    return len(params.emplacements_libres())


def attribuer_emplacements(palettes: list, params: Parametres) -> None:
    """Affecte à chaque palette les emplacements libres de sa zone, dans l'ordre (soute d'abord à l'avant). Une
    palette en trop (plus de palettes que d'emplacements libres) n'a pas d'emplacement."""
    libres = {zone: list(params.emplacements_libres(zone)) for zone in ("soute", "rampe")}
    for pal in palettes:
        pal.emplacements = []
        for _ in range(pal.type.positions):
            if libres[pal.zone]:
                pal.emplacements.append(libres[pal.zone].pop(0))


def plan_soute(params: Parametres, palettes: list | None = None) -> list[dict]:
    """Les emplacements de l'avion avec leur état : ``libre`` (utilisable), ``indisponible`` (déclaré à la main),
    ``passagers`` (pris par les sièges) et, après un calcul, la palette qui l'occupe."""
    marques, sieges = params.emplacements_marques(), set(params.emplacements_passagers())
    occupe = {n: pal for pal in (palettes or []) for n in pal.emplacements}
    plan = []
    for n in params.numeros_emplacements():
        zone = params.zone_emplacement(n)
        t = params.type_rampe() if zone == "rampe" else params.types_palettes()[0]
        etat = "indisponible" if n in marques else "passagers" if n in sieges else "libre"
        pal = occupe.get(n)
        plan.append({"numero": n, "zone": zone, "etat": etat, "masse_max": t.masse_max, "largeur": t.largeur,
                     "longueur": t.longueur, "hauteur_max": t.hauteur_max,
                     "palette": pal.numero if pal else None, "masse": round(pal.masse, 1) if pal else None})
    return plan


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
        "plan_soute": plan_soute(params, palettes),
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
        "hauteur_globale": round(max((p.hauteur for p in palettes), default=0.0), 1),
    }
