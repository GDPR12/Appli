"""Code du notebook ``CDP_4`` (heuristique, fusion, tassements), repris tel quel.

Ce module contient les fonctions du notebook sans modification de leur algorithme : ``construire_placement_heuristique``
(cellule 2), les règles IATA (cellule 5), le recentrage et les tassements vertical et horizontal (cellule 17) et
``fusionner_palettes`` (cellule 18). Les coordonnées sont celles du notebook : mètres, origine au centre de chaque
palette, centre des colis ; ``L`` (axe x) = 2,74 m, ``W`` (axe y) = 2,24 m, et les côtés « gauche » et « droite » des
colis dangereux sont les extrémités de l'axe y.

Trois accroches ``HOOKS`` marquées « [APPLICATION] » permettent à l'application d'ajouter ses propres règles (lourd sur
léger, cartons, bord des colis IATA, armes) ; elles sont inactives tant que ``HOOKS`` est vide, et le module
se comporte alors exactement comme le notebook. L'enchaînement des phases et les conversions vers les structures de
l'application sont plus bas, dans la partie « Adaptation ».
"""

# ruff: noqa
from copy import deepcopy
from itertools import combinations, permutations
from math import isfinite
from numbers import Real
from time import perf_counter


class _Accroches:
    """Règles supplémentaires de l'application (None : comportement exact du notebook)."""
    incompatibles = None    # (objet_a, objet_b) -> bool : classes à ne jamais mettre sur la même palette
    regles = None           # (objets, palette) -> bool : règles de chargement supplémentaires


HOOKS = _Accroches()

# ======================================================================================================
# Notebook CDP_4, cellule 5 : règles IATA
# ======================================================================================================
# 2. Règles physiques relatives aux matières dangereuses IATA

