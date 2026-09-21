"""Palettiseur A400M : confection de palettes 10k sous contraintes.

Le paquet est découpé en modules indépendants :

- ``modeles``      : structures de données (colis, palettes, placements, paramètres) ;
- ``iata``         : classes de matières dangereuses et tableau de séparation 9.3.A ;
- ``geometrie``    : orientations, recouvrements, calcul du support ;
- ``optimisation`` : heuristique de palettisation multi-départ ;
- ``verification`` : contrôle indépendant du plan produit (erreurs / avertissements) ;
- ``coloriage``    : coloriage du graphe de contact (« carte ») et heat map de masse ;
- ``avion``        : configuration A400M (positions, passagers, capacité) ;
- ``fichiers``     : import CSV/JSON et export du plan ;
- ``serveur``      : petit serveur HTTP local servant l'interface et l'API JSON.
"""

__version__ = "1.0.0"
