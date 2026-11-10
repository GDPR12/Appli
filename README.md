# Palettisation A400M – confection des palettes 10k

Application Windows (Python + interface web locale) qui aide les agents du transit aérien de l'ETAA à
**confectionner les palettes 10k d'un A400M** : saisie des colis, répartition automatique sur le minimum de
palettes en respectant les contraintes du projet, visualisation 3D interactive, fiches palette imprimables.

Le moteur de calcul est celui du **notebook `CDP_5`**, dont le code est repris tel quel : heuristique à cinq essais,
**VND** (transferts entre palettes, replacements plus bas, échanges), recentrage et tassement horizontal, avec un
contrôle indépendant après chaque phase. S'y ajoutent les règles propres à l'application (colis lourds/légers,
fragiles, emballages en carton à ne pas écraser, colis IATA contre un bord, BIPAL…). Les colis d'un même **DAT**
voyagent ensemble, les armes et pièces de sécurité sont les catégories **A** et **B** du notebook, et SCIP a disparu,
comme dans le notebook. Une palette fait **274 cm sur l'axe x et 224 cm sur l'axe y** (`L = 2,74` et `W = 2,24`), avec
**14 cm de couloir de chaque côté sur l'axe des côtés** (l'axe y du notebook, réglable). Un **manifeste Excel** (format
`CARGO.xlsx`) s'importe directement.