# Paires marquées d'un « x » dans le tableau 9.3.A de séparation entre colis.
# La classe 1 désigne ici la classe 1 hors division 1.4S.
INCOMPATIBILITES_IATA = {
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
CLASSES_IATA = {"1", "2.1", "2.2", "2.3", "3", "4.1", "4.2",
                "4.3", "5.1", "5.2", "8", "9"}

def classe_iata(objet):
    valeur = objet.get('iata', 0)
    if valeur in (0, None, '', '0'):
        return 0
    if isinstance(valeur, Real) and not isinstance(valeur, bool):
        return str(int(valeur)) if float(valeur).is_integer() else str(valeur)
    return str(valeur).strip().replace(',', '.')

def classes_iata_incompatibles(a, b):
    # [APPLICATION] séparation supplémentaire (armes / pièces de sécurité, classes absentes du tableau du notebook)
    if isinstance(a, dict) and isinstance(b, dict) and HOOKS.incompatibles is not None and HOOKS.incompatibles(a, b):
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
    """Mémorise un côté accessible pour les sorties et les warm starts SCIP."""
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
# Notebook CDP_4, cellule 2 : heuristique de placement
# ======================================================================================================

# Fonctions de l’heuristique réaliste exécutée avant SCIP

def construire_placement_heuristique(colis, palettes):
    """Construit plusieurs placements par points extrêmes et conserve le meilleur.

    Hiérarchie : contraintes, palettes ouvertes, IATA extérieurs, base la plus
    basse, hauteur de la palette, surface au sol, espaces libres et compacité.
    """
    from copy import deepcopy

    def orientations(objet):
        dimensions = (objet['l'], objet['w'], objet['h'])
        choix = ((dimensions[0], dimensions[1], dimensions[2]),
                 (dimensions[1], dimensions[0], dimensions[2])) if objet['r'] == 1 else permutations(dimensions)
        return list(dict.fromkeys(choix))

    def bornes(objet, axe):
        taille = {'x': 'l_place', 'y': 'w_place', 'z': 'h_place'}[axe]
        return objet[axe]-objet[taille]/2, objet[axe]+objet[taille]/2

    def recouvrement(a, b, axe):
        amin, amax = bornes(a, axe)
        bmin, bmax = bornes(b, axe)
        return max(0.0, min(amax, bmax)-max(amin, bmin))

    def aire_union(rectangles):
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

    def taux_support(candidat, places):
        bas = bornes(candidat, 'z')[0]
        if bas <= 1e-7:
            return 1.0
        rectangles = []
        xmin, xmax = bornes(candidat, 'x')
        ymin, ymax = bornes(candidat, 'y')
        for support in places:
            if abs(bornes(support, 'z')[1]-bas) > 1e-7:
                continue
            sx0, sx1 = bornes(support, 'x')
            sy0, sy1 = bornes(support, 'y')
            rectangle = (max(xmin, sx0), min(xmax, sx1),
                         max(ymin, sy0), min(ymax, sy1))
            if rectangle[1] > rectangle[0]+1e-9 and rectangle[3] > rectangle[2]+1e-9:
                rectangles.append(rectangle)
        return min(1.0, aire_union(rectangles)/(candidat['l_place']*candidat['w_place']))

    def placement_valide(candidat, places, palette):
        if sum(o['m'] for o in places)+candidat['m'] > palette['M']+1e-8:
            return False, 0.0
        if (bornes(candidat, 'x')[0] < -palette['L']/2-1e-8 or
                bornes(candidat, 'x')[1] > palette['L']/2+1e-8 or
                bornes(candidat, 'y')[0] < -palette['W']/2-1e-8 or
                bornes(candidat, 'y')[1] > palette['W']/2+1e-8 or
                bornes(candidat, 'z')[0] < -1e-8 or
                bornes(candidat, 'z')[1] > palette['H']+1e-8):
            return False, 0.0
        for autre in places:
            if all(recouvrement(candidat, autre, axe) > 1e-8 for axe in ('x', 'y', 'z')):
                return False, 0.0
        support = taux_support(candidat, places)
        if support < 0.90-1e-8:
            return False, support
        # [APPLICATION] règles supplémentaires de l'application, évaluées seulement si le notebook accepte le placement
        valide = chargement_iata_valide(places+[candidat])
        if valide and HOOKS.regles is not None:
            valide = HOOKS.regles(places+[candidat], palette)
        return valide, support

    def positions_candidates(l, w, h, places, palette):
        # Ensemble réduit de points physiques : coins du plateau, faces au sol
        # et points d'appui sur le dessus. On évite le produit de toutes les
        # coordonnées x, y et z, beaucoup trop coûteux.
        x_bords = {-palette['L']/2+l/2, 0.0, palette['L']/2-l/2}
        y_bords = {-palette['W']/2+w/2, 0.0, palette['W']/2-w/2}
        candidats = {(x, y, h/2) for x in x_bords for y in y_bords}
        for place in places:
            px0, px1 = bornes(place, 'x')
            py0, py1 = bornes(place, 'y')
            # Alignements contre chacune des quatre faces au niveau du sol.
            for x in (px0-l/2, px1+l/2):
                candidats.update((x, y, h/2) for y in (py0+w/2, place['y'], py1-w/2))
            for y in (py0-w/2, py1+w/2):
                candidats.update((x, y, h/2) for x in (px0+l/2, place['x'], px1-l/2))
            z = bornes(place, 'z')[1]+h/2
            # Neuf alignements utiles au-dessus du support.
            xs_dessus = (px0+l/2, place['x'], px1-l/2)
            ys_dessus = (py0+w/2, place['y'], py1-w/2)
            candidats.update((x, y, z) for x in xs_dessus for y in ys_dessus)
        return sorted(candidats, key=lambda xyz: (xyz[2], abs(xyz[0])+abs(xyz[1])))

    def score_position(candidat, places, palette, nouvelle, support):
        ancienne_hauteur = max((bornes(o, 'z')[1] for o in places), default=0.0)
        nouvelle_hauteur = max(ancienne_hauteur, bornes(candidat, 'z')[1])
        hausse = nouvelle_hauteur-ancienne_hauteur
        tous = places+[candidat]
        largeur_tas = max(bornes(o, 'x')[1] for o in tous)-min(bornes(o, 'x')[0] for o in tous)
        profondeur_tas = max(bornes(o, 'y')[1] for o in tous)-min(bornes(o, 'y')[0] for o in tous)
        compacite = largeur_tas*profondeur_tas/(palette['L']*palette['W'])
        masse = sum(o['m'] for o in tous)
        centre_x = sum(o['m']*o['x'] for o in tous)/masse
        centre_y = sum(o['m']*o['y'] for o in tous)/masse

        # Les colis IATA sont déjà traités avant les colis ordinaires. Pour eux,
        # on préfère maintenant une face proche de l'extrémité gauche ou droite
        # de la palette. Il s'agit d'une préférence heuristique : l'accessibilité
        # reste la contrainte absolue vérifiée après chaque déplacement ultérieur.
        if classe_iata(candidat):
            face_gauche, face_droite = bornes(candidat, 'y')
            distance_exterieur_iata = min(face_gauche + palette['W']/2,
                                           palette['W']/2 - face_droite)
        else:
            distance_exterieur_iata = 0.0

        # Surface réelle du plateau : seuls les colis posés au sol comptent.
        # L'union des bases mesure la surface couverte sans compter les trous
        # dans l'enveloppe du tas, ni la projection d'un colis déjà empilé.
        def surface_sol(objets):
            return aire_union([(*bornes(o, 'x'), *bornes(o, 'y'))
                               for o in objets if bornes(o, 'z')[0] <= 1e-7])
        gain_surface = (surface_sol(tous)-surface_sol(places))/(palette['L']*palette['W'])
        # Préserver une grande bande libre pour les prochains colis :
        # commencer au milieu peut fragmenter le plateau et empêcher des colis
        # de rentrer alors que la surface totale restante est suffisante.
        bases_sol = [o for o in tous if bornes(o, 'z')[0] <= 1e-7]
        if bases_sol:
            xmin = min(bornes(o, 'x')[0] for o in bases_sol)
            xmax = max(bornes(o, 'x')[1] for o in bases_sol)
            ymin = min(bornes(o, 'y')[0] for o in bases_sol)
            ymax = max(bornes(o, 'y')[1] for o in bases_sol)
            bandes_libres = ((xmin+palette['L']/2)*palette['W'],
                              (palette['L']/2-xmax)*palette['W'],
                              (ymin+palette['W']/2)*palette['L'],
                              (palette['W']/2-ymax)*palette['L'])
        else:
            bandes_libres = (palette['L']*palette['W'],)
        # À surface et hauteur identiques, éviter un départ au milieu
        # d'une bande : un alignement contre le plateau préserve ses extrémités.
        x0, x1 = bornes(candidat, 'x')
        y0, y1 = bornes(candidat, 'y')
        distance_bords = min(x0+palette['L']/2, palette['L']/2-x1) + min(
            y0+palette['W']/2, palette['W']/2-y1)
        # Les contraintes physiques/IATA ont déjà éliminé les candidats invalides.
        # Priorités strictes : aucune amélioration secondaire ne compense une
        # base plus haute, sauf les priorités palettes et extrémités IATA.
        return (int(nouvelle), round(distance_exterieur_iata, 8),
                round(bornes(candidat, 'z')[0], 8),
                round(nouvelle_hauteur, 8), -round(gain_surface, 8),
                -round(max(bandes_libres), 8),
                -round(sum(bande*bande for bande in bandes_libres), 8),
                round(distance_bords, 8), -support, compacite,
                abs(centre_x)/palette['L']+abs(centre_y)/palette['W'],
                abs(candidat['x'])+abs(candidat['y']))

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
    meilleurs, essais = None, []
    debut = perf_counter()
    for numero, ordre in enumerate(ordres, 1):
        charges = []
        positions_testees = 0
        echec = None
        for objet in ordre:
            meilleur = None
            # Les palettes déjà ouvertes sont testées, puis une seule nouvelle palette.
            indices = list(range(len(charges)))
            if len(charges) < len(palettes):
                indices.append(len(charges))
            for p in indices:
                palette = palettes[p]
                places = charges[p]['colis'] if p < len(charges) else []
                for l, w, h in orientations(objet):
                    for x, y, z in positions_candidates(l, w, h, places, palette):
                        positions_testees += 1
                        candidat = {'id': objet['id'], 'm': objet['m'], 'iata': classe_iata(objet),
                                     'x': x, 'y': y, 'z': z,
                                     'l_place': l, 'w_place': w, 'h_place': h}
                        valide, support = placement_valide(candidat, places, palette)
                        if not valide:
                            continue
                        score = score_position(candidat, places, palette, p == len(charges), support)
                        choix = (score, p, candidat)
                        if meilleur is None or choix[0] < meilleur[0]:
                            meilleur = choix
            if meilleur is None:
                echec = objet['id']
                break
            _, p, candidat = meilleur
            if p == len(charges):
                charges.append({'id': palettes[p]['id'], 'colis': []})
            charges[p]['colis'].append(candidat)
            attribuer_cotes_iata(charges[p]['colis'])
        if echec is not None:
            essais.append({'ordre': numero, 'echec_colis': echec, 'positions': positions_testees})
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
        essais.append({'ordre': numero, 'palettes': len(charges),
                       'hauteur': resultat['somme_hauteurs'], 'positions': positions_testees})
        if meilleurs is None or cle < meilleurs[0]:
            meilleurs = (cle, deepcopy(resultat))
    if meilleurs is None:
        raise RuntimeError('Aucun des placements heuristiques n’a réussi avec les palettes disponibles.')
    resultat = meilleurs[1]
    resultat['heuristique'] = {'duree': perf_counter()-debut, 'essais': essais,
                                'positions_testees': sum(e['positions'] for e in essais)}
    return resultat


# ======================================================================================================
# Notebook CDP_4, cellule 17 : recentrage, tassement vertical et horizontal
# ======================================================================================================

# 14. Recentrage et tassement vertical, puis horizontal

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

# Géométrie commune aux tassements vertical et horizontal.
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

def recouvrement_place(a, b, axe):
    amin, amax = bornes_colis_place(a, axe)
    bmin, bmax = bornes_colis_place(b, axe)
    return max(0.0, min(amax, bmax)-max(amin, bmin))

def chargement_physique_valide(objets, palette, tolerance=1e-7):
    """Vérifie tous les colis : limites, collisions, appuis à 90 % et IATA."""
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
        if aire_union_rectangles(rectangles)+1e-8 < 0.90*objet['l_place']*objet['w_place']:
            return False
    # [APPLICATION] règles supplémentaires de l'application, évaluées seulement si le notebook accepte le chargement
    if not chargement_iata_valide(objets, tolerance):
        return False
    return HOOKS.regles is None or HOOKS.regles(objets, palette)

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
    return resultat

def tasser_colis_verticalement(resultat, palettes, precision=1e-7,
                                donnees_colis=None, temps_limite=None):
    """Replace les colis plus bas, avec changement possible de x/y et orientation.

    Recherche de dispositions finales, pas d'une trajectoire de manutention.
    Les affectations aux palettes restent fixes. Chaque mouvement abaisse
    strictement la base et n'augmente pas le sommet du colis.
    """
    debut = perf_counter()
    actualiser_chargement(resultat, palettes)
    hauteur_avant = resultat['hauteur_globale']
    somme_avant = resultat['somme_hauteurs']
    donnees = {o['id']: o for o in (donnees_colis or [])}
    caracteristiques = {p['id']: p for p in palettes}
    mouvements = essais = passages = supports_bloquants = 0
    deplacement = lateral = 0.0
    modifies = set()
    limite_atteinte = False

    def expire():
        return temps_limite is not None and perf_counter()-debut >= temps_limite

    def orientations(objet):
        original = donnees.get(objet['id'])
        if original is None:
            # Sans donnée r, conserver l'orientation plutôt qu'autoriser une rotation inconnue.
            return [(objet['l_place'], objet['w_place'], objet['h_place'])]
        l, w, h = original['l'], original['w'], original['h']
        choix = [(l, w, h), (w, l, h)] if original['r'] == 1 else permutations((l, w, h))
        return sorted(set(choix), key=lambda d: (d[2], -d[0]*d[1], d[0], d[1]))

    def positions_xy(l, w, objets, palette, ancien):
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

    for charge in resultat.get('palettes', []):
        objets, palette = charge['colis'], caracteristiques[charge['id']]
        # Plusieurs passages permettent de retirer un support après les colis
        # qu'il portait. Nombre borné pour garder une recherche légère.
        for passage in range(max(1, len(objets))):
            mouvement_passage = False
            for objet in sorted(objets, key=lambda o: (-o['z']-o['h_place']/2, o['id'])):
                if expire():
                    limite_atteinte = True
                    break
                ancien = dict(objet)
                bas, sommet = bornes_colis_place(objet, 'z')
                if bas <= precision:
                    continue
                restants = [o for o in objets if o is not objet]
                # Le retrait ne doit pas laisser d'autres colis sans appui.
                if not chargement_physique_valide(restants, palette, precision):
                    supports_bloquants += 1
                    continue
                niveaux = sorted({0.0} | {bornes_colis_place(o, 'z')[1] for o in restants
                                         if bornes_colis_place(o, 'z')[1] < bas-precision})
                choix = None
                grilles = {}
                for niveau in niveaux:
                    for l, w, h in orientations(objet):
                        if niveau+h > sommet+precision:
                            continue
                        if (l, w) not in grilles:
                            grilles[l, w] = positions_xy(l, w, restants, palette, ancien)
                        for x, y in grilles[l, w]:
                            if expire():
                                limite_atteinte = True
                                break
                            essais += 1
                            candidat = dict(ancien, x=x, y=y, z=niveau+h/2,
                                            l_place=l, w_place=w, h_place=h)
                            # Filtre rapide avant la validation complète des appuis/IATA.
                            if any(all(recouvrement_place(candidat, autre, axe) > precision
                                       for axe in ('x', 'y', 'z')) for autre in restants):
                                continue
                            if chargement_physique_valide(restants+[candidat], palette, precision):
                                choix = candidat
                                break
                        if choix is not None or limite_atteinte:
                            break
                    if choix is not None or limite_atteinte:
                        break
                if choix is not None:
                    objet.update(choix)
                    deplacement += bas-bornes_colis_place(objet, 'z')[0]
                    lateral += ((objet['x']-ancien['x'])**2+(objet['y']-ancien['y'])**2)**0.5
                    mouvements += 1
                    modifies.add(objet['id'])
                    mouvement_passage = True
                if limite_atteinte:
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
        'mouvements': mouvements, 'nombre_passages': passages,
        'hauteur_avant': hauteur_avant, 'hauteur_apres': resultat['hauteur_globale'],
        'gain_somme_hauteurs': somme_avant-resultat['somme_hauteurs']}
    return resultat

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
# Notebook CDP_4, cellule 18 : transferts entre palettes
# ======================================================================================================

