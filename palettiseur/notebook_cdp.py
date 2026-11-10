"""Code du notebook ``CDP_5`` (heuristique, VND, recentrage et tassement), repris tel quel.

Ce module contient les fonctions du notebook sans modification de leur algorithme : règles IATA et partition par DAT
(cellules 4 et 5), heuristique à cinq essais (cellules 6 à 14), replacement plus bas (15 à 17 et 25), recentrage et
tassement horizontal (18 et 26), indicateurs (22 et 24), transferts entre palettes (27 à 34), contrôle indépendant
(35) et boucle VND (36 et 37). Les coordonnées sont celles du notebook : mètres, origine au centre de chaque palette,
centre des colis ; ``L`` (axe x) = 2,74 m, ``W`` (axe y) = 2,24 m, et les côtés « gauche » et « droite » des colis
à étiquette sont les extrémités de l'axe y. Les catégories ``A`` (armes) et ``B`` (pièces de sécurité d'armes) sont
traitées comme des classes IATA : accessibilité latérale, préférence pour les extrémités et incompatibilité A/B.

Quatre accroches ``HOOKS`` marquées « [APPLICATION] » permettent à l'application d'ajouter ses propres règles (lourd sur
léger, cartons, bord des colis IATA, classes absentes du tableau) ; elles sont inactives tant que ``HOOKS`` est vide, et
le module se comporte alors exactement comme le notebook. Les trois variables ``regrouper_par_DAT``, ``support_minimal``
et ``marge_hauteur`` sont celles de la cellule 2 du notebook. L'enchaînement des phases et les conversions vers les
structures de l'application sont plus bas, dans la partie « Adaptation ».
"""

# ruff: noqa
from copy import deepcopy
from itertools import combinations, permutations
from math import isfinite
from numbers import Real
from time import perf_counter

# Notebook CDP_5, cellule 2 : réglages (modifiés pour chaque calcul par ``notebook_cdp.Contexte``)
regrouper_par_DAT = True    # False : autoriser la répartition d'un DAT sur plusieurs palettes
support_minimal = 0.80      # fraction minimale de base soutenue
marge_hauteur = 0.05        # mètres : écart maximal de base du cinquième essai


class _Accroches:
    """Règles supplémentaires de l'application (None : comportement exact du notebook)."""
    incompatibles = None    # (a, b) -> bool, a et b étant des colis (dict) ou des classes : à ne jamais mêler
    regles = None           # (objets, palette) -> bool : règles de chargement supplémentaires


HOOKS = _Accroches()



# ======================================================================================================
# Notebook CDP_5, cellule 4 : règles et accès IATA
# ======================================================================================================


# 04. Règles et accès IATA
# Rôle : Définit les incompatibilités et les conditions d’accès latéral utilisées pendant le placement.
#Les catégories IATA "A" correspondent à "WEAPONS" et "B" correspondent à "WEAPONRY SECURITY PARTS".

INCOMPATIBILITES_IATA = {
    frozenset(("A", "B")),
    frozenset(("1", "2.1")), frozenset(("1", "2.2")),
    frozenset(("1", "2.3")), frozenset(("1", "3")),
    frozenset(("1", "4.1")), frozenset(("1", "4.2")),
    frozenset(("1", "4.3")), frozenset(("1", "5.1")),
    frozenset(("1", "5.2")), frozenset(("1", "8")),
    frozenset(("1", "9")), frozenset(("2.1", "9")),
    frozenset(("3", "5.1")), frozenset(("3", "9")),
    frozenset(("4.1", "9")), frozenset(("4.2", "5.1")),
    frozenset(("4.3", "8")), frozenset(("5.1", "9")),
}
CLASSES_IATA = {"A", "B", "1", "2.1", "2.2", "2.3", "3", "4.1", "4.2",
                "4.3", "5.1", "5.2", "8", "9"}

def classe_iata(objet):
    valeur = objet.get('iata', 0)
    if valeur in (0, None, '', '0'):
        return 0
    if isinstance(valeur, Real) and not isinstance(valeur, bool):
        return str(int(valeur)) if float(valeur).is_integer() else str(valeur)
    return str(valeur).strip().replace(',', '.')

def classes_iata_incompatibles(a, b):
    # [APPLICATION] séparation supplémentaire (classes absentes du tableau du notebook)
    if HOOKS.incompatibles is not None and HOOKS.incompatibles(a, b):
        return True
    a, b = classe_iata(a) if isinstance(a, dict) else a, classe_iata(b) if isinstance(b, dict) else b
    return bool(a and b and a != b and frozenset((a, b)) in INCOMPATIBILITES_IATA)

def nombre_incompatibilites_iata(objet):
    classe = classe_iata(objet)
    return sum(classe in paire for paire in INCOMPATIBILITES_IATA) if classe else 0

def bornes_colis_place(objet, axe):
    dimension = {'x': 'l_place', 'y': 'w_place', 'z': 'h_place'}[axe]
    return objet[axe]-objet[dimension]/2, objet[axe]+objet[dimension]/2

def recouvrement_projection_iata(a, b, axe, precision=1e-8):
    amin, amax = bornes_colis_place(a, axe)
    bmin, bmax = bornes_colis_place(b, axe)
    return min(amax, bmax)-max(amin, bmin) > precision

def cotes_accessibles_iata(objet, objets, precision=1e-8):
    """Renvoie les côtés de largeur depuis lesquels le colis reste visible."""
    if not classe_iata(objet):
        return ()
    gauche, droite = True, True
    face_gauche, face_droite = bornes_colis_place(objet, 'y')
    for autre in objets:
        if autre is objet:
            continue
        # Côte à côte en longueur ou en hauteur : aucun masquage depuis la largeur.
        if not (recouvrement_projection_iata(objet, autre, 'x', precision) and
                recouvrement_projection_iata(objet, autre, 'z', precision)):
            continue
        autre_gauche, autre_droite = bornes_colis_place(autre, 'y')
        if autre_gauche < face_gauche-precision:
            gauche = False
        if autre_droite > face_droite+precision:
            droite = False
    return tuple(cote for cote, libre in (('gauche', gauche), ('droite', droite)) if libre)

def chargement_iata_valide(objets, precision=1e-8):
    for rang, premier in enumerate(objets):
        for second in objets[rang+1:]:
            if classes_iata_incompatibles(premier, second):
                return False
    return all(not classe_iata(objet) or cotes_accessibles_iata(objet, objets, precision)
               for objet in objets)

def attribuer_cotes_iata(objets, precision=1e-8):
    """Mémorise un côté accessible pour les sorties."""
    for objet in objets:
        if not classe_iata(objet):
            objet.pop('acces_iata', None)
            continue
        cotes = cotes_accessibles_iata(objet, objets, precision)
        if not cotes:
            return False
        precedent = objet.get('acces_iata')
        objet['acces_iata'] = precedent if precedent in cotes else cotes[0]
    return True



# ======================================================================================================
# Notebook CDP_5, cellule 5 : vérification des entrées et partition par DAT
# ======================================================================================================


# 05. Vérification des entrées
# Rôle : Refuse les dimensions, identifiants ou paramètres invalides avant le calcul.

def valider_donnees(colis, palettes, temps_limite):
    if not isinstance(regrouper_par_DAT, bool):
        raise ValueError('regrouper_par_DAT doit être True ou False.')
    if any(not isinstance(c.get('DAT', ''), str) for c in colis):
        raise ValueError('DAT doit être une chaîne de caractères (vide si absent).')
    if not isinstance(support_minimal, Real) or isinstance(support_minimal, bool) or not isfinite(support_minimal) or not 0 < support_minimal <= 1:
        raise ValueError('support_minimal doit appartenir à ]0, 1].')
    if not isinstance(marge_hauteur, Real) or isinstance(marge_hauteur, bool) or not isfinite(marge_hauteur) or marge_hauteur < 0:
        raise ValueError('marge_hauteur doit être positive ou nulle, en mètres.')
    for objets, champs in ((colis, ('m', 'l', 'w', 'h')),
                           (palettes, ('M', 'L', 'W', 'H'))):
        ids = []
        for objet in objets:
            if 'id' not in objet:
                raise ValueError('Chaque colis et palette doit avoir un id.')
            ids.append(objet['id'])
            for champ in champs:
                valeur = objet.get(champ)
                if (not isinstance(valeur, Real) or isinstance(valeur, bool)
                        or not isfinite(valeur) or valeur <= 0):
                    raise ValueError(f"{objet['id']} : {champ} doit être un nombre positif fini.")
        if len(set(ids)) != len(ids):
            raise ValueError('Les identifiants doivent être uniques dans chaque liste.')
    if any(c.get('r') not in (0, 1) for c in colis):
        raise ValueError('Chaque colis doit avoir r = 0 ou r = 1.')
    classes_inconnues = sorted({classe_iata(c) for c in colis
                                if classe_iata(c) not in ({0} | CLASSES_IATA)})
    if classes_inconnues:
        raise ValueError(f'Classes IATA inconnues : {classes_inconnues}.')
    if not isinstance(temps_limite, Real) or not isfinite(temps_limite) or temps_limite <= 0:
        raise ValueError('temps_limite doit être un nombre positif fini de secondes.')

def resultat_sans_solution(statut):
    return {'statut': statut, 'optimal': False, 'nombre_palettes': None, 'palettes': []}

def partition_DAT(reference):
    """Partition stable des DAT selon le graphe d'incompatibilité.

    Coloration gloutonne des classes les plus contraintes d'abord.
    Les classes sans conflit partagent un groupe. Le nombre minimal de
    groupes n'est pas garanti pour un graphe quelconque.
    Les identifiants d'origine fixent les groupes pour toutes les phases.
    """
    par_dat = {}
    affectations = {}
    for objet in reference:
        dat = objet.get('DAT', '')
        if not regrouper_par_DAT or not dat:
            affectations[objet['id']] = ('colis', objet['id'])
        else:
            par_dat.setdefault(dat, []).append(objet)
    for dat, objets in par_dat.items():
        classes = {classe_iata(o) for o in objets}
        ordre = sorted(classes, key=lambda c: (
            -sum(classes_iata_incompatibles(c, autre) for autre in classes), str(c)))
        groupes, couleurs = [], {}
        for classe in ordre:
            numero = next((i for i, groupe in enumerate(groupes)
                           if all(not classes_iata_incompatibles(classe, autre)
                                  for autre in groupe)), len(groupes))
            if numero == len(groupes):
                groupes.append([])
            groupes[numero].append(classe)
            couleurs[classe] = numero
        for objet in objets:
            affectations[objet['id']] = ('DAT', dat, couleurs[classe_iata(objet)])
    return affectations


def groupes_DAT(objets, reference=None):
    """Conserve l'ordre de placement, avec une partition issue de toutes les entrées."""
    affectations = partition_DAT(objets if reference is None else reference)
    groupes = {}
    for objet in objets:
        groupes.setdefault(affectations[objet['id']], []).append(objet)
    return list(groupes.values())



# ======================================================================================================
# Notebook CDP_5, cellules 6 à 14 : heuristique
# ======================================================================================================


# 06. Orientations autorisées dans l’heuristique
# Rôle : Énumère les rotations possibles selon r, sans modifier les dimensions originales.

def heuristique_orientations(objet):
    dimensions = (objet['l'], objet['w'], objet['h'])
    choix = ((dimensions[0], dimensions[1], dimensions[2]),
             (dimensions[1], dimensions[0], dimensions[2])) if objet['r'] == 1 else permutations(dimensions)
    return list(dict.fromkeys(choix))


# 07. Limites géométriques des colis
# Rôle : Convertit un centre et des dimensions en bornes sur un axe.

def heuristique_bornes(objet, axe):
    taille = {'x': 'l_place', 'y': 'w_place', 'z': 'h_place'}[axe]
    return objet[axe]-objet[taille]/2, objet[axe]+objet[taille]/2


# 08. Recouvrement entre deux colis
# Rôle : Mesure leur intersection sur un axe pour les tests de collision et de support.

