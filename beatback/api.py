# SPDX-License-Identifier: GPL-3.0-or-later
"""Cliente mínimo de la API v2 de osu! (solo lo que BeatBack necesita)."""
import time
from urllib.parse import quote

import requests

from . import USER_AGENT as UA
PAGE = 50  # tamaño de página: seguro frente a cualquier límite de la API


class ApiError(Exception):
    pass


class OsuClient:
    def __init__(self, cfg: dict, session=None):
        self.base = cfg["api_base"].rstrip("/")
        self.client_id = cfg["client_id"]
        self.client_secret = cfg["client_secret"]
        self.interval = float(cfg.get("api_interval", 1.0))
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = UA
        self._tok, self._exp = None, 0.0

    def _auth(self):
        if self._tok and time.time() < self._exp - 60:
            return
        try:
            r = self.s.post(f"{self.base}/oauth/token", timeout=30, json={
                "client_id": int(self.client_id), "client_secret": self.client_secret,
                "grant_type": "client_credentials", "scope": "public"})
        except (requests.RequestException, ValueError) as e:
            raise ApiError(f"No pude pedir el token (revisa tus credenciales y tu conexión): {e}")
        if r.status_code != 200:
            raise ApiError(f"osu! rechazó las credenciales (HTTP {r.status_code}). Revisa Client ID y Secret.")
        data = r.json()
        self._tok, self._exp = data["access_token"], time.time() + int(data.get("expires_in", 3600))

    def _get(self, path, params=None):
        refreshed = False
        for attempt in range(6):
            self._auth()
            try:
                r = self.s.get(self.base + path, params=params, timeout=30,
                               headers={"Authorization": f"Bearer {self._tok}"})
            except requests.RequestException as e:
                if attempt == 5:
                    raise ApiError(f"Error de red: {e}")
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                time.sleep(min(int(r.headers.get("Retry-After", 2 ** attempt)), 120))
            elif r.status_code == 401 and not refreshed:
                self._tok, refreshed = None, True
            elif r.status_code == 404:
                raise ApiError("No encontrado (¿el nombre de usuario es correcto?)")
            elif r.status_code >= 500:
                time.sleep(2 ** attempt)
            else:
                r.raise_for_status()
                return r.json()
        raise ApiError("La API de osu! no respondió después de varios intentos.")

    def find_user(self, username: str) -> dict:
        return self._get(f"/api/v2/users/{quote(username, safe='')}/osu", {"key": "username"})

    def most_played(self, user_id, limit=None):
        """Recorre la lista de más jugados página por página (generador)."""
        offset = 0
        while True:
            page = self._get(f"/api/v2/users/{user_id}/beatmapsets/most_played",
                             {"limit": PAGE, "offset": offset})
            if not page:
                return
            for item in page:
                if limit and offset >= limit:
                    return
                yield item
                offset += 1
            time.sleep(self.interval)
