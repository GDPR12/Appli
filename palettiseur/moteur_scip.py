"""Phases SCIP du pipeline : port des cellules SCIP du notebook ``CDP_2``.

- **Phase 2** : le placement heuristique sert d'amorce (*warm start*) ; SCIP le valide puis cherche
  à réduire le nombre d'emplacements de palette (recherche progressive : cible = courant − 1, puis −2…).

Le notebook n'utilise plus SCIP pour les hauteurs : la minimisation par palette a été remplacée par le tassement
vertical géométrique (``optimisation._tasser_vertical``).

Le modèle est celui du notebook (affectation, orientations, capacité, non-chevauchement par indicateurs, support
à 90 % par aires de contact, hauteurs) et ses contraintes IATA (``ajouter_contraintes_iata``) :

- deux classes incompatibles (tableau 9.3.A) ne partagent jamais une palette ;
- chaque colis dangereux choisit un côté d'accès (``acces_gauche`` ou ``acces_droite``) et aucun colis ne le masque
  de ce côté : un colis ne masque que si les projections se chevauchent en x et en z, et qu'il passe devant en y.

Règles de l'application ajoutées au notebook :

- séparation d'armement (arme et pièce de sécurité) avec les autres paires incompatibles ;
- aucun colis lourd sur un colis plus léger, rien sur un colis fragile : contact de support interdit ;
- palettes candidates : celles de la solution heuristique (10k, BIPAL, TRIPAL, rampe) ;
  objectif = nombre d'emplacements occupés.

Conventions du notebook : mètres et kilogrammes, origine au centre de la surface de la palette, ``(x, y, z)`` =
centre du colis. Toute solution SCIP est contrôlée par ``_charge_valide`` avant d'être acceptée ; en cas d'échec,
la solution heuristique est conservée (le notebook abandonnait). PySCIPOpt est importé paresseusement :
l'application fonctionne sans lui.
"""

from __future__ import annotations

from itertools import combinations, permutations
from time import perf_counter

from .geometrie import orientations as orientations_cm
from .iata import ReglesSeparation, regles as regles_par_defaut
from .modeles import Colis, Parametres, Resultat
from .iata import classe as classe_de
from .optimisation import TOL, _Boite, _Chargement, _charge_valide, _cotes_visibles


def disponible() -> tuple[bool, str]:
    try:
        import pyscipopt
        return True, f"PySCIPOpt {getattr(pyscipopt, '__version__', '')}".strip()
    except ImportError:
        return False, "pip install pyscipopt"


# ---------------------------------------------------------------------------
# Données du modèle (mètres, kilogrammes)
# ---------------------------------------------------------------------------

def _colis_modele(colis_liste: list[Colis]) -> list[dict]:
    return [{"id": i, "obj": c, "m": c.masse, "l": c.longueur / 100, "w": c.largeur / 100,
             "h": c.hauteur / 100, "r": 0 if c.renversable else 1, "fragile": c.fragile, "classe": c.classe,
             "iata": classe_de(c.classe).iata}
            for i, c in enumerate(colis_liste)]


def _palettes_modele(chs: list[_Chargement]) -> list[dict]:
    return [{"id": p, "M": ch.type.masse_max, "L": ch.type.largeur / 100, "W": ch.type.longueur / 100,
             "H": ch.type.hauteur_max / 100, "positions": ch.type.positions} for p, ch in enumerate(chs)]


# ---------------------------------------------------------------------------
# Modèle (cellules 6 à 12 du notebook)
# ---------------------------------------------------------------------------

def creer_modele(colis, palettes, temps_limite, afficher_logs=False):
    from pyscipopt import Model
    I, P = range(len(colis)), range(len(palettes))
    modele = Model("Palettisation_3D")
    modele.setBoolParam("nlp/disable", True)
    modele.setRealParam("limits/time", float(temps_limite))
    if not afficher_logs:
        modele.hideOutput()
    a = {(i, p): modele.addVar(vtype="B", name=f"a_{i}_{p}") for i in I for p in P}
    u = {p: modele.addVar(vtype="B", name=f"u_{p}") for p in P}
    Lmax, Wmax, Hmax = (max(p[k] for p in palettes) for k in ("L", "W", "H"))
    x = {i: modele.addVar(lb=-Lmax / 2, ub=Lmax / 2, name=f"x_{i}") for i in I}
    y = {i: modele.addVar(lb=-Wmax / 2, ub=Wmax / 2, name=f"y_{i}") for i in I}
    z = {i: modele.addVar(lb=0, ub=Hmax, name=f"z_{i}") for i in I}
    return {"modele": modele, "colis": colis, "palettes": palettes, "I": I, "P": P,
            "a": a, "u": u, "x": x, "y": y, "z": z}


