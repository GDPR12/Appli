import itertools

from palettiseur.iata import CLASSES, classe, regles


def test_tableau_symetrique():
    r = regles()
    codes = [c.code for c in CLASSES if c.code]
    for a, b in itertools.product(codes, codes):
        assert r.compatibles(a, b) == r.compatibles(b, a), (a, b)


def test_paires_du_tableau_9_3_A():
    r = regles()
    incompatibles = [("1", "2.1"), ("1", "3"), ("1", "8"), ("1", "9L"), ("2.1", "9L"), ("3", "5.1"),
                     ("3", "9L"), ("4.1", "9L"), ("4.2", "5.1"), ("4.3", "8"), ("5.1", "9L"), ("1", "5.2")]
    for a, b in incompatibles:
        assert not r.compatibles(a, b), (a, b)
    compatibles = [("2.2", "3"), ("2.1", "3"), ("5.2", "8"), ("1.4S", "1"), ("1.4S", "3"), ("9", "1"),
                   ("6.1", "1"), ("7", "3"), ("", "1"), ("3", "3"), ("4.1", "4.2"), ("8", "9L")]
    for a, b in compatibles:
        assert r.compatibles(a, b), (a, b)


def test_armement():
    r = regles()
    assert not r.compatibles("ARME", "SECU")
    assert r.compatibles("ARME", "ARME") and r.compatibles("ARME", "3")
    assert "pièce de sécurité" in r.motif_incompatibilite("ARME", "SECU")


def test_classe_inconnue_et_avertissement():
    assert classe("42").libelle == "Classe 42"
    assert classe("").iata is False and classe("3").iata is True
    assert regles().avertissement("1", "1")
    assert regles().avertissement("3", "3") is None