# 15. Transferts entre palettes avant les tassements

def normaliser_palettes_identiques(resultat, palettes):
    """Renomme les palettes identiques utilisées selon l'ordre de symétrie SCIP."""
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

def fusionner_palettes(resultat, palettes, temps_limite, precision=1e-6):
    """Transfère les colis sans dépasser le budget de temps indiqué."""
    from copy import deepcopy
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

    def limites(objet, axe):
        longueur = {'x': 'l_place', 'y': 'w_place', 'z': 'h_place'}[axe]
        return objet[axe]-objet[longueur]/2, objet[axe]+objet[longueur]/2

    def recouvrement(a, b, axe):
        amin, amax = limites(a, axe)
        bmin, bmax = limites(b, axe)
        return max(0.0, min(amax, bmax)-max(amin, bmin))

    def valide(objets, palette):
        if sum(objet['m'] for objet in objets) > palette['M']+precision:
            return False
        if sum(objet['l_place']*objet['w_place']*objet['h_place'] for objet in objets) > (
                palette['L']*palette['W']*palette['H']+precision):
            return False
        for objet in objets:
            xmin, xmax = limites(objet, 'x')
            ymin, ymax = limites(objet, 'y')
            zmin, zmax = limites(objet, 'z')
            if (xmin < -palette['L']/2-precision or xmax > palette['L']/2+precision or
                    ymin < -palette['W']/2-precision or ymax > palette['W']/2+precision or
                    zmin < -precision or zmax > palette['H']+precision):
                return False
        for index, premier in enumerate(objets):
            for second in objets[index+1:]:
                if all(recouvrement(premier, second, axe) > precision for axe in ('x','y','z')):
                    return False
        for objet in objets:
            bas = limites(objet, 'z')[0]
            if bas <= precision:
                continue
            aire = sum(recouvrement(objet, support, 'x')*recouvrement(objet, support, 'y')
                       for support in objets if support is not objet and
                       abs(limites(support, 'z')[1]-bas) <= precision)
            if aire+precision < 0.90*objet['l_place']*objet['w_place']:
                return False
        # [APPLICATION] règles supplémentaires de l'application, évaluées seulement si le notebook accepte le chargement
        if not chargement_iata_valide(objets, precision):
            return False
        return HOOKS.regles is None or HOOKS.regles(objets, palette)

    def positions(objet, places, palette):
        # Points physiques seulement : coins du plateau, faces voisines et dessus.
        l, w, h = objet['l_place'], objet['w_place'], objet['h_place']
        xs = (-palette['L']/2+l/2, 0.0, palette['L']/2-l/2)
        ys = (-palette['W']/2+w/2, 0.0, palette['W']/2-w/2)
        candidats = {(x, y, h/2) for x in xs for y in ys}
        for place in places:
            px0, px1 = limites(place, 'x')
            py0, py1 = limites(place, 'y')
            for x in (px0-l/2, px1+l/2):
                candidats.update((x, y, h/2) for y in (py0+w/2, place['y'], py1-w/2))
            for y in (py0-w/2, py1+w/2):
                candidats.update((x, y, h/2) for x in (px0+l/2, place['x'], px1-l/2))
            z = limites(place, 'z')[1]+h/2
            candidats.update((x, y, z)
                              for x in (px0+l/2, place['x'], px1-l/2)
                              for y in (py0+w/2, place['y'], py1-w/2))
        return sorted(candidats, key=lambda xyz: (xyz[2], abs(xyz[0])+abs(xyz[1])))

    def volume(charge):
        return sum(c['l_place']*c['w_place']*c['h_place'] for c in charge['colis'])

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

    def mettre_a_jour(charge):
        objets = charge['colis']
        if not objets:
            charge['masse_totale'] = 0
            charge['hauteur_utilisee'] = 0
            return
        masse = sum(c['m'] for c in objets)
        charge['masse_totale'] = masse
        charge['hauteur_utilisee'] = max(limites(c, 'z')[1] for c in objets)
        charge['centre_masse_x'] = sum(c['m']*c['x'] for c in objets)/masse
        charge['centre_masse_y'] = sum(c['m']*c['y'] for c in objets)/masse

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
            for original in objets:
                if budget_paire_ecoule():
                    return transferts
                restants = [c for c in source['colis'] if c is not original]
                # Retirer un support ne doit pas rendre la source invalide.
                if not valide(restants, palette_source):
                    continue
                if receveuse['masse_totale']+original['m'] > palette_receveuse['M']+precision:
                    continue
                for x, y, z in positions(original, receveuse['colis'], palette_receveuse):
                    if budget_paire_ecoule():
                        return transferts
                    candidat = deepcopy(original)
                    candidat.update(x=x, y=y, z=z)
                    compteurs['emplacements_testes'] += 1
                    if not valide(receveuse['colis']+[candidat], palette_receveuse):
                        continue
                    receveuse['colis'].append(candidat)
                    source['colis'].remove(original)
                    attribuer_cotes_iata(receveuse['colis'], precision)
                    attribuer_cotes_iata(source['colis'], precision)
                    mettre_a_jour(receveuse)
                    mettre_a_jour(source)
                    compteurs['colis_transferes'] += 1
                    transferts += 1
                    mouvement = True
                    break
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
# Adaptation à l'application (ne fait pas partie du notebook)
# ======================================================================================================
#
# Le notebook travaille en mètres, avec l'origine au centre de chaque palette et le centre des colis, et ne connaît que
# des palettes 10k. L'application travaille en centimètres avec le coin minimal des colis. Cette partie convertit
# d'un repère à l'autre, enchaîne les phases dans l'ordre de ``optimiser_palettes`` (cellule 19) et branche les
# règles propres à l'application sur les accroches ``HOOKS``.