def ajouter_affectation_et_orientations(c):
    from pyscipopt import quicksum
    modele, colis, I, P, a = c["modele"], c["colis"], c["I"], c["P"], c["a"]
    orientations, b, dimensions = {}, {}, {}
    for i, ci in enumerate(colis):
        l, w, h = ci["l"], ci["w"], ci["h"]
        choix = [(l, w, h), (w, l, h)] if ci["r"] == 1 else list(permutations((l, w, h)))
        orientations[i] = list(dict.fromkeys(choix))
        for o in range(len(orientations[i])):
            b[i, o] = modele.addVar(vtype="B", name=f"b_{i}_{o}")
        modele.addCons(quicksum(b[i, o] for o in range(len(orientations[i]))) == 1)
        dimensions[i] = tuple(quicksum(d[k] * b[i, o] for o, d in enumerate(orientations[i])) for k in range(3))
        modele.addCons(quicksum(a[i, p] for p in P) == 1)
    c.update(orientations=orientations, b=b, dimensions=dimensions)


def ajouter_capacite_et_limites(c):
    from pyscipopt import quicksum
    modele, colis, palettes = c["modele"], c["colis"], c["palettes"]
    I, P, a, u = c["I"], c["P"], c["a"], c["u"]
    x, y, z, dimensions = c["x"], c["y"], c["z"], c["dimensions"]
    for p, pal in enumerate(palettes):
        modele.addCons(quicksum(colis[i]["m"] * a[i, p] for i in I) <= pal["M"] * u[p])
        modele.addCons(quicksum(colis[i]["l"] * colis[i]["w"] * colis[i]["h"] * a[i, p] for i in I)
                       <= pal["L"] * pal["W"] * pal["H"] * u[p])
        modele.addCons(u[p] <= quicksum(a[i, p] for i in I))
        for i in I:
            modele.addCons(a[i, p] <= u[p])
            li, wi, hi = dimensions[i]
            for contrainte in (x[i] + li / 2 <= pal["L"] / 2, -x[i] + li / 2 <= pal["L"] / 2,
                               y[i] + wi / 2 <= pal["W"] / 2, -y[i] + wi / 2 <= pal["W"] / 2,
                               z[i] + hi / 2 <= pal["H"], -z[i] + hi / 2 <= 0):
                modele.addConsIndicator(contrainte, binvar=a[i, p])


def ajouter_reduction_symetries(c):
    """Palettes identiques interchangeables : on les utilise dans l'ordre (aucune solution perdue)."""
    modele, palettes, u = c["modele"], c["palettes"], c["u"]
    groupes: dict[tuple, list[int]] = {}
    for p, pal in enumerate(palettes):
        groupes.setdefault((pal["M"], pal["L"], pal["W"], pal["H"]), []).append(p)
    for indices in groupes.values():
        for precedente, suivante in zip(indices, indices[1:]):
            modele.addCons(u[precedente] >= u[suivante])


def ajouter_non_chevauchement(c):
    from pyscipopt import quicksum
    modele, I, P, a = c["modele"], c["I"], c["P"], c["a"]
    x, y, z, dimensions = c["x"], c["y"], c["z"], c["dimensions"]
    variables_separation = {}
    for i, j in combinations(I, 2):
        separations_paire = []
        for k, coord in enumerate((x, y, z)):
            demi_somme = (dimensions[i][k] + dimensions[j][k]) / 2
            for premier, second in ((i, j), (j, i)):
                s = modele.addVar(vtype="B", name=f"s_{premier}_{second}_{k}")
                modele.addConsIndicator(coord[premier] + demi_somme <= coord[second], binvar=s)
                variables_separation[premier, second, k] = s
                separations_paire.append(s)
        for p in P:
            modele.addCons(quicksum(separations_paire) >= a[i, p] + a[j, p] - 1)
    c["separations"] = variables_separation


