"""Moteur exact : programme linéaire en nombres entiers résolu par SCIP.

Adaptation du modèle du notebook ``CDP.ipynb`` (placement géométrique 3D,
support à 90 %, cohésion du chargement, objectifs hiérarchisés : nombre de
palettes, hauteurs, recentrage, compacité) avec les contraintes du projet
qui manquaient au notebook :

- séparation IATA (tableau 9.3.A) et armement : deux colis incompatibles ne
  partagent jamais une palette ;
- règle « jamais un colis lourd sur un colis plus léger » : le contact de
  support est interdit si m_i > facteur x m_j ;
- colis fragiles : aucun colis ne peut reposer dessus ;
- colis IATA contre une face côté couloir (x = ±L/2) ;
- palettes BIPAL/TRIPAL candidates pour les colis trop longs, objectif
  pondéré par le nombre d'emplacements occupés.

Le modèle est exact mais combinatoire : il est réservé aux petites listes
(≈ 10 colis). Unités internes : mètres et kilogrammes (comme le notebook) ;
la conversion depuis les centimètres de l'application est faite ici.
PySCIPOpt est importé paresseusement : l'application fonctionne sans lui.
"""

from __future__ import annotations

from itertools import combinations, permutations
from time import perf_counter

from .geometrie import tient_dans
from .iata import ReglesSeparation, classe as classe_de, regles as regles_par_defaut
from .modeles import Colis, Palette, Parametres, Placement, Resultat, TypePalette


def disponible() -> tuple[bool, str]:
    try:
        import pyscipopt  # noqa: F401
        return True, f"PySCIPOpt {getattr(pyscipopt, '__version__', '')}".strip()
    except ImportError:
        return False, "pip install pyscipopt"


# ---------------------------------------------------------------------------
# Construction du modèle
# ---------------------------------------------------------------------------

def _candidats_palettes(colis: list[Colis], params: Parametres) -> list[TypePalette]:
    """Palettes candidates : autant de 10k que d'emplacements, plus les BIPAL/TRIPAL nécessaires."""
    types = params.types_palettes()
    candidats: list[TypePalette] = []
    for c in colis:
        for t in types:
            if c.masse <= t.masse_max and tient_dans(c, t.largeur, t.longueur, t.hauteur_max):
                if t.positions > 1:
                    candidats.append(t)
                break
    nb_simples = max(1, min(len(colis), params.positions_soute + params.positions_rampe) - sum(t.positions for t in candidats))
    candidats.extend([types[0]] * nb_simples)
    return candidats


