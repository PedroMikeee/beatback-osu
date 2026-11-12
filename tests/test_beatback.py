# SPDX-License-Identifier: GPL-3.0-or-later
"""Pruebas con una API y unos mirrors falsos (servidor local). Ejecutar: python -m unittest -v"""
import contextlib
import csv
import io
import json
import os
import re
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from beatback import cli, config
from beatback.api import ApiError, OsuClient
from beatback.planner import aggregate, plan

NOT_FOUND, BAD_HTML, RATE_LIMIT_ONCE = 900, 901, 902


def make_items():
    items = []
    ids = [NOT_FOUND, BAD_HTML, RATE_LIMIT_ONCE] + list(range(1000, 1100))
    for i, sid in enumerate(ids):
        items.append({"count": 500 - i, "beatmap_id": sid * 10,
                      "beatmap": {"mode": "mania" if sid == 1099 else "osu", "difficulty_rating": 4.5,
                                  "beatmapset_id": sid, "status": "ranked"},
                      "beatmapset": {"id": sid, "artist": f"Artista {sid}", "title": f"Canción: {sid}?",
                                     "creator": "map", "status": "ranked" if sid % 2 == 0 else "graveyard",
                                     "ranked_date": "2016-05-01T00:00:00+00:00" if sid < 1050 else "2020-01-01T00:00:00+00:00"}})
    for sid in range(1000, 1020):   # segunda dificultad de los primeros sets
        items.append({"count": 3, "beatmap_id": sid * 10 + 1,
                      "beatmap": {"mode": "osu", "difficulty_rating": 6.1, "beatmapset_id": sid},
                      "beatmapset": {"id": sid, "artist": f"Artista {sid}", "title": f"Canción: {sid}?",
                                     "creator": "map", "status": "ranked"}})
    return items


ITEMS = make_items()
ZIP_BYTES = io.BytesIO()
with zipfile.ZipFile(ZIP_BYTES, "w", zipfile.ZIP_STORED) as z:
    z.writestr("mapa.osu", "x" * 2000)
ZIP_BYTES = ZIP_BYTES.getvalue()


class Fake(BaseHTTPRequestHandler):
    seen_429 = set()
    lock = threading.Lock()

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="application/json", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/oauth/token":
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            return self._send(200, json.dumps({"access_token": "t", "expires_in": 3600}).encode())
        self._send(404)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path.startswith("/api/v2/"):
            if self.headers.get("Authorization") != "Bearer t":
                return self._send(401)
            m = re.match(r"/api/v2/users/(.+)/osu", u.path)
            if m:
                return self._send(200, json.dumps({"id": 1, "username": m.group(1)}).encode())
            if u.path == "/api/v2/users/1/beatmapsets/most_played":
                q = parse_qs(u.query)
                off, lim = int(q["offset"][0]), int(q["limit"][0])
                return self._send(200, json.dumps(ITEMS[off:off + lim]).encode())
        m = re.match(r"/(m1|m2)/d/(\d+)", u.path)
        if m:
            mirror, sid = m.group(1), int(m.group(2))
            if sid == NOT_FOUND:
                return self._send(404)
            if sid == BAD_HTML and mirror == "m1":
                return self._send(200, b"<html>" + b"x" * 2000 + b"</html>", "text/html")
            if sid == RATE_LIMIT_ONCE and mirror == "m1":
                with Fake.lock:
                    first = sid not in Fake.seen_429
                    Fake.seen_429.add(sid)
                if first:
                    return self._send(429, b"", headers={"Retry-After": "1"})
            return self._send(200, ZIP_BYTES, "application/octet-stream")
        self._send(404)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Fake.seen_429.clear()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BEATBACK_HOME"] = str(Path(self.tmp.name) / "cfg")
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "salida"

    def cfg(self, mirrors=("m1", "m2")):
        c = config.load(use_env=False)
        c.update(client_id="123", client_secret="s", username="tester", api_base=self.base,
                 api_interval=0, min_interval=0, concurrency=2, block_size=50, output_dir=str(self.out),
                 mirrors=[{"name": n, "url_novideo": f"{self.base}/{n}/d/{{id}}?nv=1",
                           "url_full": f"{self.base}/{n}/d/{{id}}"} for n in mirrors])
        config.save(c)
        return c

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.main(list(argv))
        return buf.getvalue()