def ajouter_contraintes_iata(c, regles: ReglesSeparation, params: Parametres):
    """Séparation IATA/armement et accessibilité depuis les côtés en y (cellule IATA de CDP_2).

    Tout colis dangereux choisit un côté (gauche : y minimal, droite : y maximal) et aucun autre colis de sa palette
    ne le masque de ce côté, sauf s'ils sont séparés en x ou en z.
    """
    modele, colis, I, P, a = c["modele"], c["colis"], c["I"], c["P"], c["a"]
    # Deux classes incompatibles ne peuvent jamais être affectées à la même palette.
    for i, j in combinations(I, 2):
        if regles.motif_incompatibilite(colis[i]["classe"], colis[j]["classe"]):
            for p in P:
                modele.addCons(a[i, p] + a[j, p] <= 1)
    c.update(acces_gauche={}, acces_droite={})
    if not params.iata_acces:
        return
    separations = c["separations"]
    dangereux = [i for i in I if colis[i]["iata"]]
    acces_gauche = {i: modele.addVar(vtype="B", name=f"acces_iata_gauche_{i}") for i in dangereux}
    acces_droite = {i: modele.addVar(vtype="B", name=f"acces_iata_droite_{i}") for i in dangereux}
    for i in dangereux:
        modele.addCons(acces_gauche[i] + acces_droite[i] == 1)
    # Depuis la largeur, un autre colis ne masque i que si les projections se chevauchent à la fois
    # selon la longueur x et la hauteur z.
    for i in dangereux:
        for j in I:
            if i == j:
                continue
            separation_xz = (separations[i, j, 0] + separations[j, i, 0] +
                             separations[i, j, 2] + separations[j, i, 2])
            for p in P:
                # Côté gauche (-y) : i doit être devant j, sauf s'ils sont côte à côte en x ou z.
                modele.addCons(separations[i, j, 1] + separation_xz >= a[i, p] + a[j, p] + acces_gauche[i] - 2)
                # Côté droit (+y) : ordre inverse selon y.
                modele.addCons(separations[j, i, 1] + separation_xz >= a[i, p] + a[j, p] + acces_droite[i] - 2)
    c.update(acces_gauche=acces_gauche, acces_droite=acces_droite)


def ajouter_support_minimal(c, params: Parametres):
    """Gravité : support minimal de la base ; contact interdit sous un colis plus léger ou fragile."""
    from pyscipopt import quicksum
    modele, colis, I, P, a = c["modele"], c["colis"], c["I"], c["P"], c["a"]
    x, y, z = c["x"], c["y"], c["z"]
    orientations, b, dimensions = c["orientations"], c["b"], c["dimensions"]
    s_min = params.support_minimal
    for i in I:
        li, wi, hi = dimensions[i]
        sol_i = modele.addVar(vtype="B", name=f"au_sol_{i}")
        modele.addConsIndicator(z[i] - hi / 2 <= 0, binvar=sol_i)
        base_i = quicksum(d[0] * d[1] * b[i, o] for o, d in enumerate(orientations[i]))
        base_max = max(d[0] * d[1] for d in orientations[i])
        aires = []
        for j in I:
            if i == j:
                continue
            lj, wj, hj = dimensions[j]
            contact = modele.addVar(vtype="B", name=f"contact_{i}_{j}")      # i repose sur j
            if colis[j]["fragile"] or colis[i]["m"] > params.facteur_masse * colis[j]["m"] + 1e-9:
                modele.addCons(contact <= 0)
            modele.addCons(contact + sol_i <= 1)
            for p in P:
                modele.addCons(a[i, p] - a[j, p] <= 1 - contact)
                modele.addCons(a[j, p] - a[i, p] <= 1 - contact)
            ecart_z = z[i] - hi / 2 - z[j] - hj / 2
            modele.addConsIndicator(ecart_z <= 0, binvar=contact)
            modele.addConsIndicator(-ecart_z <= 0, binvar=contact)
            largeurs = []
            for k, coord in enumerate((x, y)):
                borne = min(max(d[k] for d in orientations[i]), max(d[k] for d in orientations[j]))
                largeur = modele.addVar(lb=0, ub=borne, name=f"recouvrement_{i}_{j}_{k}")
                modele.addCons(largeur <= borne * contact)
                modele.addCons(largeur <= dimensions[i][k])
                modele.addCons(largeur <= dimensions[j][k])
                demi = (dimensions[i][k] + dimensions[j][k]) / 2
                modele.addConsIndicator(largeur <= coord[i] - coord[j] + demi, binvar=contact)
                modele.addConsIndicator(largeur <= coord[j] - coord[i] + demi, binvar=contact)
                largeurs.append(largeur)
            aire = modele.addVar(lb=0, ub=base_max, name=f"aire_contact_{i}_{j}")
            modele.addCons(aire <= largeurs[0] * largeurs[1])
            aires.append(aire)
        modele.addCons(quicksum(aires) >= s_min * base_i - s_min * base_max * sol_i)


