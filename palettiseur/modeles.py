"""Structures de données du palettiseur.

Unités : centimètres pour les longueurs, kilogrammes pour les masses.

Repère d'une palette : origine au coin de la zone utile, mêmes axes que le notebook CDP_3.

- axe X : 2,74 m (« L » du notebook) ;
- axe Y : 2,24 m (« W » du notebook ; 4,48 m pour une BIPAL, 6,72 m pour une TRIPAL) ;
- axe Z : vers le haut.

Côtés : les deux extrémités de l'axe ``axe_cotes`` sont le côté « gauche » (coordonnée minimale) et le côté « droite »
(coordonnée maximale). Le notebook vérifie la visibilité des colis dangereux depuis ces côtés « de largeur »,
c'est-à-dire sur l'axe Y (valeur par défaut). C'est aussi là que se trouve le couloir, quand on en demande un
(``reduction_couloir``, 14 cm par côté dans le projet, 0 par défaut comme dans le notebook). L'axe X peut être choisi à
la place avec ``axe_cotes="x"``.

Paramètres par défaut : ceux de la cellule 2 du notebook (``regrouper_par_DAT``, ``temps_limite``, ``support_minimal``,
``marge_hauteur`` et les palettes : 7 emplacements en soute de 4 536 kg et 2 sur la rampe de 3 000 kg, tous de
2,74 x 2,24 x 2,54 m). Les règles propres à l'application (couloir, IATA au bord, lourd sur léger) sont
désactivées par défaut : l'application donne alors le même résultat que le notebook.

Règle IATA (notebook CDP_3) : un colis dangereux reste visible depuis l'un des deux côtés, c'est-à-dire qu'aucun autre
colis ne le masque de ce côté (projections qui se chevauchent sur les deux autres axes et colis qui le dépasse).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .emballages import emballages


# ---------------------------------------------------------------------------
# Colis
# ---------------------------------------------------------------------------

@dataclass
class Colis:
    """Une unité à transporter (UAT), modélisée comme un pavé droit."""

    numero: str
    longueur: float          # cm
    largeur: float           # cm
    hauteur: float           # cm (verticale dans le sens « de base »)
    masse: float             # kg
    classe: str = ""          # code de colis spécial (voir iata.py), "" = ordinaire
    commentaire: str = ""
    fragile: bool = False     # rien ne doit être posé dessus
    emballage: str = ""       # code de matière d'emballage (voir emballages.py), "" = non précisée
    reference: str = ""       # numéro de série complet (manifeste), affiché dans le détail du colis
    dat: str = ""             # numéro de DAT complet (« Cargo Nat Ref 1 ») : les colis d'un même DAT voyagent ensemble

    # ---- propriétés dérivées -------------------------------------------------
    @property
    def volume(self) -> float:
        return self.longueur * self.largeur * self.hauteur

    @property
    def special(self) -> bool:
        return bool(self.classe)


    def valider(self) -> list[str]:
        """Retourne la liste des problèmes de saisie (vide si tout va bien)."""
        erreurs = []
        if not str(self.numero).strip():
            erreurs.append("le numéro de colis est obligatoire")
        for nom, valeur in (("longueur", self.longueur), ("largeur", self.largeur),
                            ("hauteur", self.hauteur), ("masse", self.masse)):
            try:
                ok = float(valeur) > 0
            except (TypeError, ValueError):
                ok = False
            if not ok:
                erreurs.append(f"{nom} doit être un nombre strictement positif")
        return erreurs

    @staticmethod
    def depuis_dict(d: dict[str, Any]) -> "Colis":
        def nombre(cle: str, defaut: float = 0.0) -> float:
            v = d.get(cle, defaut)
            if isinstance(v, str):
                v = v.replace(",", ".").strip() or defaut
            return float(v)

        def booleen(cle: str, defaut: bool) -> bool:
            v = d.get(cle, defaut)
            if isinstance(v, str):
                return v.strip().lower() in ("1", "oui", "o", "true", "vrai", "x", "yes", "y")
            return bool(v)

        return Colis(
            numero=str(d.get("numero", d.get("id", ""))).strip(),
            longueur=nombre("longueur"),
            largeur=nombre("largeur"),
            hauteur=nombre("hauteur"),
            masse=nombre("masse"),
            classe=str(d.get("classe", "") or "").strip(),
            commentaire=str(d.get("commentaire", "") or ""),
            fragile=booleen("fragile", False),
            emballage=emballages().code_depuis_texte(d.get("emballage", "")),
            reference=str(d.get("reference", "") or "").strip(),
            dat=str(d.get("dat", d.get("DAT", "")) or "").strip(),
        )

    def vers_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TypePalette:
    """Un format de palette : 10k simple, BIPAL (2 positions) ou TRIPAL (3)."""

    code: str            # "10K", "BIPAL", "TRIPAL"
    libelle: str
    largeur: float       # cm, étendue utile selon l'axe X
    longueur: float      # cm, étendue utile selon l'axe Y
    hauteur_max: float   # cm
    masse_max: float     # kg, masse nette maximale de chargement
    positions: int       # nombre d'emplacements occupés dans l'avion
    axe_cotes: str = "y"  # axe dont les deux extrémités sont les côtés (couloir, accès IATA) : "x" ou "y"

    @property
    def surface(self) -> float:
        return self.largeur * self.longueur

    @property
    def etendue_cotes(self) -> float:
        """Étendue utile selon l'axe des côtés (entre le côté « gauche » et le côté « droite »)."""
        return self.largeur if self.axe_cotes == "x" else self.longueur