def heuristique_recouvrement(a, b, axe):
    amin, amax = heuristique_bornes(a, axe)
    bmin, bmax = heuristique_bornes(b, axe)
    return max(0.0, min(amax, bmax)-max(amin, bmin))


# 09. Union des surfaces de support
# Rôle : Additionne les zones couvertes sans compter deux fois leurs intersections.

def heuristique_aire_union(rectangles):
    if not rectangles:
        return 0.0
    xs = sorted({x for rectangle in rectangles for x in rectangle[:2]})
    aire = 0.0
    for gauche, droite in zip(xs, xs[1:]):
        intervalles = sorted((y0, y1) for x0, x1, y0, y1 in rectangles
                              if x0 < droite-1e-9 and x1 > gauche+1e-9)
        if not intervalles:
            continue
        bas, haut = intervalles[0]
        longueur = 0.0
        for y0, y1 in intervalles[1:]:
            if y0 <= haut:
                haut = max(haut, y1)
            else:
                longueur += haut-bas
                bas, haut = y0, y1
        longueur += haut-bas
        aire += (droite-gauche)*longueur
    return aire


# 10. Support des colis dans l’heuristique
# Rôle : Calcule la fraction de base soutenue à partir de l’union des surfaces de contact.

def heuristique_taux_support(candidat, places):
    bas = heuristique_bornes(candidat, 'z')[0]
    if bas <= 1e-7:
        return 1.0
    rectangles = []
    xmin, xmax = heuristique_bornes(candidat, 'x')
    ymin, ymax = heuristique_bornes(candidat, 'y')
    for support in places:
        if abs(heuristique_bornes(support, 'z')[1]-bas) > 1e-7:
            continue
        sx0, sx1 = heuristique_bornes(support, 'x')
        sy0, sy1 = heuristique_bornes(support, 'y')
        rectangle = (max(xmin, sx0), min(xmax, sx1),
                     max(ymin, sy0), min(ymax, sy1))
        if rectangle[1] > rectangle[0]+1e-9 and rectangle[3] > rectangle[2]+1e-9:
            rectangles.append(rectangle)
    return min(1.0, heuristique_aire_union(rectangles)/(candidat['l_place']*candidat['w_place']))


# 11. Validation d’un placement heuristique
# Rôle : Filtre masse, limites, chevauchements, support et règles IATA avant le classement des candidats.

def heuristique_placement_valide(candidat, places, palette):
    if sum(o['m'] for o in places)+candidat['m'] > palette['M']+1e-8:
        return False, 0.0
    if (heuristique_bornes(candidat, 'x')[0] < -palette['L']/2-1e-8 or
            heuristique_bornes(candidat, 'x')[1] > palette['L']/2+1e-8 or
            heuristique_bornes(candidat, 'y')[0] < -palette['W']/2-1e-8 or
            heuristique_bornes(candidat, 'y')[1] > palette['W']/2+1e-8 or
            heuristique_bornes(candidat, 'z')[0] < -1e-8 or
            heuristique_bornes(candidat, 'z')[1] > palette['H']+1e-8):
        return False, 0.0
    for autre in places:
        if all(heuristique_recouvrement(candidat, autre, axe) > 1e-8 for axe in ('x', 'y', 'z')):
            return False, 0.0
    support = heuristique_taux_support(candidat, places)
    if support < support_minimal-1e-8:
        return False, support
    # [APPLICATION] règles supplémentaires, évaluées seulement si le notebook accepte le placement
    valide = chargement_iata_valide(places+[candidat])
    if valide and HOOKS.regles is not None:
        valide = HOOKS.regles(places+[candidat], palette)
    return valide, support


# 12. Positions candidates de l’heuristique
# Rôle : Génère les coins, alignements et appuis qui seront testés pour chaque colis.

def heuristique_positions_candidates(l, w, h, places, palette):
    # Ensemble réduit de points physiques : coins du plateau, faces au sol
    # et points d'appui sur le dessus. On évite le produit de toutes les
    # coordonnées x, y et z, beaucoup trop coûteux.
    x_bords = {-palette['L']/2+l/2, 0.0, palette['L']/2-l/2}
    y_bords = {-palette['W']/2+w/2, 0.0, palette['W']/2-w/2}
    candidats = {(x, y, h/2) for x in x_bords for y in y_bords}
    for place in places:
        px0, px1 = heuristique_bornes(place, 'x')
        py0, py1 = heuristique_bornes(place, 'y')
        # Alignements contre chacune des quatre faces au niveau du sol.
        for x in (px0-l/2, px1+l/2):
            candidats.update((x, y, h/2) for y in (py0+w/2, place['y'], py1-w/2))
        for y in (py0-w/2, py1+w/2):
            candidats.update((x, y, h/2) for x in (px0+l/2, place['x'], px1-l/2))
        z = heuristique_bornes(place, 'z')[1]+h/2
        # Neuf alignements utiles au-dessus du support.
        xs_dessus = (px0+l/2, place['x'], px1-l/2)
        ys_dessus = (py0+w/2, place['y'], py1-w/2)
        candidats.update((x, y, z) for x in xs_dessus for y in ys_dessus)
    return sorted(candidats, key=lambda xyz: (xyz[2], abs(xyz[0])+abs(xyz[1])))


# 13. Hiérarchie de placement de chaque colis
# Rôle : Compare palettes, extrémités IATA, base la plus basse, hauteur et occupation du sol.

def heuristique_score_position(candidat, places, palette, nouvelle, support):
    ancienne_hauteur = max((heuristique_bornes(o, 'z')[1] for o in places), default=0.0)
    nouvelle_hauteur = max(ancienne_hauteur, heuristique_bornes(candidat, 'z')[1])
    hausse = nouvelle_hauteur-ancienne_hauteur
    tous = places+[candidat]
    largeur_tas = max(heuristique_bornes(o, 'x')[1] for o in tous)-min(heuristique_bornes(o, 'x')[0] for o in tous)
    profondeur_tas = max(heuristique_bornes(o, 'y')[1] for o in tous)-min(heuristique_bornes(o, 'y')[0] for o in tous)
    compacite = largeur_tas*profondeur_tas/(palette['L']*palette['W'])
    masse = sum(o['m'] for o in tous)
    centre_x = sum(o['m']*o['x'] for o in tous)/masse
    centre_y = sum(o['m']*o['y'] for o in tous)/masse

    # Les colis IATA sont déjà traités avant les colis ordinaires. Pour eux,
    # on préfère maintenant une face proche de l'extrémité gauche ou droite
    # de la palette. Il s'agit d'une préférence heuristique : l'accessibilité
    # reste la contrainte absolue vérifiée après chaque déplacement ultérieur.
    if classe_iata(candidat):
        face_gauche, face_droite = heuristique_bornes(candidat, 'y')
        distance_exterieur_iata = min(face_gauche + palette['W']/2,
                                       palette['W']/2 - face_droite)
    else:
        distance_exterieur_iata = 0.0

    # Surface réelle du plateau : seuls les colis posés au sol comptent.
    # L'union des bases mesure la surface couverte sans compter les trous
    # dans l'enveloppe du tas, ni la projection d'un colis déjà empilé.
    def surface_sol(objets):
        return heuristique_aire_union([(*heuristique_bornes(o, 'x'), *heuristique_bornes(o, 'y'))
                           for o in objets if heuristique_bornes(o, 'z')[0] <= 1e-7])
    gain_surface = (surface_sol(tous)-surface_sol(places))/(palette['L']*palette['W'])
    # Préserver une grande bande libre pour les prochains colis :
    # commencer au milieu peut fragmenter le plateau et empêcher des colis
    # de rentrer alors que la surface totale restante est suffisante.
    bases_sol = [o for o in tous if heuristique_bornes(o, 'z')[0] <= 1e-7]
    if bases_sol:
        xmin = min(heuristique_bornes(o, 'x')[0] for o in bases_sol)
        xmax = max(heuristique_bornes(o, 'x')[1] for o in bases_sol)
        ymin = min(heuristique_bornes(o, 'y')[0] for o in bases_sol)
        ymax = max(heuristique_bornes(o, 'y')[1] for o in bases_sol)
        bandes_libres = ((xmin+palette['L']/2)*palette['W'],
                          (palette['L']/2-xmax)*palette['W'],
                          (ymin+palette['W']/2)*palette['L'],
                          (palette['W']/2-ymax)*palette['L'])
    else:
        bandes_libres = (palette['L']*palette['W'],)
    # À surface et hauteur identiques, éviter un départ au milieu
    # d'une bande : un alignement contre le plateau préserve ses extrémités.
    x0, x1 = heuristique_bornes(candidat, 'x')
    y0, y1 = heuristique_bornes(candidat, 'y')
    distance_bords = min(x0+palette['L']/2, palette['L']/2-x1) + min(
        y0+palette['W']/2, palette['W']/2-y1)
    # Les contraintes physiques/IATA ont déjà éliminé les candidats invalides.
    # Priorités strictes : aucune amélioration secondaire ne compense une
    # base plus haute, sauf les priorités palettes et extrémités IATA.
    return (int(nouvelle), round(distance_exterieur_iata, 8),
            round(heuristique_bornes(candidat, 'z')[0], 8),
            round(nouvelle_hauteur, 8), -round(gain_surface, 8),
            -round(max(bandes_libres), 8),
            -round(sum(bande*bande for bande in bandes_libres), 8),
            round(distance_bords, 8), -support, compacite,
            abs(centre_x)/palette['L']+abs(centre_y)/palette['W'],
            abs(candidat['x'])+abs(candidat['y']))


# 14. Construction du chargement heuristique
# Rôle : Essaie les ordres de colis et conserve le meilleur placement selon les priorités existantes.

