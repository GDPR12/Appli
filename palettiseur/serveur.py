"""Serveur HTTP local : sert l'interface (``ui/``) et une petite API JSON.

L'interface est une page web ; elle est affichée dans une fenêtre native
(pywebview) ou, à défaut, dans le navigateur. Toutes les requêtes restent
sur 127.0.0.1 : rien ne sort de la machine.
"""

from __future__ import annotations

import json
import mimetypes
import re
import socket
import threading
import traceback
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__, api, fichiers
from .ressources import dossier_exports, dossier_interface

mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("application/json", ".json")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")


class Gestionnaire(BaseHTTPRequestHandler):
    server_version = f"PalettiseurA400M/{__version__}"
    interface: Path = dossier_interface()
    exports: Path = dossier_exports()
    silencieux = True

    # ------------------------------------------------------------------ utilitaires
    def log_message(self, format: str, *args) -> None:  # noqa: A002
        if not self.silencieux:
            super().log_message(format, *args)

    def _json(self, donnees, statut: int = 200) -> None:
        corps = json.dumps(donnees, ensure_ascii=False).encode("utf-8")
        self.send_response(statut)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corps)

    def _fichier(self, chemin: Path) -> None:
        if not chemin.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Fichier introuvable")
            return
        type_mime = mimetypes.guess_type(str(chemin))[0] or "application/octet-stream"
        if type_mime.startswith("text/") or type_mime in ("application/json", "text/javascript"):
            type_mime += "; charset=utf-8"
        donnees = chemin.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", type_mime)
        self.send_header("Content-Length", str(len(donnees)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(donnees)

    def _lire_json(self):
        longueur = int(self.headers.get("Content-Length", "0"))
        brut = self.rfile.read(longueur) if longueur else b""
        return json.loads(brut.decode("utf-8")) if brut else {}

    @staticmethod
    def _securiser(nom: str) -> str:
        nom = re.sub(r"[^A-Za-z0-9._-]+", "_", nom).strip("._") or "fichier"
        return nom[:80]

    # ------------------------------------------------------------------ GET
    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        chemin = unquote(url.path)
        if chemin == "/api/config":
            return self._json(api.configuration())
        if chemin == "/api/exemple":
            nom = parse_qs(url.query).get("nom", ["iata"])[0]
            return self._json(api.exemple(nom))
        if chemin.startswith("/exports/"):
            cible = (self.exports / self._securiser(chemin[len("/exports/"):])).resolve()
            if self.exports.resolve() not in cible.parents:
                return self.send_error(HTTPStatus.FORBIDDEN)
            return self._fichier(cible)
        if chemin in ("", "/"):
            chemin = "/index.html"
        cible = (self.interface / chemin.lstrip("/")).resolve()
        if self.interface.resolve() not in cible.parents and cible != self.interface.resolve():
            return self.send_error(HTTPStatus.FORBIDDEN)
        return self._fichier(cible)

    # ------------------------------------------------------------------ POST
    def do_POST(self) -> None:  # noqa: N802
        chemin = unquote(urlparse(self.path).path)
        try:
            corps = self._lire_json()
            if chemin == "/api/optimiser":
                resultat = api.lancer_optimisation(corps.get("colis", []), corps.get("parametres"))
                return self._json(resultat)
            if chemin == "/api/importer":
                return self._json(api.importer_texte(corps.get("texte", "")))
            if chemin == "/api/exporter":
                return self._json(self._exporter(corps))
            return self._json({"erreur": "route inconnue"}, 404)
        except Exception as e:  # noqa: BLE001 - on renvoie l'erreur à l'interface
            traceback.print_exc()
            return self._json({"erreur": f"{type(e).__name__} : {e}"}, 500)

    def _exporter(self, corps: dict) -> dict:
        """Écrit un fichier dans le dossier ``exports`` et retourne son chemin."""
        format_ = corps.get("format", "json")
        horodatage = datetime.now().strftime("%Y-%m-%d_%Hh%M")
        base = self._securiser(corps.get("nom") or f"palettisation_{horodatage}")
        if format_ == "projet":
            contenu = json.dumps({"application": "Palettiseur A400M", "version": __version__,
                                  "date": datetime.now().isoformat(timespec="seconds"),
                                  "colis": corps.get("colis", []), "parametres": corps.get("parametres")},
                                 ensure_ascii=False, indent=2)
            nom = f"{base}.json"
        elif format_ == "colis_csv":
            colis = api.colis_depuis_liste(corps.get("colis", []))
            contenu, nom = fichiers.ecrire_csv(colis), f"{base}_colis.csv"
        elif format_ == "plan_csv":
            contenu, nom = corps.get("contenu", ""), f"{base}_plan.csv"
        elif format_ == "plan_json":
            contenu, nom = fichiers.plan_json(corps.get("resultat", {})), f"{base}_plan.json"
        elif format_ == "texte":
            contenu, nom = corps.get("contenu", ""), self._securiser(corps.get("nom", "fichier.txt"))
        else:
            raise ValueError(f"format d'export inconnu : {format_}")
        cible = self.exports / nom
        cible.write_text(contenu, encoding="utf-8-sig" if nom.endswith(".csv") else "utf-8")
        return {"chemin": str(cible), "nom": nom, "url": f"/exports/{nom}"}


def port_libre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Serveur:
    """Serveur HTTP dans un thread d'arrière-plan."""

    def __init__(self, port: int | None = None, verbeux: bool = False) -> None:
        self.port = port or port_libre()
        Gestionnaire.silencieux = not verbeux
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Gestionnaire)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="serveur-http", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def demarrer(self) -> "Serveur":
        self.thread.start()
        return self

    def arreter(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
