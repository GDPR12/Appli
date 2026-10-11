"""Structures de données du palettiseur.

Unités : centimètres pour les longueurs, kilogrammes pour les masses.

Repère d'une palette : origine au coin de la palette, axe X dans la largeur
« couloir » (2,74 m, réduite à 2,34 m), axe Y dans le sens de l'avion
(2,24 m ; 4,48 m pour une BIPAL), axe Z vers le haut. Comme dans le notebook CDP_2,
un colis IATA doit rester visible depuis l'un des deux côtés de l'axe Y : « gauche »
(y minimal) ou « droite » (y maximal).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


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
    renversable: bool = True  # False : la hauteur doit rester verticale (liquides…)
    classe: str = ""          # code de colis spécial (voir iata.py), "" = ordinaire
    commentaire: str = ""
    fragile: bool = False     # rien ne doit être posé dessus

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
            renversable=booleen("renversable", True),
            classe=str(d.get("classe", "") or "").strip(),
            commentaire=str(d.get("commentaire", "") or ""),
            fragile=booleen("fragile", False),
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
    largeur: float       # cm, axe X
    longueur: float      # cm, axe Y (sens de l'avion)
    hauteur_max: float   # cm
    masse_max: float     # kg, masse nette maximale de chargement
    positions: int       # nombre d'emplacements occupés dans l'avion

    @property
    def surface(self) -> float:
        return self.largeur * self.longueur


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

    def vers_dict(self, tare: float = 0.0) -> dict[str, Any]:
        cx, cy, cz = self.centre_de_masse()
        return {
            "numero": self.numero,
            "type": self.type.code,
            "libelle_type": self.type.libelle,
            "largeur": self.type.largeur,
            "longueur": self.type.longueur,
            "hauteur_max": self.type.hauteur_max,
            "masse_max": self.type.masse_max,
            "positions": self.type.positions,
            "zone": self.zone,
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
    """Paramètres de l'avion, des palettes et de l'algorithme (valeurs par défaut A400M)."""

    # Palette 10k : 2,74 x 2,24 m. La largeur est réduite à 2,34 m (20 cm de
    # chaque côté) pour laisser le passage côté couloir, sauf dérogation.
    largeur_palette: float = 274.0
    longueur_palette: float = 224.0
    reduction_couloir: float = 20.0
    derogation_couloir: bool = False
    hauteur_max: float = 254.0           # cm : 2,54 m, hauteur des palettes du notebook CDP_2
    masse_max_soute: float = 4536.0      # kg, masse brute maximale d'une 10k en soute
    masse_max_rampe: float = 3000.0      # kg, masse brute maximale sur la rampe
    tare_palette: float = 130.0          # kg, masse à vide d'une palette 10k (HCU-6/E)
    hauteur_max_rampe: float = 254.0     # cm (forme oblique de la rampe non modélisée)
    autoriser_bipal: bool = True
    autoriser_tripal: bool = True

    # Avion
    positions_soute: int = 7
    positions_rampe: int = 2
    passagers: int = 0
    masse_passager: float = 90.0
    masse_bagages: float = 40.0          # 2 x 20 kg
    capacite_avion: float = 30000.0      # kg, charge marchande approximative

    # Règles de confection
    support_minimal: float = 0.90        # part de la base qui doit reposer sur quelque chose
    facteur_masse: float = 1.0           # un colis posé doit avoir masse <= facteur x masse du support
    iata_acces: bool = True              # colis IATA visibles depuis au moins un côté (règle de CDP_2)
    jeu_contact: float = 0.5             # cm, tolérance pour considérer deux faces en contact

    # Algorithme
    duree_recherche: float = 4.0         # secondes, budget total de l'heuristique (phases 1, 3 et 5)
    temps_limite_scip: float = 60.0      # secondes par étape SCIP (notebook : temps_limite)
    graine: int = 0
    moteur: str = "heuristique"          # "heuristique" ou "scip"

    @property
    def largeur_utile(self) -> float:
        if self.derogation_couloir:
            return self.largeur_palette
        return self.largeur_palette - 2 * self.reduction_couloir

    @property
    def masse_nette_max_soute(self) -> float:
        return max(0.0, self.masse_max_soute - self.tare_palette)

    @property
    def masse_nette_max_rampe(self) -> float:
        return max(0.0, self.masse_max_rampe - self.tare_palette)

    def types_palettes(self) -> list[TypePalette]:
        """Les formats de palette disponibles, du plus petit au plus grand."""
        formats = [TypePalette("10K", "Palette 10k", self.largeur_utile, self.longueur_palette,
                               self.hauteur_max, self.masse_nette_max_soute, 1)]
        if self.autoriser_bipal:
            formats.append(TypePalette("BIPAL", "BIPAL (2 palettes accouplées)", self.largeur_utile,
                                       2 * self.longueur_palette, self.hauteur_max,
                                       2 * self.masse_nette_max_soute, 2))
        if self.autoriser_tripal:
            formats.append(TypePalette("TRIPAL", "TRIPAL (3 palettes accouplées)", self.largeur_utile,
                                       3 * self.longueur_palette, self.hauteur_max,
                                       3 * self.masse_nette_max_soute, 3))
        return formats

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
                else:
                    setattr(p, cle, str(valeur))
            except (TypeError, ValueError):
                pass  # on conserve la valeur par défaut
        return p

    def vers_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Résultat
# ---------------------------------------------------------------------------

@dataclass
class Etape:
    """État intermédiaire du plan (phase 1 heuristique, phase 2 SCIP, solution finale)."""

    cle: str                  # "heuristique", "scip" ou "finale"
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
