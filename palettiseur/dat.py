"""Regroupement des colis par DAT (partition du notebook ``CDP_5``).

Les colis d'un même DAT voyagent ensemble sur une palette, sauf si leurs classes sont incompatibles : le DAT est alors
divisé en sous-groupes compatibles, par une coloration gloutonne du graphe d'incompatibilité (classes les plus
contraintes d'abord, comme ``partition_DAT`` du notebook). Chaque sous-groupe reste indivisible ; les colis sans DAT
sont indépendants. Ce module sert à la vérification indépendante du plan.
"""

from __future__ import annotations

from collections import defaultdict

from .iata import ReglesSeparation, classe_notebook


def partition(colis, regles: ReglesSeparation, regrouper: bool = True) -> dict[int, tuple]:
    """Clé de groupe de chaque colis (par ``id()`` de l'objet) : ``('colis', numero)`` pour un colis indépendant,
    ``('DAT', dat, numero_de_sous_groupe)`` sinon."""
    cles: dict[int, tuple] = {}
    par_dat: dict[str, list] = defaultdict(list)
    for c in colis:
        if not regrouper or not c.dat:
            cles[id(c)] = ("colis", c.numero)
        else:
            par_dat[c.dat].append(c)
    for dat, objets in par_dat.items():
        codes = {c.classe for c in objets}

        def incompatibles(a: str, b: str) -> bool:
            return bool(a and b and a != b and not regles.compatibles(a, b))

        # même ordre que le notebook : le plus d'incompatibilités d'abord, puis la classe du notebook en texte
        ordre = sorted(codes, key=lambda c: (-sum(incompatibles(c, autre) for autre in codes), str(classe_notebook(c))))
        groupes: list[list[str]] = []
        couleurs: dict[str, int] = {}
        for code in ordre:
            numero = next((i for i, g in enumerate(groupes) if all(not incompatibles(code, autre) for autre in g)),
                          len(groupes))
            if numero == len(groupes):
                groupes.append([])
            groupes[numero].append(code)
            couleurs[code] = numero
        for c in objets:
            cles[id(c)] = ("DAT", dat, couleurs[c.classe])
    return cles
