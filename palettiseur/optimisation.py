"""Palettisation en cinq phases, alignée sur le notebook ``CDP_2``.

Pipeline (les numéros de phase sont ceux du notebook)
----------------------------------------------------

1. **Placement heuristique** : construction par points extrêmes, choix de la position par un score
   *lexicographique* (une priorité ne peut pas être compensée par les suivantes) : palette déjà ouverte avant
   une nouvelle, colis IATA proche d'une extrémité en y, base la plus basse, hauteur de la palette, surface
   couverte au sol, bandes libres, proximité des bords, support, compacité, centrage du centre de masse.
   Les colis dangereux sont placés en premier. Plusieurs ordres de placement sont essayés (grande base,
   difficulté, volume, masse du notebook, puis variantes) et la meilleure construction est conservée.
2. **Recherche SCIP** (facultative, ``moteur_scip.py``) : le placement heuristique sert d'amorce (*warm start*)
   puis SCIP tente de réduire le nombre d'emplacements.
3. **Fusion des palettes** : transferts de colis de la palette la moins remplie vers la plus remplie pour en
   vider une.
4. **Tassement vertical** : chaque colis est replacé plus bas (autre position, autre orientation permise) tant
   que le chargement reste valide ; les affectations aux palettes ne changent pas.
5. **Recentrage et tassement horizontal** : le tas est translaté vers le centre, puis chaque colis glisse vers
   le centre selon x et y ; les coordonnées z restent fixes.

Il n'y a plus de minimisation des hauteurs par SCIP : le notebook l'a remplacée par le tassement vertical.

Règles du projet
----------------

Règle IATA de CDP_2, absolue à chaque phase : deux classes incompatibles (tableau 9.3.A) ne partagent jamais une
palette et chaque colis dangereux reste visible depuis au moins un des deux côtés en y (un autre colis ne le
masque que si leurs projections se chevauchent en x et en z). Règles ajoutées par l'application : aucun colis
lourd sur un colis plus léger, rien sur un colis fragile, sens obligatoire, armement, BIPAL/TRIPAL, rampe.
Elles sont vérifiées par ``_charge_valide`` après chaque phase qui déplace des colis.

Repère : cm et kg ; coin minimal (x1, y1, z1) de chaque colis ; x = largeur, y = longueur, z = vertical.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations
from operator import itemgetter
from time import perf_counter
from typing import Iterable

from .geometrie import TOL_VISIBLE, orientations, plafond_charge, tient_dans
from .iata import ReglesSeparation, classe as classe_de, regles as regles_par_defaut
from .modeles import Colis, Etape, Palette, Parametres, Placement, Resultat, TypePalette

TOL = 5e-4      # cm : tolérance numérique des contrôles de validité (vérification : 1e-3)


# ---------------------------------------------------------------------------
# Structures internes (modifiables, à attributs plats pour les boucles rapides)
# ---------------------------------------------------------------------------

class _Boite:
    __slots__ = ("colis", "x1", "y1", "z1", "x2", "y2", "z2", "dx", "dy", "dz", "orientation", "iata")

    def __init__(self, colis: Colis, x1: float, y1: float, z1: float,
                 dx: float, dy: float, dz: float, orientation: int = 0) -> None:
        self.colis = colis
        self.dx, self.dy, self.dz = dx, dy, dz
        self.orientation = orientation
        self.iata = classe_de(colis.classe).iata
        self.placer(x1, y1, z1)

    def placer(self, x: float, y: float, z: float | None = None) -> None:
        self.x1, self.y1, self.x2, self.y2 = x, y, x + self.dx, y + self.dy
        if z is not None:
            self.z1, self.z2 = z, z + self.dz

    def copie(self) -> "_Boite":
        return _Boite(self.colis, self.x1, self.y1, self.z1, self.dx, self.dy, self.dz, self.orientation)


class _Chargement:
    __slots__ = ("type", "zone", "boites")

    def __init__(self, type_: TypePalette, zone: str = "soute", boites: list[_Boite] | None = None) -> None:
        self.type = type_
        self.zone = zone
        self.boites: list[_Boite] = boites if boites is not None else []

    @property
    def masse(self) -> float:
        return sum(b.colis.masse for b in self.boites)

    @property
    def hauteur(self) -> float:
        return max((b.z2 for b in self.boites), default=0.0)

    @property
    def classes(self) -> set[str]:
        return {b.colis.classe for b in self.boites if b.colis.classe}

    def copie(self) -> "_Chargement":
        return _Chargement(self.type, self.zone, [b.copie() for b in self.boites])


@dataclass
class Strategie:
    tri: str = "difficulte"     # ordre de placement des colis
    variante: str = "cdp"       # "cdp" (score du notebook), "plat", "contact", "dbl", "dbl_libre" ou "dbl_cdp"
    palette: str = "score"      # "score" : meilleur score toutes palettes ; "premiere" : première palette où le colis rentre
    bruit: float = 0.0          # perturbation aléatoire de l'ordre (multi-départ)

    def __str__(self) -> str:
        return (f"{self.tri}/{self.variante}" + ("/1re-palette" if self.palette == "premiere" else "")
                + (f"/bruit={self.bruit:.2f}" if self.bruit else ""))


class _Compteur:
    """Nombre de positions candidates testées (indicateur du journal de calcul)."""

    def __init__(self) -> None:
        self.positions = 0


# ---------------------------------------------------------------------------
# Validité d'un chargement (toutes les règles absolues du projet)
# ---------------------------------------------------------------------------

def _aire_commune(a: _Boite, b: _Boite) -> float:
    ox = min(a.x2, b.x2) - max(a.x1, b.x1)
    oy = min(a.y2, b.y2) - max(a.y1, b.y1)
    return ox * oy if ox > TOL and oy > TOL else 0.0


def _se_chevauchent(a: _Boite, b: _Boite) -> bool:
    return (a.x1 < b.x2 - TOL and a.x2 > b.x1 + TOL and a.y1 < b.y2 - TOL and a.y2 > b.y1 + TOL
            and a.z1 < b.z2 - TOL and a.z2 > b.z1 + TOL)


def _dans_palette(b: _Boite, t: TypePalette) -> bool:
    return (b.x1 >= -TOL and b.y1 >= -TOL and b.z1 >= -TOL and b.x2 <= t.largeur + TOL
            and b.y2 <= t.longueur + TOL and b.z2 <= t.hauteur_max + TOL)


def _support_ok(b: _Boite, boites: list[_Boite], params: Parametres, exclu: _Boite | None = None) -> bool:
    """Support d'au moins ``support_minimal`` de la base, sans porteur trop léger, fragile ou en carton."""
    if b.z1 <= TOL:
        return True
    aire = 0.0
    for o in boites:
        if o is b or o is exclu:
            continue
        if abs(o.z2 - b.z1) <= TOL:
            a = _aire_commune(o, b)
            if a > 0.0:
                if b.colis.masse > plafond_charge(o.colis, params) + TOL:
                    return False
                aire += a
    return aire >= params.support_minimal * b.dx * b.dy - TOL


def _cotes_visibles(b: _Boite, boites: list[_Boite], precision: float = TOL_VISIBLE) -> tuple[str, ...]:
    """Côtés du couloir (« gauche » : x minimal, « droite » : x maximal) depuis lesquels ``b`` reste visible.

    Même règle que ``geometrie.cotes_visibles`` (notebook CDP_3, sur l'axe du couloir) appliquée aux boîtes internes,
    pour les boucles rapides : un autre colis masque un côté s'il dépasse de ``b`` de ce côté et que leurs
    projections se chevauchent selon y et selon z.
    """
    gauche = droite = True
    for o in boites:
        if o is b:
            continue
        if min(b.y2, o.y2) - max(b.y1, o.y1) > precision and min(b.z2, o.z2) - max(b.z1, o.z1) > precision:
            if o.x1 < b.x1 - precision:
                gauche = False
            if o.x2 > b.x2 + precision:
                droite = False
            if not gauche and not droite:
                return ()
    return tuple(cote for cote, libre in (("gauche", gauche), ("droite", droite)) if libre)


