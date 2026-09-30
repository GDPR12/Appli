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