def _creer_modele(colis: list[Colis], palettes: list[TypePalette], params: Parametres,
                  regles: ReglesSeparation, afficher_logs: bool):
    from pyscipopt import Model, quicksum

    n, P = len(colis), range(len(palettes))
    I = range(n)
    modele = Model("Palettisation_A400M")
    modele.setBoolParam("nlp/disable", True)
    modele.setRealParam("limits/time", float(params.duree_recherche))
    if not afficher_logs:
        modele.hideOutput()

    # Dimensions en mètres
    dims_pal = [(t.largeur / 100, t.longueur / 100, t.hauteur_max / 100, t.masse_max) for t in palettes]
    Lmax = max(d[0] for d in dims_pal)
    Wmax = max(d[1] for d in dims_pal)
    Hmax = max(d[2] for d in dims_pal)

    a = {(i, p): modele.addVar(vtype="B", name=f"a_{i}_{p}") for i in I for p in P}
    u = {p: modele.addVar(vtype="B", name=f"u_{p}") for p in P}
    x = {i: modele.addVar(lb=-Lmax / 2, ub=Lmax / 2, name=f"x_{i}") for i in I}
    y = {i: modele.addVar(lb=-Wmax / 2, ub=Wmax / 2, name=f"y_{i}") for i in I}
    z = {i: modele.addVar(lb=0, ub=Hmax, name=f"z_{i}") for i in I}

    # Orientations et dimensions orientées
    orientations, b, dimensions = {}, {}, {}
    for i, c in enumerate(colis):
        l, w, h = c.longueur / 100, c.largeur / 100, c.hauteur / 100
        choix = list(permutations((l, w, h))) if c.renversable else [(l, w, h), (w, l, h)]
        orientations[i] = list(dict.fromkeys(choix))
        for o in range(len(orientations[i])):
            b[i, o] = modele.addVar(vtype="B", name=f"b_{i}_{o}")
        modele.addCons(quicksum(b[i, o] for o in range(len(orientations[i]))) == 1)
        dimensions[i] = tuple(quicksum(d[k] * b[i, o] for o, d in enumerate(orientations[i])) for k in range(3))
        modele.addCons(quicksum(a[i, p] for p in P) == 1)

    # Capacité, maintien dans la palette
    for p, (L, W, H, M) in enumerate(dims_pal):
        modele.addCons(quicksum(colis[i].masse * a[i, p] for i in I) <= M * u[p])
        modele.addCons(u[p] <= quicksum(a[i, p] for i in I))
        for i in I:
            modele.addCons(a[i, p] <= u[p])
            li, wi, hi = dimensions[i]
            for contrainte in (x[i] + li / 2 <= L / 2, -x[i] + li / 2 <= L / 2,
                               y[i] + wi / 2 <= W / 2, -y[i] + wi / 2 <= W / 2,
                               z[i] + hi / 2 <= H, -z[i] + hi / 2 <= 0):
                modele.addConsIndicator(contrainte, binvar=a[i, p])

    # Symétries entre palettes identiques
    groupes: dict[tuple, list[int]] = {}
    for p, d in enumerate(dims_pal):
        groupes.setdefault(d, []).append(p)
    for indices in groupes.values():
        for prec, suiv in zip(indices, indices[1:]):
            modele.addCons(u[prec] >= u[suiv])

    # Séparation IATA / armement (nouveau)
    for i, j in combinations(I, 2):
        if regles.motif_incompatibilite(colis[i].classe, colis[j].classe):
            for p in P:
                modele.addCons(a[i, p] + a[j, p] <= 1)

    # Non-chevauchement
    for i, j in combinations(I, 2):
        separations = []
        for k, coord in enumerate((x, y, z)):
            demi = (dimensions[i][k] + dimensions[j][k]) / 2
            for premier, second in ((i, j), (j, i)):
                s = modele.addVar(vtype="B", name=f"s_{premier}_{second}_{k}")
                modele.addConsIndicator(coord[premier] + demi <= coord[second], binvar=s)
                separations.append(s)
        for p in P:
            modele.addCons(quicksum(separations) >= a[i, p] + a[j, p] - 1)

    # Support minimal (gravité) + règle de masse + fragilité (nouveau)
    contacts_support = {}
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
            contact = modele.addVar(vtype="B", name=f"contact_{i}_{j}")  # i repose sur j
            contacts_support[i, j] = contact
            if colis[j].fragile or colis[i].masse > params.facteur_masse * colis[j].masse + 1e-9:
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
        s = params.support_minimal
        modele.addCons(quicksum(aires) >= s * base_i - s * base_max * sol_i)

    # Colis IATA contre une face côté couloir (nouveau)
    if params.iata_au_bord:
        jeu = params.jeu_contact / 100
        for i in I:
            if not classe_de(colis[i].classe).iata:
                continue
            gauche = modele.addVar(vtype="B", name=f"bord_gauche_{i}")
            droite = modele.addVar(vtype="B", name=f"bord_droit_{i}")
            modele.addCons(gauche + droite >= 1)
            li = dimensions[i][0]
            for p, (L, W, H, M) in enumerate(dims_pal):
                # si le colis est sur la palette p et gauche = 1 : x - l/2 <= -L/2 + jeu
                g_p = modele.addVar(vtype="B", name=f"bord_gauche_{i}_{p}")
                d_p = modele.addVar(vtype="B", name=f"bord_droit_{i}_{p}")
                modele.addCons(g_p >= gauche + a[i, p] - 1)
                modele.addCons(d_p >= droite + a[i, p] - 1)
                modele.addConsIndicator(x[i] - li / 2 <= -L / 2 + jeu, binvar=g_p)
                modele.addConsIndicator(-x[i] - li / 2 <= -L / 2 + jeu, binvar=d_p)

    # Hauteurs utilisées
    T = {p: modele.addVar(lb=0, ub=dims_pal[p][2], name=f"T_{p}") for p in P}
    for p in P:
        modele.addCons(T[p] <= dims_pal[p][2] * u[p])
        for i in I:
            modele.addConsIndicator(z[i] + dimensions[i][2] / 2 <= T[p], binvar=a[i, p])

    # Recentrage et compacité (linéarisation du produit a[i,p] * x[i])
    ecarts_centre, distances = [], []
    for p, (L, W, H, M) in enumerate(dims_pal):
        bx, by = L / 2, W / 2
        xs, ys = {}, {}
        for i in I:
            xs[i] = modele.addVar(lb=-bx, ub=bx, name=f"x_sur_palette_{i}_{p}")
            ys[i] = modele.addVar(lb=-by, ub=by, name=f"y_sur_palette_{i}_{p}")
            modele.addCons(xs[i] <= x[i] + Lmax / 2 * (1 - a[i, p]))
            modele.addCons(xs[i] >= x[i] - Lmax / 2 * (1 - a[i, p]))
            modele.addCons(xs[i] <= bx * a[i, p])
            modele.addCons(xs[i] >= -bx * a[i, p])
            modele.addCons(ys[i] <= y[i] + Wmax / 2 * (1 - a[i, p]))
            modele.addCons(ys[i] >= y[i] - Wmax / 2 * (1 - a[i, p]))
            modele.addCons(ys[i] <= by * a[i, p])
            modele.addCons(ys[i] >= -by * a[i, p])
            dxv = modele.addVar(lb=0, ub=bx, name=f"distance_x_{i}_{p}")
            dyv = modele.addVar(lb=0, ub=by, name=f"distance_y_{i}_{p}")
            modele.addCons(dxv >= xs[i]); modele.addCons(dxv >= -xs[i])
            modele.addCons(dyv >= ys[i]); modele.addCons(dyv >= -ys[i])
            modele.addCons(dxv <= bx * a[i, p]); modele.addCons(dyv <= by * a[i, p])
            distances.append(dxv / bx + dyv / by)
        mx = quicksum(colis[i].masse * xs[i] for i in I)
        my = quicksum(colis[i].masse * ys[i] for i in I)
        ex = modele.addVar(lb=0, name=f"ecart_centre_x_{p}")
        ey = modele.addVar(lb=0, name=f"ecart_centre_y_{p}")
        modele.addCons(ex >= mx); modele.addCons(ex >= -mx)
        modele.addCons(ey >= my); modele.addCons(ey >= -my)
        ecarts_centre.append(ex / (M * bx)); ecarts_centre.append(ey / (M * by))

    return {"modele": modele, "quicksum": quicksum, "I": I, "P": P, "a": a, "u": u, "x": x, "y": y, "z": z,
            "b": b, "orientations": orientations, "T": T, "dims_pal": dims_pal,
            "objectif_recentrage": quicksum(ecarts_centre), "objectif_compacite": quicksum(distances)}