def _cotes_bord(b: _Boite, largeur: float, jeu: float) -> tuple[str, ...]:
    """Côtés du couloir contre lesquels ``b`` est posé : « gauche » en x = 0, « droite » en x = largeur utile."""
    cotes = []
    if b.x1 <= jeu:
        cotes.append("gauche")
    if b.x2 >= largeur - jeu:
        cotes.append("droite")
    return tuple(cotes)


def _iata_ok(boites: list[_Boite], params: Parametres, largeur: float, nouveau: _Boite | None = None) -> bool:
    """Chaque colis IATA du chargement (avec ``nouveau`` s'il est donné) reste visible depuis un côté du couloir ;
    avec ``iata_au_bord``, il est de plus posé contre ce bord, étiquette tournée vers le couloir."""
    tous = boites if nouveau is None else boites + [nouveau]
    for b in tous:
        if not b.iata:
            continue
        visibles = _cotes_visibles(b, tous)
        if not visibles:
            return False
        if params.iata_au_bord and not any(c in visibles for c in _cotes_bord(b, largeur, params.jeu_contact)):
            return False
    return True


def _charge_valide(ch: _Chargement, params: Parametres, regles: ReglesSeparation) -> bool:
    """Contrôle indépendant d'une palette : masse, volume, chevauchement, support, règles IATA."""
    t, bs = ch.type, ch.boites
    if ch.masse > t.masse_max + TOL:
        return False
    for b in bs:
        if not _dans_palette(b, t):
            return False
    for i, a in enumerate(bs):
        for b in bs[i + 1:]:
            if _se_chevauchent(a, b) or not regles.compatibles(a.colis.classe, b.colis.classe):
                return False
    return all(_support_ok(b, bs, params) for b in bs) and _iata_ok(bs, params, t.largeur)


def _position_valide(ch: _Chargement, colis: Colis, x: float, y: float, z: float,
                     dx: float, dy: float, dz: float, params: Parametres) -> bool:
    """Un colis peut-il être posé en (x, y, z) sur la palette ? (masse et classes à tester à part)

    Contrôle les limites, les collisions, le support, la charge admissible des porteurs (lourd/léger, fragile,
    carton) et la règle IATA (visibilité et bord) du colis qu'on pose comme de ceux déjà en place."""
    t = ch.type
    if x < -TOL or y < -TOL or z < -TOL or x + dx > t.largeur + TOL or y + dy > t.longueur + TOL \
            or z + dz > t.hauteur_max + TOL:
        return False
    x2, y2, z2 = x + dx, y + dy, z + dz
    aire = 0.0
    for o in ch.boites:
        if o.x1 < x2 - TOL and o.x2 > x + TOL and o.y1 < y2 - TOL and o.y2 > y + TOL:
            if o.z1 < z2 - TOL and o.z2 > z + TOL:
                return False
            if z > TOL and abs(o.z2 - z) <= TOL:
                if colis.masse > plafond_charge(o.colis, params) + TOL:
                    return False
                aire += (min(o.x2, x2) - max(o.x1, x)) * (min(o.y2, y2) - max(o.y1, y))
            if abs(o.z1 - z2) <= TOL and o.colis.masse > plafond_charge(colis, params) + TOL:
                return False        # o reposerait sur ce colis (porte-à-faux) : trop lourd, fragile ou carton
    if z > TOL and aire < params.support_minimal * dx * dy - TOL:
        return False
    if classe_de(colis.classe).iata or any(o.iata for o in ch.boites):
        return _iata_ok(ch.boites, params, t.largeur, _Boite(colis, x, y, z, dx, dy, dz))
    return True


def _classes_compatibles(ch: _Chargement, colis: Colis, regles: ReglesSeparation) -> bool:
    code = colis.classe
    if not code:
        return True
    return all(regles.compatibles(code, c) for c in ch.classes)


def _en_contact(a: _Boite, b: _Boite, jeu: float) -> bool:
    """Deux colis partagent une face (latérale ou verticale) d'aire non nulle, à ``jeu`` cm près."""
    ox = min(a.x2, b.x2) - max(a.x1, b.x1)
    oy = min(a.y2, b.y2) - max(a.y1, b.y1)
    oz = min(a.z2, b.z2) - max(a.z1, b.z1)
    if (abs(a.x2 - b.x1) <= jeu or abs(b.x2 - a.x1) <= jeu) and oy > TOL and oz > TOL:
        return True
    if (abs(a.y2 - b.y1) <= jeu or abs(b.y2 - a.y1) <= jeu) and ox > TOL and oz > TOL:
        return True
    return (abs(a.z2 - b.z1) <= jeu or abs(b.z2 - a.z1) <= jeu) and ox > TOL and oy > TOL


def _isoles(boites: list[_Boite], jeu: float) -> set[int]:
    """Colis sans aucun contact avec un autre colis de la palette (le projet interdit qu'un colis reste isolé)."""
    if len(boites) < 2:
        return set()
    return {id(b) for b in boites if not any(o is not b and _en_contact(b, o, jeu) for o in boites)}


# ---------------------------------------------------------------------------
# Positions candidates (points extrêmes du notebook) et meilleur choix
# ---------------------------------------------------------------------------

_PAR_Z_Y_X = itemgetter(2, 1, 0)


def _candidats(boites: list[_Boite], L: float, W: float, dx: float,
               dy: float) -> list[tuple[float, float, float]]:
    """Coins du plateau, faces voisines au sol et dessus des colis, triés par (z, y, x).

    Même ensemble que ``positions_candidates`` du notebook, en coordonnées du coin minimal.
    """
    xmax, ymax = L - dx, W - dy
    lx, ly = xmax + TOL, ymax + TOL
    xs0 = (0.0, xmax / 2, xmax)
    ys0 = (0.0, ymax / 2, ymax)
    sortie = {(x, y, 0.0) for x in xs0 for y in ys0}
    ajout = sortie.add
    for b in boites:
        bx = (b.x1 + b.x2 - dx) / 2
        by = (b.y1 + b.y2 - dy) / 2
        xs_al = (b.x1, bx, b.x2 - dx)
        ys_al = (b.y1, by, b.y2 - dy)
        for x in (b.x1 - dx, b.x2):
            if -TOL <= x <= lx:
                for y in ys_al:
                    if -TOL <= y <= ly:
                        ajout((x, y, 0.0))
        for y in (b.y1 - dy, b.y2):
            if -TOL <= y <= ly:
                for x in xs_al:
                    if -TOL <= x <= lx:
                        ajout((x, y, 0.0))
        z = b.z2
        for x in xs_al:
            if -TOL <= x <= lx:
                for y in ys_al:
                    if -TOL <= y <= ly:
                        ajout((x, y, z))
    return sorted({(round(min(max(x, 0.0), xmax), 4), round(min(max(y, 0.0), ymax), 4), z)
                   for x, y, z in sortie}, key=_PAR_Z_Y_X)


def _vers_le_bord(candidats: list[tuple[float, float, float]], L: float,
                  dx: float) -> list[tuple[float, float, float]]:
    """Ramène chaque position candidate contre l'un des deux bords du couloir (x = 0 ou x = L − dx), en gardant y et z.

    Un colis IATA ne peut être posé que contre un bord : on garde donc, pour chaque (y, z) possible, la position
    contre le bord gauche et celle contre le bord droit.
    """
    xmax = max(0.0, L - dx)
    return sorted({(bx, y, z) for _, y, z in candidats for bx in (0.0, round(xmax, 4))}, key=_PAR_Z_Y_X)


