# -*- mode: python ; coding: utf-8 -*-
"""Fichier PyInstaller : python -m PyInstaller PaletteA400M.spec

Produit un dossier ``dist/PaletteA400M`` contenant l'exécutable et ses
ressources (interface web ``ui/`` et données ``data/``). Le mode « un dossier »
est retenu : il démarre plus vite et pose moins de problèmes avec pywebview
que le mode fichier unique.
"""

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("ui", "ui"), ("data", "data")],
    hiddenimports=["webview", "webview.platforms.edgechromium", "webview.platforms.winforms", "clr"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "scipy", "PIL", "pytest", "playwright"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PaletteA400M",
    debug=False,
    strip=False,
    upx=False,
    console=False,          # pas de console noire : l'interface est la fenêtre
    icon=None,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="PaletteA400M")
