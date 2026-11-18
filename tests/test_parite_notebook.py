"""Parité avec le notebook CDP_5 : on exécute les cellules du vrai notebook (``tests/reference/CDP_5.ipynb``) et on compare
son résultat à celui de l'application, avec les paramètres par défaut (ceux de la cellule 2).

Le notebook étant déterministe tant que sa VND s'arrête d'elle-même (« aucune amélioration dans les recherches
effectuées ») et non au bout du budget, la comparaison est exacte, coordonnées comprises.
"""

import copy
import json
from pathlib import Path

import pytest

from palettiseur import api, import_cargo, notebook_cdp, optimisation as opt
from palettiseur.indicateurs import indicateurs_etat
from palettiseur.iata import regles
from palettiseur.modeles import Colis, Parametres
from palettiseur.ressources import chemin_donnees
from palettiseur.verification import verifier

R = regles()
NOTEBOOK = Path(__file__).parent / "reference" / "CDP_5.ipynb"


@pytest.fixture(scope="module")
def nb():
    """Espace de noms où les cellules 2 à 39 du notebook ont été exécutées (données de la cellule 2 comprises)."""
    cellules = ["".join(c["source"]) for c in json.load(open(NOTEBOOK, encoding="utf-8"))["cells"]]
    ns = {}
    for i in range(1, 39):          # la cellule 1 installe Plotly, les cellules 40 à 43 dessinent et lancent le calcul
        exec(compile(cellules[i], f"cellule {i + 1}", "exec"), ns)
    return ns


def lot_cdp():
    ex = api.exemple("cdp")
    return [Colis.depuis_dict(c) for c in ex["colis"]], Parametres.depuis_dict(ex["parametres"])


def contexte(lot, params):
    a_placer, _ = opt._preparer(lot, params)
    return notebook_cdp.Contexte(a_placer, params, R)


def instantane(res):
    """Palettes d'un résultat du notebook : (id de palette, [(colis, x, y, z, l, w, h)])."""
    return [(c["id"], [(o["id"], o["x"], o["y"], o["z"], o["l_place"], o["w_place"], o["h_place"]) for o in c["colis"]])
            for c in res["palettes"]]


def heuristique_app(ctx):
    with ctx.actif():
        return notebook_cdp.construire_placement_heuristique(ctx.colis_nb, ctx.palettes_nb)


# --------------------------------------------------------------------------- les entrées sont celles de la cellule 2

def test_parametres_par_defaut_sont_ceux_de_la_cellule_2(nb):
    p = Parametres()
    assert p.regrouper_par_dat is nb["regrouper_par_DAT"] and p.temps_limite == nb["temps_limite"] == 180
    assert p.support_minimal == nb["support_minimal"] and p.marge_hauteur / 100 == pytest.approx(nb["marge_hauteur"])
    # aucune règle ajoutée par l'application : ni couloir, ni IATA au bord, ni lourd sur léger, ni tare
    assert (p.reduction_couloir, p.iata_au_bord, p.facteur_masse, p.tare_palette) == (0, False, 0, 0)
    assert p.emplacements_libres() == list(range(1, 10))


def test_palettes_de_l_application_sont_celles_de_la_cellule_2(nb):
    """7 emplacements en soute de 4 536 kg puis 2 sur la rampe de 3 000 kg, tous de 2,74 x 2,24 x 2,54 m."""
    lot, p = lot_cdp()
    ctx = contexte(lot, p)
    assert ctx.palettes_nb[:9] == nb["palettes"]
    assert [pal["M"] for pal in nb["palettes"]] == [4536] * 7 + [3000] * 2
    assert [ctx.zone_de[pal["id"]] for pal in nb["palettes"]] == ["soute"] * 7 + ["rampe"] * 2


def test_colis_de_l_application_sont_ceux_de_la_cellule_2(nb):
    lot, p = lot_cdp()
    ctx = contexte(lot, p)
    assert len(ctx.colis_nb) == len(nb["colis"]) == 54
    for a, b in zip(ctx.colis_nb, nb["colis"]):
        assert (a["id"], a["m"], a["l"], a["w"], a["h"], a["r"], a["DAT"]) == (b["id"], b["m"], b["l"], b["w"], b["h"], b["r"], b["DAT"])
        assert nb["classe_iata"](a) == nb["classe_iata"](b)


# --------------------------------------------------------------------------- heuristique : même résultat sur les 54 colis

@pytest.fixture(scope="module")
def heuristiques(nb):
    """Heuristique du notebook, de l'application sur les colis du notebook, et de l'application sur l'import Excel."""
    lot, p = lot_cdp()
    res_nb = nb["construire_placement_heuristique"](nb["colis"], nb["palettes"])
    res_app = heuristique_app(contexte(lot, p))
    return res_nb, res_app


