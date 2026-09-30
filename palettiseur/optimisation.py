"""Palettisation en six phases, alignée sur le notebook ``CDP_1``.

Pipeline (les numéros de phase sont ceux du notebook)
----------------------------------------------------

1. **Placement heuristique** : construction par points extrêmes, choix de la
   position par un score *lexicographique* (une priorité ne peut pas être
   compensée par les suivantes) : palette déjà ouverte avant une nouvelle,
   hausse de hauteur minimale, colis lourds bas, meilleur support, tas compact,
   centre de masse centré. Plusieurs ordres de placement sont essayés (difficulté,
   volume, masse du notebook, puis variantes) et la meilleure construction est
   conservée.
2. **Recherche SCIP** (facultative, ``moteur_scip.py``) : le placement heuristique
   sert d'amorce (*warm start*) puis SCIP tente de réduire le nombre d'emplacements.
3. **Fusion des palettes** : transferts de colis de la palette la moins remplie vers
   la plus remplie pour en vider une.
4. **Recentrage rigide et tassement** : le tas est translaté vers le centre puis chaque
   colis glisse vers le centre tant que le chargement reste valide.
5. **Minimisation des hauteurs**, palette par palette (SCIP si disponible, sinon
   reconstruction de la palette avec plusieurs stratégies).
6. **Finalisation** : dernier recentrage et tassement.

Règles du projet ajoutées au notebook
-------------------------------------

Aucun colis lourd sur un colis plus léger, rien sur un colis fragile, sens
obligatoire, séparation IATA (tableau 9.3.A) et armement, colis IATA contre une
face côté couloir, BIPAL/TRIPAL pour les colis trop longs, palettes de rampe (3 t).
Ces règles sont vérifiées par ``_charge_valide`` après chaque phase qui déplace des colis.

Repère : cm et kg ; coin minimal (x1, y1, z1) de chaque colis ; x = largeur côté
couloir, y = longueur, z = vertical.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from itertools import combinations
from operator import itemgetter
from time import perf_counter
from typing import Iterable

from .geometrie import orientations, tient_dans
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
    """Support d'au moins ``support_minimal`` de la base, sans colis plus léger ni fragile dessous."""
    if b.z1 <= TOL:
        return True
    aire = 0.0
    for o in boites:
        if o is b or o is exclu:
            continue
        if abs(o.z2 - b.z1) <= TOL:
            a = _aire_commune(o, b)
            if a > 0.0:
                if o.colis.fragile or b.colis.masse > params.facteur_masse * o.colis.masse + TOL:
                    return False
                aire += a
    return aire >= params.support_minimal * b.dx * b.dy - TOL


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
    return all(_support_ok(b, bs, params) for b in bs)


def _position_valide(ch: _Chargement, colis: Colis, x: float, y: float, z: float,
                     dx: float, dy: float, dz: float, params: Parametres) -> bool:
    """Un colis peut-il être posé en (x, y, z) sur la palette ? (masse et classes à tester à part)."""
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
                if o.colis.fragile or colis.masse > params.facteur_masse * o.colis.masse + TOL:
                    return False
                aire += (min(o.x2, x2) - max(o.x1, x)) * (min(o.y2, y2) - max(o.y1, y))
            if abs(o.z1 - z2) <= TOL and (colis.fragile or o.colis.masse > params.facteur_masse * colis.masse + TOL):
                return False        # o reposerait sur ce colis (porte-à-faux) : trop lourd ou colis fragile
    return z <= TOL or aire >= params.support_minimal * dx * dy - TOL


def _classes_compatibles(ch: _Chargement, colis: Colis, regles: ReglesSeparation) -> bool:
    code = colis.classe
    if not code:
        return True
    return all(regles.compatibles(code, c) for c in ch.classes)


# ---------------------------------------------------------------------------
# Positions candidates (points extrêmes du notebook) et meilleur choix
# ---------------------------------------------------------------------------

_PAR_Z_Y_X = itemgetter(2, 1, 0)


