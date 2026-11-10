"""Contrôle indépendant d'un plan de palettisation.

Ce module ne dépend pas de l'heuristique : il relit chaque palette et
vérifie toutes les règles du projet. Il produit des *erreurs* (règle absolue
violée) et des *avertissements* (règle de préférence non respectée ou point
d'attention pour le mécanicien navigant).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import combinations

from . import avion
from .dat import partition as dat_partition
from .emballages import emballages
from .geometrie import cotes_bord, cotes_visibles, en_contact, orientations, se_chevauchent, supports, taux_support
from .iata import ReglesSeparation, classe as classe_de, regles as regles_par_defaut
from .modeles import Palette, Parametres, Resultat


@dataclass
class Message:
    niveau: str            # "erreur" ou "avertissement"
    code: str
    message: str
    palette: int | None = None
    colis: list[str] | None = None

    def vers_dict(self) -> dict:
        return asdict(self)


def verifier_palette(pal: Palette, params: Parametres, regles: ReglesSeparation) -> list[Message]:
    msgs: list[Message] = []
    L, W, H = pal.type.largeur, pal.type.longueur, pal.type.hauteur_max
    jeu = params.jeu_contact
    n = pal.numero
    pls = pal.placements

    if pal.masse > pal.type.masse_max + 1e-6:
        msgs.append(Message("erreur", "masse", f"Palette {n} : masse nette {pal.masse:g} kg > maximum "
                            f"{pal.type.masse_max:g} kg.", n))
    for p in pls:
        c = p.colis
        # Orientation autorisée
        if not any(abs(p.dx - o[0]) < 1e-6 and abs(p.dy - o[1]) < 1e-6 and abs(p.dz - o[2]) < 1e-6
                   for o in orientations(c)):
            msgs.append(Message("erreur", "orientation", f"Colis {c.numero} : orientation non autorisée "
                                f"(la hauteur saisie doit rester verticale).", n, [c.numero]))
        # Dans le volume
        if p.x < -jeu or p.y < -jeu or p.z < -jeu or p.x2 > L + jeu or p.y2 > W + jeu or p.z2 > H + jeu:
            msgs.append(Message("erreur", "volume", f"Colis {c.numero} dépasse du volume de la palette {n}.",
                                n, [c.numero]))
        # Support
        ts = taux_support(p, pls, jeu)
        if ts < params.support_minimal - 1e-6:
            msgs.append(Message("erreur", "support", f"Colis {c.numero} : seulement {ts:.0%} de sa base est "
                                f"soutenue (minimum {params.support_minimal:.0%}).", n, [c.numero]))
        # Charge admissible des porteurs : lourd sur léger, fragile, carton
        for q, _ in supports(p, pls):
            if c.masse > params.facteur_masse * q.colis.masse + 1e-6:
                msgs.append(Message("erreur", "lourd_sur_leger", f"Colis {c.numero} ({c.masse:g} kg) est posé "
                                    f"sur le colis {q.colis.numero} plus léger ({q.colis.masse:g} kg).",
                                    n, [c.numero, q.colis.numero]))
            if q.colis.fragile:
                msgs.append(Message("erreur", "fragile", f"Colis {c.numero} est posé sur le colis fragile "
                                    f"{q.colis.numero}.", n, [c.numero, q.colis.numero]))
            elif params.emballage_ecrasable(q.colis.emballage) and c.masse > params.charge_max_carton + 1e-6:
                msgs.append(Message("erreur", "carton", f"Colis {c.numero} ({c.masse:g} kg) est posé sur le colis "
                                    f"{q.colis.numero} ({emballages().libelle(q.colis.emballage)}), qui ne doit pas être "
                                    f"écrasé (charge maximale {params.charge_max_carton:g} kg).",
                                    n, [c.numero, q.colis.numero]))
        # Colis à étiquette (classes IATA, armes A, pièces de sécurité B) : visible depuis un côté (règle du notebook
        # sur l'axe des côtés) ; les vraies classes IATA sont de plus posées contre ce bord.
        if classe_de(c.classe).visible:
            axe = pal.type.axe_cotes
            visibles = cotes_visibles(p, pls, axe)
            if not visibles:
                msgs.append(Message("erreur", "iata_acces", f"Colis {c.numero} (étiquette {c.classe}) est masqué des "
                                    "deux côtés par d'autres colis : étiquette non visible.", n, [c.numero]))
            elif params.iata_au_bord and classe_de(c.classe).iata and not any(
                    cote in visibles for cote in cotes_bord(p, pal.type.etendue_cotes, jeu, axe)):
                msgs.append(Message("erreur", "iata_bord", f"Colis {c.numero} (étiquette {c.classe}) n'est pas posé "
                                    "contre un bord du couloir : étiquette non accessible.", n, [c.numero]))
        # Colis isolé : aucun contact avec les autres colis (le projet l'interdit)
        if len(pls) > 1 and not any(en_contact(p, q, jeu) for q in pls if q is not p):
            msgs.append(Message("avertissement", "isole", f"Colis {c.numero} isolé : il ne touche aucun autre colis "
                                f"de la palette {n}.", n, [c.numero]))
        # Fragile au sommet (sangles)
        if c.fragile and abs(p.z2 - pal.hauteur) <= jeu and len(pls) > 1:
            msgs.append(Message("avertissement", "fragile_sommet", f"Colis fragile {c.numero} au sommet de la "
                                "palette : risque d'écrasement par les sangles / le filet.", n, [c.numero]))
    # Chevauchements
    for p, q in combinations(pls, 2):
        if se_chevauchent(p, q):
            msgs.append(Message("erreur", "chevauchement", f"Les colis {p.colis.numero} et {q.colis.numero} "
                                f"se chevauchent.", n, [p.colis.numero, q.colis.numero]))
    # Séparation IATA / armement
    notes: dict[str, list[str]] = {}
    for p, q in combinations(pls, 2):
        motif = regles.motif_incompatibilite(p.colis.classe, q.colis.classe)
        if motif:
            msgs.append(Message("erreur", "iata", f"Colis {p.colis.numero} et {q.colis.numero} : {motif}",
                                n, [p.colis.numero, q.colis.numero]))
        else:
            avert = regles.avertissement(p.colis.classe, q.colis.classe)
            if avert:
                liste = notes.setdefault(avert, [])
                for num in (p.colis.numero, q.colis.numero):
                    if num not in liste:
                        liste.append(num)
    for avert, numeros in notes.items():
        msgs.append(Message("avertissement", "iata_note", f"Palette {n}, colis {', '.join(numeros)} : {avert}",
                            n, numeros))
    # Rampe
    if pal.zone == "rampe":
        if pal.masse > params.masse_nette_max_rampe + 1e-6:
            msgs.append(Message("avertissement", "rampe_masse", f"Palette {n} prévue sur la rampe : masse nette "
                                f"{pal.masse:g} kg > {params.masse_nette_max_rampe:g} kg autorisés (3 t brut).", n))
        if pal.hauteur > params.hauteur_max_rampe + 1e-6:
            msgs.append(Message("avertissement", "rampe_hauteur", f"Palette {n} prévue sur la rampe : hauteur "
                                f"{pal.hauteur:g} cm > {params.hauteur_max_rampe:g} cm.", n))
    # Répartition du poids sur la surface (préférence)
    if pls and pal.taux_occupation_sol() < 0.5 and pal.hauteur > 0 and len(pls) > 2:
        msgs.append(Message("avertissement", "surface", f"Palette {n} : seulement {pal.taux_occupation_sol():.0%} "
                            "de la surface est utilisée au sol ; le poids est peu réparti.", n))
    return msgs


def _verifier_dat(resultat: Resultat, params: Parametres, regles: ReglesSeparation) -> list[Message]:
    """Regroupement par DAT (notebook CDP_5) : un sous-groupe compatible d'un DAT reste sur une seule palette."""
    if not params.regrouper_par_dat:
        return []
    placements = [(pal.numero, pl.colis) for pal in resultat.palettes for pl in pal.placements]
    cles = dat_partition([c for _, c in placements], regles, True)
    destinations: dict[tuple, dict[int, list[str]]] = {}
    for numero, c in placements:
        cle = cles[id(c)]
        if cle[0] == "DAT":
            destinations.setdefault(cle, {}).setdefault(numero, []).append(c.numero)
    msgs = []
    for cle, par_palette in destinations.items():
        if len(par_palette) > 1:
            colis = [numero for liste in par_palette.values() for numero in liste]
            msgs.append(Message("avertissement", "dat_reparti",
                                f"DAT {cle[1]} : réparti sur les palettes {', '.join(map(str, sorted(par_palette)))} alors "
                                "que le regroupement par DAT est demandé (le moteur rapide de l'application ne le "
                                "garantit pas).", None, colis))
    return msgs