def ajouter_hauteurs(c):
    modele, palettes, I, P = c["modele"], c["palettes"], c["I"], c["P"]
    a, u, z, dimensions = c["a"], c["u"], c["z"], c["dimensions"]
    T = {p: modele.addVar(lb=0, ub=palettes[p]["H"], name=f"T_{p}") for p in P}
    for p in P:
        modele.addCons(T[p] <= palettes[p]["H"] * u[p])
        for i in I:
            modele.addConsIndicator(z[i] + dimensions[i][2] / 2 <= T[p], binvar=a[i, p])
    c["T"] = T


def _construire_modele(chs, colis_liste, params, regles, temps_limite):
    colis, palettes = _colis_modele(colis_liste), _palettes_modele(chs)
    c = creer_modele(colis, palettes, temps_limite)
    ajouter_affectation_et_orientations(c)
    ajouter_reduction_symetries(c)
    ajouter_capacite_et_limites(c)
    ajouter_non_chevauchement(c)
    ajouter_contraintes_iata(c, regles, params)
    ajouter_support_minimal(c, params)
    ajouter_hauteurs(c)
    return c


# ---------------------------------------------------------------------------
# Conversions heuristique <-> modèle
# ---------------------------------------------------------------------------

def _centre_modele(b: _Boite, ch: _Chargement) -> tuple[float, float, float]:
    """Centre du colis dans le repère du notebook (mètres, origine au centre de la palette)."""
    t = ch.type
    return ((b.x1 + b.x2 - t.largeur) / 200, (b.y1 + b.y2 - t.longueur) / 200, (b.z1 + b.z2) / 200)


def _indice_orientation(c, i: int, b: _Boite) -> int:
    cible = (b.dx / 100, b.dy / 100, b.dz / 100)
    for o, d in enumerate(c["orientations"][i]):
        if all(abs(d[k] - cible[k]) <= 1e-6 for k in range(3)):
            return o
    return b.orientation


def _indices_colis(chs) -> tuple[list[Colis], dict[int, int]]:
    """Liste des colis (ordre des palettes) et correspondance id(colis) -> indice."""
    liste = [b.colis for ch in chs for b in ch.boites]
    return liste, {id(colis): i for i, colis in enumerate(liste)}


def _cote_acces(b: _Boite, ch: _Chargement) -> bool:
    """Vrai si le colis dangereux est accessible à gauche (choix par défaut), faux s'il ne l'est qu'à droite."""
    return "gauche" in _cotes_visibles(b, ch.boites) or "droite" not in _cotes_visibles(b, ch.boites)


def ajouter_amorce_heuristique(c, chs, indice):
    """Transmet à SCIP l'affectation, l'orientation, les coordonnées et le côté d'accès IATA (solution partielle)."""
    modele = c["modele"]
    solution = modele.createPartialSol()
    for p, ch in enumerate(chs):
        modele.setSolVal(solution, c["u"][p], 1.0)
        for b in ch.boites:
            i = indice[id(b.colis)]
            for q in c["P"]:
                modele.setSolVal(solution, c["a"][i, q], float(q == p))
            x, y, z = _centre_modele(b, ch)
            modele.setSolVal(solution, c["x"][i], x)
            modele.setSolVal(solution, c["y"][i], y)
            modele.setSolVal(solution, c["z"][i], z)
            choix = _indice_orientation(c, i, b)
            for o in range(len(c["orientations"][i])):
                modele.setSolVal(solution, c["b"][i, o], float(o == choix))
            if i in c["acces_gauche"]:
                gauche = _cote_acces(b, ch)
                modele.setSolVal(solution, c["acces_gauche"][i], float(gauche))
                modele.setSolVal(solution, c["acces_droite"][i], float(not gauche))
    return modele.addSol(solution)


