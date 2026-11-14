# SPDX-License-Identifier: GPL-3.0-or-later
"""Configuración local. Se guarda en ~/.config/beatback/config.json (solo lectura para ti)."""
import copy
import json
import os
from pathlib import Path

DEFAULTS = {
    "client_id": "",
    "client_secret": "",
    "username": "",
    "output_dir": str(Path.home() / "BeatBack"),
    "block_size": 150,        # mapas por carpeta (bloque)
    "no_video": True,         # descargar sin video (mucho más liviano)
    "concurrency": 3,         # descargas simultáneas (si ves errores 429, bájalo a 2)
    "min_interval": 2.0,      # segundos mínimos entre peticiones al mismo mirror
    "api_base": "https://osu.ppy.sh",
    "api_interval": 1.0,      # pausa entre páginas de la API
    # Los mirrors son servicios de terceros: pueden cambiar o caerse. "tier" = preferencia (0 = primero).
    # Los de nivel más alto solo se usan cuando los anteriores están ocupados, en pausa o no tienen el mapa.
    "mirrors": [
        {"name": "catboy", "tier": 0,
         "url_novideo": "https://catboy.best/d/{id}n",       # sufijo "n" = sin video (por confirmar con un mapa que tenga video)
         "url_full": "https://catboy.best/d/{id}"},
        {"name": "sayobot", "tier": 1,                        # funciona, pero es lento
         "url_novideo": "https://dl.sayobot.cn/beatmaps/download/novideo/{id}",
         "url_full": "https://dl.sayobot.cn/beatmaps/download/full/{id}"},
        {"name": "osu.direct", "tier": 2,                     # rápido, pero entrega el mapa CON video y tiene cupos diarios
         "url_novideo": "https://osu.direct/api/d/{id}",
         "url_full": "https://osu.direct/api/d/{id}"},
        {"name": "nerinyan", "tier": 3,                       # en las pruebas respondió "not_found" para mapas que existen
         "url_novideo": "https://api.nerinyan.moe/d/{id}?nv=1",
         "url_full": "https://api.nerinyan.moe/d/{id}"},
    ],
}


# Lista de mirrors que guardaban las versiones 0.1 y 0.2.x en config.json: si coincide exactamente, se reemplaza
# por la nueva (el usuario no la había personalizado).
LEGACY_MIRRORS = [
    {"name": "nerinyan", "url_novideo": "https://api.nerinyan.moe/d/{id}?nv=1", "url_full": "https://api.nerinyan.moe/d/{id}"},
    {"name": "sayobot", "url_novideo": "https://dl.sayobot.cn/beatmaps/download/novideo/{id}",
     "url_full": "https://dl.sayobot.cn/beatmaps/download/full/{id}"},
    {"name": "catboy", "url_novideo": "https://catboy.best/d/{id}n", "url_full": "https://catboy.best/d/{id}"},
]


def config_dir() -> Path:
    return Path(os.environ.get("BEATBACK_HOME") or Path.home() / ".config" / "beatback")


def load(use_env: bool = True) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    f = config_dir() / "config.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"El archivo de configuración está dañado ({f}): {e}")
        if data.get("mirrors") == LEGACY_MIRRORS:      # lista antigua sin personalizar: usar la nueva
            data.pop("mirrors")
        cfg.update(data)
    if use_env:  # permite no guardar el secreto en disco
        for key, env in (("client_id", "BEATBACK_CLIENT_ID"), ("client_secret", "BEATBACK_CLIENT_SECRET")):
            if os.environ.get(env):
                cfg[key] = os.environ[env]
    return cfg


def save(cfg: dict) -> Path:
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = d / "config.json"
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # solo tú puedes leerlo
    # Los mirrors solo se guardan si el usuario los personalizó: así las mejoras de la lista llegan con cada versión.
    data = {k: v for k, v in cfg.items() if not (k == "mirrors" and v == DEFAULTS["mirrors"])}
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.chmod(f, 0o600)
    return f
