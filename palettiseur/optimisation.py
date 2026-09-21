"""Heuristique de palettisation 3D multi-départ.

Principe
--------
Le problème est un *bin packing* 3D avec contraintes (masse, sens des colis,
support, matières dangereuses). Il est NP-difficile : l'application utilise
une heuristique constructive rapide, relancée avec plusieurs ordres de
placement et plusieurs règles de choix pendant une durée fixée, et conserve
la meilleure solution selon la hiérarchie des objectifs du projet :

1. aucun colis non placé, puis nombre d'emplacements (une BIPAL en occupe 2),
2. nombre de palettes,
3. colis IATA accessibles depuis le couloir,
4. somme des hauteurs des palettes,
5. recentrage des centres de masse.

Construction d'une solution
---------------------------
Les colis sont triés (colis lourds et volumineux d'abord : « base simple et
lourde en bas ») puis placés un à un. Pour chaque colis on cherche, dans les
palettes déjà ouvertes, la meilleure position parmi des *points d'ancrage*
(coins des colis déjà posés, bords de la palette). Un colis « tombe » sur le
sommet le plus haut sous son empreinte, ce qui garantit l'absence de
chevauchement ; on vérifie ensuite :

- qu'il reste dans le volume de la palette (largeur utile, longueur, hauteur) ;
- que sa base repose à au moins ``support_minimal`` (90 %) sur le sol ou sur
  d'autres colis ;
- qu'il n'est pas plus lourd que chacun des colis qui le portent
  (``facteur_masse``) et qu'aucun de ceux-ci n'est fragile ;
- que sa classe est compatible avec celles déjà présentes (tableau 9.3.A,
  armement) et que la masse maximale de la palette n'est pas dépassée.

Si aucune palette n'accepte le colis, on ouvre la plus petite palette
(10k, BIPAL, TRIPAL) où il tient. Les colis qui ne tiennent nulle part sont
retournés avec un motif (fret en vrac).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from itertools import product
from time import perf_counter
from typing import Iterable

from .geometrie import EPS, orientations, tient_dans
from .iata import ReglesSeparation, classe as classe_de, regles as regles_par_defaut
from .modeles import Colis, Palette, Parametres, Placement, Resultat, TypePalette


# ---------------------------------------------------------------------------
# Structures internes
# ---------------------------------------------------------------------------

@dataclass
class _Boite:
    """Représentation compacte d'un colis posé (pour des boucles rapides)."""
    colis: Colis
    x1: float
    y1: float
    z1: float
    x2: float
    y2: float
    z2: float
    dx: float
    dy: float
    dz: float
    orientation: int


@dataclass
class _Chargement:
    type: TypePalette
    zone: str
    boites: list[_Boite] = field(default_factory=list)
    masse: float = 0.0
    classes: set[str] = field(default_factory=set)

    def accepte_classe(self, code: str, regles: ReglesSeparation) -> bool:
        if not code:
            return True
        return all(regles.compatibles(code, c) for c in self.classes)

    def ancrages(self, dx: float, dy: float) -> set[tuple[float, float]]:
        L, W = self.type.largeur, self.type.longueur
        pts: set[tuple[float, float]] = {(0.0, 0.0), (L - dx, 0.0), (0.0, W - dy), (L - dx, W - dy)}
        for b in self.boites:
            for ax, ay in ((b.x2, b.y1), (b.x1, b.y2), (b.x1, b.y1), (b.x2, b.y2)):
                pts.add((ax, ay))
                pts.add((L - dx, ay))
                pts.add((ax, W - dy))
        return pts


@dataclass
class Strategie:
    tri: str = "masse"            # clé de tri des colis
    regle: str = "dbl"            # règle de choix de position : "dbl" ou "contact"
    orientation: str = "plat"     # "plat", "libre" ou "haut"
    choix_palette: str = "premiere"  # "premiere" (first fit) ou "meilleure" (best fit)
    bruit: float = 0.0            # perturbation aléatoire de l'ordre (0 = déterministe)

    def __str__(self) -> str:
        return f"{self.tri}/{self.regle}/{self.orientation}/{self.choix_palette}/bruit={self.bruit:.2f}"