def contraindre_amorce_heuristique(c, chs, indice):
    """Fixe brièvement le placement afin que SCIP complète lui-même ses variables auxiliaires."""
    modele = c["modele"]
    contraintes = []
    for p, ch in enumerate(chs):
        contraintes.append(modele.addCons(c["u"][p] == 1))
        for b in ch.boites:
            i = indice[id(b.colis)]
            for q in c["P"]:
                contraintes.append(modele.addCons(c["a"][i, q] == int(q == p)))
            x, y, z = _centre_modele(b, ch)
            contraintes.extend((modele.addCons(c["x"][i] == x), modele.addCons(c["y"][i] == y),
                                modele.addCons(c["z"][i] == z)))
            contraintes.append(modele.addCons(c["b"][i, _indice_orientation(c, i, b)] == 1))
            if i in c["acces_gauche"]:
                gauche = _cote_acces(b, ch)
                contraintes.append(modele.addCons(c["acces_gauche"][i] == int(gauche)))
                contraintes.append(modele.addCons(c["acces_droite"][i] == int(not gauche)))
    return contraintes


def memoriser_solution(c, solution):
    variables = c["modele"].getVars(transformed=False)
    return variables, {v.name: float(c["modele"].getSolVal(solution, v)) for v in variables}


def restaurer_solution(modele, variables, sauvegarde):
    depart = modele.createSol()
    for v in variables:
        modele.setSolVal(depart, v, sauvegarde[v.name])
    modele.addSol(depart)


def _extraire(c, sauvegarde, chs_modeles: list[_Chargement], colis_liste: list[Colis]) -> list[_Chargement]:
    """Reconstruit les chargements (cm, coin minimal) depuis les valeurs SCIP sauvegardées."""
    resultat = []
    for p, ch in enumerate(chs_modeles):
        if sauvegarde[c["u"][p].name] < 0.5:
            continue
        nouveau = _Chargement(ch.type, ch.zone)
        for i, colis in enumerate(colis_liste):
            if sauvegarde[c["a"][i, p].name] < 0.5:
                continue
            o = max(range(len(c["orientations"][i])), key=lambda o: sauvegarde[c["b"][i, o].name])
            li, wi, hi = orientations_cm(colis)[o]      # dimensions exactes en cm (même ordre que le modèle)
            t = ch.type
            x1 = sauvegarde[c["x"][i].name] * 100 - li / 2 + t.largeur / 2
            y1 = sauvegarde[c["y"][i].name] * 100 - wi / 2 + t.longueur / 2
            z1 = sauvegarde[c["z"][i].name] * 100 - hi / 2
            nouveau.boites.append(_Boite(colis, max(0.0, x1) if x1 > -TOL else x1,
                                         max(0.0, y1) if y1 > -TOL else y1,
                                         max(0.0, z1) if z1 > -TOL else z1, li, wi, hi, o))
        if nouveau.boites:
            resultat.append(nouveau)
    return resultat


# ---------------------------------------------------------------------------
# Phase 2 : réduction progressive du nombre d'emplacements (cellule 13)
# ---------------------------------------------------------------------------

