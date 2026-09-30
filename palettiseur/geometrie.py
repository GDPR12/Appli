"""Fonctions géométriques : orientations, recouvrements, support, contacts."""

from __future__ import annotations

from itertools import permutations

from .modeles import Colis, Placement

EPS = 1e-3          # cm : recouvrement inférieur à 0,01 mm = pas de chevauchement (bruit numérique)
TOL_SUPPORT = 0.01  # cm : deux faces horizontales à moins de 0,1 mm sont considérées jointives


def orientations(colis: Colis) -> list[tuple[float, float, float]]:
    """Dimensions (dx, dy, dz) possibles d'un colis.

    - non renversable : la hauteur reste verticale, on peut seulement tourner
      le colis autour de l'axe vertical (2 orientations) ;
    - renversable : les 6 permutations des dimensions sont permises.
    Les doublons (colis à faces égales) sont supprimés en gardant l'ordre.
    """
    l, w, h = colis.longueur, colis.largeur, colis.hauteur
    if colis.renversable:
        choix = list(permutations((l, w, h)))
    else:
        choix = [(l, w, h), (w, l, h)]
    return list(dict.fromkeys(choix))


def recouvrement_1d(a1: float, a2: float, b1: float, b2: float) -> float:
    return max(0.0, min(a2, b2) - max(a1, b1))


def aire_recouvrement(p: Placement, q: Placement) -> float:
    """Aire de l'intersection des projections au sol de deux placements."""
    return (recouvrement_1d(p.x, p.x2, q.x, q.x2)
            * recouvrement_1d(p.y, p.y2, q.y, q.y2))


def se_chevauchent(p: Placement, q: Placement) -> bool:
    """Vrai si les deux pavés ont un volume commun strictement positif."""
    return (recouvrement_1d(p.x, p.x2, q.x, q.x2) > EPS
            and recouvrement_1d(p.y, p.y2, q.y, q.y2) > EPS
            and recouvrement_1d(p.z, p.z2, q.z, q.z2) > EPS)


def supports(p: Placement, autres: list[Placement], jeu: float = TOL_SUPPORT) -> list[tuple[Placement, float]]:
    """Colis sur lesquels ``p`` repose : sommet à la hauteur de sa base, avec aire commune > 0."""
    res = []
    for q in autres:
        if q is p:
            continue
        if abs(q.z2 - p.z) <= jeu:
            aire = aire_recouvrement(p, q)
            if aire > EPS:
                res.append((q, aire))
    return res


def taux_support(p: Placement, autres: list[Placement], jeu: float = 0.5) -> float:
    """Part de la base de ``p`` qui repose sur le sol (z = 0, à ``jeu`` près) ou sur d'autres colis."""
    if p.z <= jeu:
        return 1.0
    aire = sum(a for _, a in supports(p, autres))
    return min(1.0, aire / p.base) if p.base > 0 else 0.0


def en_contact(p: Placement, q: Placement, jeu: float = 0.5) -> bool:
    """Vrai si les deux pavés partagent une face (contact latéral ou vertical) d'aire > 0."""
    ox = recouvrement_1d(p.x, p.x2, q.x, q.x2)
    oy = recouvrement_1d(p.y, p.y2, q.y, q.y2)
    oz = recouvrement_1d(p.z, p.z2, q.z, q.z2)
    # contact selon X : faces x2/x jointes, recouvrement en y et z
    if (abs(p.x2 - q.x) <= jeu or abs(q.x2 - p.x) <= jeu) and oy > EPS and oz > EPS:
        return True
    if (abs(p.y2 - q.y) <= jeu or abs(q.y2 - p.y) <= jeu) and ox > EPS and oz > EPS:
        return True
    if (abs(p.z2 - q.z) <= jeu or abs(q.z2 - p.z) <= jeu) and ox > EPS and oy > EPS:
        return True
    return False


def touche_bord_couloir(p: Placement, largeur_palette: float, jeu: float = 0.5) -> bool:
    """Vrai si une face du colis est contre une des deux faces côté couloir (x = 0 ou x = L)."""
    return p.x <= jeu or p.x2 >= largeur_palette - jeu


def tient_dans(colis: Colis, largeur: float, longueur: float, hauteur: float) -> bool:
    """Vrai si le colis tient dans le volume (dans au moins une orientation autorisée)."""
    for dx, dy, dz in orientations(colis):
        if dx <= largeur + EPS and dy <= longueur + EPS and dz <= hauteur + EPS:
            return True
    return False