# ---------------------------------------------------------------------------
# Tri des colis
# ---------------------------------------------------------------------------

def _cle_tri(colis: Colis, positions_requises: int, strategie: Strategie, rng: random.Random) -> tuple:
    bruit = 1.0 + strategie.bruit * (rng.random() - 0.5) * 2 if strategie.bruit else 1.0
    base_max = max(colis.longueur * colis.largeur, colis.longueur * colis.hauteur,
                   colis.largeur * colis.hauteur)
    if strategie.tri == "masse":
        cle = (-colis.masse * bruit, -colis.volume)
    elif strategie.tri == "volume":
        cle = (-colis.volume * bruit, -colis.masse)
    elif strategie.tri == "base":
        cle = (-base_max * bruit, -colis.masse)
    elif strategie.tri == "hauteur":
        cle = (-min(colis.longueur, colis.largeur, colis.hauteur) * bruit, -colis.masse)
    elif strategie.tri == "special":
        cle = (0 if colis.special else 1, -colis.masse * bruit, -colis.volume)
    else:  # "densite" : masse par unité de surface au sol
        cle = (-(colis.masse / base_max) * bruit, -colis.volume)
    # Les colis qui exigent une BIPAL/TRIPAL sont toujours placés en premier.
    return (-positions_requises,) + cle


# ---------------------------------------------------------------------------
# Recherche d'une position dans une palette
# ---------------------------------------------------------------------------

def _meilleure_position(ch: _Chargement, colis: Colis, params: Parametres,
                        strategie: Strategie, regles: ReglesSeparation):
    """Retourne (score, x, y, z, dx, dy, dz, orientation) ou None si impossible."""
    if ch.masse + colis.masse > ch.type.masse_max + EPS:
        return None
    if not ch.accepte_classe(colis.classe, regles):
        return None

    L, W, H = ch.type.largeur, ch.type.longueur, ch.type.hauteur_max
    jeu = params.jeu_contact
    support_min = params.support_minimal
    facteur = params.facteur_masse
    iata = params.iata_au_bord and classe_de(colis.classe).iata
    boites = ch.boites
    meilleur = None

    for oi, (dx, dy, dz) in enumerate(orientations(colis)):
        if dx > L + EPS or dy > W + EPS or dz > H + EPS:
            continue
        base = dx * dy
        for (x, y) in ch.ancrages(dx, dy):
            if x < -EPS or y < -EPS or x + dx > L + EPS or y + dy > W + EPS:
                continue
            x2, y2 = x + dx, y + dy
            # Hauteur d'appui : sommet le plus haut sous l'empreinte.
            z = 0.0
            for b in boites:
                if b.x1 < x2 - EPS and b.x2 > x + EPS and b.y1 < y2 - EPS and b.y2 > y + EPS:
                    if b.z2 > z:
                        z = b.z2
            if z + dz > H + EPS:
                continue
            aire_support = base
            contact_lateral = 0.0
            ok = True
            if z > EPS:
                aire_support = 0.0
                for b in boites:
                    if abs(b.z2 - z) <= jeu:
                        ox = min(b.x2, x2) - max(b.x1, x)
                        oy = min(b.y2, y2) - max(b.y1, y)
                        if ox > EPS and oy > EPS:
                            if b.colis.fragile or colis.masse > facteur * b.colis.masse + EPS:
                                ok = False
                                break
                            aire_support += ox * oy
                if not ok or aire_support < support_min * base - EPS:
                    continue
            if strategie.regle == "contact":
                z2 = z + dz
                for b in boites:
                    oz = min(b.z2, z2) - max(b.z1, z)
                    if oz <= EPS:
                        continue
                    oy = min(b.y2, y2) - max(b.y1, y)
                    ox = min(b.x2, x2) - max(b.x1, x)
                    if oy > EPS and (abs(b.x2 - x) <= jeu or abs(b.x1 - x2) <= jeu):
                        contact_lateral += oy * oz
                    if ox > EPS and (abs(b.y2 - y) <= jeu or abs(b.y1 - y2) <= jeu):
                        contact_lateral += ox * oz
                # Les parois de la palette comptent comme contact (stabilité du chargement).
                if x <= jeu or x2 >= L - jeu:
                    contact_lateral += dy * dz
                if y <= jeu or y2 >= W - jeu:
                    contact_lateral += dx * dz
            bord = 0 if (not iata or x <= jeu or x2 >= L - jeu) else 1
            if strategie.orientation == "plat":
                pref_orient = dz
            elif strategie.orientation == "haut":
                pref_orient = -dz
            else:
                pref_orient = 0.0
            if strategie.regle == "contact":
                score = (round(z, 3), bord, -round(contact_lateral + aire_support, 1),
                         pref_orient, round(y, 3), round(x, 3))
            else:  # deepest-bottom-left
                score = (round(z, 3), bord, pref_orient, round(y, 3), round(x, 3),
                         -round(aire_support, 1))
            if meilleur is None or score < meilleur[0]:
                meilleur = (score, x, y, z, dx, dy, dz, oi)
    return meilleur