import threading
from contextlib import contextmanager

LIMITE_COLIS = 150          # au-delà, le code du notebook (non optimisé) est trop lent : l'application prend le relais
_VERROU = threading.Lock()  # ``HOOKS`` est global au module : un seul calcul à la fois


def _niveau(z: float) -> int:
    return round(z * 1e6)


class Contexte:
    """Problème de l'application mis dans la forme attendue par le notebook, et conversions dans les deux sens."""

    def __init__(self, a_placer, params, regles):
        from .iata import classe as classe_de
        from .geometrie import plafond_charge
        self.params, self.regles = params, regles
        self.transpose = params.axe_cotes == "x"     # le notebook voit les côtés sur y : on échange x et y sinon
        types = params.types_palettes()
        self.base = types[0]
        self.colis = [c for c, _ in a_placer]
        self.numero_vers_id = {id(c): i + 1 for i, c in enumerate(self.colis)}
        self.par_id = {i + 1: c for i, c in enumerate(self.colis)}
        self.plafond = {i: plafond_charge(c, params) for i, c in self.par_id.items()}
        self.colis_nb = []
        for i, c in self.par_id.items():
            dangereux = classe_de(c.classe).iata
            code = "9" if c.classe == "9L" else c.classe
            l, w = (c.largeur, c.longueur) if self.transpose else (c.longueur, c.largeur)
            self.colis_nb.append({"id": i, "m": c.masse, "l": l / 100, "w": w / 100, "h": c.hauteur / 100, "r": 1,
                                  "iata": code if dangereux else 0})
        # Palettes : emplacements de soute, puis de rampe (masse et hauteur limitées), puis des palettes de soute
        # supplémentaires pour ne jamais manquer d'emplacement (l'application signale le dépassement ensuite).
        from .optimisation import _type_rampe
        self.palettes_nb, self.type_de, self.zone_de = [], {}, {}
        rampe = _type_rampe(params, self.base)
        for k in range(max(params.positions_soute + params.positions_rampe, len(self.colis))):
            en_rampe = params.positions_soute <= k < params.positions_soute + params.positions_rampe
            t, zone = (rampe, "rampe") if en_rampe else (self.base, "soute")
            L, W = (t.longueur, t.largeur) if self.transpose else (t.largeur, t.longueur)
            self.palettes_nb.append({"id": k + 1, "M": t.masse_max, "L": L / 100, "W": W / 100, "H": t.hauteur_max / 100})
            self.type_de[k + 1], self.zone_de[k + 1] = t, zone
        self.hooks_actifs = False

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
    def _regles(self, objets, palette) -> bool:
        """Règles de l'application ajoutées à celles du notebook : charge admissible des porteurs (lourd sur léger,
        fragile, carton) et colis dangereux contre un bord (avec leur visibilité)."""
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
                if not o["iata"]:
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

    def _jeu_bord(self) -> float:
        """Tolérance (m) pour « posé contre un bord », légèrement inférieure à celle de l'application (0,5 cm) pour que
        les arrondis des conversions ne fassent jamais sortir un colis du bord que le notebook juge atteint."""
        return max(0.0, self.params.jeu_contact / 100 - 1e-7)

    def _incompatibles(self, a, b) -> bool:
        return not self.regles.compatibles(self.par_id[a["id"]].classe, self.par_id[b["id"]].classe)

    @contextmanager
    def actif(self):
        """Branche les règles de l'application sur le code du notebook pendant un calcul (un seul à la fois)."""
        with _VERROU:
            HOOKS.regles, HOOKS.incompatibles = self._regles, self._incompatibles
            try:
                yield self
            finally:
                HOOKS.regles = HOOKS.incompatibles = None

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

    def depuis_chargements(self, chs):
        """Chargements de l'application -> résultat du notebook (avant la phase de fusion)."""
        libres = {"soute": [k for k, z in self.zone_de.items() if z == "soute"],
                  "rampe": [k for k, z in self.zone_de.items() if z == "rampe"]}
        resultat = {"statut": "application", "palettes": []}
        for ch in chs:
            pid = libres[ch.zone if libres.get(ch.zone) else "soute"].pop(0)
            colis = []
            for b in ch.boites:
                t = ch.type
                lx, ly = (b.dy, b.dx) if self.transpose else (b.dx, b.dy)
                if self.transpose:
                    x, y = (b.y1 + b.dy / 2 - t.longueur / 2) / 100, (b.x1 + b.dx / 2 - t.largeur / 2) / 100
                else:
                    x, y = (b.x1 + b.dx / 2 - t.largeur / 2) / 100, (b.y1 + b.dy / 2 - t.longueur / 2) / 100
                i = self.numero_vers_id[id(b.colis)]
                colis.append({"id": i, "m": b.colis.masse, "iata": self.colis_nb[i - 1]["iata"], "x": x, "y": y,
                              "z": (b.z1 + b.dz / 2) / 100, "l_place": lx / 100, "w_place": ly / 100,
                              "h_place": b.dz / 100})
            resultat["palettes"].append({"id": pid, "colis": colis})
        actualiser_chargement(resultat, self.palettes_nb)
        return resultat

    # ------------------------------------------------------------------ phases (ordre de ``optimiser_palettes``)
    def phase1(self):
        """Phase 1 : les quatre ordres du notebook, meilleure construction selon sa clé. Retourne (résultat, info)."""
        resultat = construire_placement_heuristique(self.colis_nb, self.palettes_nb)
        actualiser_chargement(resultat, self.palettes_nb)
        info = dict(resultat["heuristique"])
        info["constructions"] = len(info["essais"])
        return resultat, info

    def phase3(self, resultat, limite):
        fusionner_palettes(resultat, self.palettes_nb, limite)
        normaliser_palettes_identiques(resultat, self.palettes_nb)
        actualiser_chargement(resultat, self.palettes_nb)
        return resultat["fusion_palettes"]

    def phase4(self, resultat, limite):
        tasser_colis_verticalement(resultat, self.palettes_nb, donnees_colis=self.colis_nb, temps_limite=limite)
        bilan = dict(resultat["tassement_vertical"])
        for cle in ("hauteur_avant", "hauteur_apres"):
            bilan[cle] *= 100                  # le notebook compte en mètres, le journal de l'application en centimètres
        return bilan

    def phase5(self, resultat):
        global recentrer_tas_rigidement
        original = recentrer_tas_rigidement
        recentrer_tas_rigidement = self._recentrer           # appelé aussi par ``tasser_colis_vers_centre``
        try:
            t0 = perf_counter()
            self._recentrer(resultat, self.palettes_nb)
            tasser_colis_vers_centre(resultat, self.palettes_nb)
        finally:
            recentrer_tas_rigidement = original
        resultat["temps_phase5"] = perf_counter() - t0
        return resultat["tassement_local"]

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
                if not o["iata"]:
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


recentrer_tas_rigidement_notebook = recentrer_tas_rigidement