def construire_placement_heuristique(colis, palettes):
    """Construit plusieurs placements par points extrêmes et conserve le meilleur.

    Cinq essais, dont un essai de plateformes avec regard en avance borné.
    Hiérarchie : contraintes, palettes ouvertes, IATA extérieurs, base la plus
    basse, hauteur de la palette, surface au sol, espaces libres et compacité.
    """
    from copy import deepcopy

    orientations = heuristique_orientations

    bornes = heuristique_bornes

    recouvrement = heuristique_recouvrement

    aire_union = heuristique_aire_union

    taux_support = heuristique_taux_support

    placement_valide = heuristique_placement_valide

    positions_candidates = heuristique_positions_candidates

    score_position = heuristique_score_position

    maxima = {cle: max(c[cle] if cle == 'm' else c['l']*c['w']*c['h'] if cle == 'volume'
                       else max(c['l'], c['w'], c['h']) for c in colis)
              for cle in ('m', 'volume', 'dimension')}
    def difficulte(c):
        volume = c['l']*c['w']*c['h']
        surface = max(c['l']*c['w'], c['l']*c['h'], c['w']*c['h'])
        surface_palette = min(p['L']*p['W'] for p in palettes)
        return (4*volume/maxima['volume'] + 3*surface/surface_palette +
                2*c['m']/maxima['m'] + max(c['l'], c['w'], c['h'])/maxima['dimension'])

    def priorite_iata(c):
        # Tous les colis dangereux passent avant les colis ordinaires.
        return (bool(classe_iata(c)), nombre_incompatibilites_iata(c))

    ordres = [
        # Avec r=0, on privilégie l'orientation basse offrant la plus grande base.
        sorted(colis, key=lambda c: (*priorite_iata(c),
               max(l*w for l, w, h in orientations(c)), c['m']), reverse=True),
        sorted(colis, key=lambda c: (*priorite_iata(c), difficulte(c)), reverse=True),
        sorted(colis, key=lambda c: (*priorite_iata(c), c['l']*c['w']*c['h']), reverse=True),
        sorted(colis, key=lambda c: (*priorite_iata(c), c['m']), reverse=True),
    ]
    # Cinquième essai : couches partielles de hauteurs proches, sans imposer
    # une couche uniforme à toute la palette. Les IATA restent prioritaires.
    def groupe_hauteur(objet):
        hauteur = min(h for l, w, h in orientations(objet))
        return round(hauteur/max(marge_hauteur, 0.01))
    ordres.append(sorted(colis, key=lambda c: (
        *priorite_iata(c), -groupe_hauteur(c),
        max(l*w for l, w, h in orientations(c)), c['m']), reverse=True))

    def potentiel_plateforme(candidat, places, palette, restants):
        # Petit regard en avance : essayer de poser jusqu'à trois grands colis
        # restants sur le niveau créé, simultanément, sans chevauchement.
        # Les appuis doivent être exactement coplanaires ; la marge n'autorise
        # jamais de faux contacts entre des faces à des hauteurs différentes.
        niveau = bornes(candidat, 'z')[1]
        simulation = places+[candidat]
        prochains = sorted(restants, key=lambda o: max(
            l*w for l, w, h in orientations(o)), reverse=True)[:3]
        nombre, surface = 0, 0.0
        for futur in prochains:
            retenu = None
            for l, w, h in orientations(futur):
                for x, y, z in positions_candidates(l, w, h, simulation, palette):
                    if abs(z-h/2-niveau) > 1e-7:
                        continue
                    proposition = dict(id=futur['id'], DAT=futur.get('DAT', ''), m=futur['m'], iata=classe_iata(futur),
                        x=x, y=y, z=z, l_place=l, w_place=w, h_place=h)
                    valide, _ = placement_valide(proposition, simulation, palette)
                    if valide:
                        retenu = proposition
                        break
                if retenu is not None:
                    break
            if retenu is not None:
                simulation.append(retenu)
                nombre += 1
                surface += retenu['l_place']*retenu['w_place']
        return nombre, surface

    meilleurs, essais = None, []
    debut = perf_counter()
    for numero, ordre in enumerate(ordres, 1):
        charges = []
        positions_testees = 0
        echec = None
        def placer_sequence(sequence, charges, indice_force=None):
            nonlocal positions_testees
            echec = None
            for rang_objet, objet in enumerate(sequence):
                meilleur = None
                candidats_valides = []
                # Les palettes déjà ouvertes sont testées, puis une seule nouvelle palette.
                indices = list(range(len(charges)))
                if len(charges) < len(palettes):
                    indices.append(len(charges))
                if indice_force is not None:
                    indices = [indice_force]
                for p in indices:
                    palette = palettes[p]
                    places = charges[p]['colis'] if p < len(charges) else []
                    for l, w, h in orientations(objet):
                        for x, y, z in positions_candidates(l, w, h, places, palette):
                            positions_testees += 1
                            candidat = {'id': objet['id'], 'DAT': objet.get('DAT', ''), 'm': objet['m'], 'iata': classe_iata(objet),
                                         'x': x, 'y': y, 'z': z,
                                         'l_place': l, 'w_place': w, 'h_place': h}
                            valide, support = placement_valide(candidat, places, palette)
                            if not valide:
                                continue
                            score = score_position(candidat, places, palette, p == len(charges), support)
                            choix = (score, p, candidat)
                            if numero == 5:
                                candidats_valides.append(choix)
                            if meilleur is None or choix[0] < meilleur[0]:
                                meilleur = choix
                if numero == 5 and candidats_valides:
                    # Garder les deux priorités absolues de choix (palette et bord IATA),
                    # puis borner la base à minimum + marge. Seul ce cinquième essai
                    # peut préférer une base légèrement plus haute.
                    priorite = min(choix[0][:2] for choix in candidats_valides)
                    groupe = [choix for choix in candidats_valides if choix[0][:2] == priorite]
                    base_min = min(bornes(choix[2], 'z')[0] for choix in groupe)
                    proches = [choix for choix in groupe
                               if bornes(choix[2], 'z')[0] <= base_min+marge_hauteur+1e-8]
                    # Regard en avance borné : diversifier les hauteurs avant de
                    # compléter jusqu'à huit candidats pour limiter le surcoût.
                    proches.sort(key=lambda choix: choix[0])
                    selection, niveaux = [], set()
                    for choix in proches:
                        cle = (choix[1], round(bornes(choix[2], 'z')[0], 7),
                               round(choix[2]['h_place'], 7))
                        if cle not in niveaux and len(selection) < 8:
                            selection.append(choix)
                            niveaux.add(cle)
                    for choix in proches:
                        if len(selection) >= 8:
                            break
                        if not any(choix is retenu for retenu in selection):
                            selection.append(choix)
                    meilleur = min(selection, key=lambda choix: (
                        tuple(-v for v in potentiel_plateforme(
                            choix[2], charges[choix[1]]['colis'] if choix[1] < len(charges) else [],
                            palettes[choix[1]], sequence[rang_objet+1:])),
                        choix[0]))
                if meilleur is None:
                    echec = objet['id']
                    break
                _, p, candidat = meilleur
                if p == len(charges):
                    charges.append({'id': palettes[p]['id'], 'colis': []})
                charges[p]['colis'].append(candidat)
                attribuer_cotes_iata(charges[p]['colis'])
            return echec

        if not regrouper_par_DAT:
            echec = placer_sequence(ordre, charges)
        else:
            # Chaque groupe est essayé en entier sur une copie de chaque palette.
            # Un essai incomplet est abandonné : aucun sous-groupe compatible n'est engagé partiellement.
            for groupe_dat in groupes_DAT(ordre):
                meilleur_groupe = None
                indices = list(range(len(charges)))
                if len(charges) < len(palettes):
                    indices.append(len(charges))
                for p in indices:
                    simulation = deepcopy(charges)
                    if placer_sequence(groupe_dat, simulation, p) is not None:
                        continue
                    objets = simulation[p]['colis']
                    hauteur = max(bornes(o, 'z')[1] for o in objets)
                    surface = aire_union([(*bornes(o, 'x'), *bornes(o, 'y'))
                        for o in objets if bornes(o, 'z')[0] <= 1e-7])
                    cle_groupe = (p == len(charges), hauteur, -surface, p)
                    if meilleur_groupe is None or cle_groupe < meilleur_groupe[0]:
                        meilleur_groupe = (cle_groupe, simulation)
                if meilleur_groupe is None:
                    echec = groupe_dat[0]['id']
                    break
                charges = meilleur_groupe[1]
        if echec is not None:
            essais.append({'ordre': numero, 'plateformes': numero == 5, 'echec_colis': echec, 'positions': positions_testees})
            continue
        for charge in charges:
            objets = charge['colis']
            masse = sum(o['m'] for o in objets)
            charge.update(masse_totale=masse,
                          hauteur_utilisee=max(bornes(o, 'z')[1] for o in objets),
                          centre_masse_x=sum(o['m']*o['x'] for o in objets)/masse,
                          centre_masse_y=sum(o['m']*o['y'] for o in objets)/masse)
        resultat = {'statut': 'heuristique', 'optimal': False, 'optimal_nombre': False,
                    'optimal_hauteur': False, 'nombre_palettes': len(charges),
                    'palettes': charges, 'somme_hauteurs': sum(c['hauteur_utilisee'] for c in charges)}
        surface_utilisee = sum(aire_union([(*bornes(o, 'x'), *bornes(o, 'y'))
            for o in charge['colis'] if bornes(o, 'z')[0] <= 1e-7]) for charge in charges)
        hauteur_globale = max(c['hauteur_utilisee'] for c in charges)
        cle = (len(charges), hauteur_globale, -surface_utilisee, resultat['somme_hauteurs'],
               sum(abs(c['centre_masse_x'])+abs(c['centre_masse_y']) for c in charges))
        essais.append({'ordre': numero, 'plateformes': numero == 5, 'palettes': len(charges),
                       'hauteur': resultat['somme_hauteurs'], 'positions': positions_testees})
        if meilleurs is None or cle < meilleurs[0]:
            meilleurs = (cle, deepcopy(resultat), numero)
    if meilleurs is None:
        raise RuntimeError(
            "Aucun placement heuristique trouvé avec les palettes disponibles"
            + (" en gardant chaque groupe compatible de DAT sur une seule palette" if regrouper_par_DAT else "")
            + ". Cela ne prouve pas l’impossibilité géométrique ; aucun groupe n’a été scindé.")
    resultat = meilleurs[1]
    resultat['heuristique'] = {'ordre_retenu': meilleurs[2], 'duree': perf_counter()-debut, 'essais': essais,
                                'positions_testees': sum(e['positions'] for e in essais)}
    return resultat



# ======================================================================================================
# Notebook CDP_5, cellules 15 à 17 : replacement
# ======================================================================================================


# 15. Orientations permises pour le replacement
# Rôle : Respecte r et les dimensions originales du colis.

def abaissement_orientations(objet, donnees):
    original = donnees.get(objet['id'])
    if original is None:
        # Sans donnée r, conserver l'orientation plutôt qu'autoriser une rotation inconnue.
        return [(objet['l_place'], objet['w_place'], objet['h_place'])]
    l, w, h = original['l'], original['w'], original['h']
    choix = [(l, w, h), (w, l, h)] if original['r'] == 1 else permutations((l, w, h))
    return sorted(set(choix), key=lambda d: (d[2], -d[0]*d[1], d[0], d[1]))


# 16. Emplacements latéraux pour le replacement
# Rôle : Combine les alignements avec les bords et les faces des colis.

def abaissement_positions_xy(l, w, objets, palette, ancien, precision):
    # Toutes les combinaisons des alignements avec les bords et faces :
    # elles permettent de rejoindre les trous au sol et les appuis voisins.
    xs = {-palette['L']/2+l/2, palette['L']/2-l/2, 0.0, ancien['x']}
    ys = {-palette['W']/2+w/2, palette['W']/2-w/2, 0.0, ancien['y']}
    for autre in objets:
        for face in bornes_colis_place(autre, 'x'):
            xs.update((face-l/2, face+l/2))
        for face in bornes_colis_place(autre, 'y'):
            ys.update((face-w/2, face+w/2))
    xs = sorted(x for x in xs if -palette['L']/2-precision <= x-l/2
                and x+l/2 <= palette['L']/2+precision)
    ys = sorted(y for y in ys if -palette['W']/2-precision <= y-w/2
                and y+w/2 <= palette['W']/2+precision)
    # Favoriser les petits déplacements entre emplacements d'égale hauteur.
    return sorted(((x, y) for x in xs for y in ys),
                  key=lambda xy: (abs(xy[0]-ancien['x'])+abs(xy[1]-ancien['y']), xy))


# 17. Comparaison des replacements
# Rôle : Classe les solutions par hauteur, surface utilisée et sommets des colis.

def abaissement_qualite(objets, palette, precision):
    # Objectifs lexicographiques ; l'union au sol ne compte pas les trous.
    sommets = tuple(sorted((round(o['z']+o['h_place']/2, 7)
                            for o in objets), reverse=True))
    bases = [o for o in objets if o['z']-o['h_place']/2 <= precision]
    surface = aire_union_rectangles([(*bornes_colis_place(o, 'x'),
                                      *bornes_colis_place(o, 'y')) for o in bases])
    bandes = []
    if bases:
        for axe, taille, largeur in (('x', palette['L'], palette['W']),
                                     ('y', palette['W'], palette['L'])):
            bas = min(bornes_colis_place(o, axe)[0] for o in bases)
            haut = max(bornes_colis_place(o, axe)[1] for o in bases)
            bandes.extend(((bas+taille/2)*largeur, (taille/2-haut)*largeur))
    # Les bandes libres favorisent les espaces utilisables pour la suite.
    return (sommets[0] if sommets else 0.0, -round(surface, 7), sommets,
            -round(sum(b*b for b in bandes), 7))



# ======================================================================================================
# Notebook CDP_5, cellules 18 à 26 : recentrage, validation, indicateurs, tassements
# ======================================================================================================


# 18. Translation du tas vers le centre
# Rôle : Déplace tout le chargement d’une palette ensemble pour conserver les contacts entre colis.