def _ancrages_dbl(boites: list[_Boite], L: float, W: float, dx: float,
                  dy: float) -> list[tuple[float, float, float]]:
    """Ancrages « coins » (coins du plateau et des colis déjà posés), sans hauteur : la chute la calcule."""
    xmax, ymax = L - dx, W - dy
    pts = {(0.0, 0.0), (xmax, 0.0), (0.0, ymax), (xmax, ymax)}
    for b in boites:
        for ax, ay in ((b.x2, b.y1), (b.x1, b.y2), (b.x1, b.y1), (b.x2, b.y2)):
            pts.add((ax, ay))
            pts.add((xmax, ay))
            pts.add((ax, ymax))
    return [(round(x, 4), round(y, 4), 0.0) for x, y in pts
            if -TOL <= x <= xmax + TOL and -TOL <= y <= ymax + TOL]


def _meilleure_position(ch: _Chargement, colis: Colis, orients: list[tuple[float, float, float]],
                        params: Parametres, regles: ReglesSeparation, strat: Strategie,
                        seuil: tuple | None, cpt: _Compteur):
    """Meilleure position du colis sur la palette, strictement meilleure que ``seuil`` (sinon None).

    Variante ``cdp`` : score lexicographique de CDP_3, dans cet ordre : distance d'un colis IATA au bord du couloir
    le plus proche (en x), base la plus basse, hauteur de la palette, surface couverte au sol, plus grande
    bande libre, somme des carrés des bandes libres, distance aux bords, support, compacité du tas, centrage
    du centre de masse, distance au centre. Les autres variantes sont des stratégies complémentaires de
    l'application. Un colis IATA n'est posé que contre un bord du couloir (avec ``iata_au_bord``) et toute position
    doit laisser visibles les colis IATA (celui qu'on pose et ceux déjà en place).
    """
    t = ch.type
    L, W, H = t.largeur, t.longueur, t.hauteur_max
    bs = ch.boites
    m = colis.masse
    if ch.masse + m > t.masse_max + TOL or not _classes_compatibles(ch, colis, regles):
        return None
    jeu, support_min = params.jeu_contact, params.support_minimal
    iata = classe_de(colis.classe).iata
    bord_iata = iata and params.iata_au_bord
    controle_iata = iata or any(b.iata for b in bs)
    plafond_colis = plafond_charge(colis, params)        # charge admissible sur le colis qu'on pose
    donnees = [(b.x1, b.x2, b.y1, b.y2, b.z1, b.z2, b.colis.masse, plafond_charge(b.colis, params)) for b in bs]
    hauteur0 = max((d[5] for d in donnees), default=0.0)
    masse0 = sum(d[6] for d in donnees)
    mx0 = sum(d[6] * (d[0] + d[1]) / 2 for d in donnees)
    my0 = sum(d[6] * (d[2] + d[3]) / 2 for d in donnees)
    if donnees:
        bxmin, bxmax = min(d[0] for d in donnees), max(d[1] for d in donnees)
        bymin, bymax = min(d[2] for d in donnees), max(d[3] for d in donnees)
    else:
        bxmin = bxmax = bymin = bymax = 0.0
    sol = [d for d in donnees if d[4] <= TOL]          # colis posés sur le plateau
    if sol:
        sx1, sx2 = min(d[0] for d in sol), max(d[1] for d in sol)
        sy1, sy2 = min(d[2] for d in sol), max(d[3] for d in sol)
    masse_tot = masse0 + m
    variante = strat.variante
    dbl = variante.startswith("dbl")
    meilleur = None
    seuil_k = seuil[0][:2] if seuil is not None else None

    for oi, (dx, dy, dz) in enumerate(orients):
        if dx > L + TOL or dy > W + TOL or dz > H + TOL:
            continue
        if variante in ("dbl", "dbl_libre"):
            candidats = _ancrages_dbl(bs, L, W, dx, dy)
        else:
            candidats = _candidats(bs, L, W, dx, dy)
        if bord_iata:
            candidats = _vers_le_bord(candidats, L, dx)
        if dbl:
            # « le plus bas, le plus au fond » : chaque position est testée après chute jusqu'au sommet
            # le plus haut sous l'empreinte, ce qui remplit les couches sans vides.
            chute = set(candidats)
            for x, y in {(c[0], c[1]) for c in candidats}:
                zc = 0.0
                for ox1, ox2, oy1, oy2, oz1, oz2, _om, _ofr in donnees:
                    if ox1 < x + dx - TOL and ox2 > x + TOL and oy1 < y + dy - TOL and oy2 > y + TOL and oz2 > zc:
                        zc = oz2
                chute.add((x, y, round(zc, 5)))
            candidats = sorted((c for c in chute if c[2] > 0.0 or (c[0], c[1], 0.0) in chute), key=_PAR_Z_Y_X)
        cpt.positions += len(candidats)
        base = dx * dy
        for x, y, z in candidats:
            z2 = z + dz
            if z2 > H + TOL:
                break                            # candidats triés par z croissant
            if dbl or variante == "cdp":
                primaire = z                     # on minimise d'abord la hauteur de pose
            else:
                primaire = z2 - hauteur0 if z2 > hauteur0 else 0.0
            # Hors colis IATA, la clé croît avec z : on s'arrête dès qu'une meilleure est connue.
            if not iata:
                if meilleur is not None and primaire > meilleur[0][1] + 1e-6:
                    break
                if seuil_k is not None and primaire > seuil_k[1] + 1e-6:
                    break
            x2, y2 = x + dx, y + dy
            # Un colis IATA se range d'abord contre un bord du couloir (préférence de CDP_3, sur l'axe x).
            distance_iata = round(min(x, L - x2), 6) if iata else 0.0
            cle = (distance_iata, round(primaire, 6))
            if meilleur is not None and cle > meilleur[0][:2]:
                continue
            if seuil_k is not None and cle > seuil_k:
                continue
            # validité : recouvrement, support, règle de masse, fragilité
            ok = True
            aire = 0.0
            contact = 0.0
            for ox1, ox2, oy1, oy2, oz1, oz2, om, oplafond in donnees:
                recoupe_xy = ox1 < x2 - TOL and ox2 > x + TOL and oy1 < y2 - TOL and oy2 > y + TOL
                if recoupe_xy:
                    if oz1 < z2 - TOL and oz2 > z + TOL:
                        ok = False
                        break
                    if z > TOL and abs(oz2 - z) <= TOL:
                        if m > oplafond + TOL:
                            ok = False      # le porteur ne supporte pas ce colis : trop léger, fragile ou en carton
                            break
                        aire += (min(ox2, x2) - max(ox1, x)) * (min(oy2, y2) - max(oy1, y))
                    if abs(oz1 - z2) <= TOL and om > plafond_colis + TOL:
                        ok = False      # le colis déjà posé en o reposerait sur celui-ci (porte-à-faux) : trop lourd, fragile ou carton
                        break
                elif variante == "contact" and oz1 < z2 - TOL and oz2 > z + TOL:
                    if (abs(ox2 - x) <= jeu or abs(ox1 - x2) <= jeu) and oy1 < y2 - TOL and oy2 > y + TOL:
                        contact += (min(oy2, y2) - max(oy1, y)) * (min(oz2, z2) - max(oz1, z))
                    elif (abs(oy2 - y) <= jeu or abs(oy1 - y2) <= jeu) and ox1 < x2 - TOL and ox2 > x + TOL:
                        contact += (min(ox2, x2) - max(ox1, x)) * (min(oz2, z2) - max(oz1, z))
            if not ok:
                continue
            if z > TOL and aire < support_min * base - TOL:
                continue
            support = 1.0 if z <= TOL else aire / base
            compacite = ((max(bxmax, x2) - min(bxmin, x)) * (max(bymax, y2) - min(bymin, y))) / (L * W) \
                if donnees else base / (L * W)
            cmx = (mx0 + m * (x + dx / 2)) / masse_tot
            cmy = (my0 + m * (y + dy / 2)) / masse_tot
            centrage = abs(cmx - L / 2) / L + abs(cmy - W / 2) / W
            distance = abs(x + dx / 2 - L / 2) + abs(y + dy / 2 - W / 2)
            if dbl:
                pref = 0.0 if variante == "dbl_libre" else dz
                score = cle + (pref, round(y, 3), round(x, 3), round(-support, 6))
            elif variante == "cdp":
                au_sol = z <= TOL
                gain_surface = base / (L * W) if au_sol else 0.0
                # Bande libre de chaque côté du tas posé au sol : on évite de la fragmenter.
                if au_sol:
                    ex1, ex2, ey1, ey2 = (min(sx1, x), max(sx2, x2), min(sy1, y), max(sy2, y2)) if sol else (x, x2, y, y2)
                    bandes = (ex1 * W, (L - ex2) * W, ey1 * L, (W - ey2) * L)
                elif sol:
                    bandes = (sx1 * W, (L - sx2) * W, sy1 * L, (W - sy2) * L)
                else:
                    bandes = (L * W,)
                distance_bords = min(x, L - x2) + min(y, W - y2)
                score = cle + (round(max(hauteur0, z2), 6), -round(gain_surface, 8), -round(max(bandes), 4),
                               -round(sum(b * b for b in bandes), 2), round(distance_bords, 6),
                               round(-support, 6), round(compacite, 6), round(centrage, 6), round(distance, 4))
            else:
                reste = (round(m * (z + dz / 2), 4), round(-support, 6), round(compacite, 6),
                         round(centrage, 6), round(distance, 4))
                if variante == "plat":
                    score = cle + (dz,) + reste
                else:       # "contact"
                    score = cle + (-round(contact, 3),) + reste
            if meilleur is None or score < meilleur[0]:
                if controle_iata and not _iata_ok(bs, params, L, _Boite(colis, x, y, z, dx, dy, dz)):
                    continue
                meilleur = (score, x, y, z, dx, dy, dz, oi)
    return meilleur