def _candidats(boites: list[_Boite], L: float, W: float, dx: float, dy: float,
               forcer_bord: bool = False) -> list[tuple[float, float, float]]:
    """Coins du plateau, faces voisines au sol et dessus des colis, triés par (z, y, x).

    Même ensemble que ``positions_candidates`` du notebook, en coordonnées du coin minimal.
    Pour un colis IATA, des copies contre chaque face côté couloir sont ajoutées.
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
    if forcer_bord:
        for x, y, z in list(sortie):
            ajout((0.0, y, z))
            ajout((xmax, y, z))
    return sorted({(round(min(max(x, 0.0), xmax), 4), round(min(max(y, 0.0), ymax), 4), z)
                   for x, y, z in sortie}, key=_PAR_Z_Y_X)


def _ancrages_dbl(boites: list[_Boite], L: float, W: float, dx: float, dy: float,
                  forcer_bord: bool) -> list[tuple[float, float, float]]:
    """Ancrages « coins » (coins du plateau et des colis déjà posés), sans hauteur : la chute la calcule."""
    xmax, ymax = L - dx, W - dy
    pts = {(0.0, 0.0), (xmax, 0.0), (0.0, ymax), (xmax, ymax)}
    for b in boites:
        for ax, ay in ((b.x2, b.y1), (b.x1, b.y2), (b.x1, b.y1), (b.x2, b.y2)):
            pts.add((ax, ay))
            pts.add((xmax, ay))
            pts.add((ax, ymax))
    if forcer_bord:
        for x, y in list(pts):
            pts.add((0.0, y))
            pts.add((xmax, y))
    return [(round(x, 4), round(y, 4), 0.0) for x, y in pts
            if -TOL <= x <= xmax + TOL and -TOL <= y <= ymax + TOL]


def _meilleure_position(ch: _Chargement, colis: Colis, orients: list[tuple[float, float, float]],
                        params: Parametres, regles: ReglesSeparation, strat: Strategie,
                        seuil: tuple | None, cpt: _Compteur):
    """Meilleure position du colis sur la palette, strictement meilleure que ``seuil`` (sinon None).

    Score lexicographique du notebook : (hausse de hauteur, colis lourd bas m·z, -support,
    compacité du tas, centrage du centre de masse, distance au centre). Un colis IATA
    préfère d'abord une position contre une face côté couloir.
    """
    t = ch.type
    L, W, H = t.largeur, t.longueur, t.hauteur_max
    bs = ch.boites
    m = colis.masse
    if ch.masse + m > t.masse_max + TOL or not _classes_compatibles(ch, colis, regles):
        return None
    jeu, facteur, support_min = params.jeu_contact, params.facteur_masse, params.support_minimal
    iata = params.iata_au_bord and classe_de(colis.classe).iata
    donnees = [(b.x1, b.x2, b.y1, b.y2, b.z1, b.z2, b.colis.masse, b.colis.fragile) for b in bs]
    hauteur0 = max((d[5] for d in donnees), default=0.0)
    masse0 = sum(d[6] for d in donnees)
    mx0 = sum(d[6] * (d[0] + d[1]) / 2 for d in donnees)
    my0 = sum(d[6] * (d[2] + d[3]) / 2 for d in donnees)
    if donnees:
        bxmin, bxmax = min(d[0] for d in donnees), max(d[1] for d in donnees)
        bymin, bymax = min(d[2] for d in donnees), max(d[3] for d in donnees)
    else:
        bxmin = bxmax = bymin = bymax = 0.0
    masse_tot = masse0 + m
    variante = strat.variante
    dbl = variante.startswith("dbl")
    meilleur = None
    seuil_k = seuil[0][:2] if seuil is not None else None

    for oi, (dx, dy, dz) in enumerate(orients):
        if dx > L + TOL or dy > W + TOL or dz > H + TOL:
            continue
        if variante in ("dbl", "dbl_libre"):
            candidats = _ancrages_dbl(bs, L, W, dx, dy, iata)
        else:
            candidats = _candidats(bs, L, W, dx, dy, iata)
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
            if dbl:
                primaire = z                     # variantes dbl : on minimise d'abord la hauteur de pose
            else:
                primaire = z2 - hauteur0 if z2 > hauteur0 else 0.0
            # Sans contrainte de bord, la clé croît avec z : on s'arrête dès qu'une meilleure est connue.
            if not iata:
                if meilleur is not None and primaire > meilleur[0][1] + 1e-6:
                    break
                if seuil_k is not None and primaire > seuil_k[1] + 1e-6:
                    break
            x2, y2 = x + dx, y + dy
            hors_bord = 1 if (iata and not (x <= jeu or x2 >= L - jeu)) else 0
            hausse = round(primaire, 6)
            cle = (hors_bord, hausse)
            if meilleur is not None and cle > meilleur[0][:2]:
                continue
            if seuil_k is not None and cle > seuil_k:
                continue
            # validité : recouvrement, support, règle de masse, fragilité
            ok = True
            aire = 0.0
            contact = 0.0
            for ox1, ox2, oy1, oy2, oz1, oz2, om, ofr in donnees:
                recoupe_xy = ox1 < x2 - TOL and ox2 > x + TOL and oy1 < y2 - TOL and oy2 > y + TOL
                if recoupe_xy:
                    if oz1 < z2 - TOL and oz2 > z + TOL:
                        ok = False
                        break
                    if z > TOL and abs(oz2 - z) <= TOL:
                        if ofr or m > facteur * om + TOL:
                            ok = False
                            break
                        aire += (min(ox2, x2) - max(ox1, x)) * (min(oy2, y2) - max(oy1, y))
                    if abs(oz1 - z2) <= TOL and (colis.fragile or om > facteur * m + TOL):
                        ok = False      # le colis déjà posé en o reposerait sur celui-ci (porte-à-faux) : trop lourd ou fragile
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
            mz = m * (z + dz / 2)
            compacite = ((max(bxmax, x2) - min(bxmin, x)) * (max(bymax, y2) - min(bymin, y))) / (L * W) \
                if donnees else base / (L * W)
            cmx = (mx0 + m * (x + dx / 2)) / masse_tot
            cmy = (my0 + m * (y + dy / 2)) / masse_tot
            centrage = abs(cmx - L / 2) / L + abs(cmy - W / 2) / W
            distance = abs(x + dx / 2 - L / 2) + abs(y + dy / 2 - W / 2)
            reste = (round(mz, 4), round(-support, 6), round(compacite, 6), round(centrage, 6), round(distance, 4))
            if dbl:
                pref = 0.0 if variante == "dbl_libre" else dz
                score = cle + (pref, round(y, 3), round(x, 3), round(-support, 6))
            elif variante == "plat":
                score = cle + (dz,) + reste
            elif variante == "contact":
                score = cle + (-round(contact, 3),) + reste
            else:
                score = cle + reste
            if meilleur is None or score < meilleur[0]:
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
                     rng: random.Random) -> list[tuple[Colis, TypePalette]]:
    """Ordre de placement : colis exigeant une BIPAL/TRIPAL d'abord, puis selon la stratégie."""
    surface_palette = min(t.surface for t in params.types_palettes())
    cols = [c for c, _ in a_placer]
    max_m = max(c.masse for c in cols)
    max_v = max(c.volume for c in cols)
    max_d = max(max(c.longueur, c.largeur, c.hauteur) for c in cols)

    def face(c: Colis) -> float:
        return max(c.longueur * c.largeur, c.longueur * c.hauteur, c.largeur * c.hauteur)

    def cle(item: tuple[Colis, TypePalette]):
        c, t = item
        bruit = 1.0 + strat.bruit * (rng.random() - 0.5) * 2 if strat.bruit else 1.0
        if strat.tri == "difficulte":    # formule du notebook
            k = (-(4 * c.volume / max_v + 3 * face(c) / surface_palette + 2 * c.masse / max_m
                   + max(c.longueur, c.largeur, c.hauteur) / max_d) * bruit,)
        elif strat.tri == "volume":
            k = (-c.volume * bruit, -c.masse)
        elif strat.tri == "masse":
            k = (-c.masse * bruit, -c.volume)
        elif strat.tri == "base":
            k = (-face(c) * bruit, -c.masse)
        elif strat.tri == "hauteur":
            k = (-min(c.longueur, c.largeur, c.hauteur) * bruit, -c.masse)
        elif strat.tri == "special":
            k = (0 if c.special else 1, -c.masse * bruit, -c.volume)
        else:                            # "densite"
            k = (-(c.masse / face(c)) * bruit, -c.volume)
        return (-t.positions,) + k + (c.numero,)

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
    """Comparaison lexicographique des constructions (plus petit = meilleur)."""
    positions = sum(ch.type.positions for ch in chs)
    bord, dispersion = 0, 0.0
    for ch in chs:
        L, W = ch.type.largeur, ch.type.longueur
        if params.iata_au_bord:
            bord += sum(1 for b in ch.boites
                        if b.iata and not (b.x1 <= params.jeu_contact or b.x2 >= L - params.jeu_contact))
        masse = ch.masse
        if masse > 0:
            cx = sum(b.colis.masse * (b.x1 + b.x2) / 2 for b in ch.boites) / masse
            cy = sum(b.colis.masse * (b.y1 + b.y2) / 2 for b in ch.boites) / masse
            dispersion += abs(cx - L / 2) / L + abs(cy - W / 2) / W
    return (echecs, positions, len(chs), bord, round(sum(ch.hauteur for ch in chs), 1), round(dispersion, 4))