# ---------------------------------------------------------------------------
# Résolution hiérarchique (nombre d'emplacements > hauteurs > recentrage > compacité)
# ---------------------------------------------------------------------------

def _memoriser(c, solution):
    variables = c["modele"].getVars(transformed=False)
    return variables, {v.name: float(c["modele"].getSolVal(solution, v)) for v in variables}


def _restaurer(modele, variables, sauvegarde):
    depart = modele.createSol()
    for v in variables:
        modele.setSolVal(depart, v, sauvegarde[v.name])
    modele.addSol(depart)


def _etape(c, objectif, contrainte_precedente, etat, temps_limite):
    modele = c["modele"]
    if etat is not None:
        modele.freeTransform()
        if contrainte_precedente is not None:
            modele.addCons(contrainte_precedente)
    modele.setObjective(objectif, "minimize")
    if etat is not None:
        _restaurer(modele, etat["variables"], etat["sauvegarde"])
        modele.setRealParam("limits/time", modele.getSolvingTime() + float(temps_limite))
    modele.optimize()
    statut = str(modele.getStatus())
    if modele.getNSols() == 0:
        return None, statut
    solution = modele.getBestSol()
    variables, sauvegarde = _memoriser(c, solution)
    return {"variables": variables, "sauvegarde": sauvegarde, "statut": statut,
            "valeur": float(modele.getSolObjVal(solution))}, statut