def recentrer_tas_rigidement(resultat, palettes):
    """Translate chaque tas vers le centre sans changer ses contacts."""
    caracteristiques = {palette['id']: palette for palette in palettes}
    for charge in resultat.get('palettes', []):
        objets = charge['colis']
        if not objets:
            continue
        palette = caracteristiques[charge['id']]
        masse = sum(objet['m'] for objet in objets)
        centre_x = sum(objet['m']*objet['x'] for objet in objets)/masse
        centre_y = sum(objet['m']*objet['y'] for objet in objets)/masse
        gauche = max(-palette['L']/2-objet['x']+objet['l_place']/2 for objet in objets)
        droite = min(palette['L']/2-objet['x']-objet['l_place']/2 for objet in objets)
        bas = max(-palette['W']/2-objet['y']+objet['w_place']/2 for objet in objets)
        haut = min(palette['W']/2-objet['y']-objet['w_place']/2 for objet in objets)
        translation_x = min(max(-centre_x, gauche), droite)
        translation_y = min(max(-centre_y, bas), haut)
        for objet in objets:
            objet['x'] += translation_x
            objet['y'] += translation_y
        charge['centre_masse_x'] = centre_x + translation_x
        charge['centre_masse_y'] = centre_y + translation_y
    return resultat


# 19. Surface d’une union de rectangles
# Rôle : Calcule une aire réelle de contact ou d’occupation, sans double comptage.

def aire_union_rectangles(rectangles):
    """Mesure les appuis et l'occupation du sol sans double comptage."""
    xs = sorted({x for rectangle in rectangles for x in rectangle[:2]})
    aire = 0.0
    for gauche, droite in zip(xs, xs[1:]):
        intervalles = sorted((y0, y1) for x0, x1, y0, y1 in rectangles
                             if x0 < droite and x1 > gauche)
        if not intervalles:
            continue
        bas, haut = intervalles[0]
        longueur = 0.0
        for y0, y1 in intervalles[1:]:
            if y0 <= haut:
                haut = max(haut, y1)
            else:
                longueur += haut-bas
                bas, haut = y0, y1
        aire += (droite-gauche)*(longueur+haut-bas)
    return aire


# 20. Intersection des colis placés
# Rôle : Mesure le recouvrement sur un axe pour les contrôles internes de déplacement.

def recouvrement_place(a, b, axe):
    amin, amax = bornes_colis_place(a, axe)
    bmin, bmax = bornes_colis_place(b, axe)
    return max(0.0, min(amax, bmax)-max(amin, bmin))


# 21. Validation interne des déplacements
# Rôle : Vérifie les mouvements proposés ; le contrôle indépendant reste une vérification séparée.

def chargement_physique_valide(objets, palette, tolerance=1e-7):
    """Vérifie tous les colis : limites, collisions, appuis au seuil configuré et IATA."""
    if sum(o['m'] for o in objets) > palette['M']+tolerance:
        return False
    for objet in objets:
        for axe, mini, maxi in (('x', -palette['L']/2, palette['L']/2),
                                ('y', -palette['W']/2, palette['W']/2),
                                ('z', 0.0, palette['H'])):
            bas, haut = bornes_colis_place(objet, axe)
            if bas < mini-tolerance or haut > maxi+tolerance:
                return False
    for premier, second in combinations(objets, 2):
        if all(recouvrement_place(premier, second, axe) > tolerance
               for axe in ('x', 'y', 'z')):
            return False
    for objet in objets:
        bas = bornes_colis_place(objet, 'z')[0]
        if bas <= tolerance:
            continue
        rectangles = []
        x0, x1 = bornes_colis_place(objet, 'x')
        y0, y1 = bornes_colis_place(objet, 'y')
        for support in objets:
            if support is objet or abs(bornes_colis_place(support, 'z')[1]-bas) > tolerance:
                continue
            sx0, sx1 = bornes_colis_place(support, 'x')
            sy0, sy1 = bornes_colis_place(support, 'y')
            rectangle = (max(x0, sx0), min(x1, sx1), max(y0, sy0), min(y1, sy1))
            if rectangle[1] > rectangle[0] and rectangle[3] > rectangle[2]:
                rectangles.append(rectangle)
        if aire_union_rectangles(rectangles)+1e-8 < support_minimal*objet['l_place']*objet['w_place']:
            return False
    # [APPLICATION] règles supplémentaires, évaluées seulement si le notebook accepte le chargement
    if not chargement_iata_valide(objets, tolerance):
        return False
    return HOOKS.regles is None or HOOKS.regles(objets, palette)


# 22. Calcul des indicateurs de qualité
# Rôle : Mesure hauteurs, compacité, remplissage volumique et décalage du centre de masse.

def calculer_indicateurs(resultat, palettes):
    """Mesures descriptives, sans effet sur le placement ou ses priorités.

    Compacité = volume des colis / volume de leur boîte englobante.
    Remplissage = volume des colis / (L * W * H) de la palette.
    Centre de masse : cargaison seule, mesuré depuis le centre du plateau.
    """
    caracteristiques = {p['id']: p for p in palettes}
    mesures = []
    for charge in resultat.get('palettes', []):
        objets = charge['colis']
        if not objets:
            continue
        p = caracteristiques[charge['id']]
        volume = sum(o['l_place']*o['w_place']*o['h_place'] for o in objets)
        enveloppe = 1.0
        for axe in ('x', 'y', 'z'):
            mini = min(bornes_colis_place(o, axe)[0] for o in objets)
            maxi = max(bornes_colis_place(o, axe)[1] for o in objets)
            enveloppe *= maxi-mini
        masse = sum(o['m'] for o in objets)
        cx = sum(o['m']*o['x'] for o in objets)/masse
        cy = sum(o['m']*o['y'] for o in objets)/masse
        mesures.append({
            'palette': charge['id'],
            'hauteur': max(bornes_colis_place(o, 'z')[1] for o in objets),
            'compacite': volume/enveloppe if enveloppe > 0 else 0.0,
            'remplissage_volumique': volume/(p['L']*p['W']*p['H']),
            'volume_colis': volume, 'volume_enveloppe': enveloppe,
            'centre_masse_x': cx, 'centre_masse_y': cy,
            'decalage_horizontal': (cx*cx+cy*cy)**0.5})
    return {
        'nombre_palettes': len(mesures),
        'hauteur_maximale': max((m['hauteur'] for m in mesures), default=0.0),
        'somme_hauteurs': sum(m['hauteur'] for m in mesures),
        'palettes': mesures}


# 24. Actualisation des mesures du chargement
# Rôle : Met à jour masses, hauteurs, surfaces et centres après un déplacement.

def actualiser_chargement(resultat, palettes):
    """Actualise les hauteurs, surfaces occupées et centres après déplacement."""
    caracteristiques = {p['id']: p for p in palettes}
    for charge in resultat.get('palettes', []):
        objets = charge['colis']
        palette = caracteristiques[charge['id']]
        masse = sum(o['m'] for o in objets)
        charge['masse_totale'] = masse
        charge['hauteur_utilisee'] = max((o['z']+o['h_place']/2 for o in objets), default=0.0)
        charge['centre_masse_x'] = sum(o['m']*o['x'] for o in objets)/masse if masse else 0.0
        charge['centre_masse_y'] = sum(o['m']*o['y'] for o in objets)/masse if masse else 0.0
        charge['surface_sol'] = aire_union_rectangles([
            (*bornes_colis_place(o, 'x'), *bornes_colis_place(o, 'y'))
            for o in objets if bornes_colis_place(o, 'z')[0] <= 1e-7])
        charge['taux_surface_sol'] = charge['surface_sol']/(palette['L']*palette['W'])
        attribuer_cotes_iata(objets)
    resultat['somme_hauteurs'] = sum(p['hauteur_utilisee'] for p in resultat.get('palettes', []))
    resultat['hauteur_globale'] = max((p['hauteur_utilisee']
                                     for p in resultat.get('palettes', [])), default=0.0)
    resultat['indicateurs'] = calculer_indicateurs(resultat, palettes)
    return resultat


# 25. Recherche de replacements plus bas
# Rôle : Compare les positions et orientations sur chaque palette ; respecte les appuis, les règles IATA et le budget.

def tasser_colis_verticalement(resultat, palettes, precision=1e-7,
                                donnees_colis=None, temps_limite=None, partager_budget=True):
    """Replace les colis plus bas, avec changement possible de x/y et orientation.

    Recherche de dispositions finales, pas d'une trajectoire de manutention.
    Les affectations restent fixes. On compare la hauteur de la palette, la
    surface au sol et les sommets triés. Le premier placement valide ne suffit plus.
    """
    debut = perf_counter()
    actualiser_chargement(resultat, palettes)
    hauteur_avant = resultat['hauteur_globale']
    somme_avant = resultat['somme_hauteurs']
    donnees = {o['id']: o for o in (donnees_colis or [])}
    caracteristiques = {p['id']: p for p in palettes}
    mouvements = essais = passages = supports_bloquants = 0
    comparaisons_valides = 0
    bilans_avant = {c['id']: (c['hauteur_utilisee'], c['surface_sol'])
                   for c in resultat.get('palettes', [])}
    deplacement = lateral = 0.0
    modifies = set()
    limite_atteinte = False
    recherches_interrompues = 0

    def expire():
        return temps_limite is not None and perf_counter()-debut >= temps_limite

    def orientations(objet):
        return abaissement_orientations(objet, donnees)

    def positions_xy(l, w, objets, palette, ancien):
        return abaissement_positions_xy(l, w, objets, palette, ancien, precision)

    def qualite(objets, palette):
        return abaissement_qualite(objets, palette, precision)

    for charge in resultat.get('palettes', []):
        objets, palette = charge['colis'], caracteristiques[charge['id']]
        # Plusieurs passages permettent de retirer un support après les colis
        # qu'il portait. Nombre borné pour garder une recherche légère.
        for passage in range(max(1, len(objets)**2)):
            mouvement_passage = False
            for objet in sorted(objets, key=lambda o: (-o['z']-o['h_place']/2, o['id'])):
                if expire():
                    limite_atteinte = True
                    break
                ancien = dict(objet)
                bas, sommet = bornes_colis_place(objet, 'z')
                # Même au sol, une rotation r=0 peut abaisser un colis haut.
                fin_colis = (perf_counter()+max(0.0, temps_limite-(perf_counter()-debut))/
                             max(1, len(objets))) if temps_limite is not None and partager_budget else float('inf')
                restants = [o for o in objets if o is not objet]
                # Le retrait ne doit pas laisser d'autres colis sans appui.
                if not chargement_physique_valide(restants, palette, precision):
                    supports_bloquants += 1
                    continue
                niveaux = sorted({0.0} | {bornes_colis_place(o, 'z')[1] for o in restants
                                         if bornes_colis_place(o, 'z')[1] <= bas+precision})
                choix = None
                qualite_depart = qualite(objets, palette)
                meilleure_qualite = qualite_depart
                grilles = {}
                for niveau in niveaux:
                    for l, w, h in orientations(objet):
                        if niveau+h > sommet+precision:
                            continue
                        if (l, w) not in grilles:
                            grilles[l, w] = positions_xy(l, w, restants, palette, ancien)
                        for x, y in grilles[l, w]:
                            if expire() or perf_counter() >= fin_colis:
                                limite_atteinte = expire()
                                break
                            essais += 1
                            candidat = dict(ancien, x=x, y=y, z=niveau+h/2,
                                            l_place=l, w_place=w, h_place=h)
                            # Filtre rapide avant la validation complète des appuis/IATA.
                            if any(all(recouvrement_place(candidat, autre, axe) > precision
                                       for axe in ('x', 'y', 'z')) for autre in restants):
                                continue
                            evaluation = qualite(restants+[candidat], palette)
                            # Il faut améliorer un objectif réel, pas seulement déplacer
                            # des colis horizontalement ; cela évite les cycles.
                            if evaluation[:3] >= qualite_depart[:3] or evaluation >= meilleure_qualite:
                                continue
                            if chargement_physique_valide(restants+[candidat], palette, precision):
                                comparaisons_valides += 1
                                choix = candidat
                                meilleure_qualite = evaluation
                        if limite_atteinte or perf_counter() >= fin_colis:
                            break
                    if limite_atteinte or perf_counter() >= fin_colis:
                        break
                if perf_counter() >= fin_colis and not limite_atteinte:
                    recherches_interrompues += 1
                if choix is not None:
                    objet.update(choix)
                    deplacement += bas-bornes_colis_place(objet, 'z')[0]
                    lateral += ((objet['x']-ancien['x'])**2+(objet['y']-ancien['y'])**2)**0.5
                    mouvements += 1
                    modifies.add(objet['id'])
                    mouvement_passage = True
                if limite_atteinte or mouvement_passage:
                    # Après un mouvement, recalculer immédiatement les colis les plus hauts.
                    break
            passages += 1
            if limite_atteinte or not mouvement_passage:
                break
        if limite_atteinte:
            break
    actualiser_chargement(resultat, palettes)
    resultat['tassement_vertical'] = {
        'duree': perf_counter()-debut, 'deplacement_total': deplacement,
        'deplacement_lateral': lateral, 'emplacements_testes': essais,
        'colis_deplaces': len(modifies), 'supports_bloquants': supports_bloquants,
        'limite_atteinte': limite_atteinte,
        'recherches_interrompues': recherches_interrompues,
        'ameliorations_comparees': comparaisons_valides,
        'bilans_palettes': [
            {'palette': c['id'], 'hauteur_avant': bilans_avant[c['id']][0],
             'hauteur_apres': c['hauteur_utilisee'],
             'surface_avant': bilans_avant[c['id']][1], 'surface_apres': c['surface_sol']}
            for c in resultat.get('palettes', [])],
        'mouvements': mouvements, 'nombre_passages': passages,
        'hauteur_avant': hauteur_avant, 'hauteur_apres': resultat['hauteur_globale'],
        'gain_somme_hauteurs': somme_avant-resultat['somme_hauteurs']}
    return resultat