# ---------------------------------------------------------------------------
# Phase 1 : construction d'un placement
# ---------------------------------------------------------------------------

def _type_requis(colis: Colis, types: list[TypePalette]) -> TypePalette | None:
    """Plus petit format de palette où le colis tient seul (dimensions et masse)."""
    for t in types:
        if colis.masse <= t.masse_max + TOL and tient_dans(colis, t.largeur, t.longueur, t.hauteur_max):
            return t
    return None


def _type_rampe(params: Parametres, base: TypePalette) -> TypePalette:
    return TypePalette("10K", "Palette 10k (rampe)", base.largeur, base.longueur,
                       min(base.hauteur_max, params.hauteur_max_rampe), params.masse_nette_max_rampe, 1)


def _ordre_placement(a_placer: list[tuple[Colis, TypePalette]], params: Parametres, strat: Strategie,
                     rng: random.Random, regles: ReglesSeparation) -> list[tuple[Colis, TypePalette]]:
    """Ordre de placement : colis exigeant une BIPAL/TRIPAL d'abord, puis les colis dangereux (les plus
    incompatibles en premier, comme ``priorite_iata`` de CDP_2), puis selon la stratégie."""
    surface_palette = min(t.surface for t in params.types_palettes())
    cols = [c for c, _ in a_placer]
    max_m = max(c.masse for c in cols)
    max_v = max(c.volume for c in cols)
    max_d = max(max(c.longueur, c.largeur, c.hauteur) for c in cols)
    matrice = regles.matrice()

    def face(c: Colis) -> float:
        return max(c.longueur * c.largeur, c.longueur * c.hauteur, c.largeur * c.hauteur)

    def base_max(c: Colis) -> float:
        """Plus grande base posable : tient compte du sens obligatoire (CDP_2, ordre 1)."""
        return max(dx * dy for dx, dy, _ in orientations(c))

    def priorite_iata(c: Colis) -> tuple[int, int]:
        if not classe_de(c.classe).iata:
            return (0, 0)
        # « 9L » est un synonyme de « 9 » : on ne le compte pas deux fois
        return (1, sum(1 for autre in matrice.get(c.classe, []) if autre != "9L"))

    rang = {id(c): i for i, (c, _) in enumerate(a_placer)}

    def cle(item: tuple[Colis, TypePalette]):
        c, t = item
        bruit = 1.0 + strat.bruit * (rng.random() - 0.5) * 2 if strat.bruit else 1.0
        # Les quatre ordres de CDP_2 n'ont pas de critère secondaire : à égalité, on garde l'ordre de saisie.
        if strat.tri == "difficulte":
            k = (-(4 * c.volume / max_v + 3 * face(c) / surface_palette + 2 * c.masse / max_m
                   + max(c.longueur, c.largeur, c.hauteur) / max_d) * bruit,)
        elif strat.tri == "volume":
            k = (-c.volume * bruit,)
        elif strat.tri == "masse":
            k = (-c.masse * bruit,)
        elif strat.tri == "base":
            k = (-base_max(c) * bruit, -c.masse)
        elif strat.tri == "hauteur":
            k = (-min(c.longueur, c.largeur, c.hauteur) * bruit, -c.masse)
        elif strat.tri == "special":
            k = (0 if c.special else 1, -c.masse * bruit, -c.volume)
        else:                            # "densite"
            k = (-(c.masse / face(c)) * bruit, -c.volume)
        dangereux, incompatibilites = priorite_iata(c)
        return (-t.positions, -dangereux, -incompatibilites) + k + (rang[id(c)],)

    return sorted(a_placer, key=cle)


def _construire(ordre: list[tuple[Colis, TypePalette]], params: Parametres, regles: ReglesSeparation,
                strat: Strategie, cpt: _Compteur, limite_palettes: int | None = None,
                type_force: TypePalette | None = None, zone_force: str = "soute"):
    """Construit un placement ; retourne (chargements, échecs) ou None si ``limite_palettes`` est dépassée."""
    chargements: list[_Chargement] = []
    echecs: list[Colis] = []
    positions = 0
    for colis, type_min in ordre:
        orients = orientations(colis)
        choix = None
        for idx, ch in enumerate(chargements):
            res = _meilleure_position(ch, colis, orients, params, regles, strat,
                                      choix[0] if choix and strat.palette == "score" else None, cpt)
            if res is not None and (choix is None or res[0] < choix[0][0]):
                choix = (res, idx)
            if choix is not None and strat.palette == "premiere":
                break
        if choix is None:
            if limite_palettes is not None and len(chargements) >= limite_palettes:
                return None
            type_ouvert, zone = type_force or type_min, zone_force
            if type_force is None and positions >= params.positions_soute and type_min.positions == 1 \
                    and params.positions_rampe > 0 and positions < params.positions_soute + params.positions_rampe:
                t_rampe = _type_rampe(params, type_min)
                if colis.masse <= t_rampe.masse_max and tient_dans(colis, t_rampe.largeur, t_rampe.longueur,
                                                                   t_rampe.hauteur_max):
                    type_ouvert, zone = t_rampe, "rampe"
            ch = _Chargement(type_ouvert, zone)
            res = _meilleure_position(ch, colis, orients, params, regles, strat, None, cpt)
            if res is None:
                if limite_palettes is not None:
                    return None
                echecs.append(colis)
                continue
            chargements.append(ch)
            positions += type_ouvert.positions
            choix = (res, len(chargements) - 1)
        (_, x, y, z, dx, dy, dz, oi), idx = choix
        chargements[idx].boites.append(_Boite(colis, x, y, z, dx, dy, dz, oi))
    return chargements, echecs


