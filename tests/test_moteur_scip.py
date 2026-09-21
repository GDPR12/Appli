import pytest

from palettiseur.modeles import Colis, Parametres
from palettiseur.verification import verifier

pyscipopt = pytest.importorskip("pyscipopt")
from palettiseur import moteur_scip  # noqa: E402


def test_scip_petite_instance():
    colis = [Colis("1", 80, 60, 50, 120), Colis("2", 70, 50, 40, 90, renversable=False),
             Colis("3", 60, 60, 60, 100, classe="3"), Colis("4", 60, 60, 60, 100, classe="5.1")]
    r = moteur_scip.optimiser(colis, Parametres(duree_recherche=15))
    assert r.moteur == "scip" and not r.non_places
    assert len(r.palettes) == 2  # 3 et 5.1 incompatibles
    assert [m for m in verifier(r) if m.niveau == "erreur"] == []
    for pal in r.palettes:
        for p in pal.placements:
            if p.colis.numero == "2":
                assert p.dz == 40
            if p.colis.classe:
                assert p.x <= 0.5 or p.x2 >= pal.type.largeur - 0.5