# 26. Tassement horizontal final
# Rôle : Rapproche les colis du centre après la VND, puis recentre le tas en conservant ses contraintes.

def tasser_colis_vers_centre(resultat, palettes, pas=0.01, precision=0.001):
    """Fait glisser chaque colis vers le centre tant que le chargement reste valide."""

    def chargement_valide(objets, palette):
        # La précision du glissement (1 mm) n'est pas une autorisation de collision.
        return chargement_physique_valide(objets, palette)

    def glisser(objet, axe, objets, palette):
        depart = objet[axe]
        if abs(depart) <= precision:
            return 0.0
        direction = -1.0 if depart > 0 else 1.0
        derniere_position_valide = depart
        while abs(derniere_position_valide) > precision:
            candidate = derniere_position_valide + direction*min(pas, abs(derniere_position_valide))
            objet[axe] = candidate
            if chargement_valide(objets, palette):
                derniere_position_valide = candidate
                continue

            # Recherche au millimètre près du premier point de blocage.
            invalide, valide = candidate, derniere_position_valide
            objet[axe] = valide
            while abs(invalide-valide) > precision:
                milieu = (invalide+valide)/2
                objet[axe] = milieu
                if chargement_valide(objets, palette):
                    valide = milieu
                else:
                    invalide = milieu
            objet[axe] = valide
            break
        return abs(depart-objet[axe])

    deplacement_total, passages_total = 0.0, 0
    correspondance = {p['id']: p for p in palettes}
    for charge in resultat.get('palettes', []):
        palette, objets = correspondance[charge['id']], charge['colis']
        # Les colis lourds sont placés en premier ; plusieurs passages permettent
        # de profiter des espaces libérés par les déplacements précédents.
        for passage in range(10):
            mouvement_passage = 0.0
            for objet in sorted(objets, key=lambda c: (-c['m'], c['id'])):
                mouvement_passage += glisser(objet, 'x', objets, palette)
                mouvement_passage += glisser(objet, 'y', objets, palette)
            passages_total += 1
            deplacement_total += mouvement_passage
            if mouvement_passage <= precision:
                break

    for charge in resultat.get('palettes', []):
        attribuer_cotes_iata(charge['colis'], precision)

    # Le tassement peut déplacer le centre de masse : translation finale.
    recentrer_tas_rigidement(resultat, palettes)

    actualiser_chargement(resultat, palettes)
    resultat['tassement_local'] = {'deplacement_total': deplacement_total,
                                   'nombre_passages': passages_total,
                                   'precision': precision}
    return resultat



# ======================================================================================================
# Notebook CDP_5, cellules 27 à 34 : transferts entre palettes
# ======================================================================================================


# 27. Bornes géométriques utilisées par la fusion
# Rôle : Détermine les faces de chaque colis selon x, y ou z.

def fusion_limites(objet, axe):
    longueur = {'x': 'l_place', 'y': 'w_place', 'z': 'h_place'}[axe]
    return objet[axe]-objet[longueur]/2, objet[axe]+objet[longueur]/2


# 28. Intersections utilisées par la fusion
# Rôle : Mesure les recouvrements nécessaires aux contrôles de collision et d’appui.

def fusion_recouvrement(a, b, axe):
    amin, amax = fusion_limites(a, axe)
    bmin, bmax = fusion_limites(b, axe)
    return max(0.0, min(amax, bmax)-max(amin, bmin))


# 29. Validation interne d’un transfert
# Rôle : Vérifie que les palettes source et receveuse restent physiquement valides et compatibles IATA.

def fusion_valide(objets, palette, precision):
    if sum(objet['m'] for objet in objets) > palette['M']+precision:
        return False
    if sum(objet['l_place']*objet['w_place']*objet['h_place'] for objet in objets) > (
            palette['L']*palette['W']*palette['H']+precision):
        return False
    for objet in objets:
        xmin, xmax = fusion_limites(objet, 'x')
        ymin, ymax = fusion_limites(objet, 'y')
        zmin, zmax = fusion_limites(objet, 'z')
        if (xmin < -palette['L']/2-precision or xmax > palette['L']/2+precision or
                ymin < -palette['W']/2-precision or ymax > palette['W']/2+precision or
                zmin < -precision or zmax > palette['H']+precision):
            return False
    for index, premier in enumerate(objets):
        for second in objets[index+1:]:
            if all(fusion_recouvrement(premier, second, axe) > precision for axe in ('x','y','z')):
                return False
    for objet in objets:
        bas = fusion_limites(objet, 'z')[0]
        if bas <= precision:
            continue
        aire = sum(fusion_recouvrement(objet, support, 'x')*fusion_recouvrement(objet, support, 'y')
                   for support in objets if support is not objet and
                   abs(fusion_limites(support, 'z')[1]-bas) <= precision)
        if aire+precision < support_minimal*objet['l_place']*objet['w_place']:
            return False
    # [APPLICATION] règles supplémentaires, évaluées seulement si le notebook accepte le chargement
    if not chargement_iata_valide(objets, precision):
        return False
    return HOOKS.regles is None or HOOKS.regles(objets, palette)


# 30. Positions candidates pour les transferts
# Rôle : Cherche des emplacements près des bords, des colis voisins et sur leurs faces supérieures.

def fusion_positions(objet, places, palette):
    # Points physiques seulement : coins du plateau, faces voisines et dessus.
    l, w, h = objet['l_place'], objet['w_place'], objet['h_place']
    xs = (-palette['L']/2+l/2, 0.0, palette['L']/2-l/2)
    ys = (-palette['W']/2+w/2, 0.0, palette['W']/2-w/2)
    candidats = {(x, y, h/2) for x in xs for y in ys}
    for place in places:
        px0, px1 = fusion_limites(place, 'x')
        py0, py1 = fusion_limites(place, 'y')
        for x in (px0-l/2, px1+l/2):
            candidats.update((x, y, h/2) for y in (py0+w/2, place['y'], py1-w/2))
        for y in (py0-w/2, py1+w/2):
            candidats.update((x, y, h/2) for x in (px0+l/2, place['x'], px1-l/2))
        z = fusion_limites(place, 'z')[1]+h/2
        candidats.update((x, y, z)
                          for x in (px0+l/2, place['x'], px1-l/2)
                          for y in (py0+w/2, place['y'], py1-w/2))
    return sorted(candidats, key=lambda xyz: (xyz[2], abs(xyz[0])+abs(xyz[1])))


# 31. Volume chargé sur une palette
# Rôle : Additionne le volume des colis afin de comparer l’occupation des palettes.

def fusion_volume(charge):
    return sum(c['l_place']*c['w_place']*c['h_place'] for c in charge['colis'])


# 32. Mesures après un transfert
# Rôle : Actualise la masse, la hauteur et le centre de masse de la palette modifiée.

def fusion_mettre_a_jour(charge):
    objets = charge['colis']
    if not objets:
        charge['masse_totale'] = 0
        charge['hauteur_utilisee'] = 0
        return
    masse = sum(c['m'] for c in objets)
    charge['masse_totale'] = masse
    charge['hauteur_utilisee'] = max(fusion_limites(c, 'z')[1] for c in objets)
    charge['centre_masse_x'] = sum(c['m']*c['x'] for c in objets)/masse
    charge['centre_masse_y'] = sum(c['m']*c['y'] for c in objets)/masse


# 33. Identification des palettes équivalentes
# Rôle : Conserve une numérotation cohérente des palettes aux caractéristiques identiques.

def normaliser_palettes_identiques(resultat, palettes):
    """Renomme les palettes identiques utilisées dans leur ordre initial."""
    groupes = {}
    indices = {palette['id']: rang for rang, palette in enumerate(palettes)}
    for palette in palettes:
        signature = tuple(palette[cle] for cle in ('M', 'L', 'W', 'H'))
        groupes.setdefault(signature, []).append(palette['id'])
    for ids in groupes.values():
        charges = [charge for charge in resultat['palettes'] if charge['id'] in ids]
        charges.sort(key=lambda charge: indices[charge['id']])
        for charge, nouvel_id in zip(charges, ids):
            charge['id'] = nouvel_id
    return resultat


# 34. Recherche de transferts entre palettes
# Rôle : Répartit le temps entre les paires et construit une proposition de fusion pour la VND.

