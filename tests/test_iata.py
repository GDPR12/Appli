import itertools

from palettiseur.iata import CLASSES, classe, regles


def test_tableau_symetrique():
    r = regles()
    codes = [c.code for c in CLASSES if c.code]
    for a, b in itertools.product(codes, codes):
        assert r.compatibles(a, b) == r.compatibles(b, a), (a, b)


def test_paires_du_tableau_9_3_A():
    r = regles()
    incompatibles = [("1", "2.1"), ("1", "3"), ("1", "8"), ("1", "9"), ("2.1", "9"), ("3", "5.1"),
                     ("3", "9"), ("4.1", "9"), ("4.2", "5.1"), ("4.3", "8"), ("5.1", "9"), ("1", "5.2"), ("1", "9L")]
    for a, b in incompatibles:
        assert not r.compatibles(a, b), (a, b)
    compatibles = [("2.2", "3"), ("2.1", "3"), ("5.2", "8"), ("1.4S", "1"), ("1.4S", "3"),
                   ("6.1", "1"), ("7", "3"), ("", "1"), ("3", "3"), ("4.1", "4.2"), ("8", "9"), ("2.2", "9"), ("9", "9")]
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


# Copie exacte de INCOMPATIBILITES_IATA du notebook CDP_2 (classe 9 = colonne « 9 voir 9.3.2.1.3 »)
PAIRES_CDP2 = {
    frozenset(("1", "2.1")), frozenset(("1", "2.2")), frozenset(("1", "2.3")), frozenset(("1", "3")),
    frozenset(("1", "4.1")), frozenset(("1", "4.2")), frozenset(("1", "4.3")), frozenset(("1", "5.1")),
    frozenset(("1", "5.2")), frozenset(("1", "8")), frozenset(("1", "9")), frozenset(("2.1", "9")),
    frozenset(("3", "5.1")), frozenset(("3", "9")), frozenset(("4.1", "9")), frozenset(("4.2", "5.1")),
    frozenset(("4.3", "8")), frozenset(("5.1", "9")),
}
CLASSES_CDP2 = ["1", "2.1", "2.2", "2.3", "3", "4.1", "4.2", "4.3", "5.1", "5.2", "8", "9"]


def test_paires_identiques_au_notebook_cdp2():
    r = regles()
    app = {frozenset((a, b)) for a in CLASSES_CDP2 for b in CLASSES_CDP2 if a != b and not r.compatibles(a, b)}
    assert app == PAIRES_CDP2


def test_ancien_code_9L_equivaut_a_la_classe_9():
    r = regles()
    for autre in CLASSES_CDP2:
        assert r.compatibles("9L", autre) == r.compatibles("9", autre)
