"""Fonctions géométriques : orientations, recouvrements, support, contacts."""

from __future__ import annotations

from .modeles import Colis, Parametres, Placement

EPS = 1e-3          # cm : recouvrement inférieur à 0,01 mm = pas de chevauchement (bruit numérique)
TOL_SUPPORT = 0.01  # cm : deux faces horizontales à moins de 0,1 mm sont considérées jointives
TOL_VISIBLE = 1e-5  # cm : tolérance des projections et des dépassements pour la visibilité IATA (1e-7 m, comme CDP_2)


def orientations(colis: Colis) -> list[tuple[float, float, float]]:
    """Dimensions (dx, dy, dz) possibles d'un colis : la hauteur saisie reste verticale (le colis n'est jamais
    couché ni retourné), seule une rotation d'un quart de tour autour de la verticale est permise.
    Les doublons (base carrée) sont supprimés en gardant l'ordre.
    """
    l, w, h = colis.longueur, colis.largeur, colis.hauteur
    return list(dict.fromkeys([(l, w, h), (w, l, h)]))


def recouvrement_1d(a1: float, a2: float, b1: float, b2: float) -> float:
    return max(0.0, min(a2, b2) - max(a1, b1))


def aire_recouvrement(p: Placement, q: Placement) -> float:
    """Aire de l'intersection des projections au sol de deux placements.

    Un recouvrement d'au plus ``EPS`` selon x ou selon y n'est pas une surface de contact (bruit numérique
    du tassement) : l'aire est alors nulle, comme dans les contrôles de l'optimiseur.
    """
    ox = recouvrement_1d(p.x, p.x2, q.x, q.x2)
    oy = recouvrement_1d(p.y, p.y2, q.y, q.y2)
    return ox * oy if ox > EPS and oy > EPS else 0.0


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


def _bornes(p: Placement, axe: str) -> tuple[float, float, float, float]:
    """(début, fin) du colis sur l'axe des côtés puis sur l'autre axe horizontal."""
    if axe == "x":
        return p.x, p.x2, p.y, p.y2
    return p.y, p.y2, p.x, p.x2


def cotes_visibles(p: Placement, autres: list[Placement], axe: str = "y",
                   precision: float = TOL_VISIBLE) -> tuple[str, ...]:
    """Côtés depuis lesquels le colis ``p`` reste visible : ``"gauche"`` (coordonnée minimale sur ``axe``) et/ou
    ``"droite"`` (coordonnée maximale).

    Règle du notebook CDP_4 (``cotes_accessibles_iata``, sur l'axe y par défaut) : un autre colis ne masque ``p``
    que si leurs projections se chevauchent à la fois selon l'autre axe horizontal et selon z ; il masque alors le
    côté vers lequel il dépasse de ``p`` sur l'axe des côtés. Un colis côte à côte sur l'autre axe, ou à un autre
    niveau en z, ne masque rien.
    """
    gauche = droite = True
    s1, s2, t1, t2 = _bornes(p, axe)
    for q in autres:
        if q is p:
            continue
        q1, q2, u1, u2 = _bornes(q, axe)
        if min(t2, u2) - max(t1, u1) > precision and min(p.z2, q.z2) - max(p.z, q.z) > precision:
            if q1 < s1 - precision:
                gauche = False
            if q2 > s2 + precision:
                droite = False
    return tuple(cote for cote, libre in (("gauche", gauche), ("droite", droite)) if libre)


def cotes_bord(p: Placement, etendue: float, jeu: float, axe: str = "y") -> tuple[str, ...]:
    """Côtés contre lesquels le colis est posé (à ``jeu`` cm près) : « gauche » en 0 sur ``axe``, « droite » à
    ``etendue`` (étendue utile sur cet axe). Vide si le colis n'est contre aucun bord."""
    s1, s2, _, _ = _bornes(p, axe)
    cotes = []
    if s1 <= jeu:
        cotes.append("gauche")
    if s2 >= etendue - jeu:
        cotes.append("droite")
    return tuple(cotes)


def iata_en_regle(p: Placement, autres: list[Placement], etendue: float, params: Parametres,
                  axe: str = "y") -> bool:
    """Un colis dangereux est visible d'au moins un côté ; avec ``iata_au_bord`` il est de plus posé contre un bord et
    visible depuis ce bord-là."""
    visibles = cotes_visibles(p, autres, axe)
    if not visibles:
        return False
    if params.iata_au_bord:
        return any(c in visibles for c in cotes_bord(p, etendue, params.jeu_contact, axe))
    return True


def plafond_charge(colis: Colis, params: Parametres) -> float:
    """Masse maximale (kg) d'un colis posé directement sur ``colis``.

    Trois règles se combinent : un colis ne porte pas plus lourd que lui (``facteur_masse``), rien sur un colis
    fragile, et un colis dans une matière à ne pas écraser (carton, fibre…) ne porte que ``charge_max_carton`` kg
    (0 : rien).
    """
    if colis.fragile:
        return 0.0
    plafond = params.facteur_masse * colis.masse
    if params.emballage_ecrasable(colis.emballage):
        plafond = min(plafond, params.charge_max_carton)
    return plafond


def tient_dans(colis: Colis, largeur: float, longueur: float, hauteur: float) -> bool:
    """Vrai si le colis tient dans le volume (dans au moins une orientation autorisée)."""
    for dx, dy, dz in orientations(colis):
        if dx <= largeur + EPS and dy <= longueur + EPS and dz <= hauteur + EPS:
            return True
    return False