def reduire_nombre_palettes(chs: list[_Chargement], params: Parametres, regles: ReglesSeparation,
                            temps_limite: float):
    """Valide l'amorce heuristique puis tente d'utiliser moins d'emplacements.

    Retourne (chargements, info). En cas d'échec de SCIP (amorce non validée, solution rejetée par le
    contrôle indépendant), les chargements d'origine sont renvoyés tels quels.
    """
    from pyscipopt import quicksum
    debut_global = perf_counter()
    info: dict = {"historique": [], "statut": "", "texte": ""}
    colis_liste, indice = _indices_colis(chs)
    positions = [ch.type.positions for ch in chs]
    maximum = sum(positions)

    debut = perf_counter()
    c = _construire_modele(chs, colis_liste, params, regles, temps_limite)
    modele, P, u = c["modele"], c["P"], c["u"]
    duree_modele = perf_counter() - debut

    def somme_positions():
        return quicksum(positions[p] * u[p] for p in P)

    def regler_limite(duree):
        modele.setRealParam("limits/time", modele.getSolvingTime() + max(0.1, duree))

    def solution_respecte(cible):
        if modele.getNSols() == 0:
            return None
        candidate = modele.getBestSol()
        utilisees = sum(positions[p] * float(modele.getSolVal(candidate, u[p])) for p in P)
        return candidate if utilisees <= cible + 0.5 else None

    modele.setObjective(0.0, "minimize")
    contraintes_initiales = [modele.addCons(somme_positions() <= maximum)]
    amorce_acceptee = ajouter_amorce_heuristique(c, chs, indice)
    contraintes_initiales.extend(contraindre_amorce_heuristique(c, chs, indice))
    debut = perf_counter()
    regler_limite(temps_limite)
    modele.optimize()
    duree = perf_counter() - debut
    solution = solution_respecte(maximum)
    statut = str(modele.getStatus())
    info["historique"].append({"cible": maximum, "trouvee": solution is not None, "statut": statut,
                               "duree": round(duree, 3), "noeuds": modele.getNNodes(), "validation_amorce": True})
    info["verification_amorce"] = {"validee_par_scip": solution is not None, "statut_validation": statut,
                                   "duree_validation": round(duree, 3), "partielle_enregistree": bool(amorce_acceptee),
                                   "duree_modele": round(duree_modele, 3)}
    if solution is None:
        info["statut"] = statut
        info["texte"] = (f"Amorce heuristique ({maximum} emplacement(s)) NON VALIDÉE par SCIP (statut {statut}, "
                         f"{duree:.1f} s ; modèle construit en {duree_modele:.1f} s). La solution heuristique est conservée.")
        return chs, info

    variables, sauvegarde = memoriser_solution(c, solution)
    nombre_fixe = maximum
    modele.freeTransform()
    for contrainte in contraintes_initiales:
        modele.delCons(contrainte)
    preuve = maximum == 1
    dernier_statut = str(modele.getStatus())

    # Chaque cible réussie devient la nouvelle solution de secours ; en cas d'échec ou de dépassement du
    # temps, la contrainte de la cible est retirée et la solution précédente reste utilisable.
    for cible in range(maximum - 1, 0, -1):
        contrainte_cible = modele.addCons(somme_positions() <= cible)
        debut = perf_counter()
        regler_limite(temps_limite)
        modele.optimize()
        solution = solution_respecte(cible)
        duree = perf_counter() - debut
        dernier_statut = str(modele.getStatus())
        info["historique"].append({"cible": cible, "trouvee": solution is not None, "statut": dernier_statut,
                                   "duree": round(duree, 3), "noeuds": modele.getNNodes()})
        if solution is not None:
            variables, sauvegarde = memoriser_solution(c, solution)
            nombre_fixe = cible
            if cible == 1:
                preuve = True
                break
            modele.freeTransform()
            continue
        if dernier_statut == "infeasible":
            preuve = True           # aucune solution à "cible" emplacements : la précédente est minimale
        modele.freeTransform()
        modele.delCons(contrainte_cible)
        break

    nouveaux = _extraire(c, sauvegarde, chs, colis_liste)
    # Contrôle indépendant : on n'accepte la solution SCIP que si chaque palette respecte toutes les règles.
    if len(colis_liste) != sum(len(n.boites) for n in nouveaux) or not all(_charge_valide(n, params, regles) for n in nouveaux):
        info["statut"] = "rejetée"
        info["texte"] = ("La solution SCIP a été rejetée par le contrôle indépendant (tolérance numérique ou règle "
                         "non modélisée) : la solution heuristique est conservée.")
        return chs, info
    info["statut"] = "optimal" if preuve else dernier_statut
    essais = " ; ".join(f"≤ {e['cible']} emplacement(s) : {'solution' if e['trouvee'] else 'aucune solution'}, "
                        f"{e['duree']:.1f} s, {e['noeuds']} nœuds, {e['statut']}" for e in info["historique"])
    info["texte"] = (f"Amorce VALIDÉE ({maximum} emplacement(s), {info['verification_amorce']['duree_validation']:.1f} s). "
                     f"Recherche progressive : {essais}. Résultat : {sum(n.type.positions for n in nouveaux)} emplacement(s), "
                     + ("minimum prouvé." if preuve else "minimum non prouvé.")
                     + f" Temps total de la phase : {perf_counter() - debut_global:.1f} s.")
    return nouveaux, info


# ---------------------------------------------------------------------------
# Compatibilité : point d'entrée direct
# ---------------------------------------------------------------------------

def optimiser(colis: list[Colis], params: Parametres | None = None,
              regles: ReglesSeparation | None = None) -> Resultat:
    """Pipeline complet avec le moteur SCIP (équivalent à ``optimisation.optimiser`` avec moteur = "scip")."""
    from . import optimisation
    params = params or Parametres()
    params.moteur = "scip"
    return optimisation.optimiser(colis, params, regles or regles_par_defaut())