def fusionner_palettes(resultat, palettes, temps_limite, precision=1e-6, donnees_colis=None):
    """Transfère les colis sans dépasser le budget de temps indiqué."""
    from copy import deepcopy
    reference_dat = (donnees_colis if donnees_colis is not None else
                     [o for charge in resultat["palettes"] for o in charge["colis"]])
    debut_fusion = perf_counter()
    temps_limite = max(0.0, float(temps_limite))

    caracteristiques = {palette['id']: palette for palette in palettes}
    compteurs = {'paires_testees': 0, 'fusions_reussies': 0,
                 'colis_transferes': 0, 'emplacements_testes': 0,
                 'paires_planifiees': 0, 'tours_complets': 0,
                 'limite_secondes': temps_limite, 'limite_atteinte': False,
                 'methode': 'transferts partiels conservés, placement glouton'}

    def temps_ecoule():
        depasse = perf_counter()-debut_fusion >= temps_limite
        compteurs['limite_atteinte'] |= depasse
        return depasse

    limites = fusion_limites

    recouvrement = fusion_recouvrement

    def valide(objets, palette):
        return fusion_valide(objets, palette, precision)

    positions = fusion_positions

    volume = fusion_volume

    def occupation(charge):
        # Comparaison lexicographique, sans score pondéré : volume puis masse.
        palette = caracteristiques[charge['id']]
        taux_volume = volume(charge)/(palette['L']*palette['W']*palette['H'])
        taux_masse = charge['masse_totale']/palette['M']
        return taux_volume, taux_masse

    def repartir_paire(premiere, seconde):
        # La plus remplie reçoit ; la moins remplie est celle que l'on tente de vider.
        return ((premiere, seconde) if occupation(premiere) >= occupation(seconde)
                else (seconde, premiere))

    mettre_a_jour = fusion_mettre_a_jour

    def essayer_transferts(receveuse, source, fin_paire):
        palette_receveuse = caracteristiques[receveuse['id']]
        palette_source = caracteristiques[source['id']]
        transferts = 0
        def budget_paire_ecoule():
            return temps_ecoule() or perf_counter() >= fin_paire
        while not budget_paire_ecoule():
            mouvement = False
            # Les plus grands colis sont tentés d'abord. Un nouveau passage
            # permet de retirer un colis bas après ceux qu'il soutenait.
            objets = sorted(source['colis'],
                            key=lambda c: (c['l_place']*c['w_place']*c['h_place'],
                                           limites(c, 'z')[0]), reverse=True)
            for groupe in groupes_DAT(objets, reference_dat):
                if budget_paire_ecoule():
                    return transferts
                ids = {o['id'] for o in groupe}
                restants = [c for c in source['colis'] if c['id'] not in ids]
                # Retirer tout le groupe ne doit pas priver les autres colis d'appui.
                if not valide(restants, palette_source):
                    continue
                if receveuse['masse_totale']+sum(o['m'] for o in groupe) > palette_receveuse['M']+precision:
                    continue
                # Transaction : seules les simulations complètes sont conservées.
                # Placer les supports bas avant les colis qui pouvaient reposer dessus.
                simulation = deepcopy(receveuse['colis'])
                ordre_groupe = sorted(groupe, key=lambda c: (
                    limites(c, 'z')[0], -c['l_place']*c['w_place']))
                complet = True
                for original in ordre_groupe:
                    retenu = None
                    for x, y, z in positions(original, simulation, palette_receveuse):
                        if budget_paire_ecoule():
                            return transferts
                        candidat = deepcopy(original)
                        candidat.update(x=x, y=y, z=z)
                        compteurs['emplacements_testes'] += 1
                        if valide(simulation+[candidat], palette_receveuse):
                            retenu = candidat
                            break
                    if retenu is None:
                        complet = False
                        break
                    simulation.append(retenu)
                if not complet:
                    continue
                receveuse['colis'] = simulation
                source['colis'] = restants
                attribuer_cotes_iata(receveuse['colis'], precision)
                attribuer_cotes_iata(source['colis'], precision)
                mettre_a_jour(receveuse)
                mettre_a_jour(source)
                compteurs['colis_transferes'] += len(groupe)
                transferts += len(groupe)
                mouvement = True
            if not mouvement:
                return transferts
        return transferts

    # À chaque tour, le temps restant est partagé entre toutes les paires.
    # Aucune paire n'accapare donc le budget au détriment des suivantes.
    while len(resultat['palettes']) > 1 and not temps_ecoule():
        charges = resultat['palettes']
        paires = list(combinations(charges, 2))
        # Priorité absolue à la source la moins remplie ; à source comparable,
        # la receveuse la plus remplie est traitée d'abord. Les autres paires
        # restent présentes plus loin dans ce même tour.
        paires.sort(key=lambda pair: (occupation(repartir_paire(*pair)[1]),
                                       tuple(-x for x in occupation(repartir_paire(*pair)[0]))))
        compteurs['paires_planifiees'] += len(paires)
        progression = False
        for rang, (premiere, seconde) in enumerate(paires):
            if temps_ecoule():
                break
            paires_restantes = len(paires)-rang
            temps_restant = max(0.0, temps_limite-(perf_counter()-debut_fusion))
            fin_paire = perf_counter()+temps_restant/paires_restantes
            # Le plus grand volume de colis détermine la receveuse.
            # En cas d'égalité, l'ordre des palettes tranche.
            destination, source = repartir_paire(premiere, seconde)
            compteurs['paires_testees'] += 1
            deplaces = essayer_transferts(destination, source, fin_paire)
            progression |= deplaces > 0
        vides = [charge for charge in charges if not charge['colis']]
        for charge in vides:
            charges.remove(charge)
        compteurs['fusions_reussies'] += len(vides)
        if compteurs['paires_testees'] >= compteurs['paires_planifiees']:
            compteurs['tours_complets'] += 1
        if not progression:
            break

    compteurs['duree'] = perf_counter()-debut_fusion
    resultat['fusion_palettes'] = compteurs
    resultat['nombre_palettes'] = len(resultat['palettes'])
    resultat['somme_hauteurs'] = sum(p['hauteur_utilisee'] for p in resultat['palettes'])
    if compteurs['colis_transferes']:
        # Les hauteurs seront actualisées après les tassements et le recentrage.
        resultat['optimal_hauteur'] = False
        if compteurs['fusions_reussies']:
            resultat['optimal_nombre'] = resultat['nombre_palettes'] == 1
        resultat['optimal'] = False
    return resultat



# ======================================================================================================
# Notebook CDP_5, cellule 35 : contrôle indépendant
# ======================================================================================================


# 35. Contrôle indépendant du chargement
# Rôle : Recalcule les contraintes depuis les entrées et les coordonnées, indépendamment des méthodes de placement.

def controler_chargement_independant(resultat, colis, palettes, tolerance=1e-6):
    from collections import Counter
    debut = perf_counter()
    erreurs = []
    entrees = {c['id']: c for c in colis}
    plateaux = {p['id']: p for p in palettes}
    vus, palettes_vues = [], []
    mesures = []

    def classe(v):
        if v in (None, '', 0, '0'):
            return 0
        texte = str(v).strip().replace(',', '.')
        try:
            nombre = float(texte)
            return str(int(nombre)) if nombre.is_integer() else texte
        except ValueError:
            return texte

    def union(rectangles):
        # Intégration par bandes disjointes, indépendante des calculs de placement.
        bornes = sorted({v for r in rectangles for v in r[:2]})
        total = 0.0
        for a, b in zip(bornes, bornes[1:]):
            segments = sorted((r[2], r[3]) for r in rectangles if r[0] < b and r[1] > a)
            fin = None
            longueur = 0.0
            for bas, haut in segments:
                longueur += max(0.0, haut-max(bas, fin if fin is not None else bas))
                fin = haut if fin is None else max(fin, haut)
            total += (b-a)*longueur
        return total

    for charge in resultat.get('palettes', []):
        pid = charge.get('id')
        palettes_vues.append(pid)
        if pid not in plateaux:
            erreurs.append(f"Palette inconnue : {pid}.")
            vus.extend(o.get('id') for o in charge.get('colis', []))
            continue
        p = plateaux[pid]
        geometries = []
        masse = 0.0
        for objet in charge.get('colis', []):
            ident = objet.get('id')
            vus.append(ident)
            if ident not in entrees:
                erreurs.append(f"Colis inconnu : {ident}.")
                continue
            original = entrees[ident]
            masse += original['m']
            champs = ('x', 'y', 'z', 'l_place', 'w_place', 'h_place', 'm')
            if any(not isinstance(objet.get(k), Real) or isinstance(objet.get(k), bool)
                   or not isfinite(objet[k]) for k in champs):
                erreurs.append(f"Colis {ident} : coordonnées/dimensions/masse invalides.")
                continue
            dims = tuple(objet[k] for k in ('l_place', 'w_place', 'h_place'))
            l, w, h = original['l'], original['w'], original['h']
            orientations = [(l,w,h),(w,l,h)] if original['r'] == 1 else list(permutations((l,w,h)))
            if not any(all(abs(x-y) <= tolerance for x,y in zip(dims,d)) for d in orientations):
                erreurs.append(f"Colis {ident} : dimensions ou orientation interdites par r={original['r']}.")
            if objet.get('DAT', '') != original.get('DAT', ''):
                erreurs.append(f"Colis {ident} : DAT différent de l'entrée.")
            if abs(objet['m']-original['m']) > tolerance:
                erreurs.append(f"Colis {ident} : masse différente de l'entrée.")
            if classe(objet.get('iata', 0)) != classe(original.get('iata', 0)):
                erreurs.append(f"Colis {ident} : classe IATA différente de l'entrée.")
            bornes = tuple((objet[axe]-taille/2, objet[axe]+taille/2)
                           for axe, taille in zip(('x','y','z'), dims))
            limites = ((-p['L']/2,p['L']/2),(-p['W']/2,p['W']/2),(0,p['H']))
            for axe, (bas,haut), (mini,maxi) in zip('xyz', bornes, limites):
                if bas < mini-tolerance or haut > maxi+tolerance:
                    erreurs.append(f"Colis {ident}, palette {pid} : dépassement en {axe}, [{bas:.6f}, {haut:.6f}].")
            geometries.append((ident, bornes, dims, classe(original.get('iata',0))))
        if masse > p['M']+tolerance:
            erreurs.append(f"Palette {pid} : surcharge de {masse-p['M']:.6f} kg.")
        for i, (ident, bornes, dims, danger) in enumerate(geometries):
            rects = []
            gauche = droite = True
            for j, (autre, autres_bornes, _, autre_danger) in enumerate(geometries):
                if i == j:
                    continue
                chev = [min(a[1],b[1])-max(a[0],b[0]) for a,b in zip(bornes,autres_bornes)]
                if j > i:
                    if all(v > tolerance for v in chev):
                        erreurs.append(f"Palette {pid} : collision entre {ident} et {autre}.")
                    if danger and autre_danger and frozenset((danger,autre_danger)) in INCOMPATIBILITES_IATA:
                        erreurs.append(f"Palette {pid} : colis {ident}/{autre} incompatibles IATA.")
                if chev[0] > tolerance and chev[2] > tolerance:
                    if autres_bornes[1][0] < bornes[1][0]-tolerance:
                        gauche = False
                    if autres_bornes[1][1] > bornes[1][1]+tolerance:
                        droite = False
                if abs(autres_bornes[2][1]-bornes[2][0]) <= tolerance and chev[0] > 0 and chev[1] > 0:
                    rects.append((max(bornes[0][0],autres_bornes[0][0]),
                                  min(bornes[0][1],autres_bornes[0][1]),
                                  max(bornes[1][0],autres_bornes[1][0]),
                                  min(bornes[1][1],autres_bornes[1][1])))
            if dims[0] > 0 and dims[1] > 0 and bornes[2][0] > tolerance:
                taux = union(rects)/(dims[0]*dims[1])
                if taux < support_minimal-1e-7:
                    erreurs.append(f"Colis {ident} : support {100*taux:.4f} %, minimum {100*support_minimal:g} %.")
            if danger and not (gauche or droite):
                erreurs.append(f"Colis {ident} : aucun accès IATA libre.")
        hauteur = max((g[1][2][1] for g in geometries), default=0.0)
        surface = union([(*g[1][0],*g[1][1]) for g in geometries if g[1][2][0] <= tolerance])
        mesures.append({'id':pid,'masse':masse,'hauteur':hauteur,'surface_sol':surface,
                        'taux_surface_sol':surface/(p['L']*p['W'])})
        for champ, attendu in (('masse_totale',masse),('hauteur_utilisee',hauteur),
                               ('surface_sol',surface),('taux_surface_sol',surface/(p['L']*p['W']))):
            if champ in charge and (not isinstance(charge[champ], Real) or
                                    not isfinite(charge[champ]) or abs(charge[champ]-attendu)>1e-5):
                erreurs.append(f"Palette {pid} : indicateur {champ} incohérent, attendu {attendu:.6f}.")
    # Les groupes attendus sont définis depuis les entrées complètes.
    # Les incompatibilités sont aussi revérifiées directement plus haut.
    if regrouper_par_DAT:
        groupes_attendus = partition_DAT(colis)
        destinations = {}
        for charge in resultat.get('palettes', []):
            for objet in charge.get('colis', []):
                ident = objet.get('id')
                if ident in groupes_attendus:
                    cle = groupes_attendus[ident]
                    destinations.setdefault(cle, set()).add(charge.get('id'))
        for cle, palettes_dat in destinations.items():
            if cle[0] == 'DAT' and len(palettes_dat) > 1:
                erreurs.append(
                    f"DAT {cle[1]}, groupe compatible {cle[2]+1} : "
                    f"réparti sur {len(palettes_dat)} palettes ; regroupement obligatoire.")
    comptes = Counter(vus)
    erreurs.extend(f"Colis {i} : {comptes[i]} occurrence(s), attendu 1." for i in entrees if comptes[i]!=1)
    erreurs.extend(f"Palette {i} répétée." for i, n in Counter(palettes_vues).items() if n>1)
    for champ, attendu in (('nombre_palettes',len(palettes_vues)),
                           ('somme_hauteurs',sum(m['hauteur'] for m in mesures)),
                           ('hauteur_globale',max((m['hauteur'] for m in mesures),default=0.0))):
        if champ in resultat and (not isinstance(resultat[champ], Real) or
                                 not isfinite(resultat[champ]) or abs(resultat[champ]-attendu)>1e-5):
            erreurs.append(f"Indicateur {champ} incohérent, attendu {attendu}.")
    return {'valide':not erreurs, 'erreurs':erreurs, 'mesures':mesures,
            'duree':perf_counter()-debut}



