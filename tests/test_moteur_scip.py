import pytest

from palettiseur.modeles import Colis, Parametres
from palettiseur.optimisation import optimiser
from palettiseur.verification import verifier

pyscipopt = pytest.importorskip("pyscipopt")
from palettiseur import moteur_scip  # noqa: E402


def petite_liste():
    return [Colis("1", 80, 60, 50, 120), Colis("2", 70, 50, 40, 90, renversable=False),
            Colis("3", 60, 60, 60, 100, classe="3"), Colis("4", 60, 60, 60, 100, classe="5.1"),
            Colis("5", 50, 40, 30, 60), Colis("6", 120, 80, 60, 250, renversable=False)]


def test_pipeline_scip_valide_l_amorce_et_respecte_les_regles():
    res = optimiser(petite_liste(), Parametres(duree_recherche=1.0, temps_limite_scip=20, moteur="scip"))
    assert res.moteur == "scip" and not res.non_places
    assert [e.cle for e in res.etapes] == ["heuristique", "scip", "finale"]
    phase2 = res.journal[1]
    assert not phase2["ignoree"] and "VALIDÉE" in phase2["texte"] and "NON VALIDÉE" not in phase2["texte"]
    assert len(res.palettes) == 2                     # les classes 3 et 5.1 sont incompatibles
    assert [m for m in verifier(res) if m.niveau == "erreur"] == []
    for pal in res.palettes:
        for p in pal.placements:
            if p.colis.numero in ("2", "6"):          # sens obligatoire
                assert p.dz == p.colis.hauteur


def test_scip_reduit_le_nombre_d_emplacements_quand_l_heuristique_en_utilise_trop():
    """Amorce volontairement médiocre (un colis par palette) : SCIP doit regrouper les colis."""
    from palettiseur import optimisation as opt
    from palettiseur.iata import regles
    p = Parametres(derogation_couloir=True)
    chs = []
    for i in range(3):
        ch = opt._Chargement(p.types_palettes()[0])
        ch.boites.append(opt._Boite(Colis(str(i), 50, 50, 50, 40), 0, 0, 0, 50, 50, 50))
        chs.append(ch)
    nouveaux, info = moteur_scip.reduire_nombre_palettes(chs, p, regles(), 20)
    assert "VALIDÉE" in info["texte"] and len(nouveaux) == 1
    assert all(opt._charge_valide(n, p, regles()) for n in nouveaux)


def test_scip_indisponible_est_ignore_proprement(monkeypatch):
    monkeypatch.setattr(moteur_scip, "disponible", lambda: (False, "absent"))
    res = optimiser(petite_liste(), Parametres(duree_recherche=0.5, moteur="scip"))
    assert res.moteur == "heuristique" and res.journal[1]["ignoree"] and "indisponible" in res.journal[1]["texte"]


def _chargement(p, *boites):
    from palettiseur import optimisation as opt
    ch = opt._Chargement(p.types_palettes()[0])
    for colis, x, y, z, dx, dy, dz in boites:
        ch.boites.append(opt._Boite(colis, x, y, z, dx, dy, dz))
    return ch


def test_le_modele_expose_les_variables_d_acces_iata():
    from palettiseur.iata import regles
    p = Parametres(derogation_couloir=True)
    ch = _chargement(p, (Colis("D", 50, 50, 50, 10, classe="3"), 0, 0, 0, 50, 50, 50),
                     (Colis("O", 50, 50, 50, 10), 100, 0, 0, 50, 50, 50),
                     (Colis("E", 50, 50, 50, 10, classe="8"), 0, 100, 0, 50, 50, 50))
    liste, _ = moteur_scip._indices_colis([ch])
    c = moteur_scip._construire_modele([ch], liste, p, regles(), 5)
    assert sorted(c["acces_gauche"]) == [0, 2] and sorted(c["acces_droite"]) == [0, 2]      # D et E sont dangereux
    assert len(c["separations"]) == 3 * 2 * 3                                                 # 3 paires, 2 sens, 3 axes


def _statut_avec_placement_fixe(p, boites):
    from palettiseur.iata import regles
    ch = _chargement(p, *boites)
    liste, indice = moteur_scip._indices_colis([ch])
    c = moteur_scip._construire_modele([ch], liste, p, regles(), 20)
    moteur_scip.contraindre_amorce_heuristique(c, [ch], indice)       # fixe positions, orientations et côté d'accès
    c["modele"].setObjective(0.0, "minimize")
    c["modele"].optimize()
    return str(c["modele"].getStatus())


def test_scip_refuse_un_colis_dangereux_masque_des_deux_cotes():
    p = Parametres(derogation_couloir=True)
    dangereux = (Colis("M", 50, 50, 50, 10, classe="3"), 50, 100, 0, 50, 50, 50)
    devant = (Colis("P", 30, 100, 50, 10), 60, 0, 0, 30, 100, 50)            # y 0-100 : masque le côté gauche
    derriere = (Colis("Q", 30, 70, 50, 10), 60, 150, 0, 30, 70, 50)          # y 150-220 : masque le côté droit
    assert _statut_avec_placement_fixe(p, [dangereux, devant, derriere]) == "infeasible"
    assert _statut_avec_placement_fixe(p, [dangereux, devant]) == "optimal"   # reste visible côté droit


def test_scip_accepte_deux_colis_dangereux_cote_a_cote():
    """Côte à côte en x, ils ne se masquent pas : la contrainte ne doit pas être trop stricte."""
    p = Parametres(derogation_couloir=True)
    a = (Colis("A", 50, 50, 50, 10, classe="3"), 0, 100, 0, 50, 50, 50)
    b = (Colis("B", 50, 50, 50, 10, classe="8"), 50, 100, 0, 50, 50, 50)
    assert _statut_avec_placement_fixe(p, [a, b]) == "optimal"