# ---------------------------------------------------------------------------
# Construction d'une solution complète
# ---------------------------------------------------------------------------

def _type_requis(colis: Colis, types: list[TypePalette]) -> TypePalette | None:
    """Plus petit format de palette où le colis tient seul (dimensions et masse)."""
    for t in types:
        if colis.masse <= t.masse_max + EPS and tient_dans(colis, t.largeur, t.longueur, t.hauteur_max):
            return t
    return None


def _type_rampe(params: Parametres, base: TypePalette) -> TypePalette:
    return TypePalette("10K", "Palette 10k (rampe)", base.largeur, base.longueur,
                       min(base.hauteur_max, params.hauteur_max_rampe),
                       params.masse_nette_max_rampe, 1)


def _construire(colis_tries: list[tuple[Colis, TypePalette]], params: Parametres,
                strategie: Strategie, regles: ReglesSeparation, types: list[TypePalette]) -> list[_Chargement]:
    chargements: list[_Chargement] = []
    positions = 0
    for colis, type_min in colis_tries:
        choix = None
        for idx, ch in enumerate(chargements):
            pos = _meilleure_position(ch, colis, params, strategie, regles)
            if pos is None:
                continue
            if strategie.choix_palette == "premiere":
                choix = (idx, pos)
                break
            cle = (pos[0][0], idx)  # hauteur d'appui la plus basse, puis palette la plus ancienne
            if choix is None or cle < choix[2]:
                choix = (idx, pos, cle)
        if choix is None:
            # Ouverture d'une nouvelle palette du plus petit format possible.
            rampe = positions >= params.positions_soute
            type_ouvert = type_min
            zone = "soute"
            if rampe and type_min.positions == 1 and params.positions_rampe > 0 \
                    and positions < params.positions_soute + params.positions_rampe:
                t_rampe = _type_rampe(params, type_min)
                if colis.masse <= t_rampe.masse_max and tient_dans(colis, t_rampe.largeur, t_rampe.longueur,
                                                                   t_rampe.hauteur_max):
                    type_ouvert, zone = t_rampe, "rampe"
            ch = _Chargement(type_ouvert, zone)
            pos = _meilleure_position(ch, colis, params, strategie, regles)
            if pos is None:  # ne devrait pas arriver : type_min a été calculé pour ce colis
                continue
            chargements.append(ch)
            positions += type_ouvert.positions
            choix = (len(chargements) - 1, pos)
        idx, pos = choix[0], choix[1]
        _, x, y, z, dx, dy, dz, oi = pos
        ch = chargements[idx]
        ch.boites.append(_Boite(colis, x, y, z, x + dx, y + dy, z + dz, dx, dy, dz, oi))
        ch.masse += colis.masse
        if colis.classe:
            ch.classes.add(colis.classe)
    return chargements


# ---------------------------------------------------------------------------
# Post-traitement : recentrage rigide et conversion en Palette
# ---------------------------------------------------------------------------