def strategies_deterministes() -> list[Strategie]:
    """Ordre d'essai des stratégies : les trois ordres du notebook (score sur toutes les palettes),
    puis le remplissage « le plus bas, le plus au fond » palette par palette, puis les autres variantes."""
    ordres = ("difficulte", "volume", "masse", "base", "hauteur", "densite", "special")
    liste = [Strategie(t, "cdp") for t in ordres[:3]]
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
        # Les 12 premières stratégies sont déterministes (dont les trois ordres du notebook) ; ensuite on
        # alterne la liste déterministe et des variantes bruitées des 30 stratégies les plus prometteuses.
        if i < 12 or (i % 2 == 0 and (i - 12) // 2 + 12 < len(strategies)):
            strat = strategies[i if i < 12 else (i - 12) // 2 + 12]
        else:
            base = strategies[rng.randrange(min(30, len(strategies)))]
            strat = Strategie(base.tri, base.variante, base.palette, rng.choice((0.1, 0.25, 0.5)))
        i += 1
        ordre = _ordre_placement(a_placer, params, strat, rng)
        debut_c = cpt.positions
        chs, echecs = _construire(ordre, params, regles, strat, cpt)
        obj = _objectif(chs, len(echecs), params)
        if i <= 3:
            essais.append({"strategie": str(strat), "palettes": len(chs), "positions": sum(c.type.positions for c in chs),
                           "hauteur": round(sum(c.hauteur for c in chs), 1), "positions_testees": cpt.positions - debut_c})
        if meilleur_obj is None or obj < meilleur_obj:
            meilleur, meilleur_obj, sans_amelioration = (chs, echecs), obj, 0
        else:
            sans_amelioration += 1
        ecoule = perf_counter() - debut
        if ecoule >= duree and (i >= 3 or ecoule >= 4 * duree):
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


def _au_bord(x1: float, x2: float, L: float, jeu: float) -> bool:
    return x1 <= jeu or x2 >= L - jeu


def _placer_dans(dest: _Chargement, colis: Colis, params: Parametres, regles: ReglesSeparation,
                 exiger_bord: bool = False, compteur: list[int] | None = None):
    """Première position valide (la plus basse) d'un colis sur la palette, toutes orientations permises.

    Retourne (clé, x, y, z, dx, dy, dz, orientation) ou None. Un colis IATA essaie d'abord les positions
    contre une face côté couloir ; ``exiger_bord`` interdit les autres. Masse et classes sont contrôlées.
    """
    t = dest.type
    if dest.masse + colis.masse > t.masse_max + TOL or not _classes_compatibles(dest, colis, regles):
        return None
    jeu = params.jeu_contact
    iata = params.iata_au_bord and classe_de(colis.classe).iata
    meilleur = None
    for oi, (dx, dy, dz) in enumerate(orientations(colis)):
        if dx > t.largeur + TOL or dy > t.longueur + TOL or dz > t.hauteur_max + TOL:
            continue
        candidats = _candidats(dest.boites, t.largeur, t.longueur, dx, dy, iata)
        listes = [candidats]
        if iata:
            bord = [c for c in candidats if _au_bord(c[0], c[0] + dx, t.largeur, jeu)]
            listes = [bord] if exiger_bord else [bord, candidats]
        for liste in listes:
            trouve = None
            for x, y, z in liste:
                if compteur is not None:
                    compteur[0] += 1
                if _position_valide(dest, colis, x, y, z, dx, dy, dz, params):
                    trouve = (x, y, z)
                    break
            if trouve is not None:
                x, y, z = trouve
                hb = 1 if (iata and not _au_bord(x, x + dx, t.largeur, jeu)) else 0
                cle = (hb, z, abs(x + dx / 2 - t.largeur / 2) + abs(y + dy / 2 - t.longueur / 2))
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

    La translation est limitée par les bords de la palette et ne décolle pas un colis
    IATA de la face côté couloir contre laquelle il est posé.
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
                gauche, droite = b.x1 <= jeu, b.x2 >= L - jeu
                if gauche and not droite:
                    haut_x = min(haut_x, jeu - b.x1)
                elif droite and not gauche:
                    bas_x = max(bas_x, (L - jeu) - b.x2)
        tx = min(max(L / 2 - cx, bas_x), haut_x) if bas_x <= haut_x else 0.0
        ty = min(max(W / 2 - cy, bas_y), haut_y) if bas_y <= haut_y else 0.0
        for b in bs:
            b.placer(b.x1 + tx, b.y1 + ty)


def _deplacement_valide(ch: _Chargement, b: _Boite, params: Parametres) -> bool:
    bs = ch.boites
    if not _support_ok(b, bs, params):
        return False
    return all(_support_ok(c, bs, params) for c in bs if c is not b and abs(c.z1 - b.z2) <= TOL)


def _glisser(ch: _Chargement, b: _Boite, axe: str, params: Parametres, precision: float) -> float:
    """Fait glisser ``b`` vers le centre de la palette selon ``axe`` tant que le chargement reste valide."""
    L, W = ch.type.largeur, ch.type.longueur
    x_axe = axe == "x"
    centre_palette = L / 2 if x_axe else W / 2
    centre = (b.x1 + b.x2) / 2 if x_axe else (b.y1 + b.y2) / 2
    ecart = centre_palette - centre
    if abs(ecart) <= precision:
        return 0.0
    if x_axe and params.iata_au_bord and b.iata and (b.x1 <= params.jeu_contact or b.x2 >= L - params.jeu_contact):
        return 0.0      # un colis IATA reste contre sa face côté couloir
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

    def essayer(d: float) -> bool:
        if x_axe:
            b.placer(depart + sens * d, b.y1)
        else:
            b.placer(b.x1, depart + sens * d)
        return _deplacement_valide(ch, b, params)

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
# Phase 5 : minimisation des hauteurs (version heuristique)
# ---------------------------------------------------------------------------

def _delester(chs: list[_Chargement], params: Parametres, regles: ReglesSeparation, limite: float) -> dict:
    """Abaisse la palette la plus haute en déplaçant ses colis du sommet vers l'espace libre des autres.

    Un déplacement est retenu si la hauteur perdue par la palette source dépasse la hauteur gagnée par
    la palette d'arrivée (la somme des hauteurs baisse), ou s'il ne coûte aucune hauteur et loge le
    colis sous le sommet de l'arrivée. Le nombre de palettes ne change pas.
    """
    debut = perf_counter()
    avant = sum(c.hauteur for c in chs)
    deplaces = 0
    jeu = params.jeu_contact
    progression = True
    while progression and len(chs) > 1 and perf_counter() - debut < limite:
        progression = False
        for src in sorted(chs, key=lambda c: -c.hauteur):
            h_src = src.hauteur
            sommets = sorted((b for b in src.boites if b.z2 >= h_src - TOL), key=lambda b: -b.colis.masse)
            for b in sommets:
                if perf_counter() - debut >= limite:
                    break
                if not _reste_valide_sans(src, b, params):
                    continue
                h_apres = max((o.z2 for o in src.boites if o is not b), default=0.0)
                gain = h_src - h_apres
                exiger = params.iata_au_bord and b.iata and _au_bord(b.x1, b.x2, src.type.largeur, jeu)
                choix = None
                for dest in chs:
                    if dest is src:
                        continue
                    res = _placer_dans(dest, b.colis, params, regles, exiger_bord=exiger)
                    if res is None:
                        continue
                    _, x, y, z, dx, dy, dz, oi = res
                    h_dest = dest.hauteur
                    cout = max(0.0, z + dz - h_dest)
                    net = gain - cout
                    sous_sommet = z + dz < h_dest - TOL
                    if (gain > 0.01 and net > 0.01) or (cout <= TOL and sous_sommet):
                        cle = (-net, z)
                        if choix is None or cle < choix[0]:
                            choix = (cle, dest, res)
                if choix is not None:
                    _, dest, (_, x, y, z, dx, dy, dz, oi) = choix
                    dest.boites.append(_Boite(b.colis, x, y, z, dx, dy, dz, oi))
                    src.boites.remove(b)
                    deplaces += 1
                    progression = True
                    break
            if progression:
                break
        for c in [c for c in chs if not c.boites]:
            chs.remove(c)
    return {"colis_deplaces": deplaces, "avant": avant, "apres": sum(c.hauteur for c in chs),
            "duree": perf_counter() - debut}


def _reduire_hauteurs(chs: list[_Chargement], params: Parametres, regles: ReglesSeparation,
                      budget: float, rng: random.Random) -> list[dict]:
    """Reconstruit chaque palette seule avec plusieurs stratégies et garde la moins haute.

    Même idée que la minimisation des hauteurs par palette du notebook (les palettes sont
    indépendantes une fois les colis affectés), sans solveur : la reconstruction doit
    placer tous les colis sur la palette d'origine et être valide.
    """
    debut = perf_counter()
    bilans = []
    poids = [max(1, len(ch.boites) ** 2) for ch in chs]
    strategies = strategies_deterministes()
    for index, ch in enumerate(chs):
        restant = max(0.0, budget - (perf_counter() - debut))
        part = restant * poids[index] / sum(poids[index:]) if restant else 0.0
        fin = perf_counter() + part
        avant = ch.hauteur
        bilan = {"palette": index + 1, "colis": len(ch.boites), "avant": avant, "apres": avant, "amelioree": False}
        if len(ch.boites) > 1:
            a_placer = [(b.colis, ch.type) for b in ch.boites]
            cpt = _Compteur()
            meilleur = None
            for i, strat in enumerate(strategies):
                if i > 0 and perf_counter() >= fin:
                    break
                ordre = _ordre_placement(a_placer, params, strat, rng)
                res = _construire(ordre, params, regles, strat, cpt, limite_palettes=1,
                                  type_force=ch.type, zone_force=ch.zone)
                if res is None or res[1] or len(res[0]) != 1:
                    continue
                nouvelle = res[0][0]
                if meilleur is None or nouvelle.hauteur < meilleur.hauteur - 1e-6:
                    meilleur = nouvelle
            if meilleur is not None and meilleur.hauteur < avant - 0.01 and _charge_valide(meilleur, params, regles):
                ch.boites = meilleur.boites
                bilan.update(apres=meilleur.hauteur, amelioree=True)
        bilans.append(bilan)
    return bilans


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
            "hauteurs": round(sum(c.hauteur for c in chs), 1)}


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
    """Confectionne les palettes : pipeline en six phases (voir le docstring du module)."""
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
    stats = _fusionner(chs, params, regles, 0.2 * duree)
    _normaliser_zones(chs, params)
    journal.append(_entree_journal(
        3, "Fusion des palettes", stats["duree"],
        f"{stats['colis_transferes']} colis déplacé(s), {stats['fusions_reussies']} palette(s) vidée(s) "
        f"({avant_fusion} → {len(chs)}), {stats['paires_testees']}/{stats['paires_planifiees']} paire(s) traitée(s), "
        f"{stats['emplacements_testes']} emplacement(s) testés."
        + (" Limite de temps atteinte : les transferts validés sont conservés." if stats["limite_atteinte"] else ""),
        chs, **{k: stats[k] for k in ("colis_transferes", "fusions_reussies", "emplacements_testes")}))

    # ---- Phase 4 : recentrage et tassement initiaux -------------------------------------------
    t0 = perf_counter()
    _recentrer(chs, params)
    tass = _tasser(chs, params)
    journal.append(_entree_journal(
        4, "Recentrage et tassement initial", perf_counter() - t0,
        f"Chargement recentré puis tassé en {tass['nombre_passages']} passage(s), "
        f"{tass['deplacement_total']:.3f} m de déplacement cumulé.", chs))

    # ---- Phase 5 : minimisation des hauteurs ---------------------------------------------------
    t0 = perf_counter()
    desequilibre = _delester(chs, params, regles, 0.1 * duree)
    _recentrer(chs, params)
    if utilise_scip:
        from . import moteur_scip
        bilans = moteur_scip.minimiser_hauteurs_par_palette(chs, params, regles, params.temps_limite_scip)
        methode = "modèles SCIP indépendants, un par palette"
    else:
        bilans = _reduire_hauteurs(chs, params, regles, 0.2 * duree, rng)
        methode = "reconstruction de chaque palette avec plusieurs stratégies"
    gains = [b for b in bilans if b.get("amelioree")]
    detail = " ; ".join(f"palette {b['palette']} : {b['avant']:.1f} → {b['apres']:.1f} cm" for b in bilans)
    journal.append(_entree_journal(
        5, "Minimisation des hauteurs", perf_counter() - t0,
        f"Rééquilibrage : {desequilibre['colis_deplaces']} colis du sommet déplacés vers l'espace libre des autres "
        f"palettes (somme des hauteurs {desequilibre['avant']:.1f} → {desequilibre['apres']:.1f} cm). "
        f"Puis {len(gains)} palette(s) abaissée(s) par {methode}. {detail}.", chs, bilans=bilans,
        colis_deplaces=desequilibre["colis_deplaces"]))

    # ---- Phase 6 : finalisation ---------------------------------------------------------------------
    t0 = perf_counter()
    _recentrer(chs, params)
    tass = _tasser(chs, params)
    _normaliser_zones(chs, params)
    journal.append(_entree_journal(
        6, "Finalisation", perf_counter() - t0,
        f"Dernier recentrage et {tass['nombre_passages']} passage(s) de tassement "
        f"({tass['deplacement_total']:.3f} m). Solution finale : {len(chs)} palette(s), "
        f"{sum(c.type.positions for c in chs)} emplacement(s), somme des hauteurs "
        f"{sum(c.hauteur for c in chs):.1f} cm.", chs))

    palettes = _vers_palettes(chs)
    etapes.append(Etape("finale", "Solution finale", "Après fusion, recentrage, tassement et réduction des hauteurs.",
                        palettes))
    return Resultat(palettes, non_places, params, perf_counter() - debut, info["constructions"],
                    "scip" if utilise_scip else "heuristique", "ok", etapes, journal)