def verifier(resultat: Resultat, regles: ReglesSeparation | None = None) -> list[Message]:
    regles = regles or regles_par_defaut()
    params = resultat.parametres
    msgs: list[Message] = []
    for pal in resultat.palettes:
        msgs.extend(verifier_palette(pal, params, regles))
    for c, motif in resultat.non_places:
        msgs.append(Message("erreur", "non_place", f"Colis {c.numero} non palettisé : {motif}.", None, [c.numero]))
    msgs.extend(_verifier_dat(resultat, params, regles))

    # Contraintes de l'avion
    bilan = avion.bilan(resultat)
    if bilan["positions_utilisees"] > bilan["positions_disponibles"]:
        msgs.append(Message("erreur", "positions", f"{bilan['positions_utilisees']} emplacements nécessaires pour "
                            f"{bilan['positions_disponibles']} disponibles avec {params.passagers} passagers."))
    if bilan["masse_totale_brute"] > params.capacite_avion:
        msgs.append(Message("erreur", "capacite", f"Charge totale {bilan['masse_totale_brute']:.0f} kg (fret + palettes"
                            f" + passagers) > capacité indicative {params.capacite_avion:.0f} kg."))
    # Palettes contenant des classes à séparer entre elles dans l'avion (information mécanicien navigant)
    for a, b in combinations(resultat.palettes, 2):
        paires = [f"{ca} / {cb}" for ca in sorted(a.classes) for cb in sorted(b.classes)
                  if regles.motif_incompatibilite(ca, cb)]
        if paires:
            msgs.append(Message("avertissement", "avion_separation",
                                f"Palettes {a.numero} et {b.numero} : à ne pas charger côte à côte dans l'avion "
                                f"(étiquettes {', '.join(paires)}).", None))
    # Dédoublonnage
    vus, uniques = set(), []
    for m in msgs:
        cle = (m.niveau, m.code, m.message)
        if cle not in vus:
            vus.add(cle)
            uniques.append(m)
    uniques.sort(key=lambda m: (0 if m.niveau == "erreur" else 1, m.palette or 0))
    return uniques