def _vers_palettes(chargements: list[_Chargement], params: Parametres) -> list[Palette]:
    palettes = []
    for n, ch in enumerate(chargements, start=1):
        pal = Palette(n, ch.type, [], ch.zone)
        if not ch.boites:
            continue
        xmin = min(b.x1 for b in ch.boites)
        xmax = max(b.x2 for b in ch.boites)
        ymin = min(b.y1 for b in ch.boites)
        ymax = max(b.y2 for b in ch.boites)
        L, W = ch.type.largeur, ch.type.longueur
        # Recentrage rigide du tas : on translate tous les colis ensemble, ce qui
        # conserve les supports, les contacts et l'absence de chevauchement.
        dy = W / 2 - (ymin + ymax) / 2
        dx = L / 2 - (xmin + xmax) / 2
        bord_iata = params.iata_au_bord and any(
            classe_de(b.colis.classe).iata and (b.x1 <= params.jeu_contact or b.x2 >= L - params.jeu_contact)
            for b in ch.boites)
        if bord_iata:
            dx = 0.0  # ne pas décoller les colis IATA des faces côté couloir
        for b in ch.boites:
            pal.placements.append(Placement(b.colis, b.x1 + dx, b.y1 + dy, b.z1, b.dx, b.dy, b.dz, b.orientation))
        palettes.append(pal)
    return palettes


def _objectif(palettes: list[Palette], non_places: int, params: Parametres) -> tuple:
    positions = sum(p.type.positions for p in palettes)
    bord = 0
    dispersion = 0.0
    for p in palettes:
        L, W = p.type.largeur, p.type.longueur
        if params.iata_au_bord:
            for pl in p.placements:
                if classe_de(pl.colis.classe).iata and not (pl.x <= params.jeu_contact or pl.x2 >= L - params.jeu_contact):
                    bord += 1
        cx, cy, _ = p.centre_de_masse()
        dispersion += abs(cx - L / 2) / L + abs(cy - W / 2) / W
    hauteurs = sum(p.hauteur for p in palettes)
    return (non_places, positions, len(palettes), bord, round(hauteurs, 1), round(dispersion, 4))


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def strategies_deterministes() -> list[Strategie]:
    res = []
    for tri, regle, orient in product(("masse", "volume", "base", "hauteur", "densite", "special"),
                                      ("dbl", "contact"), ("plat", "libre")):
        res.append(Strategie(tri, regle, orient, "premiere"))
    res.append(Strategie("masse", "dbl", "plat", "meilleure"))
    res.append(Strategie("volume", "contact", "plat", "meilleure"))
    return res


def optimiser(colis: Iterable[Colis], params: Parametres | None = None,
              regles: ReglesSeparation | None = None) -> Resultat:
    """Confectionne les palettes pour la liste de colis donnée."""
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
        return Resultat([], non_places, params, perf_counter() - debut, 0, "heuristique",
                        "vide" if not non_places else "aucun colis plaçable")

    meilleur: list[Palette] | None = None
    meilleur_obj: tuple | None = None
    iterations = 0
    sans_amelioration = 0
    strategies = strategies_deterministes()
    i = 0
    while True:
        if i < len(strategies):
            strat = strategies[i]
        else:
            base = strategies[rng.randrange(len(strategies))]
            strat = Strategie(base.tri, base.regle, rng.choice(("plat", "libre", "haut")),
                              rng.choice(("premiere", "meilleure")), rng.choice((0.1, 0.25, 0.5)))
        i += 1
        tries = sorted(a_placer, key=lambda ct: _cle_tri(ct[0], ct[1].positions, strat, rng))
        chargements = _construire(tries, params, strat, regles, types)
        palettes = _vers_palettes(chargements, params)
        places = sum(len(p.placements) for p in palettes)
        obj = _objectif(palettes, len(a_placer) - places, params)
        iterations += 1
        if meilleur_obj is None or obj < meilleur_obj:
            meilleur, meilleur_obj = palettes, obj
            sans_amelioration = 0
        else:
            sans_amelioration += 1
        ecoule = perf_counter() - debut
        if ecoule >= params.duree_recherche and i >= min(len(strategies), 4):
            break
        if i >= len(strategies) and sans_amelioration >= 300:
            break

    assert meilleur is not None
    # Colis éventuellement laissés de côté par la construction (cas limite).
    places_ids = {id(pl.colis) for p in meilleur for pl in p.placements}
    for c, _ in a_placer:
        if id(c) not in places_ids:
            non_places.append((c, "aucune position compatible trouvée par l'heuristique"))
    return Resultat(meilleur, non_places, params, perf_counter() - debut, iterations, "heuristique", "ok")