class TestPlanner(unittest.TestCase):
    def test_une_dificultades_y_filtra(self):
        sets = aggregate(ITEMS)
        self.assertEqual(len(sets), 102)                       # 103 sets menos el de mania
        first = next(s for s in sets if s["id"] == 1000)
        self.assertEqual(first["playcount"], (500 - 3) + 3)    # suma de sus dos dificultades
        self.assertEqual(first["stars"], 6.1)
        self.assertEqual(len(aggregate(ITEMS, "todos")), 103)

    def test_filtros_y_bloques(self):
        sets = aggregate(ITEMS)
        rows, _ = plan(sets, estados=["ranked"], block_size=10)
        self.assertTrue(all(r["status"] == "ranked" for r in rows))
        rows, _ = plan(sets, desde=2020, block_size=10)
        self.assertTrue(all(r["year"] >= 2020 for r in rows if r["year"]))
        rows, _ = plan(sets, min_playcount=400, block_size=10)
        self.assertTrue(all(r["playcount"] >= 400 for r in rows))
        self.assertEqual([r["block"] for r in rows][:1], [1])
        self.assertEqual(rows, sorted(rows, key=lambda r: (-r["playcount"], r["id"])))


class TestApi(Base):
    def test_paginacion_completa_y_limite(self):
        c = OsuClient(self.cfg())
        self.assertEqual(len(list(c.most_played(1))), len(ITEMS))
        self.assertEqual(len(list(c.most_played(1, limit=7))), 7)

    def test_credenciales_malas(self):
        c = self.cfg()
        c["client_id"] = "no-es-numero"
        with self.assertRaises(ApiError):
            OsuClient(c).find_user("x")


class TestFlujoCompleto(Base):
    def test_plan_download_status_export(self):
        self.cfg()
        out = self.run_cli("plan", "--usuario", "tester")
        self.assertIn("102", out)
        self.assertIn("3 bloque", out)
        out = self.run_cli("download")
        self.assertIn("101 descargados", out)
        self.assertIn("1 no disponibles", out)
        files = list(self.out.rglob("*.osz"))
        self.assertEqual(len(files), 101)
        self.assertEqual(list(self.out.rglob("*.part")), [])          # nunca quedan archivos a medias
        self.assertTrue((self.out / "bloque_001").is_dir())
        self.assertTrue((self.out / "bloque_003").is_dir())
        self.assertTrue(any(f.name.startswith("901 ") for f in files))    # cayó al 2.º mirror
        self.assertTrue(any(f.name.startswith("902 ") for f in files))    # esperó tras el 429
        self.assertFalse(any(re.search(r'[:?]', f.name) for f in files))  # nombres seguros
        self.assertIn("No hay nada pendiente", self.run_cli("download"))  # reanudar no repite
        self.assertIn("not_found=1", self.run_cli("status"))
        csv_path = Path(self.tmp.name) / "lista.csv"
        self.run_cli("export", str(csv_path))
        with open(csv_path, encoding="utf-8") as fh:
            self.assertEqual(len(list(csv.DictReader(fh))), 102)

    def test_prueba_y_continuar(self):
        self.cfg()
        self.assertIn("a descargar: 5 en 1 bloque", self.run_cli("plan", "--prueba", "5"))
        out = self.run_cli("download", "--limite", "3")          # 900 (no existe), 901 y 902
        self.assertIn("2 descargados", out)
        self.assertEqual(len(list(self.out.rglob("*.osz"))), 2)
        out = self.run_cli("download")                           # continúa con los 2 que faltaban
        self.assertIn("2 descargados", out)
        self.assertEqual(len(list(self.out.rglob("*.osz"))), 4)

    def test_un_solo_mirror_con_respuesta_invalida_falla_sin_dejar_basura(self):
        self.cfg(mirrors=("m1",))
        self.run_cli("plan", "--prueba", "2")            # sets 900 y 901
        out = self.run_cli("download", "--concurrencia", "1")
        self.assertIn("1 no disponibles", out)
        self.assertEqual(len(list(self.out.rglob("*.part"))), 0)
        self.assertEqual(len(list(self.out.rglob("901 *.osz"))), 0)
        self.assertIn("failed=1", self.run_cli("status"))

    def test_config_solo_lectura_del_dueno(self):
        self.cfg()
        mode = (config.config_dir() / "config.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
