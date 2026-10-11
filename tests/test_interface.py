"""Parcours complet de l'interface dans Chromium (ignoré si Playwright ou le navigateur sont absents).

Si la variable d'environnement PALETTISEUR_CAPTURES désigne un dossier, les captures d'écran du README y sont écrites.
"""

import os
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
from palettiseur.serveur import Serveur  # noqa: E402

CHROMIUM = "/opt/pw-browsers/chromium"


@pytest.fixture(scope="module")
def app():
    serveur = Serveur().demarrer()
    with sync_api.sync_playwright() as pw:
        options = {"args": ["--no-sandbox", "--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"]}
        if os.path.exists(CHROMIUM):
            options["executable_path"] = CHROMIUM
        try:
            navigateur = pw.chromium.launch(**options)
        except Exception as e:  # navigateur non installé
            serveur.arreter()
            pytest.skip(f"Chromium indisponible : {e}")
        page = navigateur.new_page(viewport={"width": 1480, "height": 920})
        page.on("dialog", lambda d: d.accept())
        yield page, serveur
        navigateur.close()
    serveur.arreter()


def test_parcours_complet(app):
    page, serveur = app
    dossier = os.environ.get("PALETTISEUR_CAPTURES")
    if dossier:
        Path(dossier).mkdir(parents=True, exist_ok=True)

    def capture(nom):
        if dossier:
            page.wait_for_timeout(400)
            page.screenshot(path=str(Path(dossier) / f"{nom}.png"))

    erreurs = []
    page.on("pageerror", lambda e: erreurs.append(str(e)))
    page.goto(serveur.url)
    page.wait_for_function("window.palettiseur && window.palettiseur.etat.config")

    # --- saisie manuelle d'un colis spécial
    page.fill("#f-longueur", "80"); page.fill("#f-largeur", "60"); page.fill("#f-hauteur", "50")
    page.fill("#f-masse", "120")
    page.select_option("#f-classe", "3")
    page.uncheck("#f-renversable")
    page.fill("#f-commentaire", "Carburant")
    page.click("#btn-ajouter")
    colis = page.evaluate("window.palettiseur.etat.colis")
    assert len(colis) == 1 and colis[0]["classe"] == "3" and colis[0]["renversable"] is False

    # --- numéro en double refusé par la validation de l'application
    page.fill("#f-numero", colis[0]["numero"])
    page.fill("#f-longueur", "10"); page.fill("#f-largeur", "10"); page.fill("#f-hauteur", "10"); page.fill("#f-masse", "5")
    page.click("#btn-ajouter")
    assert page.is_visible("#erreur-formulaire") and "existe déjà" in page.inner_text("#erreur-formulaire")
    assert len(page.evaluate("window.palettiseur.etat.colis")) == 1

    # --- exemple du notebook : 54 colis et ses paramètres
    page.select_option("#sel-exemple", "cdp")
    page.wait_for_function("window.palettiseur.etat.colis.length === 54")
    assert page.evaluate("window.palettiseur.etat.parametres.derogation_couloir") is True
    page.evaluate("window.palettiseur.etat.parametres.duree_recherche = 3")

    # --- calcul
    page.click("#btn-optimiser")
    page.wait_for_function("window.palettiseur.etat.resultat !== null", timeout=60000)
    res = page.evaluate("({n: window.palettiseur.etat.resultat.palettes.length, err: window.palettiseur.etat.resultat.verification.filter(m => m.niveau === 'erreur').length})")
    assert res == {"n": 2, "err": 0}

    # --- étapes et journal
    assert page.locator("button.etape").count() == 2
    assert page.locator("#liste-journal li").count() == 5
    page.click("button.etape >> nth=0")
    assert page.evaluate("window.palettiseur.etat.etape") == "heuristique"
    assert page.locator("button.onglet").count() == 1 + len(page.evaluate("window.palettiseur.etat.resultat.etapes[0].palettes"))
    capture("etape_heuristique")
    page.click("button.etape >> nth=1")
    assert page.evaluate("window.palettiseur.etat.etape") == "finale"
    capture("vue_ensemble")

    # --- palette 1 et modes de couleur
    page.click(".onglet[data-palette='1']")
    page.wait_for_timeout(500)
    for mode, nom in (("carte_tous", "carte_tous"), ("carte_speciaux", None), ("poids", "heat_map"), ("categories", None)):
        page.click(f"button.mode[data-mode='{mode}']")
        if nom:
            capture(nom)

    # --- clic sur un colis de la vue 3D
    point = page.evaluate("""() => {
      const v = window.palettiseur.vue();
      const b = [...v.boites.values()].find(x => x.placement.dz > 40);
      const pos = b.mesh.getWorldPosition(new b.mesh.position.constructor()); pos.project(v.camera);
      const r = v.renderer.domElement.getBoundingClientRect();
      const x = r.left + (pos.x + 1) / 2 * r.width, y = r.top + (1 - pos.y) / 2 * r.height;
      const hit = v.intersection({clientX: x, clientY: y});
      return {x, y, numero: hit ? hit.placement.colis.numero : null};
    }""")
    assert point["numero"] is not None
    page.mouse.click(point["x"], point["y"])
    page.wait_for_timeout(300)
    assert page.evaluate("window.palettiseur.etat.selection.numero") == point["numero"]
    texte = page.inner_text("#contenu-colis")
    assert point["numero"] in texte and "cm" in texte and "kg" in texte
    capture("palette_selection")

    if dossier:
        page.locator("#carte-journal").screenshot(path=str(Path(dossier) / "journal.png"))

    # --- colis dangereux : l'accès IATA est affiché
    page.evaluate("window.palettiseur.selectionnerColis('2')")                 # colis 2 : classe IATA 3
    panneau = page.inner_text("#contenu-colis")
    assert "étiquette visible" in panneau and ("gauche" in panneau or "droite" in panneau)

    # --- fiche palette et paramètres
    page.click("#btn-fiche")
    assert page.locator("#contenu-fiche .page-fiche").count() == 1
    capture("fiche_palette")
    page.click("#btn-fiche-fermer")
    page.click("#btn-parametres")
    assert page.is_visible("[data-param=temps_limite_scip]") and page.is_visible("[data-param=iata_acces]")
    capture("parametres")
    page.click("#btn-params-annuler")

    # --- export du plan
    page.click("#btn-plan-csv")
    page.wait_for_selector("#info-export a")
    assert "plan.csv" in page.inner_text("#info-export")
    assert erreurs == []