![Vue d'ensemble](docs/captures/vue_ensemble.png)

## Sommaire

1. [Fonctionnalités](#fonctionnalités)
2. [Installation et lancement](#installation-et-lancement)
3. [Utilisation](#utilisation)
4. [Règles et contraintes prises en compte](#règles-et-contraintes-prises-en-compte)
5. [Le pipeline du notebook CDP_5](#le-pipeline-du-notebook-cdp_5)
6. [Correspondance avec le notebook CDP_5](#correspondance-avec-le-notebook-cdp_5)
7. [Architecture du code](#architecture-du-code)
8. [Tests](#tests)
9. [Limites et pistes d'amélioration](#limites-et-pistes-damélioration)

---

## Fonctionnalités

| Demande | Réalisation |
|---|---|
| Saisie simple d'un colis | Formulaire : numéro, longueur / largeur / hauteur (cm), masse (kg), menu déroulant **Colis spécial** (classes IATA : explosifs, carburant, gaz, comburants, corrosifs, radioactifs… + armement), **matière d'emballage**, case **Fragile**, commentaire libre (vide par défaut), quantité. Plus de case « renversable » : un colis garde toujours sa hauteur à la verticale. |
| Import d'un manifeste Excel | Bouton **Importer…** (un seul pour tous les formats, reconnus à l'extension puis au contenu) : lit un fichier `.xlsx` au format `CARGO.xlsx` (voir [Import Excel](#import-dun-manifeste-excel)) : armes et pièces de sécurité reconnues dans la désignation, numéro = 4 derniers chiffres de la dernière colonne, matière d'emballage lue, température ignorée. |
| Import des listes MEAT / SILRIA | Import CSV (séparateur `;` `,` ou tabulation, noms de colonnes reconnus automatiquement) et JSON ; export CSV de la liste. |
| Répartition des colis | Pipeline de CDP_5 : cinq essais d'heuristique, VND, recentrage, tassement horizontal (voir [Le pipeline](#le-pipeline-du-notebook-cdp_5)). Le moteur **du notebook** (par défaut) exécute son code tel quel ; il est plus lent. Un **moteur rapide** de l'application (quelques secondes) est proposé et prend le relais pour les BIPAL / TRIPAL. Aucun solveur : SCIP a été retiré, comme dans le notebook. |
| Colis lourd jamais sur colis léger | Contrainte dure dans toutes les phases : un colis ne repose que sur des colis au moins aussi lourds que lui (facteur paramétrable). |
| Colis incompatibles | Tableau IATA 9.3.A (éditable dans `data/iata_9_3_A.json`) + séparation arme / pièce de sécurité : jamais sur la même palette ; avertissement « à ne pas charger côte à côte » entre palettes pour le mécanicien navigant. |
| Colis à étiquette | Règle de CDP_5 (`cotes_accessibles_iata`) : un colis IATA, une arme (catégorie **A**) ou une pièce de sécurité (catégorie **B**) reste **visible depuis l'un des deux côtés** (les extrémités de l'axe y, comme dans le notebook) ; un autre colis ne le masque que si leurs projections se chevauchent sur l'autre axe et en z et qu'il le dépasse de ce côté. Les colis dangereux ont en plus l'obligation (application) d'être **posés contre ce bord**, côté couloir : option « Colis IATA posés sur le bord ». Contour rouge en 3D, bord et côté d'accès dans le détail du colis. |
| Regroupement par DAT | Les colis d'un même **DAT** (colonne « Cargo Nat Ref 1 » du manifeste) voyagent ensemble : un DAT sans conflit reste sur une palette ; un DAT contenant des classes incompatibles (arme et pièce de sécurité, par exemple) est divisé en sous-groupes compatibles, chacun indivisible. Option « Regrouper les colis d'un même DAT » (`regrouper_par_DAT` du notebook). Si les règles rendent ce regroupement impossible, le moteur le relâche et le signale. |
| Cartons non écrasés | Un colis dont l'emballage est en carton ou en fibre (« Box, Fibreboard ») ne porte rien par défaut. Le réglage « Charge maximale sur ces matières » autorise une charge limitée, et la liste « Matières à ne pas écraser » se modifie dans les paramètres (voir [Emballages](#emballages-et-cartons)). |
| Colis fragile | Seule option conservée : rien ne repose sur un colis fragile. |
| Colis plus grands qu'une palette | Création automatique d'une **BIPAL** (2 palettes accouplées, 2,74 × 4,48 m) ou d'une **TRIPAL** ; au-delà, le colis est signalé « à charger en vrac ». |
| Visualisation 3D | Three.js, rotation / zoom / déplacement à la souris, **clic sur un colis** → numéro, dimensions, masse, orientation posée, position, colis porteurs et portés, taux de support. Vue d'ensemble ou palette par palette, vue de dessus, transparence. |
| Code couleur | Mode **Catégories** : colis ordinaires en gris, une couleur par classe de colis spécial (légende). |
| Heat map | Mode **Heat map poids** : couleur du colis proportionnelle à sa masse (échelle commune à toutes les palettes). |
| « Raisonnement des cartes » | Modes **Carte spéciaux** / **Carte tous** : deux colis en contact n'ont jamais la même couleur (coloriage du graphe de contact par l'algorithme DSatur, comme les régions frontalières d'une carte). |
| Comparer les étapes du calcul | Comme les visualisations du notebook : un sélecteur d'**étape** affiche en 3D le placement heuristique, l'état après la VND et la solution finale. |
| Journal de calcul | Durée, palettes et somme des hauteurs de chaque phase (heuristique, VND, recentrage, tassement horizontal), avec le détail : les cinq essais, les voisinages de la VND (appels, améliorations, rejets), les contrôles indépendants. Un clic sur une phase affiche l'état correspondant. |
| Exemple du notebook | Menu **Exemples…** → « CDP – 54 colis du notebook » : les colis et les paramètres de CDP_5 (numéros à 4 chiffres, DAT, catégories A et B, palettes 7 + 2 de 2,54 m de haut) ; « CARGO » (le manifeste Excel avec les règles de l'application) ou un jeu mixte avec matières dangereuses, armement et BIPAL. |
| Autres éléments issus des documents | Palette de 274 × 224 cm avec 14 cm de couloir de chaque côté de l'axe des côtés, soit 274 × 196 cm utiles (dérogation possible), palettes rampe limitées à 3 t, emplacements selon le nombre de passagers (table « Version A400M »), masse totale avion (fret + tare + passagers 90 kg + bagages 40 kg), fiche palette imprimable avec plan de dessus, vérification indépendante du plan (erreurs / avertissements cliquables), enregistrement du projet. |

![Sélection d'un colis](docs/captures/palette_selection.png)

## Installation et lancement

### Sur Windows, depuis les sources

1. Installer **Python 3.10 ou plus récent** depuis <https://www.python.org> (cocher *Add python.exe to PATH*).
2. Double-cliquer sur **`lancer.bat`** : il crée un environnement virtuel, installe `pywebview` et ouvre l'application dans une fenêtre native (WebView2 / Edge, présent sur Windows 10 et 11).

   En ligne de commande :

   ```bat
   python -m venv .venv
   .venv\Scripts\pip install -r requirements.txt
   .venv\Scripts\python main.py
   ```

Options : `python main.py --navigateur` ouvre l'interface dans le navigateur par défaut ; `--port 8765` fixe le port ; `--serveur` démarre seulement le serveur local.

### Exécutable Windows autonome

Double-cliquer sur **`build_windows.bat`** (ou `python -m PyInstaller PaletteA400M.spec`). L'exécutable et ses ressources sont produits dans `dist\PaletteA400M\` ; copier ce dossier complet sur le poste cible et lancer `PaletteA400M.exe`. Aucune connexion internet n'est nécessaire (Three.js est embarqué dans `ui/vendor/`).

## Utilisation

1. **Saisir les colis** dans le formulaire de gauche (ou *Exemples…*, ou *Importer…* un manifeste Excel `.xlsx`, un CSV ou un projet JSON : le format est reconnu automatiquement). La liste est conservée automatiquement d'une session à l'autre.
2. **Paramètres** (⚙) : nombre de passagers, dimensions de la palette (274 × 224 cm), axe des côtés, espace de couloir par côté (14 cm), dérogation sans couloir, hauteur maximale, masses limites, règles (support minimal, facteur masse, colis IATA sur le bord, matières à ne pas écraser et charge tolérée dessus), moteur, budget de la VND, marge de hauteur du cinquième essai, regroupement par DAT, support minimal (80 %).
3. **Confectionner les palettes** : le plan apparaît en 3D. Onglets : vue d'ensemble / palette par palette. Boutons de mode : *Catégories*, *Carte spéciaux*, *Carte tous*, *Heat map poids*. Barre **Étape du calcul** : *Phase 1 – placement heuristique*, *Phase 2 – après la VND*, *Solution finale*.
4. **Cliquer sur un colis** (dans la vue 3D, dans la liste ou dans un message de vérification) pour ses détails.
5. **Exports** : *Plan CSV* (une ligne par colis avec palette et position), *Plan JSON*, *Fiche palette* (imprimable, avec plan de dessus et zone de signatures), *Enregistrer* (projet JSON rechargeable). Les fichiers sont écrits dans le dossier `exports/`.

| Étape 1 – placement heuristique | Journal de calcul |
|---|---|
| ![Étape heuristique](docs/captures/etape_heuristique.png) | ![Journal](docs/captures/journal.png) |

| Carte (coloriage) | Heat map de masse |
|---|---|
| ![Carte tous](docs/captures/carte_tous.png) | ![Heat map](docs/captures/heat_map.png) |

Format CSV d'import (l'ordre des colonnes est libre, les alias usuels `poids`, `long`, `larg`, `haut`, `ref`, `sscc`, `type`… sont reconnus) :

```csv
numero;longueur;largeur;hauteur;masse;classe;fragile;emballage;reference;commentaire
C-001;80;60;50;120;;non;bois;;Caisse outillage
JER-01;47;35;35;22;3;non;plastique;;Jerrican carburant
```

Codes d'emballage : `carton`, `fibre`, `bois`, `metal`, `plastique`, `composite`, `palette` ; vide = non précisé. L'ancienne colonne `renversable` est ignorée si elle figure dans un fichier.

Codes de classe : `1`, `1.4S`, `2.1`, `2.2`, `2.3`, `3`, `4.1`, `4.2`, `4.3`, `5.1`, `5.2`, `6.1`, `6.2`, `7`, `8`, `9` (séparée comme les piles au lithium, comme dans le notebook), `9L` (ancien code, équivalent à `9`), `ARME`, `SECU` ; vide = colis ordinaire.

## Import d'un manifeste Excel

Le fichier est toujours au même format que `CARGO.xlsx` (exemple fourni : `data/exemple_cargo.xlsx`, disponible aussi dans *Exemples… → Manifeste CARGO*). Les colonnes sont retrouvées **par leur en-tête**, quel que soit leur ordre ; seule la colonne du numéro de série doit rester la dernière.

| Donnée de l'application | Colonne du manifeste | Règle |
|---|---|---|
| Numéro du colis | dernière colonne (`Cargo Nat Ref 2`) | **4 derniers chiffres** du numéro de série ; le numéro complet est conservé dans « N° de série ». Deux séries qui finissent pareil donnent `0836` puis `0836-2` (avertissement). |
| Dimensions, masse | `Length (cm)`, `Width (cm)`, `Height (cm)`, `Weight (Kg)` | Une ligne sans dimensions est ignorée et signalée. Une masse nulle devient 0,1 kg avec un avertissement (sur l'exemple : colis 5725). |
| DAT | `Cargo Nat Ref 1` | **Chaîne complète**, sans troncature : les colis d'un même DAT voyagent ensemble (regroupement par DAT de CDP_5). |
| Classe | `Class/Div` | Classe IATA lue telle quelle. Si elle est vide, la désignation (`Proper Shipping Name`) décide : **`SECU`** pour les pièces de sécurité d'armes (`SECURITY PARTS`, `WEAPONRY SECURITY`, « pièce de sécurité »…) et **`ARME`** pour les armes (`WEAPONS`, `WEAPONRY`, « arme », « armement »…). Les pièces de sécurité sont testées d'abord : « WEAPONRY SECURITY PARTS » n'est jamais prise pour une arme. Les mots-clés sont dans `data/import_cargo.json`. |
| Emballage | `Type of Packing` | `Box, Cardboard` → carton, `Box, Fibreboard` → fibre, `Box, Wood` → bois, etc. (`data/emballages.json`). |
| Fragile | – | Jamais déduit : à cocher dans la liste. |
| Température | – | **Ignorée**. |
| Commentaire | `UN/ID No`, désignation, `Sub Haz`, type d'emballage | Pour retrouver le colis ; le risque subsidiaire n'est pas utilisé pour les règles de séparation (classe principale seule, comme dans le notebook). |

Une fenêtre de résumé s'affiche avant l'import (colis lus, armes, pièces de sécurité, colis IATA, colis en carton, avertissements) avec le choix *Remplacer la liste* ou *Ajouter à la liste*. L'exemple fourni donne 54 colis, dont 14 armes, 10 pièces de sécurité et 5 colis IATA (classes 9, 3 et 2.2).

![Import d'un manifeste Excel](docs/captures/import_excel.png)

Le lecteur `.xlsx` n'a besoin d'aucune bibliothèque : il ouvre l'archive et lit le XML avec la bibliothèque standard (taille limitée, entités XML refusées). L'ancien format `.xls` n'est pas lu (message explicite) : enregistrez le fichier en `.xlsx`. Un fichier ouvert dans Excel peut être refusé par le navigateur : fermez-le ou importez une copie. Attention aux numéros de série de 18 chiffres : si Excel les a enregistrés comme des nombres, leurs derniers chiffres peuvent déjà être arrondis dans le fichier ; l'application le signale, enregistrez alors la colonne au format texte.

## Emballages et cartons

Chaque colis porte une matière d'emballage. Les matières **à ne pas écraser** sont `carton` et `fibre` par défaut (réglage « Matières à ne pas écraser » : liste de codes séparés par des virgules). Un colis dans l'une de ces matières ne porte que la **charge maximale** réglée dans les paramètres, **0 kg par défaut : rien dessus**. La règle s'applique dans toutes les phases (heuristique, VND, tassements) et dans la vérification (erreur « carton »). Avec les DAT, elle peut rendre le regroupement impossible : le moteur le relâche alors et le signale.

Ce choix coûte des palettes. Sur le manifeste d'exemple (54 colis, dont 10 en carton et 18 en fibre), mesuré avec le budget par défaut de l'heuristique :

| Réglage | Palettes |
|---|---|
| Carton et fibre protégés, rien dessus (défaut) | 4 |
| Carton seul protégé | 3 |
| Carton et fibre, 60 kg tolérés dessus | 3 |
| Aucune matière protégée | 2 |

![Manifeste CARGO : 4 palettes](docs/captures/cargo_ensemble.png)

La fibre (« fibreboard ») est traitée comme du carton par défaut : c'est une hypothèse, à modifier dans les paramètres si ces colis supportent une charge.

## Règles et contraintes prises en compte

Repère d'une palette, celui du notebook : X = 274 cm (`L`), Y = 224 cm (`W` ; 448 cm pour une BIPAL, 672 cm pour une TRIPAL), Z vertical. **Les côtés sont les deux extrémités de l'axe des côtés**, l'axe Y par défaut : le notebook y vérifie que les colis dangereux restent visibles (« côtés de largeur », `gauche` en y minimal, `droite` en y maximal), et c'est là que l'application laisse **le couloir, 14 cm de chaque côté** (zone utile 274 × 196 cm ; 274 × 224 cm avec la dérogation). Le paramètre « Côtés du couloir et des colis IATA » permet de prendre l'axe X à la place (zone utile 246 × 224 cm). Pour une BIPAL ou une TRIPAL, le couloir n'est laissé qu'aux deux extrémités de l'ensemble (420 et 644 cm utiles). Unités : cm et kg, coin minimal de chaque colis.

**Contraintes absolues** (erreur si violée ; contrôlées après chaque phase qui déplace des colis, puis par la vérification indépendante)

- Le colis reste dans le volume utile de la palette (274 × 196 cm sauf dérogation, hauteur 2,54 m par défaut, comme les palettes du notebook). Un colis de plus de 196 cm sur l'axe des côtés passe donc tourné, ou sur une BIPAL ou une TRIPAL s'il ne tient pas autrement.
- Aucun chevauchement entre colis.
- Chaque colis repose sur le sol ou sur d'autres colis sur **au moins 90 %** de sa base (paramètre).
- **Jamais un colis lourd sur un colis plus léger** : masse du colis ≤ facteur × masse de chaque colis porteur (facteur 1,0 par défaut).
- Rien ne repose sur un colis **fragile**.
- Un colis dans une matière **à ne pas écraser** (carton, fibre) ne porte que la charge maximale réglée (0 kg par défaut).
- La hauteur saisie reste toujours verticale : seule la rotation autour de la verticale est permise.
- **Séparation IATA** (tableau 9.3.A, mêmes 18 paires que le notebook) et **armement** (arme ≠ pièce de sécurité) : jamais sur la même palette.
- **Visibilité IATA** : chaque colis dangereux reste visible depuis le côté gauche ou le côté droit. Un autre colis le masque d'un côté s'il le dépasse de ce côté sur l'axe des côtés et que leurs projections se chevauchent sur l'autre axe et en z.
- **IATA sur le bord** : ce colis dangereux est de plus posé contre le bord (x = 0 ou x = largeur utile, à 0,5 cm près) du côté d'où il est visible. Les colis dangereux sont donc placés en priorité contre un bord, et les recentrages et tassements ne les en décollent jamais.
- Masse nette maximale : 4 536 kg brut − 130 kg de tare en soute, 3 000 kg brut sur la rampe ; BIPAL = 2 ×, TRIPAL = 3 ×.
- Emplacements utilisés ≤ emplacements disponibles selon les passagers (54 pax → 9, 64 → 7, 74 → 6, 84 → 5, 94 → 4, 104 → 3, 116 → 2) ; charge totale ≤ capacité indicative de l'avion.

**Préférences** (objectifs et avertissements)

- Minimiser le nombre d'emplacements puis de palettes (objectif principal), puis la somme des hauteurs, puis recentrer le chargement.
- Base la plus basse, surface couverte au sol, bandes libres préservées, tas compact (score de CDP_5), poids réparti sur la surface.
- Colis dangereux placés en premier (les plus incompatibles d'abord), contre l'un des deux bords des côtés.
- Aucun colis isolé : un colis touche toujours un autre colis de sa palette (les tassements refusent un déplacement qui en isolerait un, la vérification émet un avertissement sinon).
- Palettes de rampe : les plus légères (3 t).
- Palettes contenant des classes incompatibles entre elles : avertissement « ne pas charger côte à côte » (le positionnement dans l'avion reste hors périmètre, comme le précise le projet).

## Le pipeline du notebook CDP_5

Même enchaînement que `optimiser_palettes` du notebook `CDP_5` (cellule 38). Le journal de calcul de l'interface rend compte de chaque phase.

| Phase | Ce qui se passe | Code du notebook |
|---|---|---|
| **1. Placement heuristique** | **Cinq essais** de construction par points extrêmes : grande base, difficulté, volume, masse, puis un essai de **plateformes** (couches partielles de hauteurs proches, regard en avance sur trois colis, marge de hauteur de 5 cm). Les colis à étiquette passent d'abord. Avec le regroupement par DAT, chaque groupe est essayé en entier sur une copie de chaque palette. Le meilleur essai est conservé selon : palettes, hauteur globale, surface au sol, somme des hauteurs, centrage. | `construire_placement_heuristique` |
| **2. VND** | *Variable Neighborhood Descent* pendant le budget indiqué : **transferts** entre palettes (groupes de DAT complets, de la palette la moins remplie vers la plus remplie), **replacements** plus bas (autre position, autre orientation) puis **échanges** de deux colis. Une amélioration acceptée relance les transferts. Le score est lexicographique : nombre de palettes, concentration du chargement, hauteur maximale, somme des hauteurs, surface au sol. Chaque proposition passe par le contrôle indépendant avant d'être acceptée. | `ameliorer_vnd`, `fusionner_palettes`, `tasser_colis_verticalement` |
| **3. Recentrage** | Translation rigide de chaque tas vers le centre de la palette, sans changer ses contacts. | `recentrer_tas_rigidement` |
| **4. Tassement horizontal** | Chaque colis (les plus lourds d'abord) glisse vers le centre selon x puis y, au millimètre, tant que le chargement reste valide ; z reste fixe ; le tas est recentré à la fin. | `tasser_colis_vers_centre` |

Après l'heuristique et après chaque phase, le **contrôle indépendant** (`controler_chargement_independant`) recalcule les contraintes depuis les entrées : masses, limites, collisions, orientations, appui minimal, accès des colis à étiquette, incompatibilités, regroupement par DAT. Si une phase échoue au contrôle, la dernière solution valide est restaurée. La VND compare les solutions par concentration avant la hauteur : une amélioration acceptée peut donc augmenter la hauteur. Les recherches sont bornées et ne garantissent pas un optimum global.

Étapes affichables dans la vue 3D : `heuristique` (fin de la phase 1), `apres_vnd` (fin de la phase 2) et `finale`.

**Exemple du notebook (54 colis, 2 290 kg, 27 DAT)** : *Exemples… → CDP* charge les colis et les paramètres de CDP_5 (274 × 224 cm sans couloir, appui minimal de 80 %, aucune règle propre à l'application). Valeurs relevées en exécutant le notebook : les cinq essais donnent 3, 3, 3, 4 et 3 palettes (4,38 / 3,44 / 3,59 / 3,77 / 5,07 m de hauteurs cumulées, 290 514 positions testées), l'essai 2 est retenu, et la VND de 30 s ramène le chargement à **2 palettes** (1,41 et 2,38 m). Le moteur de l'application retrouve exactement les cinq essais ; la VND dépend du budget de temps, donc du matériel. Environ 45 s de calcul.

## Correspondance avec le notebook CDP_5

| Notebook `CDP_5` | Application |
|---|---|
| Cellule 2 : `regrouper_par_DAT`, `temps_limite`, `support_minimal`, `marge_hauteur`, `palettes`, `colis` | `data/exemple_cdp.json` (*Exemples… → CDP*) et paramètres : « Regrouper les colis d'un même DAT », « Budget de la VND », « Support minimal », « Marge de hauteur du 5e essai » |
| Cellule 2, données `(id, masse, longueur, largeur, hauteur, r, IATA, DAT)` | `Colis` : `numero` (4 derniers chiffres de « Cargo Nat Ref 2 »), `dat` (« Cargo Nat Ref 1 » complet), `classe` ; `r = 1` toujours |
| Cellule 4 : catégories `A` (WEAPONS) et `B` (WEAPONRY SECURITY PARTS) | classes `ARME` et `SECU` (`iata.classe_notebook`) : visibles depuis un côté, incompatibles entre elles |
| Cellules 4 et 5 : règles IATA, `partition_DAT`, `groupes_DAT` | `palettiseur/notebook_cdp.py` (code repris tel quel) ; `geometrie.cotes_visibles`, `dat.partition` pour la vérification de l'application |
| Cellules 6 à 14 : heuristique à cinq essais | `notebook_cdp.construire_placement_heuristique` |
| Cellules 15 à 17, 25 : replacement plus bas | `notebook_cdp.tasser_colis_verticalement` |
| Cellules 18 et 26 : recentrage, tassement horizontal | `notebook_cdp.recentrer_tas_rigidement` (avec la contrainte du bord des colis IATA), `tasser_colis_vers_centre` |
| Cellules 27 à 34 : transferts entre palettes | `notebook_cdp.fusionner_palettes` |
| Cellule 35 : contrôle indépendant | `notebook_cdp.controler_chargement_independant` |
| Cellules 36 et 37 : VND | `notebook_cdp.ameliorer_vnd` |
| Cellule 38 : `optimiser_palettes` | `notebook_cdp.Contexte.executer` (mêmes contrôles et restaurations), puis `optimisation._optimiser_notebook` pour le journal |
| Cellules 22, 23, 39 à 43 : indicateurs et graphiques | indicateurs du bilan et du panneau « Palette », vue 3D, étapes `heuristique` / `apres_vnd` / `finale` |

**Différences et ajouts**

- Unités et repère : le notebook travaille en mètres avec l'origine au centre de la palette et le centre des colis ; l'application en cm avec le coin minimal (conversions dans `notebook_cdp`). Les axes sont les mêmes (x = 274 cm, y = 224 cm) et les côtés d'accès sont les extrémités de l'axe y ; l'application y ajoute le couloir de 14 cm. L'axe x est possible : le problème est alors transposé avant d'être donné au notebook.
- **Accroches** : quatre points du code du notebook, marqués « [APPLICATION] », laissent l'application ajouter ses règles (lourd sur léger, fragile, cartons, colis IATA contre un bord, classes absentes du tableau 9.3.A). Elles sont inactives sans règles supplémentaires, et le module se comporte alors exactement comme le notebook. L'absence de colis isolé n'est pas garantie par ce moteur (la vérification la signale).
- **Catégories A et B** : comme dans le notebook, elles déclenchent l'accessibilité latérale et la préférence pour les extrémités ; l'application n'exige le bord que pour les vraies classes IATA.
- **Regroupement impossible** : si aucun des cinq essais ne peut garder chaque groupe de DAT sur une palette avec les règles demandées (par exemple cartons non écrasés, couloir et gros DAT), le notebook s'arrête. L'application relâche alors le regroupement (option `regrouper_par_DAT = False` du notebook) avant de changer de moteur, et la vérification signale les DAT répartis.
- **Relais par le moteur rapide** : le notebook ne connaît ni la BIPAL ni la TRIPAL, et son code n'est pas optimisé (au-delà de 150 colis, trop lent). Dans ces cas, ou si le moteur du notebook échoue, le moteur de l'application calcule (heuristique multi-départ, fusion, tassement vertical, recentrage, tassement horizontal ; sans regroupement par DAT) et le journal le dit.
- `temps_limite` du notebook (180 s) = « Budget de la VND » (60 s par défaut) ; le temps total est plus long : heuristique (13 s pour 54 colis), contrôles et tassement s'y ajoutent.
- SCIP est retiré, comme dans le notebook : plus de moteur exact, plus de dépendance `pyscipopt`.
- Le support minimal passe de 90 % à 80 % (`support_minimal` du notebook).
- Règles de l'application absentes du notebook : colis lourd/léger, fragile, matières d'emballage à ne pas écraser, classes IATA supplémentaires (1.4S, 6.x, 7), BIPAL/TRIPAL, rampe.
- Les paramètres enregistrés par une version précédente du navigateur sont ignorés (nouvelle clé de stockage), pour repartir des valeurs de `CDP_5`.

## Architecture du code

```
main.py                     point d'entrée (fenêtre pywebview ou navigateur)
lancer.bat / build_windows.bat / PaletteA400M.spec   lancement et packaging Windows
palettiseur/
  modeles.py                Colis, TypePalette, Placement, Palette, Parametres, Etape, Resultat
  iata.py                   classes de colis spéciaux, couleurs, règles de séparation
  geometrie.py              orientations, chevauchement, support, contacts
  optimisation.py           choix du moteur, journal et moteur rapide de l'application (heuristique multi-départ, fusion, tassements)
  dat.py                    partition des colis par DAT (vérification)
  verification.py           contrôle indépendant du plan (erreurs / avertissements)
  coloriage.py              coloriage DSatur, bornes de la heat map
  avion.py                  emplacements selon passagers, bilan de masse
  notebook_cdp.py           code du notebook CDP_5 repris tel quel (moteur par défaut) et sa conversion vers l'application
  fichiers.py               import CSV / JSON, exports
  import_cargo.py           lecture du manifeste Excel (colonnes, classes, numéro de série, emballage)
  lecteur_xlsx.py           lecteur .xlsx sans dépendance (zip + XML)
  emballages.py             matières d'emballage et matières à ne pas écraser
  api.py                    fonctions appelées par le serveur (exemples, sérialisation)
  serveur.py                serveur HTTP local (fichiers statiques + API JSON)
data/iata_9_3_A.json        tableau IATA 9.3.A éditable
data/exemple_cdp.json       les 54 colis du notebook (dont 5 dangereux) et leurs paramètres
data/exemple_cargo.xlsx     manifeste Excel d'exemple (54 colis dont 14 armes et 10 pièces de sécurité)
data/import_cargo.json      mots-clés armes / pièces de sécurité de l'import Excel (éditable)
data/emballages.json        matières d'emballage, mots-clés du manifeste, matières à ne pas écraser (éditable)
ui/                         interface : index.html, style.css, app.js, vue3d.js (Three.js embarqué)
tests/                      tests pytest (dont parcours complet de l'interface dans Chromium)
docs/captures/              captures d'écran
```

L'interface est une page web servie sur `127.0.0.1` uniquement ; rien ne sort du poste. Le cœur n'utilise que la bibliothèque standard de Python.

## Tests

```bash
pip install pytest            # + playwright pour les tests d'interface, openpyxl pour comparer le lecteur .xlsx (ignorés sinon)
python -m pytest tests -q
```

Les tests couvrent la géométrie, le tableau IATA, chaque phase du pipeline (validité d'un chargement, visibilité IATA, fusion, tassement vertical et horizontal, recentrage, zones), l'heuristique sur des listes aléatoires (aucune erreur de vérification, y compris pour chaque état intermédiaire), les cinq essais de l'heuristique du notebook CDP_5 (reproduits à l'identique sur ses 54 colis), le pipeline complet avec la VND et les contrôles indépendants, le regroupement par DAT (groupes compatibles, DAT conflictuels, regroupement relâché), les catégories A et B, les deux axes des côtés, les colis dangereux sur le bord à chaque étape, le couloir de 14 cm de chaque côté, la règle des cartons, l'absence de colis isolé, le lecteur `.xlsx` (comparé à openpyxl) et l'import du manifeste, la vérification, le coloriage, l'import/export, le moteur rapide de l'application, le relais vers lui et le parcours complet de l'interface (dont l'import Excel). Le test de parité exécute le code du notebook : compter 15 s pour l'heuristique, et près d'une minute pour le parcours d'interface. Pour régénérer les captures : `PALETTISEUR_CAPTURES=docs/captures python -m pytest tests/test_interface.py`.

## Limites et pistes d'amélioration

- Les colis sont modélisés comme des pavés (comme les données MEAT / SILRIA) ; les formes réelles (pneus, hélices) ne sont pas traitées.
- La forme oblique des emplacements de rampe n'est pas modélisée (seulement une hauteur maximale paramétrable).
- La règle de masse est appliquée entre un colis et ses porteurs directs, pas sur la charge cumulée d'une pile.
- La résistance mécanique des colis, la stabilité dynamique en vol et l'arrimage (sangles, filet) ne sont pas calculés.
- Le moteur du notebook n'est pas optimisé : environ 15 s pour l'heuristique de 54 colis, plus le budget de la VND ; au-delà de 150 colis, ou avec des BIPAL / TRIPAL, le moteur rapide de l'application prend le relais.
- Les échanges de la VND sont des essais bornés par le budget : le résultat dépend du temps accordé et du matériel, pas seulement des données.
- Pistes : charge cumulée par colis, priorisation CSOA des UAT, déplacement manuel d'un colis dans la vue 3D, lecture directe des exports MEAT / SILRIA, groupes de compatibilité des explosifs (9.3.2.2.5), accélérer le code du notebook.

## Licence des composants tiers

Three.js (`ui/vendor/`) est distribué sous licence MIT (`ui/vendor/LICENSE-three.txt`).