def test_heuristique_identique_au_notebook(heuristiques):
    res_nb, res_app = heuristiques
    essais = lambda r: [(e["ordre"], e["plateformes"], e["palettes"], e["hauteur"], e["positions"]) for e in r["heuristique"]["essais"]]
    assert essais(res_app) == essais(res_nb)
    assert [(e["ordre"], e["palettes"], round(e["hauteur"], 2), e["positions"]) for e in res_nb["heuristique"]["essais"]] == [
        (1, 3, 4.38, 59426), (2, 3, 3.44, 59683), (3, 3, 3.59, 59716), (4, 4, 3.77, 58529), (5, 3, 5.07, 53160)]
    assert res_app["heuristique"]["ordre_retenu"] == res_nb["heuristique"]["ordre_retenu"] == 2
    assert res_app["heuristique"]["positions_testees"] == res_nb["heuristique"]["positions_testees"] == 290514
    assert instantane(res_app) == instantane(res_nb)         # chaque colis exactement à la même position
    assert [len(c["colis"]) for c in res_nb["palettes"]] == [13, 23, 18]
    assert [round(c["hauteur_utilisee"], 2) for c in res_nb["palettes"]] == [1.15, 1.07, 1.22]


def test_import_excel_avec_matieres_donne_le_meme_placement_que_le_notebook(nb, heuristiques):
    """Le manifeste Excel porte des matières d'emballage (carton, fibre, bois…) : elles ne doivent rien changer."""
    res_nb, _ = heuristiques
    lu = import_cargo.lire_fichier(chemin_donnees("exemple_cargo.xlsx"))
    lot = [Colis.depuis_dict(c) for c in lu["colis"]]
    assert {c.emballage for c in lot} >= {"carton", "fibre", "bois"}
    # une seule ligne du manifeste n'a pas de mesures (colis 5725) : le notebook utilise celles de CDP_5
    for c in lot:
        if c.numero == "5725":
            c.masse, c.hauteur = 20, 20
    res_app = heuristique_app(contexte(lot, Parametres()))
    assert instantane(res_app) == instantane(res_nb)


# --------------------------------------------------------------------------- fonctions du notebook reprises à l'identique

def test_les_phases_deterministes_sont_les_memes_que_celles_du_notebook(nb, heuristiques):
    """Recentrage, tassement horizontal, indicateurs, score de la VND et contrôle indépendant, appliqués au même état."""
    res_nb, _ = heuristiques
    nb["actualiser_chargement"](res_nb, nb["palettes"])
    mien = copy.deepcopy(res_nb)
    with contexte(*lot_cdp()).actif():                       # mêmes réglages globaux que le notebook
        nb["recentrer_tas_rigidement"](res_nb, nb["palettes"])
        notebook_cdp.recentrer_tas_rigidement(mien, nb["palettes"])
        assert instantane(mien) == instantane(res_nb)
        nb["tasser_colis_vers_centre"](res_nb, nb["palettes"])
        notebook_cdp.tasser_colis_vers_centre(mien, nb["palettes"])
        assert instantane(mien) == instantane(res_nb)
        assert notebook_cdp.calculer_indicateurs(mien, nb["palettes"]) == nb["calculer_indicateurs"](res_nb, nb["palettes"])
        assert notebook_cdp.score_vnd(mien, nb["palettes"]) == nb["score_vnd"](res_nb, nb["palettes"])
        colis = nb["colis"]
        assert notebook_cdp.controler_chargement_independant(mien, colis, nb["palettes"])["erreurs"] == \
            nb["controler_chargement_independant"](res_nb, colis, nb["palettes"])["erreurs"] == []


# --------------------------------------------------------------------------- pipeline complet

@pytest.fixture(scope="module")
def pipeline(nb):
    """Les 30 premiers colis : la VND converge d'elle-même (sans toucher au budget), le résultat est déterministe."""
    lot, p = lot_cdp()
    sous = lot[:30]
    ids = {c.numero for c in sous}
    res_nb = nb["optimiser_palettes"]([c for c in nb["colis"] if c["id"] in ids], nb["palettes"], temps_limite=120)
    ctx = contexte(sous, p)
    sortie = ctx.executer(120)
    return res_nb, sortie, ctx, sous, p


def test_pipeline_complet_identique_au_notebook(pipeline):
    """Heuristique, VND, recentrage et tassement : les trois états ont les mêmes coordonnées que ceux du notebook."""
    res_nb, sortie, ctx, *_ = pipeline
    assert res_nb["vnd"]["arret"] == sortie["resultat"]["vnd"]["arret"] == "aucune amélioration dans les recherches effectuées"
    for etat in ("heuristique", "apres_vnd"):
        assert instantane(sortie["etapes"][etat]) == instantane(res_nb["etapes_visualisation"][etat])
    assert instantane(sortie["resultat"]) == instantane(res_nb)
    assert sortie["resultat"]["nombre_palettes"] == res_nb["nombre_palettes"]
    assert [c["valide"] for c in sortie["controles"]] == [c["valide"] for c in res_nb["controles_independants"]] == [True] * 4
    assert len(sortie["resultat"]["vnd"]["historique"]) == len(res_nb["vnd"]["historique"]) >= 1     # la VND a bien amélioré