# ======================================================================================================
# Notebook CDP_5, cellules 36 et 37 : VND
# ======================================================================================================


# 36. Hiérarchie commune de la VND
# Rôle : Définit le score lexicographique utilisé pour accepter ou rejeter une proposition.

def score_vnd(resultat, palettes):
    """Plus petit = meilleur. Aucune somme pondérée ne mélange les priorités."""
    indicateurs = calculer_indicateurs(resultat, palettes)
    charges = resultat.get('palettes', [])
    sommets = tuple(sorted((round(o['z']+o['h_place']/2, 6)
                           for p in charges for o in p['colis']), reverse=True))
    surface = sum(p.get('surface_sol', 0.0) for p in charges)
    enveloppe = sum(p['volume_enveloppe'] for p in indicateurs['palettes'])
    volume = sum(p['volume_colis'] for p in indicateurs['palettes'])
    # Même occupation que l'ancienne fusion : volume relatif, puis masse relative.
    # Trier du moins rempli au plus rempli valorise chaque transfert partiel
    # vers une palette déjà plus remplie, sans attendre qu'une palette soit vide.
    capacites = {p['id']: p for p in palettes}
    concentration = tuple(sorted((
        round(sum(o['l_place']*o['w_place']*o['h_place'] for o in charge['colis'])/
              (capacites[charge['id']]['L']*capacites[charge['id']]['W']*capacites[charge['id']]['H']), 8),
        round(sum(o['m'] for o in charge['colis'])/capacites[charge['id']]['M'], 8)
    ) for charge in charges if charge['colis']))
    return (indicateurs['nombre_palettes'], concentration,
            round(indicateurs['hauteur_maximale'], 6),
            round(indicateurs['somme_hauteurs'], 6), -round(surface, 6),
            sommets, -round(volume/enveloppe, 6) if enveloppe else 0.0)


# 37. Boucle VND et budget commun
# Rôle : Essaie fusion, replacement puis échange ; toute amélioration validée relance le premier voisinage.

def ameliorer_vnd(resultat, colis, palettes, temps_limite, afficher_logs=False):
    """VND bornée : chaque amélioration strictement valide relance la fusion.

    Les propositions sont isolées sur une copie. La fusion et le replacement
    utilisent leurs recherches existantes ; l'échange teste les emplacements
    de deux colis, en conservant leurs orientations et les niveaux d'appui.
    Une fin de recherche bornée ne prouve pas un optimum local exhaustif.
    """
    from copy import deepcopy
    debut = perf_counter()
    fin = debut+temps_limite
    groupes_reference = partition_DAT(colis)
    tailles_groupes = {}
    for cle in groupes_reference.values():
        tailles_groupes[cle] = tailles_groupes.get(cle, 0)+1
    courant = deepcopy(resultat)
    actualiser_chargement(courant, palettes)
    initial = controler_chargement_independant(courant, colis, palettes)
    if not initial['valide']:
        raise ValueError("Entrée VND invalide : " + "; ".join(initial['erreurs']))
    noms = ('fusion', 'replacement', 'echange')
    statistiques = {nom: {'appels':0, 'secondes':0.0, 'candidats':0,
                          'acceptations':0, 'rejets_controle':0, 'rejets_objectif':0} for nom in noms}
    historique = []
    anomalies = []
    controles_secondes = initial['duree']
    score_initial = score_vnd(courant, palettes)
    k = 0
    approfondissement = False
    recherche_elargie = False
    while (k < len(noms) or approfondissement) and perf_counter() < fin:
        if k == len(noms):
            # Les trois voisins ont eu leur tour. Consacrer le reliquat aux
            # placements tronqués par le petit quota par colis, sans le réimposer.
            k = 1
            approfondissement = False
            recherche_elargie = True
        nom = noms[k]
        stats = statistiques[nom]
        stats['appels'] += 1
        depart = perf_counter()
        # Réserver du temps aux voisins suivants, notamment les échanges.
        budget = max(0.0, (fin-depart)/(1 if recherche_elargie else len(noms)-k))
        fin_voisin = min(fin, depart+budget)
        ancien_score = score_vnd(courant, palettes)
        accepte = None
        proposition = deepcopy(courant)

        def examiner(candidate):
            nonlocal controles_secondes
            actualiser_chargement(candidate, palettes)
            evaluation = score_vnd(candidate, palettes)
            if evaluation >= ancien_score:
                stats['rejets_objectif'] += 1
                return None
            controle = controler_chargement_independant(candidate, colis, palettes)
            controles_secondes += controle['duree']
            if not controle['valide']:
                stats['rejets_controle'] += 1
                anomalies.append({'voisinage':nom, 'erreurs':controle['erreurs']})
                return None
            return evaluation

        if nom == 'fusion':
            fusionner_palettes(proposition, palettes, budget, donnees_colis=colis)
            normaliser_palettes_identiques(proposition, palettes)
            stats['candidats'] += proposition.get('fusion_palettes', {}).get('emplacements_testes', 0)
            evaluation = examiner(proposition)
            if evaluation is not None:
                accepte = (proposition, evaluation)
        elif nom == 'replacement':
            tasser_colis_verticalement(proposition, palettes, donnees_colis=colis,
                                       temps_limite=budget, partager_budget=not recherche_elargie)
            approfondissement |= (not recherche_elargie and
                proposition.get('tassement_vertical', {}).get('recherches_interrompues', 0) > 0)
            stats['candidats'] += proposition.get('tassement_vertical', {}).get('emplacements_testes', 0)
            evaluation = examiner(proposition)
            if evaluation is not None:
                accepte = (proposition, evaluation)
        else:
            # Échange simultané : les deux colis sont retirés puis replacés
            # avant tout contrôle, pour ne pas rejeter un retrait temporaire.
            positions = [(p, i) for p, charge in enumerate(courant['palettes'])
                         for i in range(len(charge['colis']))]
            positions.sort(key=lambda pi: -(
                courant['palettes'][pi[0]]['colis'][pi[1]]['z'] +
                courant['palettes'][pi[0]]['colis'][pi[1]]['h_place']/2))
            for (pa, ia), (pb, ib) in combinations(positions, 2):
                if perf_counter() >= fin_voisin:
                    break
                stats['candidats'] += 1
                proposition = deepcopy(courant)
                a = proposition['palettes'][pa]['colis'][ia]
                b = proposition['palettes'][pb]['colis'][ib]
                if regrouper_par_DAT and pa != pb:
                    # Un échange ne peut scinder un sous-groupe compatible de DAT.
                    # Les groupes multiples changent de palette via la fusion atomique.
                    def groupe_multiple(objet, charge):
                        return tailles_groupes[groupes_reference[objet['id']]] > 1
                    if (groupe_multiple(a, proposition['palettes'][pa]) or
                            groupe_multiple(b, proposition['palettes'][pb])):
                        continue
                ax, ay, az = a['x'], a['y'], a['z']-a['h_place']/2
                bx, by, bz = b['x'], b['y'], b['z']-b['h_place']/2
                a.update(x=bx, y=by, z=bz+a['h_place']/2)
                b.update(x=ax, y=ay, z=az+b['h_place']/2)
                proposition['palettes'][pa]['colis'][ia] = b
                proposition['palettes'][pb]['colis'][ib] = a
                # Vérifier les limites, masses et appuis des palettes touchées.
                valides = True
                for index in {pa, pb}:
                    charge = proposition['palettes'][index]
                    plateau = next(p for p in palettes if p['id'] == charge['id'])
                    if not chargement_physique_valide(charge['colis'], plateau):
                        valides = False
                        break
                if not valides:
                    continue
                evaluation = examiner(proposition)
                if evaluation is not None:
                    accepte = (proposition, evaluation)
                    break
        stats['secondes'] += perf_counter()-depart
        if accepte is not None:
            courant, nouveau_score = accepte
            stats['acceptations'] += 1
            historique.append({'voisinage':nom, 'avant':ancien_score,
                               'apres':nouveau_score, 'temps_ecoule':perf_counter()-debut,
                               'transferts':deepcopy(courant.get('fusion_palettes', {})) if nom == 'fusion' else {}})
            if afficher_logs:
                print("VND :", nom, "accepté ; retour à la fusion.")
            k = 0
            recherche_elargie = False
        else:
            k += 1
    courant['vnd'] = {
        'duree':perf_counter()-debut, 'limite_secondes':temps_limite,
        'limite_atteinte':perf_counter() >= fin,
        'arret': 'budget atteint' if perf_counter() >= fin else 'aucune amélioration dans les recherches effectuées',
        'voisinages':statistiques, 'historique':historique, 'anomalies':anomalies,
        'controles_secondes':controles_secondes,
        'score_initial':score_initial, 'score_final':score_vnd(courant, palettes)}
    resultat.clear()
    resultat.update(courant)
    return resultat



recentrer_tas_rigidement_notebook = recentrer_tas_rigidement

# ======================================================================================================
# Adaptation à l'application (ne fait pas partie du notebook)
# ======================================================================================================
#
# Le notebook travaille en mètres, avec l'origine au centre de chaque palette et le centre des colis, et ne connaît que
# des palettes 10k. L'application travaille en centimètres avec le coin minimal des colis. Cette partie convertit
# d'un repère à l'autre, enchaîne les phases dans l'ordre de ``optimiser_palettes`` (cellule 38 : heuristique, VND,
# recentrage, tassement horizontal, avec contrôle indépendant après chaque phase et restauration de la dernière
# solution valide) et branche les règles propres à l'application sur les accroches ``HOOKS``.

import threading
from contextlib import contextmanager

LIMITE_COLIS = 150          # au-delà, le code du notebook (non optimisé) est trop lent : l'application prend le relais
_VERROU = threading.Lock()  # ``HOOKS`` et les réglages sont globaux au module : un seul calcul à la fois


def _niveau(z: float) -> int:
    return round(z * 1e6)


from .iata import classe_application, classe_notebook   # noqa: E402