@dataclass
class Placement:
    """Un colis posé sur une palette, avec son orientation effective."""

    colis: Colis
    x: float   # coin minimal
    y: float
    z: float
    dx: float  # dimensions orientées (dx selon X, dy selon Y, dz vertical)
    dy: float
    dz: float
    orientation: int = 0

    # ---- géométrie ------------------------------------------------------------
    @property
    def x2(self) -> float:
        return self.x + self.dx

    @property
    def y2(self) -> float:
        return self.y + self.dy

    @property
    def z2(self) -> float:
        return self.z + self.dz

    @property
    def centre(self) -> tuple[float, float, float]:
        return (self.x + self.dx / 2, self.y + self.dy / 2, self.z + self.dz / 2)

    @property
    def base(self) -> float:
        return self.dx * self.dy

    def vers_dict(self) -> dict[str, Any]:
        return {
            "colis": self.colis.vers_dict(),
            "x": round(self.x, 3), "y": round(self.y, 3), "z": round(self.z, 3),
            "dx": round(self.dx, 3), "dy": round(self.dy, 3), "dz": round(self.dz, 3),
            "orientation": self.orientation,
        }


@dataclass
class Palette:
    numero: int
    type: TypePalette
    placements: list[Placement] = field(default_factory=list)
    zone: str = "soute"     # "soute" ou "rampe"
    emplacements: list[int] = field(default_factory=list)   # numéros des emplacements de l'avion occupés (1 à 9)

    @property
    def masse(self) -> float:
        return sum(p.colis.masse for p in self.placements)

    @property
    def hauteur(self) -> float:
        return max((p.z2 for p in self.placements), default=0.0)

    @property
    def volume_colis(self) -> float:
        return sum(p.colis.volume for p in self.placements)

    @property
    def classes(self) -> set[str]:
        return {p.colis.classe for p in self.placements if p.colis.classe}

    def centre_de_masse(self) -> tuple[float, float, float]:
        m = self.masse
        if m <= 0:
            return (self.type.largeur / 2, self.type.longueur / 2, 0.0)
        cx = sum(p.colis.masse * p.centre[0] for p in self.placements) / m
        cy = sum(p.colis.masse * p.centre[1] for p in self.placements) / m
        cz = sum(p.colis.masse * p.centre[2] for p in self.placements) / m
        return (cx, cy, cz)

    def taux_remplissage_volume(self) -> float:
        """Volume des colis rapporté au volume de l'enveloppe utilisée (0..1)."""
        h = self.hauteur
        if h <= 0:
            return 0.0
        return min(1.0, self.volume_colis / (self.type.surface * h))

    def taux_occupation_sol(self) -> float:
        """Surface au sol couverte par les colis posés directement sur la palette."""
        sol = sum(p.base for p in self.placements if p.z <= 1e-6)
        return min(1.0, sol / self.type.surface)

    def vers_dict(self, tare: float = 0.0, largeur_physique: float | None = None,
                  longueur_physique: float | None = None) -> dict[str, Any]:
        """Résumé sérialisable. ``largeur_physique`` et ``longueur_physique`` : dimensions du plateau (274 cm et
        224 cm par emplacement) ; l'écart avec la zone utile donne le passage laissé de chaque côté du couloir."""
        cx, cy, cz = self.centre_de_masse()
        phys_x = max(largeur_physique or self.type.largeur, self.type.largeur)
        phys_y = max(longueur_physique or self.type.longueur, self.type.longueur)
        return {
            "numero": self.numero,
            "type": self.type.code,
            "libelle_type": self.type.libelle,
            "largeur": self.type.largeur,
            "largeur_physique": phys_x,
            "marge_x": round((phys_x - self.type.largeur) / 2, 3),
            "marge_y": round((phys_y - self.type.longueur) / 2, 3),
            "axe_cotes": self.type.axe_cotes,
            "longueur_physique": phys_y,
            "longueur": self.type.longueur,
            "hauteur_max": self.type.hauteur_max,
            "masse_max": self.type.masse_max,
            "positions": self.type.positions,
            "zone": self.zone,
            "emplacements": list(self.emplacements),
            "masse": round(self.masse, 1),
            "masse_brute": round(self.masse + tare * self.type.positions, 1),
            "hauteur": round(self.hauteur, 1),
            "nombre_colis": len(self.placements),
            "classes": sorted(self.classes),
            "centre_de_masse": [round(cx, 1), round(cy, 1), round(cz, 1)],
            "taux_volume": round(self.taux_remplissage_volume(), 3),
            "taux_sol": round(self.taux_occupation_sol(), 3),
            "placements": [p.vers_dict() for p in self.placements],
        }