def optimiser(colis: list[Colis], params: Parametres | None = None, regles: ReglesSeparation | None = None,
              afficher_logs: bool = False) -> Resultat:
    params = params or Parametres()
    regles = regles or regles_par_defaut()
    debut = perf_counter()
    types = params.types_palettes()
    non_places: list[tuple[Colis, str]] = []
    valides: list[Colis] = []
    for c in colis:
        erreurs = c.valider()
        if erreurs:
            non_places.append((c, "saisie invalide : " + ", ".join(erreurs)))
        elif not any(c.masse <= t.masse_max and tient_dans(c, t.largeur, t.longueur, t.hauteur_max) for t in types):
            non_places.append((c, "ne tient sur aucun format de palette : à charger en vrac"))
        else:
            valides.append(c)
    if not valides:
        return Resultat([], non_places, params, perf_counter() - debut, 0, "scip", "vide")

    palettes = _candidats_palettes(valides, params)
    c = _creer_modele(valides, palettes, params, regles, afficher_logs)
    modele, qs, P, u, T = c["modele"], c["quicksum"], c["P"], c["u"], c["T"]
    tol = 1e-6
    statuts = []

    # 1. Nombre d'emplacements occupés
    etat, statut = _etape(c, qs(palettes[p].positions * u[p] for p in P), None, None, params.duree_recherche)
    statuts.append(statut)
    if etat is None:
        return Resultat([], non_places + [(x, f"SCIP : aucune solution ({statut})") for x in valides], params,
                        perf_counter() - debut, 1, "scip", statut)
    fixe = round(etat["valeur"])
    # 2. Somme des hauteurs
    etat2, statut = _etape(c, qs(T[p] for p in P), qs(palettes[p].positions * u[p] for p in P) <= fixe + tol,
                           etat, params.duree_recherche)
    statuts.append(statut)
    if etat2 is not None:
        etat = etat2
        # 3. Recentrage
        etat3, statut = _etape(c, c["objectif_recentrage"], qs(T[p] for p in P) <= etat["valeur"] + tol, etat,
                               params.duree_recherche)
        statuts.append(statut)
        if etat3 is not None:
            etat = etat3
            # 4. Compacité
            etat4, statut = _etape(c, c["objectif_compacite"], c["objectif_recentrage"] <= etat["valeur"] + tol,
                                   etat, params.duree_recherche)
            statuts.append(statut)
            if etat4 is not None:
                etat = etat4

    # Extraction
    sauvegarde = etat["sauvegarde"]
    val = lambda v: sauvegarde[v.name]  # noqa: E731
    resultat_palettes: list[Palette] = []
    numero = 0
    for p in P:
        contenu = []
        for i, colis_i in enumerate(valides):
            if val(c["a"][i, p]) > 0.5:
                o = max(range(len(c["orientations"][i])), key=lambda o: val(c["b"][i, o]))
                dx, dy, dz = (d * 100 for d in c["orientations"][i][o])
                L, W = palettes[p].largeur, palettes[p].longueur
                contenu.append(Placement(colis_i, val(c["x"][i]) * 100 + L / 2 - dx / 2,
                                         val(c["y"][i]) * 100 + W / 2 - dy / 2,
                                         max(0.0, val(c["z"][i]) * 100 - dz / 2), dx, dy, dz, o))
        if contenu:
            numero += 1
            pal = Palette(numero, palettes[p], contenu, "soute")
            resultat_palettes.append(pal)
    positions = 0
    for pal in resultat_palettes:
        positions += pal.type.positions
        if positions > params.positions_soute and pal.type.positions == 1:
            pal.zone = "rampe"
    statut_global = "optimal" if all(s == "optimal" for s in statuts) else " / ".join(statuts)
    return Resultat(resultat_palettes, non_places, params, perf_counter() - debut, len(statuts), "scip", statut_global)