def _objectif(chs: list[_Chargement], echecs: int, params: Parametres) -> tuple:
    """Comparaison lexicographique des constructions (plus petit = meilleur).

    Colis non placés, emplacements, palettes, puis la clé de CDP_3 : hauteur globale, surface couverte au sol
    (la plus grande), somme des hauteurs, puis (ajout de l'application) nombre de colis isolés et décentrage des
    centres de masse.
    """
    positions = sum(ch.type.positions for ch in chs)
    dispersion, surface, isoles = 0.0, 0.0, 0
    for ch in chs:
        isoles += len(_isoles(ch.boites, params.jeu_contact))
        L, W = ch.type.largeur, ch.type.longueur
        surface += sum(b.dx * b.dy for b in ch.boites if b.z1 <= TOL)
        masse = ch.masse
        if masse > 0:
            cx = sum(b.colis.masse * (b.x1 + b.x2) / 2 for b in ch.boites) / masse
            cy = sum(b.colis.masse * (b.y1 + b.y2) / 2 for b in ch.boites) / masse
            dispersion += abs(cx - L / 2) / L + abs(cy - W / 2) / W
    hauteur_globale = max((ch.hauteur for ch in chs), default=0.0)
    return (echecs, positions, len(chs), round(hauteur_globale, 1), -round(surface, 1),
            round(sum(ch.hauteur for ch in chs), 1), isoles, round(dispersion, 4))


def strategies_deterministes() -> list[Strategie]:
    """Ordre d'essai des stratégies : les quatre ordres de CDP_2 (grande base, difficulté, volume, masse, avec le
    score du notebook sur toutes les palettes), puis le remplissage « le plus bas, le plus au fond » palette par
    palette, puis les autres variantes."""
    ordres = ("base", "difficulte", "volume", "masse", "hauteur", "densite", "special")
    liste = [Strategie(t, "cdp") for t in ordres[:4]]
    for variante in ("dbl", "dbl_libre", "dbl_cdp", "cdp", "plat", "contact"):
        liste += [Strategie(t, variante, "premiere") for t in ordres]
    for palette in ("score",):
        for variante in ("plat", "contact", "dbl", "dbl_libre", "dbl_cdp", "cdp"):
            liste += [Strategie(t, variante, palette) for t in ordres]
    vus, uniques = set(), []
    for st in liste:
        cle = (st.tri, st.variante, st.palette)
        if cle not in vus:
            vus.add(cle)
            uniques.append(st)
    return uniques