# ---------------------------------------------------------------------------
# Paramètres
# ---------------------------------------------------------------------------

@dataclass
class Parametres:
    """Paramètres du calcul, des palettes et de l'avion. Les valeurs par défaut sont celles de la cellule 2 du notebook."""

    # ---- Cellule 2 du notebook ------------------------------------------------------------------------------------
    regrouper_par_dat: bool = True       # ``regrouper_par_DAT`` : un DAT sans conflit reste sur une seule palette
    temps_limite: float = 180.0          # secondes : ``temps_limite``, budget commun ; chaque phase peut l'utiliser en entier
    support_minimal: float = 0.80        # ``support_minimal`` : part de la base qui doit reposer sur quelque chose
    marge_hauteur: float = 5.0           # cm : ``marge_hauteur`` (0,05 m), écart maximal de base du cinquième essai

    # Palettes de la soute (emplacements 1 à 7) : L = 2,74 m sur l'axe X, W = 2,24 m sur l'axe Y, H = 2,54 m, M = 4 536 kg.
    largeur_palette: float = 274.0       # axe X
    longueur_palette: float = 224.0      # axe Y, par emplacement (BIPAL : 2 x, TRIPAL : 3 x)
    hauteur_max: float = 254.0           # cm
    masse_max_soute: float = 4536.0      # kg : charge maximale d'une palette de la soute
    # Palettes de la rampe (emplacements 8 et 9) : mêmes dimensions, mais seulement 3 000 kg.
    largeur_rampe: float = 274.0         # axe X
    longueur_rampe: float = 224.0        # axe Y
    hauteur_max_rampe: float = 254.0     # cm (forme oblique de la rampe non modélisée)
    masse_max_rampe: float = 3000.0      # kg
    positions_soute: int = 7
    positions_rampe: int = 2
    emplacements_indisponibles: list = field(default_factory=list)   # numéros (1 à 9) des emplacements non utilisables

    # ---- Règles propres à l'application (désactivées par défaut : le notebook ne les connaît pas) -----------------
    reduction_couloir: float = 0.0       # cm laissés libres de chaque côté (14 dans le projet) ; 0 = pas de couloir
    axe_cotes: str = "y"                 # axe des côtés (couloir et accès IATA) : "y" comme le notebook, ou "x"
    iata_au_bord: bool = False           # colis IATA contre un bord côté couloir (en plus de la règle de visibilité)
    facteur_masse: float = 0.0           # un colis posé doit avoir masse <= facteur x masse du support ; 0 = pas de règle
    tare_palette: float = 0.0            # kg, masse à vide d'une palette (130 kg pour une HCU-6/E) retirée de la charge
    autoriser_bipal: bool = True         # formats pour les colis plus grands qu'une palette (hors notebook)
    autoriser_tripal: bool = True
    jeu_contact: float = 0.5             # cm, tolérance pour considérer deux faces en contact

    # ---- Avion -------------------------------------------------------------------------------------------------
    passagers: int = 0
    masse_passager: float = 90.0
    masse_bagages: float = 40.0          # 2 x 20 kg
    capacite_avion: float = 30000.0      # kg, charge marchande approximative

    # ---- Algorithme ---------------------------------------------------------------------------------------------
    moteur: str = "notebook"             # "notebook" : code de CDP_5 ; "heuristique" : moteur rapide de l'application
    graine: int = 0

    # ---- Emplacements de l'avion ----------------------------------------------------------------------------------
    def numeros_emplacements(self, zone: str | None = None) -> list[int]:
        """Numéros des emplacements : 1 à ``positions_soute`` en soute, puis ceux de la rampe."""
        soute = list(range(1, self.positions_soute + 1))
        rampe = list(range(self.positions_soute + 1, self.positions_soute + self.positions_rampe + 1))
        return soute if zone == "soute" else rampe if zone == "rampe" else soute + rampe

    def zone_emplacement(self, numero: int) -> str:
        return "soute" if numero <= self.positions_soute else "rampe"

    def emplacements_marques(self) -> set[int]:
        """Emplacements déclarés indisponibles (liste ou texte « 1, 2 » acceptés)."""
        valeur = self.emplacements_indisponibles
        if isinstance(valeur, str):
            valeur = [v for v in valeur.replace(";", ",").split(",") if v.strip()]
        marques = set()
        for v in valeur or []:
            try:
                marques.add(int(float(v)))
            except (TypeError, ValueError):
                pass
        return marques

    def emplacements_passagers(self) -> list[int]:
        """Emplacements pris par les sièges quand il y a beaucoup de passagers : ceux de l'avant de la soute (numéros
        les plus petits) parmi les emplacements encore libres. Hypothèse d'implantation, modifiable à la main."""
        from .avion import limite_passagers
        libres = [n for n in self.numeros_emplacements() if n not in self.emplacements_marques()]
        exces = len(libres) - limite_passagers(self.passagers, len(self.numeros_emplacements()))
        return libres[:exces] if exces > 0 else []

    def emplacements_libres(self, zone: str | None = None) -> list[int]:
        """Emplacements utilisables, dans l'ordre : ni déclarés indisponibles, ni pris par les sièges des passagers."""
        pris = self.emplacements_marques() | set(self.emplacements_passagers())
        return [n for n in self.numeros_emplacements(zone) if n not in pris]

    @property
    def places_soute(self) -> int:
        return len(self.emplacements_libres("soute"))

    @property
    def places_rampe(self) -> int:
        return len(self.emplacements_libres("rampe"))

    def plafond_relatif(self, masse: float) -> float:
        """Masse maximale (kg) posable sur un colis de ``masse`` kg, selon ``facteur_masse`` (0 : pas de règle)."""
        return self.facteur_masse * masse if self.facteur_masse > 0 else float("inf")

    # ---- Dimensions ------------------------------------------------------------------------------------------------
    def _reduction(self, axe: str) -> float:
        """Réduction (cm, total des deux côtés) de l'étendue selon l'axe donné."""
        if axe != self.axe_cotes:
            return 0.0
        return 2 * max(0.0, self.reduction_couloir)

    @property
    def largeur_utile(self) -> float:
        """Étendue utile selon X."""
        return max(0.0, self.largeur_palette - self._reduction("x"))

    @property
    def longueur_utile(self) -> float:
        """Étendue utile selon Y pour une palette simple."""
        return max(0.0, self.longueur_palette - self._reduction("y"))

    def longueur_utile_pour(self, positions: int) -> float:
        """Étendue utile selon Y pour un format de ``positions`` emplacements accouplés : le couloir n'est laissé qu'aux
        deux extrémités de l'ensemble."""
        return max(0.0, positions * self.longueur_palette - self._reduction("y"))

    @property
    def masse_nette_max_soute(self) -> float:
        return max(0.0, self.masse_max_soute - self.tare_palette)

    @property
    def masse_nette_max_rampe(self) -> float:
        return max(0.0, self.masse_max_rampe - self.tare_palette)

    def types_palettes(self) -> list[TypePalette]:
        """Les formats de palette disponibles en soute, du plus petit au plus grand."""
        axe = "x" if self.axe_cotes == "x" else "y"
        formats = [TypePalette("10K", "Palette 10k", self.largeur_utile, self.longueur_utile_pour(1),
                               self.hauteur_max, self.masse_nette_max_soute, 1, axe)]
        if self.autoriser_bipal:
            formats.append(TypePalette("BIPAL", "BIPAL (2 palettes accouplées)", self.largeur_utile,
                                       self.longueur_utile_pour(2), self.hauteur_max,
                                       2 * self.masse_nette_max_soute, 2, axe))
        if self.autoriser_tripal:
            formats.append(TypePalette("TRIPAL", "TRIPAL (3 palettes accouplées)", self.largeur_utile,
                                       self.longueur_utile_pour(3), self.hauteur_max,
                                       3 * self.masse_nette_max_soute, 3, axe))
        return formats

    def type_rampe(self) -> TypePalette:
        """Palette d'un emplacement de la rampe : ses propres dimensions et sa propre charge maximale."""
        axe = "x" if self.axe_cotes == "x" else "y"
        return TypePalette("10K", "Palette 10k (rampe)", max(0.0, self.largeur_rampe - self._reduction("x")),
                           max(0.0, self.longueur_rampe - self._reduction("y")), self.hauteur_max_rampe,
                           self.masse_nette_max_rampe, 1, axe)

    @staticmethod
    def depuis_dict(d: dict[str, Any] | None) -> "Parametres":
        p = Parametres()
        if not d:
            return p
        for cle, valeur in d.items():
            if not hasattr(p, cle):
                continue
            actuel = getattr(p, cle)
            try:
                if isinstance(actuel, bool):
                    if isinstance(valeur, str):
                        valeur = valeur.strip().lower() in ("1", "true", "oui", "vrai", "on")
                    setattr(p, cle, bool(valeur))
                elif isinstance(actuel, int):
                    setattr(p, cle, int(float(valeur)))
                elif isinstance(actuel, float):
                    if isinstance(valeur, str):
                        valeur = valeur.replace(",", ".")
                    setattr(p, cle, float(valeur))
                elif isinstance(actuel, list):
                    if isinstance(valeur, str):
                        valeur = [v for v in valeur.replace(";", ",").split(",") if v.strip()]
                    setattr(p, cle, sorted({int(float(v)) for v in valeur}))
                else:
                    setattr(p, cle, str(valeur))
            except (TypeError, ValueError):
                pass  # on conserve la valeur par défaut
        # Projets enregistrés avec l'ancienne dérogation au couloir : pas de couloir.
        if d.get("derogation_couloir"):
            p.reduction_couloir = 0.0
        return p

    def vers_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Résultat
# ---------------------------------------------------------------------------

@dataclass
class Etape:
    """État intermédiaire du plan (phase 1 heuristique, phase 2 après la VND, solution finale)."""

    cle: str                  # "heuristique", "apres_vnd" ou "finale"
    titre: str
    description: str
    palettes: list[Palette]


@dataclass
class Resultat:
    palettes: list[Palette]
    non_places: list[tuple[Colis, str]]   # (colis, raison)
    parametres: Parametres
    duree: float = 0.0
    iterations: int = 0
    moteur: str = "heuristique"
    statut: str = "ok"
    etapes: list[Etape] = field(default_factory=list)   # états successifs du pipeline
    journal: list[dict] = field(default_factory=list)   # une entrée par phase (durée, texte, bilan)

    @property
    def positions_utilisees(self) -> int:
        return sum(p.type.positions for p in self.palettes)

    @property
    def masse_totale(self) -> float:
        return sum(p.masse for p in self.palettes)

    @property
    def somme_hauteurs(self) -> float:
        return sum(p.hauteur for p in self.palettes)