class Contexte:
    """Problème de l'application mis dans la forme attendue par le notebook, et conversions dans les deux sens."""

    def __init__(self, a_placer, params, regles):
        from .iata import classe as classe_de
        from .geometrie import plafond_charge
        from .optimisation import _type_rampe
        self.params, self.regles = params, regles
        self.transpose = params.axe_cotes == "x"     # le notebook voit les côtés sur y : on échange x et y sinon
        self.base = params.types_palettes()[0]
        self.colis = [c for c, _ in a_placer]
        # Identifiants du notebook : les numéros de colis (texte), rendus uniques si besoin.
        self.ids, vus = [], set()
        for i, c in enumerate(self.colis):
            ident = c.numero if c.numero and c.numero not in vus else f"{c.numero or 'colis'}#{i + 1}"
            vus.add(ident)
            self.ids.append(ident)
        self.par_id = dict(zip(self.ids, self.colis))
        self.id_de = {id(c): ident for ident, c in zip(self.ids, self.colis)}
        self.plafond = {i: plafond_charge(c, params) for i, c in self.par_id.items()}
        self.dangereux = {i: classe_de(c.classe).iata for i, c in self.par_id.items()}   # vraie classe IATA : bord exigé
        self.colis_nb = []
        for ident, c in self.par_id.items():
            l, w = (c.largeur, c.longueur) if self.transpose else (c.longueur, c.largeur)
            self.colis_nb.append({"id": ident, "m": c.masse, "l": l / 100, "w": w / 100, "h": c.hauteur / 100, "r": 1,
                                  "iata": classe_notebook(c.classe), "DAT": c.dat})
        # Palettes : emplacements de soute, puis de rampe (masse et hauteur limitées), puis des palettes de soute
        # supplémentaires pour ne jamais manquer d'emplacement (l'application signale le dépassement ensuite).
        self.palettes_nb, self.type_de, self.zone_de = [], {}, {}
        rampe = _type_rampe(params, self.base)
        for k in range(max(params.positions_soute + params.positions_rampe, len(self.colis))):
            en_rampe = params.positions_soute <= k < params.positions_soute + params.positions_rampe
            t, zone = (rampe, "rampe") if en_rampe else (self.base, "soute")
            L, W = (t.longueur, t.largeur) if self.transpose else (t.largeur, t.longueur)
            self.palettes_nb.append({"id": k + 1, "M": t.masse_max, "L": L / 100, "W": W / 100, "H": t.hauteur_max / 100})
            self.type_de[k + 1], self.zone_de[k + 1] = t, zone

    # ------------------------------------------------------------------ éligibilité
    @staticmethod
    def eligible(a_placer, params) -> str | None:
        """None si le code du notebook peut traiter le problème, sinon la raison."""
        if any(t.positions > 1 for _, t in a_placer):
            return "des colis exigent une BIPAL ou une TRIPAL, formats que le notebook ne connaît pas"
        if len(a_placer) > LIMITE_COLIS:
            return f"plus de {LIMITE_COLIS} colis : le code du notebook serait trop lent"
        return None

    # ------------------------------------------------------------------ règles de l'application
    def _jeu_bord(self) -> float:
        """Tolérance (m) pour « posé contre un bord », légèrement inférieure à celle de l'application (0,5 cm) pour que
        les arrondis des conversions ne fassent jamais sortir un colis du bord que le notebook juge atteint."""
        return max(0.0, self.params.jeu_contact / 100 - 1e-7)

    def _regles(self, objets, palette) -> bool:
        """Règles de l'application ajoutées à celles du notebook : charge admissible des porteurs (lourd sur léger,
        fragile, carton) et colis dangereux (vraies classes IATA) contre un bord avec leur visibilité."""
        niveaux: dict[int, list] = {}
        for o in objets:
            niveaux.setdefault(_niveau(o["z"] + o["h_place"] / 2), []).append(o)
        for o in objets:
            bas = o["z"] - o["h_place"] / 2
            if bas <= 1e-6:
                continue
            k = _niveau(bas)
            x0, x1 = o["x"] - o["l_place"] / 2, o["x"] + o["l_place"] / 2
            y0, y1 = o["y"] - o["w_place"] / 2, o["y"] + o["w_place"] / 2
            for kk in (k - 1, k, k + 1):
                for s in niveaux.get(kk, ()):
                    if s is o:
                        continue
                    if min(x1, s["x"] + s["l_place"] / 2) - max(x0, s["x"] - s["l_place"] / 2) > 1e-7 and \
                            min(y1, s["y"] + s["w_place"] / 2) - max(y0, s["y"] - s["w_place"] / 2) > 1e-7:
                        if o["m"] > self.plafond[s["id"]] + 1e-6:
                            return False
        if self.params.iata_au_bord:
            jeu = self._jeu_bord()
            for o in objets:
                if not self.dangereux.get(o["id"]):
                    continue
                y0, y1 = o["y"] - o["w_place"] / 2, o["y"] + o["w_place"] / 2
                bords = set()
                if y0 <= -palette["W"] / 2 + jeu:
                    bords.add("gauche")
                if y1 >= palette["W"] / 2 - jeu:
                    bords.add("droite")
                if not bords & set(cotes_accessibles_iata(o, objets)):
                    return False
        return True

    def _classe(self, x) -> str:
        """Classe de l'application d'un colis du notebook (dict) ou d'une classe du notebook."""
        if isinstance(x, dict):
            colis = self.par_id.get(x.get("id"))
            return colis.classe if colis is not None else classe_application(x.get("iata"))
        return classe_application(x)

    def _incompatibles(self, a, b) -> bool:
        ca, cb = self._classe(a), self._classe(b)
        return bool(ca and cb and not self.regles.compatibles(ca, cb))

    @contextmanager
    def actif(self):
        """Branche les réglages et les règles de l'application sur le code du notebook pendant un calcul (un seul à
        la fois)."""
        global regrouper_par_DAT, support_minimal, marge_hauteur
        with _VERROU:
            ancien = (regrouper_par_DAT, support_minimal, marge_hauteur)
            regrouper_par_DAT = bool(self.params.regrouper_par_dat)
            support_minimal = float(self.params.support_minimal)
            marge_hauteur = max(0.0, float(self.params.marge_hauteur)) / 100
            HOOKS.regles, HOOKS.incompatibles = self._regles, self._incompatibles
            try:
                yield self
            finally:
                HOOKS.regles = HOOKS.incompatibles = None
                regrouper_par_DAT, support_minimal, marge_hauteur = ancien

    # ------------------------------------------------------------------ conversions
    def vers_chargements(self, resultat):
        """Résultat du notebook -> chargements de l'application (cm, coin minimal)."""
        from .optimisation import _Boite, _Chargement
        chs = []
        for charge in resultat["palettes"]:
            t = self.type_de[charge["id"]]
            ch = _Chargement(t, self.zone_de[charge["id"]])
            for o in charge["colis"]:
                c = self.par_id[o["id"]]
                lx, ly = o["l_place"] * 100, o["w_place"] * 100
                if self.transpose:
                    dx, dy = ly, lx
                    x1, y1 = o["y"] * 100 + t.largeur / 2 - dx / 2, o["x"] * 100 + t.longueur / 2 - dy / 2
                else:
                    dx, dy = lx, ly
                    x1, y1 = o["x"] * 100 + t.largeur / 2 - dx / 2, o["y"] * 100 + t.longueur / 2 - dy / 2
                dz = o["h_place"] * 100
                oi = 0 if abs(dx - c.longueur) < 1e-6 and abs(dy - c.largeur) < 1e-6 else 1
                ch.boites.append(_Boite(c, x1, y1, o["z"] * 100 - dz / 2, dx, dy, dz, oi))
            chs.append(ch)
        return chs

    # ------------------------------------------------------------------ phases (ordre de ``optimiser_palettes``)
    def _recentrer(self, resultat, palettes):
        """``recentrer_tas_rigidement`` du notebook ; avec « IATA au bord », un colis dangereux contre un bord y reste
        (la translation sur l'axe des côtés est bornée)."""
        if not self.params.iata_au_bord:
            return recentrer_tas_rigidement_notebook(resultat, palettes)
        jeu = self._jeu_bord()
        caracteristiques = {p["id"]: p for p in palettes}
        for charge in resultat.get("palettes", []):
            objets = charge["colis"]
            if not objets:
                continue
            palette = caracteristiques[charge["id"]]
            masse = sum(o["m"] for o in objets)
            cx = sum(o["m"] * o["x"] for o in objets) / masse
            cy = sum(o["m"] * o["y"] for o in objets) / masse
            gauche = max(-palette["L"] / 2 - o["x"] + o["l_place"] / 2 for o in objets)
            droite = min(palette["L"] / 2 - o["x"] - o["l_place"] / 2 for o in objets)
            bas = max(-palette["W"] / 2 - o["y"] + o["w_place"] / 2 for o in objets)
            haut = min(palette["W"] / 2 - o["y"] - o["w_place"] / 2 for o in objets)
            for o in objets:
                if not self.dangereux.get(o["id"]):
                    continue
                y0, y1 = o["y"] - o["w_place"] / 2, o["y"] + o["w_place"] / 2
                if y0 <= -palette["W"] / 2 + jeu:
                    haut = min(haut, -palette["W"] / 2 + 1e-9 - y0 + 1e-9)      # reste contre le bord gauche
                if y1 >= palette["W"] / 2 - jeu:
                    bas = max(bas, palette["W"] / 2 - 1e-9 - y1 - 1e-9)         # ... ou contre le bord droit
            tx = min(max(-cx, gauche), droite)
            ty = min(max(-cy, bas), haut) if bas <= haut else 0.0
            for o in objets:
                o["x"] += tx
                o["y"] += ty
            charge["centre_masse_x"], charge["centre_masse_y"] = cx + tx, cy + ty
        return resultat

    def executer(self, temps_limite, afficher_logs=False):
        """``optimiser_palettes`` (cellule 38) : heuristique, VND, recentrage, tassement horizontal.

        Chaque phase est suivie du contrôle indépendant du notebook ; si une phase échoue au contrôle, la dernière
        solution valide est restaurée. Lève ``RuntimeError`` si aucun des cinq essais de l'heuristique n'aboutit et
        ``ValueError`` si l'heuristique est refusée par le contrôle. Retourne un dictionnaire : ``resultat`` (format du
        notebook), ``etapes`` (états ``heuristique``, ``apres_vnd``, ``finale``), ``temps``, ``controles``.
        """
        global recentrer_tas_rigidement
        debut_global = perf_counter()
        palettes, colis = self.palettes_nb, self.colis_nb
        with self.actif():
            etat = {"resultat": construire_placement_heuristique(colis, palettes)}
            actualiser_chargement(etat["resultat"], palettes)
            temps = {"heuristique": etat["resultat"]["heuristique"]["duree"]}
            controles = []

            def verifier(nom):
                bilan = controler_chargement_independant(etat["resultat"], colis, palettes)
                bilan["phase"] = nom
                controles.append(bilan)
                return bilan

            bilan = verifier("heuristique")
            if not bilan["valide"]:
                raise ValueError("Heuristique refusée par le contrôle indépendant : " + " ; ".join(bilan["erreurs"][:3]))
            etapes = {"heuristique": deepcopy(etat["resultat"])}

            def executer_phase(nom, fonction):
                sauvegarde = deepcopy(etat["resultat"])
                debut = perf_counter()
                fonction()
                temps[nom] = perf_counter() - debut
                actualiser_chargement(etat["resultat"], palettes)
                bilan = verifier(nom)
                if not bilan["valide"]:
                    etat["resultat"] = sauvegarde
                    bilan["restauration"] = True
                return bilan

            executer_phase("vnd", lambda: ameliorer_vnd(etat["resultat"], colis, palettes, temps_limite, afficher_logs))
            etapes["apres_vnd"] = deepcopy(etat["resultat"])
            recentrer_tas_rigidement = self._recentrer           # appelé aussi par ``tasser_colis_vers_centre``
            try:
                executer_phase("recentrage", lambda: self._recentrer(etat["resultat"], palettes))
                executer_phase("tassement_horizontal", lambda: tasser_colis_vers_centre(etat["resultat"], palettes))
            finally:
                recentrer_tas_rigidement = recentrer_tas_rigidement_notebook
            resultat = etat["resultat"]
            partition = partition_DAT(colis)
            groupes_par_dat: dict = {}
            for cle in partition.values():
                if cle[0] == "DAT":
                    groupes_par_dat.setdefault(cle[1], set()).add(cle[2])
            resultat["dat_separes_incompatibilite"] = {
                dat: len(g) for dat, g in groupes_par_dat.items() if len(g) > 1}
            temps["controles"] = sum(c["duree"] for c in controles)
            temps["total"] = perf_counter() - debut_global
            etapes["finale"] = deepcopy(resultat)
        return {"resultat": resultat, "etapes": etapes, "temps": temps, "controles": controles}