def test_indicateurs_de_qualite_identiques_a_ceux_du_notebook(pipeline):
    """Hauteur, compacité, remplissage volumique, centre de masse et décalage : mêmes valeurs que ``calculer_indicateurs``."""
    res_nb, sortie, ctx, *_ = pipeline
    attendu = res_nb["indicateurs"]
    palettes = opt._vers_palettes(ctx.vers_chargements(sortie["resultat"]))
    mesure = indicateurs_etat(palettes)
    assert mesure["nombre_palettes"] == attendu["nombre_palettes"]
    assert mesure["hauteur_maximale"] == pytest.approx(attendu["hauteur_maximale"], abs=1e-9)
    assert mesure["somme_hauteurs"] == pytest.approx(attendu["somme_hauteurs"], abs=1e-9)
    for m, a, charge in zip(mesure["palettes"], attendu["palettes"], res_nb["palettes"]):
        for cle in ("hauteur", "compacite", "remplissage_volumique", "volume_colis", "volume_enveloppe",
                    "centre_masse_x", "centre_masse_y", "decalage_horizontal"):
            assert m[cle] == pytest.approx(a[cle], abs=1e-9, rel=1e-9), (m["palette"], cle)
        assert m["surface_sol"] == pytest.approx(charge["surface_sol"], abs=1e-9)
        assert m["taux_surface_sol"] == pytest.approx(charge["taux_surface_sol"], abs=1e-9)


def test_api_donne_les_memes_indicateurs(pipeline):
    """Le résultat renvoyé à l'interface contient ces indicateurs, pour chaque palette et chaque étape."""
    res_nb, sortie, ctx, sous, p = pipeline
    p2 = copy.copy(p)
    p2.temps_limite = 120
    d = api.resultat_vers_dict(opt.optimiser(sous, p2))
    assert d["moteur"] == "notebook" and not [m for m in d["verification"] if m["niveau"] == "erreur"]
    attendu = res_nb["indicateurs"]
    assert d["indicateurs"]["nombre_palettes"] == attendu["nombre_palettes"]
    assert d["indicateurs"]["hauteur_maximale"] == pytest.approx(attendu["hauteur_maximale"], abs=1e-6)
    assert [e["cle"] for e in d["etapes"]] == ["heuristique", "apres_vnd", "finale"]
    assert all("indicateurs" in e for e in d["etapes"])
    for pal, m in zip(d["palettes"], d["indicateurs"]["palettes"]):
        assert pal["indicateurs"] == m and {"compacite", "remplissage_volumique", "decalage_horizontal"} <= set(m)
        assert pal["emplacements"] and pal["zone"] == "soute"


@pytest.mark.parametrize("regle", [dict(facteur_masse=1), dict(reduction_couloir=14)], ids=["lourd_sur_leger", "couloir"])
def test_les_regles_du_projet_s_ecartent_du_notebook_quand_on_les_active(nb, regle):
    """Contre-épreuve : les règles ajoutées par l'application changent le résultat, d'où leur désactivation par défaut."""
    lot, _ = lot_cdp()
    sous = lot[:30]
    ids = {c.numero for c in sous}
    ref = nb["construire_placement_heuristique"]([c for c in nb["colis"] if c["id"] in ids], nb["palettes"])
    assert instantane(heuristique_app(contexte(sous, Parametres(**regle)))) != instantane(ref)
    assert instantane(heuristique_app(contexte(sous, Parametres()))) == instantane(ref)


# --------------------------------------------------------------------------- un seul budget de temps

def test_un_seul_parametre_de_temps(nb):
    """``temps_limite`` est l'unique budget (celui de la VND du notebook) ; plus de budget propre au moteur rapide."""
    assert "duree_recherche" not in Parametres().vers_dict()
    assert Parametres.depuis_dict({"duree_recherche": 3}).temps_limite == 180
    lot, p = lot_cdp()
    p.temps_limite = 3
    res = opt.optimiser(lot[:12], p)
    assert res.moteur == "notebook" and "budget 3 s" in res.journal[1]["texte"]
    # moteur rapide : chaque phase de recherche peut utiliser tout le budget, jamais davantage
    p = Parametres(moteur="heuristique", temps_limite=0.5)
    res = opt.optimiser(lot[:12], p)
    assert res.moteur == "heuristique" and res.duree < 3 * 0.5 + 3
    assert [m for m in verifier(res) if m.niveau == "erreur"] == []