def _placement_heuristique(a_placer, params, regles, duree, rng):
    """Phase 1 : essaie plusieurs stratégies pendant ``duree`` s et garde la meilleure construction."""
    debut = perf_counter()
    cpt = _Compteur()
    strategies = strategies_deterministes()
    meilleur, meilleur_obj, essais = None, None, []
    sans_amelioration, i = 0, 0
    while True:
        # Les 12 premières stratégies sont déterministes (dont les quatre ordres du notebook) ; ensuite on
        # alterne la liste déterministe et des variantes bruitées des 30 stratégies les plus prometteuses.
        if i < 12 or (i % 2 == 0 and (i - 12) // 2 + 12 < len(strategies)):
            strat = strategies[i if i < 12 else (i - 12) // 2 + 12]
        else:
            base = strategies[rng.randrange(min(30, len(strategies)))]
            strat = Strategie(base.tri, base.variante, base.palette, rng.choice((0.1, 0.25, 0.5)))
        i += 1
        ordre = _ordre_placement(a_placer, params, strat, rng, regles)
        debut_c = cpt.positions
        chs, echecs = _construire(ordre, params, regles, strat, cpt)
        obj = _objectif(chs, len(echecs), params)
        if i <= 4:
            essais.append({"strategie": str(strat), "palettes": len(chs), "positions": sum(c.type.positions for c in chs),
                           "hauteur": round(sum(c.hauteur for c in chs), 1), "positions_testees": cpt.positions - debut_c})
        if meilleur_obj is None or obj < meilleur_obj:
            meilleur, meilleur_obj, sans_amelioration = (chs, echecs), obj, 0
        else:
            sans_amelioration += 1
        ecoule = perf_counter() - debut
        if ecoule >= duree and (i >= 4 or ecoule >= 4 * duree):
            break
        if i >= 2 * len(strategies) and sans_amelioration >= 300:
            break
    return meilleur[0], meilleur[1], {"constructions": i, "positions_testees": cpt.positions,
                                      "essais": essais, "duree": perf_counter() - debut}


# ---------------------------------------------------------------------------
# Phase 3 : fusion des palettes
# ---------------------------------------------------------------------------

def _reste_valide_sans(ch: _Chargement, original: _Boite, params: Parametres) -> bool:
    """Retirer ``original`` laisse-t-il chaque colis qui reposait dessus suffisamment soutenu ?"""
    reste = [b for b in ch.boites if b is not original]
    for b in reste:
        if abs(b.z1 - original.z2) <= TOL and _aire_commune(b, original) > 0.0:
            if not _support_ok(b, reste, params):
                return False
    return True


def _placer_dans(dest: _Chargement, colis: Colis, params: Parametres, regles: ReglesSeparation,
                 compteur: list[int] | None = None):
    """Première position valide (la plus basse) d'un colis sur la palette, toutes orientations permises.

    Retourne (clé, x, y, z, dx, dy, dz, orientation) ou None. Masse, classes, support, charge admissible des porteurs
    et règle IATA sont contrôlés ; un colis IATA n'est posé que contre un bord du couloir.
    """
    t = dest.type
    if dest.masse + colis.masse > t.masse_max + TOL or not _classes_compatibles(dest, colis, regles):
        return None
    iata = classe_de(colis.classe).iata
    meilleur = None
    for oi, (dx, dy, dz) in enumerate(orientations(colis)):
        if dx > t.largeur + TOL or dy > t.longueur + TOL or dz > t.hauteur_max + TOL:
            continue
        candidats = _candidats(dest.boites, t.largeur, t.longueur, dx, dy)
        if iata and params.iata_au_bord:
            candidats = _vers_le_bord(candidats, t.largeur, dx)
        for x, y, z in candidats:
            if compteur is not None:
                compteur[0] += 1
            if _position_valide(dest, colis, x, y, z, dx, dy, dz, params):
                distance_iata = round(min(x, t.largeur - (x + dx)), 6) if iata else 0.0
                cle = (distance_iata, z, abs(x + dx / 2 - t.largeur / 2) + abs(y + dy / 2 - t.longueur / 2))
                if meilleur is None or cle < meilleur[0]:
                    meilleur = (cle, x, y, z, dx, dy, dz, oi)
                break
    return meilleur


def _occupation(ch: _Chargement) -> tuple[float, float]:
    t = ch.type
    volume = sum(b.dx * b.dy * b.dz for b in ch.boites)
    return volume / (t.largeur * t.longueur * t.hauteur_max), ch.masse / t.masse_max


def _fusionner(chs: list[_Chargement], params: Parametres, regles: ReglesSeparation, limite: float) -> dict:
    """Transfère des colis de la palette la moins remplie vers la plus remplie (notebook, cellule 14)."""
    debut = perf_counter()
    c = {"paires_testees": 0, "fusions_reussies": 0, "colis_transferes": 0, "emplacements_testes": 0,
         "paires_planifiees": 0, "tours_complets": 0, "limite_secondes": limite, "limite_atteinte": False}

    def temps_ecoule() -> bool:
        depasse = perf_counter() - debut >= limite
        c["limite_atteinte"] = c["limite_atteinte"] or depasse
        return depasse

    def repartir(p1: _Chargement, p2: _Chargement):
        return (p1, p2) if _occupation(p1) >= _occupation(p2) else (p2, p1)

    def essayer_transferts(recv: _Chargement, src: _Chargement, fin: float) -> int:
        transferts = 0
        t = recv.type

        def fini() -> bool:
            return temps_ecoule() or perf_counter() >= fin

        while not fini():
            mouvement = False
            # les plus grands colis d'abord ; un nouveau passage retire un colis bas après ceux qu'il portait
            for original in sorted(src.boites, key=lambda b: (b.dx * b.dy * b.dz, b.z1), reverse=True):
                if fini():
                    return transferts
                colis = original.colis
                if recv.masse + colis.masse > t.masse_max + TOL or not _classes_compatibles(recv, colis, regles):
                    continue
                if not _reste_valide_sans(src, original, params):
                    continue
                compteur = [0]
                trouve = _placer_dans(recv, colis, params, regles, compteur=compteur)
                c["emplacements_testes"] += compteur[0]
                if trouve is not None:
                    _, x, y, z, dx, dy, dz, oi = trouve
                    recv.boites.append(_Boite(colis, x, y, z, dx, dy, dz, oi))
                    src.boites.remove(original)
                    c["colis_transferes"] += 1
                    transferts += 1
                    mouvement = True
                    break
            if not mouvement:
                return transferts
        return transferts

    while len(chs) > 1 and not temps_ecoule():
        paires = list(combinations(chs, 2))
        paires.sort(key=lambda p: (_occupation(repartir(*p)[1]), tuple(-v for v in _occupation(repartir(*p)[0]))))
        c["paires_planifiees"] += len(paires)
        progression = False
        for rang, (p1, p2) in enumerate(paires):
            if temps_ecoule():
                break
            restant = max(0.0, limite - (perf_counter() - debut))
            fin = perf_counter() + restant / (len(paires) - rang)
            recv, src = repartir(p1, p2)
            c["paires_testees"] += 1
            progression |= essayer_transferts(recv, src, fin) > 0
        vides = [ch for ch in chs if not ch.boites]
        for ch in vides:
            chs.remove(ch)
        c["fusions_reussies"] += len(vides)
        if c["paires_testees"] >= c["paires_planifiees"]:
            c["tours_complets"] += 1
        if not progression:
            break
    c["duree"] = perf_counter() - debut
    return c


# ---------------------------------------------------------------------------
# Phases 4 et 6 : recentrage rigide et tassement local
# ---------------------------------------------------------------------------

def _recentrer(chs: list[_Chargement], params: Parametres) -> None:
    """Translate chaque tas pour centrer son centre de masse, sans changer ses contacts.

    La translation est limitée par les bords de la palette et ne décolle jamais un colis IATA du bord du couloir
    contre lequel il est posé ; les positions relatives ne changent pas, donc la visibilité des colis IATA non plus.
    """
    jeu = params.jeu_contact
    for ch in chs:
        bs = ch.boites
        if not bs:
            continue
        L, W = ch.type.largeur, ch.type.longueur
        masse = ch.masse
        cx = sum(b.colis.masse * (b.x1 + b.x2) / 2 for b in bs) / masse
        cy = sum(b.colis.masse * (b.y1 + b.y2) / 2 for b in bs) / masse
        bas_x, haut_x = max(-b.x1 for b in bs), min(L - b.x2 for b in bs)
        bas_y, haut_y = max(-b.y1 for b in bs), min(W - b.y2 for b in bs)
        if params.iata_au_bord:
            for b in bs:
                if not b.iata:
                    continue
                cotes = _cotes_bord(b, L, jeu)
                if "gauche" in cotes:
                    haut_x = min(haut_x, TOL - b.x1)          # le colis reste exactement contre le bord gauche
                if "droite" in cotes:
                    bas_x = max(bas_x, (L - TOL) - b.x2)      # ... ou contre le bord droit
        tx = min(max(L / 2 - cx, bas_x), haut_x) if bas_x <= haut_x else 0.0
        ty = min(max(W / 2 - cy, bas_y), haut_y) if bas_y <= haut_y else 0.0
        for b in bs:
            b.placer(b.x1 + tx, b.y1 + ty)


def _deplacement_valide(ch: _Chargement, b: _Boite, params: Parametres) -> bool:
    bs = ch.boites
    if not _support_ok(b, bs, params):
        return False
    if not all(_support_ok(c, bs, params) for c in bs if c is not b and abs(c.z1 - b.z2) <= TOL):
        return False
    return _iata_ok(bs, params, ch.type.largeur)      # le glissement doit respecter la règle IATA (bord, visibilité)


def _glisser(ch: _Chargement, b: _Boite, axe: str, params: Parametres, precision: float) -> float:
    """Fait glisser ``b`` vers le centre de la palette selon ``axe`` tant que le chargement reste valide.

    Un colis IATA contre un bord du couloir ne glisse pas en x, et un glissement ne doit isoler aucun colis : ``b`` garde
    un contact s'il en avait un, et chaque colis qui le touchait en garde un aussi.
    """
    L, W = ch.type.largeur, ch.type.longueur
    x_axe = axe == "x"
    jeu = params.jeu_contact
    if x_axe and b.iata and params.iata_au_bord and _cotes_bord(b, L, jeu):
        return 0.0
    centre_palette = L / 2 if x_axe else W / 2
    centre = (b.x1 + b.x2) / 2 if x_axe else (b.y1 + b.y2) / 2
    ecart = centre_palette - centre
    if abs(ecart) <= precision:
        return 0.0
    sens = 1.0 if ecart > 0 else -1.0
    dmax = abs(ecart)
    for o in ch.boites:
        if o is b:
            continue
        if x_axe:
            if o.y1 < b.y2 - TOL and o.y2 > b.y1 + TOL and o.z1 < b.z2 - TOL and o.z2 > b.z1 + TOL:
                if sens > 0 and o.x1 >= b.x2 - TOL:
                    dmax = min(dmax, max(0.0, o.x1 - b.x2))
                elif sens < 0 and o.x2 <= b.x1 + TOL:
                    dmax = min(dmax, max(0.0, b.x1 - o.x2))
        else:
            if o.x1 < b.x2 - TOL and o.x2 > b.x1 + TOL and o.z1 < b.z2 - TOL and o.z2 > b.z1 + TOL:
                if sens > 0 and o.y1 >= b.y2 - TOL:
                    dmax = min(dmax, max(0.0, o.y1 - b.y2))
                elif sens < 0 and o.y2 <= b.y1 + TOL:
                    dmax = min(dmax, max(0.0, b.y1 - o.y2))
    if dmax <= precision:
        return 0.0
    depart = b.x1 if x_axe else b.y1
    voisins = [o for o in ch.boites if o is not b and _en_contact(b, o, jeu)]      # contacts avant le glissement

    def sans_isolement() -> bool:
        if voisins and not any(o is not b and _en_contact(b, o, jeu) for o in ch.boites):
            return False
        return all(any(c is not o and _en_contact(o, c, jeu) for c in ch.boites) for o in voisins)

    def essayer(d: float) -> bool:
        if x_axe:
            b.placer(depart + sens * d, b.y1)
        else:
            b.placer(b.x1, depart + sens * d)
        return _deplacement_valide(ch, b, params) and sans_isolement()

    if essayer(dmax):
        return dmax
    bas, haut = 0.0, dmax            # bas : position valide, haut : position invalide
    while haut - bas > precision:
        milieu = (bas + haut) / 2
        if essayer(milieu):
            bas = milieu
        else:
            haut = milieu
    essayer(bas)
    return bas


def _tasser(chs: list[_Chargement], params: Parametres, precision: float = 0.1, passages_max: int = 10) -> dict:
    """Fait glisser chaque colis (les plus lourds d'abord) vers le centre, au millimètre près."""
    deplacement, passages = 0.0, 0
    for ch in chs:
        for _ in range(passages_max):
            mouvement = 0.0
            for b in sorted(ch.boites, key=lambda b: (-b.colis.masse, b.colis.numero)):
                mouvement += _glisser(ch, b, "x", params, precision)
                mouvement += _glisser(ch, b, "y", params, precision)
            passages += 1
            deplacement += mouvement
            if mouvement <= precision:
                break
    _recentrer(chs, params)      # le tassement déplace le centre de masse : translation finale
    return {"deplacement_total": deplacement / 100.0, "nombre_passages": passages, "precision": precision}


# ---------------------------------------------------------------------------
# Phase 4 : tassement vertical
# ---------------------------------------------------------------------------

def _positions_xy(b: _Boite, dx: float, dy: float, restants: list[_Boite], t: TypePalette) -> list[tuple[float, float]]:
    """Alignements possibles d'un colis de base dx × dy : plateau, centre, position actuelle, faces des autres colis.

    Trié par déplacement croissant depuis la position actuelle, pour préférer les petits mouvements.
    """
    xmax, ymax = t.largeur - dx, t.longueur - dy
    xs = {0.0, xmax, xmax / 2, b.x1}
    ys = {0.0, ymax, ymax / 2, b.y1}
    for o in restants:
        xs.update((o.x1, o.x2, o.x1 - dx, o.x2 - dx))
        ys.update((o.y1, o.y2, o.y1 - dy, o.y2 - dy))
    xs = sorted({round(min(max(x, 0.0), xmax), 4) for x in xs if -TOL <= x <= xmax + TOL})
    ys = sorted({round(min(max(y, 0.0), ymax), 4) for y in ys if -TOL <= y <= ymax + TOL})
    return sorted(((x, y) for x in xs for y in ys), key=lambda xy: (abs(xy[0] - b.x1) + abs(xy[1] - b.y1), xy))


def _tasser_vertical(chs: list[_Chargement], params: Parametres, limite: float) -> dict:
    """Replace chaque colis plus bas : autre position (x, y), autre orientation permise (CDP_2, cellule 14).

    Recherche de dispositions finales, pas d'une trajectoire de manutention. Les affectations aux palettes ne
    changent pas. Chaque déplacement abaisse strictement la base du colis sans relever son sommet ; il est
    accepté seulement si le chargement reste valide (collisions, support à 90 %, masse, charge admissible des
    porteurs, règle IATA), si le retrait du colis ne laisse aucun autre colis sans appui et s'il n'isole aucun
    colis. Plusieurs passages permettent de retirer un support après les colis qu'il portait.
    """
    debut = perf_counter()
    hauteur_avant = max((c.hauteur for c in chs), default=0.0)
    somme_avant = sum(c.hauteur for c in chs)
    mouvements = essais = passages = supports_bloquants = 0
    descente = lateral = 0.0
    modifies: set[int] = set()
    limite_atteinte = False
    support_min = params.support_minimal
    jeu = params.jeu_contact

    def expire() -> bool:
        return perf_counter() - debut >= limite

    for ch in chs:
        t = ch.type
        for _ in range(max(1, len(ch.boites))):
            mouvement_passage = False
            for b in sorted(ch.boites, key=lambda o: (-o.z2, o.colis.numero)):
                if expire():
                    limite_atteinte = True
                    break
                bas, sommet = b.z1, b.z2
                if bas <= TOL:
                    continue
                if not _reste_valide_sans(ch, b, params):      # le retrait laisserait un colis sans appui
                    supports_bloquants += 1
                    continue
                toutes = ch.boites
                restants = [o for o in toutes if o is not b]
                niveaux = sorted({0.0} | {o.z2 for o in restants if o.z2 < bas - TOL})
                orientations_b = sorted(enumerate(orientations(b.colis)),
                                        key=lambda e: (e[1][2], -e[1][0] * e[1][1], e[1][0], e[1][1]))
                choix = None
                isoles_avant = _isoles(toutes, jeu)
                b_isole = id(b) in isoles_avant
                isoles_autres = isoles_avant - {id(b)}
                ch.boites = restants                              # _position_valide ignore ainsi le colis déplacé
                try:
                    grilles: dict[tuple[float, float], list[tuple[float, float]]] = {}
                    for niveau in niveaux:
                        for oi, (dx, dy, dz) in orientations_b:
                            if niveau + dz > sommet + TOL or dx > t.largeur + TOL or dy > t.longueur + TOL:
                                continue
                            if (dx, dy) not in grilles:
                                grilles[dx, dy] = _positions_xy(b, dx, dy, restants, t)
                            # filtres rapides avant la validation complète : collisions et appui à ce niveau
                            obstacles = [o for o in restants if o.z1 < niveau + dz - TOL and o.z2 > niveau + TOL]
                            portants = [o for o in restants if abs(o.z2 - niveau) <= TOL] if niveau > TOL else []
                            for x, y in grilles[dx, dy]:
                                if expire():
                                    limite_atteinte = True
                                    break
                                essais += 1
                                x2, y2 = x + dx, y + dy
                                if any(o.x1 < x2 - TOL and o.x2 > x + TOL and o.y1 < y2 - TOL and o.y2 > y + TOL
                                       for o in obstacles):
                                    continue
                                if niveau > TOL:
                                    aire = sum(max(0.0, min(o.x2, x2) - max(o.x1, x)) * max(0.0, min(o.y2, y2) - max(o.y1, y))
                                               for o in portants)
                                    if aire < support_min * dx * dy - TOL:
                                        continue
                                if _position_valide(ch, b.colis, x, y, niveau, dx, dy, dz, params):
                                    candidat = _Boite(b.colis, x, y, niveau, dx, dy, dz, oi)
                                    isoles_apres = _isoles(restants + [candidat], jeu)
                                    if (isoles_apres - {id(candidat)} <= isoles_autres
                                            and (id(candidat) not in isoles_apres or b_isole)):
                                        choix = (x, y, niveau, dx, dy, dz, oi)
                                        break
                            if choix is not None or limite_atteinte:
                                break
                        if choix is not None or limite_atteinte:
                            break
                finally:
                    ch.boites = toutes
                if choix is not None:
                    x, y, niveau, dx, dy, dz, oi = choix
                    deplacement_xy = ((x - b.x1) ** 2 + (y - b.y1) ** 2) ** 0.5
                    b.dx, b.dy, b.dz, b.orientation = dx, dy, dz, oi
                    b.placer(x, y, niveau)
                    descente += bas - niveau
                    lateral += deplacement_xy
                    mouvements += 1
                    modifies.add(id(b))
                    mouvement_passage = True
                if limite_atteinte:
                    break
            passages += 1
            if limite_atteinte or not mouvement_passage:
                break
        if limite_atteinte:
            break
    hauteur_apres = max((c.hauteur for c in chs), default=0.0)
    return {"duree": perf_counter() - debut, "deplacement_total": descente / 100.0,
            "deplacement_lateral": lateral / 100.0, "emplacements_testes": essais, "colis_deplaces": len(modifies),
            "supports_bloquants": supports_bloquants, "limite_atteinte": limite_atteinte, "mouvements": mouvements,
            "nombre_passages": passages, "hauteur_avant": hauteur_avant, "hauteur_apres": hauteur_apres,
            "gain_somme_hauteurs": somme_avant - sum(c.hauteur for c in chs)}


# ---------------------------------------------------------------------------
# Conversions, zones, journal
# ---------------------------------------------------------------------------

def _normaliser_zones(chs: list[_Chargement], params: Parametres) -> None:
    """Une palette de rampe repasse en soute (limites plus larges) s'il reste de la place en soute."""
    types = params.types_palettes()
    soute = sum(ch.type.positions for ch in chs if ch.zone == "soute")
    for ch in sorted((c for c in chs if c.zone == "rampe"), key=lambda c: -c.masse):
        if soute + 1 <= params.positions_soute:
            ch.type, ch.zone = types[0], "soute"
            soute += 1
    chs.sort(key=lambda c: (c.zone == "rampe", -c.masse))


def _vers_palettes(chs: list[_Chargement]) -> list[Palette]:
    palettes = []
    for n, ch in enumerate(chs, start=1):
        if not ch.boites:
            continue
        pal = Palette(n, ch.type, [], ch.zone)
        for b in ch.boites:
            pal.placements.append(Placement(b.colis, b.x1, b.y1, b.z1, b.dx, b.dy, b.dz, b.orientation))
        palettes.append(pal)
    return palettes


def _bilan_phase(chs: list[_Chargement]) -> dict:
    return {"palettes": len(chs), "positions": sum(c.type.positions for c in chs),
            "hauteurs": round(sum(c.hauteur for c in chs), 1),
            "hauteur_globale": round(max((c.hauteur for c in chs), default=0.0), 1)}


def _entree_journal(phase: int, titre: str, duree: float, texte: str, chs: list[_Chargement] | None,
                    ignoree: bool = False, **details) -> dict:
    entree = {"phase": phase, "titre": titre, "duree": round(duree, 3), "texte": texte, "ignoree": ignoree}
    if chs is not None:
        entree.update(_bilan_phase(chs))
    entree.update(details)
    return entree


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def optimiser(colis: Iterable[Colis], params: Parametres | None = None,
              regles: ReglesSeparation | None = None) -> Resultat:
    """Confectionne les palettes : pipeline en cinq phases (voir le docstring du module)."""
    params = params or Parametres()
    regles = regles or regles_par_defaut()
    debut = perf_counter()
    types = params.types_palettes()
    rng = random.Random(params.graine)

    non_places: list[tuple[Colis, str]] = []
    a_placer: list[tuple[Colis, TypePalette]] = []
    for c in colis:
        erreurs = c.valider()
        if erreurs:
            non_places.append((c, "saisie invalide : " + ", ".join(erreurs)))
            continue
        t = _type_requis(c, types)
        if t is None:
            plus_grand = types[-1]
            if c.masse > plus_grand.masse_max:
                motif = (f"masse {c.masse:g} kg supérieure à la charge nette maximale "
                         f"d'une {plus_grand.libelle} ({plus_grand.masse_max:g} kg)")
            else:
                motif = (f"dimensions supérieures à une {plus_grand.libelle} "
                         f"({plus_grand.largeur:g} x {plus_grand.longueur:g} x {plus_grand.hauteur_max:g} cm) : "
                         "à charger en vrac, sanglé au plancher")
            non_places.append((c, motif))
            continue
        a_placer.append((c, t))

    if not a_placer:
        return Resultat([], non_places, params, perf_counter() - debut, 0, params.moteur,
                        "vide" if not non_places else "aucun colis plaçable")

    journal: list[dict] = []
    etapes: list[Etape] = []
    duree = max(0.2, params.duree_recherche)

    # ---- Phase 1 : placement heuristique --------------------------------------------------
    chs, echecs, info = _placement_heuristique(a_placer, params, regles, 0.5 * duree, rng)
    essais = " ; ".join(f"{e['strategie']} → {e['palettes']} palette(s), {e['hauteur']:.0f} cm" for e in info["essais"])
    journal.append(_entree_journal(
        1, "Placement heuristique", info["duree"],
        f"{info['constructions']} construction(s) et {info['positions_testees']} positions testées. "
        f"Ordres du notebook : {essais}. Meilleure construction : {len(chs)} palette(s).", chs,
        constructions=info["constructions"], positions_testees=info["positions_testees"]))
    etapes.append(Etape("heuristique", "Phase 1 – placement heuristique",
                        "Construction par points extrêmes avant toute amélioration.", _vers_palettes(chs)))
    for c in echecs:
        non_places.append((c, "aucune position compatible trouvée par l'heuristique"))

    # ---- Phase 2 : recherche SCIP (facultative) -------------------------------------------
    utilise_scip = False
    if params.moteur == "scip":
        from . import moteur_scip
        dispo, detail = moteur_scip.disponible()
        if dispo:
            t0 = perf_counter()
            chs, info_scip = moteur_scip.reduire_nombre_palettes(chs, params, regles, params.temps_limite_scip)
            utilise_scip = True
            journal.append(_entree_journal(2, "Validation et recherche SCIP", perf_counter() - t0,
                                           info_scip["texte"], chs, historique=info_scip.get("historique", []),
                                           statut=info_scip.get("statut", "")))
            etapes.append(Etape("scip", "Phase 2 – après SCIP, avant fusion",
                                "Amorce heuristique validée puis réduction progressive du nombre d'emplacements.",
                                _vers_palettes(chs)))
        else:
            journal.append(_entree_journal(2, "Recherche SCIP", 0.0, f"SCIP indisponible ({detail}) : phase ignorée.",
                                           None, ignoree=True))
    else:
        journal.append(_entree_journal(2, "Recherche SCIP", 0.0,
                                       "Non utilisée (moteur heuristique). Choisir le moteur SCIP dans les paramètres.",
                                       None, ignoree=True))

    # ---- Phase 3 : fusion des palettes --------------------------------------------------
    avant_fusion = len(chs)
    stats = _fusionner(chs, params, regles, 0.15 * duree)
    _normaliser_zones(chs, params)
    journal.append(_entree_journal(
        3, "Fusion des palettes", stats["duree"],
        f"{stats['colis_transferes']} colis déplacé(s), {stats['fusions_reussies']} palette(s) vidée(s) "
        f"({avant_fusion} → {len(chs)}), {stats['paires_testees']}/{stats['paires_planifiees']} paire(s) traitée(s), "
        f"{stats['emplacements_testes']} emplacement(s) testés."
        + (" Limite de temps atteinte : les transferts validés sont conservés." if stats["limite_atteinte"] else ""),
        chs, **{k: stats[k] for k in ("colis_transferes", "fusions_reussies", "emplacements_testes")}))

    # ---- Phase 4 : tassement vertical ---------------------------------------------------------
    vert = _tasser_vertical(chs, params, 0.25 * duree)
    texte = (f"{vert['mouvements']} replacement(s) plus bas, {vert['colis_deplaces']} colis distinct(s), "
             f"{vert['emplacements_testes']} emplacements testés, {vert['deplacement_lateral']:.3f} m de déplacement "
             f"latéral cumulé, {vert['deplacement_total']:.3f} m de descente cumulée. Hauteur globale : "
             f"{vert['hauteur_avant']:.1f} → {vert['hauteur_apres']:.1f} cm. Les collisions, les appuis à 90 % et la "
             "règle IATA (bord du couloir, visibilité) sont vérifiés après chaque descente.")
    if not vert["mouvements"]:
        texte += (" Aucun replacement plus bas trouvé parmi les positions testées ; cet échec ne prouve pas qu'aucune "
                  "réorganisation ne serait possible.")
    if vert["limite_atteinte"]:
        texte += " Budget de temps atteint : les replacements déjà validés sont conservés."
    journal.append(_entree_journal(4, "Tassement vertical", vert["duree"], texte, chs,
                                   **{k: vert[k] for k in ("mouvements", "colis_deplaces", "emplacements_testes",
                                                           "hauteur_avant", "hauteur_apres")}))

    # ---- Phase 5 : recentrage et tassement horizontal -------------------------------------------
    t0 = perf_counter()
    _recentrer(chs, params)
    tass = _tasser(chs, params)
    _normaliser_zones(chs, params)
    palettes = _vers_palettes(chs)
    journal.append(_entree_journal(
        5, "Recentrage et tassement horizontal", perf_counter() - t0,
        f"{tass['nombre_passages']} passage(s), {tass['deplacement_total']:.3f} m de déplacement cumulé ; les "
        f"coordonnées z restent fixes et le tas est recentré à la fin. Résultat final : {len(chs)} palette(s), "
        f"{sum(c.type.positions for c in chs)} emplacement(s), hauteur globale "
        f"{max((c.hauteur for c in chs), default=0.0):.1f} cm, somme des hauteurs {sum(c.hauteur for c in chs):.1f} cm. "
        "Les hauteurs sont améliorées géométriquement, sans preuve de minimum.", chs))

    etapes.append(Etape("finale", "Solution finale", "Après fusion, tassement vertical, recentrage et tassement horizontal.",
                        palettes))
    return Resultat(palettes, non_places, params, perf_counter() - debut, info["constructions"],
                    "scip" if utilise_scip else "heuristique", "ok", etapes, journal)
