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
import time
import unittest
import zipfile
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from beatback import cli, config
from beatback.db import State
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
    hits = Counter()
    agents = []
    budget = 10
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
        m = re.match(r"/(m1|m2|m3|m4|m4s)/d/(\d+)", u.path)
        if m:
            mirror, sid = m.group(1), int(m.group(2))
            with Fake.lock:
                Fake.hits[mirror] += 1
                Fake.agents.append(self.headers.get("User-Agent", ""))
            if mirror == "m4":                                   # como osu.direct: redirige y informa el cupo
                if sid == NOT_FOUND:
                    return self._send(404)
                with Fake.lock:
                    Fake.budget -= 1
                    left = Fake.budget
                return self._send(302, headers={"Location": f"/m4s/d/{sid}", "X-Download-Budget-Limit": "10",
                                                "X-Download-Budget-Remaining": str(left),
                                                "X-Download-Budget-Reset": str(int(time.time()) + 3600)})
            if mirror == "m3":
                return self._send(500)                          # mirror caído
            if mirror == "m1" and 1050 <= sid <= 1059:          # corte a mitad de la descarga
                self.send_response(200)
                self.send_header("Content-Length", "5000")
                self.end_headers()
                self.wfile.write(b"PK\x03\x04" + b"x" * 100)
                return
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
        Fake.hits.clear()
        Fake.agents.clear()
        Fake.budget = 10
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["BEATBACK_HOME"] = str(Path(self.tmp.name) / "cfg")
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "salida"

    def cfg(self, mirrors=("m1", "m2"), tiers=None):
        c = config.load(use_env=False)
        c.update(client_id="123", client_secret="s", username="tester", api_base=self.base,
                 api_interval=0, min_interval=0, concurrency=2, block_size=50, output_dir=str(self.out),
                 mirrors=[{"name": n, "tier": (tiers or {}).get(n, 0), "url_novideo": f"{self.base}/{n}/d/{{id}}?nv=1",
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


class TestVersion02(Base):
    def test_reparte_entre_mirrors_y_registra_cual_sirvio(self):
        c = self.cfg()
        c["min_interval"] = 0.05          # pausas reales: así los turnos rotan entre mirrors
        config.save(c)
        self.run_cli("plan")
        out = self.run_cli("download")
        self.assertIn("Por mirror", out)
        rows = State(config.config_dir() / "beatback.db").all_rows()
        usados = {r["mirror"] for r in rows if r["state"] == "done"}
        self.assertEqual(usados, {"m1", "m2"})                  # ya no usa siempre el primero
        self.assertTrue(all(r["bytes"] for r in rows if r["state"] == "done"))

    def test_list_muestra_sin_descargar(self):
        self.cfg()
        self.run_cli("plan")
        out = self.run_cli("list", "--limite", "5")
        self.assertIn("Mostrando 5 de 102", out)
        self.assertIn("pending", out)
        self.assertEqual(list(self.out.rglob("*.osz")) if self.out.exists() else [], [])   # nada descargado
        self.assertIn("Mostrando 102 de 102", self.run_cli("list", "--limite", "0"))

    def test_list_filtra_por_estado(self):
        self.cfg()
        self.run_cli("plan", "--prueba", "3")
        self.run_cli("download")
        self.assertIn("Mostrando 1 de 1", self.run_cli("list", "--estado", "not_found"))
        self.assertIn("Mostrando 2 de 2", self.run_cli("list", "--estado", "done"))

    def test_migracion_desde_0_1_conserva_el_historial(self):
        import sqlite3
        d = config.config_dir()
        d.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(d / "beatback.db")
        con.execute("CREATE TABLE sets(id INTEGER PRIMARY KEY, artist TEXT, title TEXT, creator TEXT, status TEXT,"
                    " year INTEGER, playcount INTEGER, stars REAL, block INTEGER, state TEXT NOT NULL DEFAULT 'pending',"
                    " error TEXT, path TEXT, bytes INTEGER, updated TEXT)")
        con.execute("INSERT INTO sets(id,artist,title,state,bytes,block,playcount) VALUES(7,'A','T','done',3145728,1,9)")
        con.commit()
        con.close()
        st = State(d / "beatback.db")                 # abre la base vieja sin romperse
        rows = st.all_rows()
        self.assertEqual((rows[0]["id"], rows[0]["state"]), (7, "done"))
        self.assertIn("mirror", rows[0])
        avg, n = st.avg_done_mb()
        self.assertEqual((round(avg), n), (3, 1))

    def test_export_incluye_mirror_y_tamano(self):
        self.cfg()
        self.run_cli("plan", "--prueba", "3")
        self.run_cli("download")
        p = Path(self.tmp.name) / "h.csv"
        self.run_cli("export", str(p))
        with open(p, encoding="utf-8") as fh:
            filas = list(csv.DictReader(fh))
        self.assertTrue({"mirror", "bytes", "updated"} <= set(filas[0]))
        self.assertTrue(any(f["mirror"] in ("m1", "m2") for f in filas))

    def test_formato_de_tamano(self):
        self.assertEqual(cli.fmt_size(15), "15 MB")
        self.assertEqual(cli.fmt_size(2048), "2.0 GB")


class TestVersion021(Base):
    def _pool(self, *names):
        from beatback.downloader import MirrorPool
        return MirrorPool([{"name": n, "url_novideo": f"{self.base}/{n}/d/{{id}}", "url_full": ""} for n in names], True, 0)

    def test_corte_a_mitad_de_descarga_no_tumba_el_programa(self):
        import requests
        from beatback.downloader import fetch_set
        dest = self.out / "bloque_001" / "1050.osz"
        res = fetch_set(1050, dest, self._pool("m1"), requests.Session(), rounds=1)    # no lanza excepción
        self.assertEqual(res[0], "failed")
        self.assertIn("se cortó", res[1])
        self.assertFalse(dest.exists())
        self.assertEqual(list(self.out.rglob("*.part")), [])

    def test_corte_con_otro_mirror_de_respaldo_termina_bien(self):
        import requests
        from beatback.downloader import fetch_set
        dest = self.out / "bloque_001" / "1051.osz"
        st, info, mirror, notes, secs = fetch_set(1051, dest, self._pool("m1", "m2"), requests.Session(), rounds=1)
        self.assertEqual((st, mirror), ("done", "m2"))
        self.assertIn("m1:corte", notes)
        self.assertGreater(secs, 0)

    def test_mirror_caido_se_pone_en_pausa_y_no_frena_el_resto(self):
        c = self.cfg(mirrors=("m3", "m2"))
        c["concurrency"] = 1
        config.save(c)
        self.run_cli("plan")
        out = self.run_cli("download")
        self.assertIn("101 descargados", out)
        self.assertIn("en pausa", out)
        self.assertIn("m3:HTTP500", out)               # se ve por qué falló
        self.assertLess(Fake.hits["m3"], 12)           # tras 3 mapas fallidos deja de insistir (no ~100 visitas)

    def test_opcion_mirror_usa_solo_el_elegido(self):
        self.cfg()
        self.run_cli("plan", "--prueba", "8")
        self.run_cli("download", "--mirror", "m2")
        self.assertEqual(Fake.hits["m1"], 0)
        rows = State(config.config_dir() / "beatback.db").all_rows()
        self.assertEqual({r["mirror"] for r in rows if r["state"] == "done"}, {"m2"})
        with self.assertRaises(SystemExit):
            self.run_cli("download", "--mirror", "no-existe")

    def test_comando_mirrors_diagnostica_cada_uno(self):
        c = self.cfg(mirrors=("m1", "m2", "m3"))
        self.run_cli("plan", "--prueba", "3")
        out = self.run_cli("mirrors", "--id", "1000", "--id", "900")
        self.assertRegex(out, r"m1\s+mapa 1000\s+OK")
        self.assertRegex(out, r"m2\s+mapa 900\s+no tiene este mapa")
        self.assertRegex(out, r"m3\s+mapa 1000\s+respondió HTTP 500")

    def test_un_solo_mirror_con_mapa_malo_no_se_pone_en_pausa(self):
        import time
        self.cfg(mirrors=("m1",))
        self.run_cli("plan", "--prueba", "2")           # 900 (404) y 901 (respuesta inválida en m1)
        t0 = time.monotonic()
        self.run_cli("download", "--concurrencia", "1")
        self.assertLess(time.monotonic() - t0, 30)      # antes se quedaba esperando minutos


class TestVersion022(Base):
    def test_se_identifica_con_nombre_version_y_direccion(self):
        from beatback import USER_AGENT
        self.cfg()
        self.run_cli("plan", "--prueba", "4")
        self.run_cli("download")
        self.assertTrue(Fake.agents)
        self.assertTrue(all(a == USER_AGENT for a in Fake.agents))
        self.assertRegex(USER_AGENT, r"^BeatBack/\d+\.\d+\.\d+ \(\+https://")

    def test_los_mirrors_de_respaldo_no_se_usan_si_los_principales_funcionan(self):
        self.cfg(mirrors=("m2", "m4"), tiers={"m2": 0, "m4": 1})
        self.run_cli("plan", "--prueba", "12")
        self.run_cli("download")
        self.assertGreater(Fake.hits["m2"], 0)
        # m4 solo se consulta por el mapa 900, que m2 no tiene; no sirve ninguna descarga normal
        self.assertEqual(Fake.hits["m4"], 1)
        rows = State(config.config_dir() / "beatback.db").all_rows()
        self.assertEqual({r["mirror"] for r in rows if r["state"] == "done"}, {"m2"})

    def test_el_respaldo_entra_cuando_el_principal_falla(self):
        self.cfg(mirrors=("m3", "m4"), tiers={"m3": 0, "m4": 1})
        self.run_cli("plan", "--prueba", "8")
        out = self.run_cli("download", "--concurrencia", "1")
        rows = State(config.config_dir() / "beatback.db").all_rows()
        self.assertTrue(all(r["mirror"] == "m4" for r in rows if r["state"] == "done"))
        self.assertIn("m3:HTTP500", out)

    def test_orden_prefiere_nivel_bajo_pero_no_espera_a_uno_ocupado(self):
        from beatback.downloader import MirrorPool
        mirrors = [{"name": "rapido", "tier": 0, "url_novideo": "x", "url_full": "x"},
                   {"name": "lento", "tier": 1, "url_novideo": "x", "url_full": "x"}]
        pool = MirrorPool(mirrors, True, 2.0)
        self.assertEqual([m["name"] for m in pool.ordered()], ["rapido", "lento"])
        pool.penalize(pool.items[0], 60)                      # el preferido está en pausa un minuto
        self.assertEqual([m["name"] for m in pool.ordered()], ["lento", "rapido"])

    def test_lee_el_cupo_en_la_redireccion_y_lo_muestra_en_el_resumen(self):
        self.cfg(mirrors=("m4",))
        self.run_cli("plan", "--prueba", "4")                  # 900 (404), 901, 902, 1000
        out = self.run_cli("download", "--concurrencia", "1")
        self.assertIn("cupo restante 7/10", out)               # 3 descargas gastaron 3 de 10

    def test_cupo_agotado_pausa_el_mirror_y_avisa(self):
        from beatback.downloader import MirrorPool

        class Resp:
            def __init__(self, headers, history=()):
                self.headers, self.history = headers, list(history)
        pool = MirrorPool([{"name": "od", "url_novideo": "x", "url_full": "x"}], True, 0)
        m = pool.items[0]
        hop = Resp({"X-Download-Budget-Remaining": "1", "X-Download-Budget-Limit": "200",
                    "X-Download-Budget-Reset": str(int(time.time()) + 3600)})
        pool.note_headers(m, Resp({}, history=[hop]))           # la cabecera viene en la redirección, no en la final
        self.assertEqual(m["budget"], "1/200")
        self.assertIsNone(pool.reserve(m))                      # pausa larga: se salta
        self.assertTrue(any("agotó su cupo de descargas" in e for e in pool.pop_events()))
        # cupo diario de datos casi agotado
        pool2 = MirrorPool([{"name": "od", "url_novideo": "x", "url_full": "x"}], True, 0)
        pool2.note_headers(pool2.items[0], Resp({"X-Download-Budget-Bytes-Remaining": str(10 * 1048576),
                                                 "X-Download-Budget-Bytes-Reset": str(int(time.time()) + 7200)}))
        self.assertTrue(any("cupo diario de datos" in e for e in pool2.pop_events()))

    def test_config_vieja_con_mirrors_antiguos_se_actualiza(self):
        d = config.config_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "config.json").write_text(json.dumps({"client_id": "1", "concurrency": 2,
                                                   "mirrors": config.LEGACY_MIRRORS}), encoding="utf-8")
        cfg = config.load(use_env=False)
        self.assertEqual(cfg["mirrors"], config.DEFAULTS["mirrors"])
        self.assertEqual(cfg["mirrors"][0]["name"], "catboy")
        self.assertEqual(cfg["concurrency"], 2)                  # lo demás se respeta
        # una lista personalizada NO se toca
        mine = [{"name": "mio", "url_novideo": "u{id}", "url_full": "u{id}"}]
        (d / "config.json").write_text(json.dumps({"mirrors": mine}), encoding="utf-8")
        self.assertEqual(config.load(use_env=False)["mirrors"], mine)

    def test_guardar_no_congela_la_lista_por_defecto(self):
        config.save(config.load(use_env=False))
        guardado = json.loads((config.config_dir() / "config.json").read_text(encoding="utf-8"))
        self.assertNotIn("mirrors", guardado)


if __name__ == "__main__":
    unittest.main()
