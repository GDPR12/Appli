"""Couleurs d'affichage : coloriage de carte et heat map de masse.

Coloriage « carte » : deux colis en contact (face commune) reçoivent des
couleurs différentes, comme deux régions frontalières sur une carte. Le
graphe de contact est colorié par l'algorithme DSatur (Brélaz, 1979) qui
choisit à chaque étape le sommet ayant le plus de couleurs voisines
distinctes. Pour une carte plane 4 couleurs suffisent ; un empilement 3D
peut en demander davantage, la palette compte donc 10 couleurs.
"""

from __future__ import annotations

from itertools import combinations

from .geometrie import en_contact
from .modeles import Palette

# Palette qualitative à fort contraste (tons distincts pour des voisins).
COULEURS_CARTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1",
                  "#edc948", "#76b7b2", "#ff9da7", "#9c755f", "#bab0ac"]


def graphe_contact(pal: Palette, jeu: float = 0.5, seulement_speciaux: bool = False) -> dict[int, set[int]]:
    """Graphe d'adjacence (indices des placements) des colis en contact."""
    indices = [i for i, p in enumerate(pal.placements) if not seulement_speciaux or p.colis.special]
    adj: dict[int, set[int]] = {i: set() for i in indices}
    for i, j in combinations(indices, 2):
        if en_contact(pal.placements[i], pal.placements[j], jeu):
            adj[i].add(j)
            adj[j].add(i)
    return adj


def dsatur(adj: dict[int, set[int]]) -> dict[int, int]:
    """Coloriage DSatur : retourne un numéro de couleur par sommet."""
    couleur: dict[int, int] = {}
    sommets = list(adj)
    while len(couleur) < len(sommets):
        def saturation(v: int) -> int:
            return len({couleur[u] for u in adj[v] if u in couleur})
        candidats = [v for v in sommets if v not in couleur]
        v = max(candidats, key=lambda s: (saturation(s), len(adj[s]), -s))
        utilisees = {couleur[u] for u in adj[v] if u in couleur}
        c = 0
        while c in utilisees:
            c += 1
        couleur[v] = c
    return couleur


def coloriage_carte(pal: Palette, jeu: float = 0.5, seulement_speciaux: bool = False) -> dict[str, int]:
    """Numéro de couleur par numéro de colis (colis en contact -> couleurs différentes)."""
    adj = graphe_contact(pal, jeu, seulement_speciaux)
    res = dsatur(adj)
    return {pal.placements[i].colis.numero: c for i, c in res.items()}


def heat_map(palettes: list[Palette]) -> dict:
    """Bornes de masse pour la heat map (valeurs, l'interpolation se fait dans l'interface)."""
    masses = [p.colis.masse for pal in palettes for p in pal.placements]
    if not masses:
        return {"min": 0, "max": 0}
    return {"min": min(masses), "max": max(masses)}
