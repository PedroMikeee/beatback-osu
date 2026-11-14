# SPDX-License-Identifier: GPL-3.0-or-later
"""Descarga de .osz desde mirrors: con pausas, reintentos, verificación y escritura segura."""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from .api import UA

MIN_SIZE = 1024        # un .osz real pesa más que esto
LONG_COOLDOWN = 120    # si un mirror pide esperar más, lo saltamos y probamos otro
TIMEOUT = (10, 30)     # (conectar, esperar datos): un mirror colgado cuesta 30 s como máximo, no 60
HARD_FAILS = 3         # fallos seguidos antes de poner un mirror en pausa
MISS_LIMIT = 15        # veces seguidas que dice "no lo tengo" mientras otro mirror sí lo tenía


class MirrorPool:
    """Reparte las peticiones entre mirrors, respeta su pausa mínima y aparta a los que fallan."""

    def __init__(self, mirrors, no_video, min_interval):
        key = "url_novideo" if no_video else "url_full"
        self.items = [{"name": m["name"], "url": m[key], "next": 0.0, "fails": 0, "misses": 0, "bench_n": 0,
                       "n_ok": 0, "n_err": 0, "n_404": 0} for m in mirrors]
        self.min_interval = float(min_interval)
        self.lock = threading.Lock()
        self.events = []          # avisos para mostrar en pantalla

    def _event(self, text):
        self.events.append(text)

    def pop_events(self):
        with self.lock:
            ev, self.events = self.events, []
        return ev

    def reserve(self, m):
        """Segundos a esperar antes de usar el mirror, o None si está en pausa larga."""
        with self.lock:
            now = time.monotonic()
            wait = max(0.0, m["next"] - now)
            if wait > LONG_COOLDOWN:
                return None
            m["next"] = now + wait + self.min_interval
            return wait

    def penalize(self, m, secs):
        with self.lock:
            m["next"] = max(m["next"], time.monotonic() + secs)

    def ordered(self):
        """Mirrors ordenados por disponibilidad: el que lleva más tiempo sin usarse va primero."""
        with self.lock:
            return sorted(self.items, key=lambda m: m["next"])

    def soonest(self):
        with self.lock:
            return max(0.0, min(m["next"] for m in self.items) - time.monotonic())

    def bump(self, m, key):
        with self.lock:
            m[key] += 1

    def failure(self, m):
        """Fallo 'duro' (sin conexión, corte, respuesta inválida, error 5xx). Tras varios seguidos, pausa."""
        with self.lock:
            m["n_err"] += 1
            m["fails"] += 1
            if m["fails"] < HARD_FAILS or len(self.items) == 1:   # con un solo mirror, apartarlo no sirve de nada
                return
            m["bench_n"] += 1
            secs = min(120 * 2 ** (m["bench_n"] - 1), 1800)
            m["next"] = max(m["next"], time.monotonic() + secs)
            m["fails"] = 0
            self._event(f"AVISO: {m['name']} falló {HARD_FAILS} veces seguidas; en pausa {secs // 60:.0f} min.")

    def success(self, m, gone):
        """Éxito de m. Los mirrors que dijeron 'no lo tengo' para este mapa suman un punto en contra."""
        with self.lock:
            m["n_ok"] += 1
            m["fails"] = m["misses"] = m["bench_n"] = 0
            for g in self.items:
                if g["name"] in gone:
                    g["misses"] += 1
                    if g["misses"] >= MISS_LIMIT:
                        g["misses"] = 0
                        g["next"] = max(g["next"], time.monotonic() + 600)
                        self._event(f"AVISO: {g['name']} no tiene mapas que otros mirrors sí tienen; en pausa 10 min.")


def _retry_after(r):
    try:
        return min(max(int(r.headers.get("Retry-After", 60)), 1), 600)
    except ValueError:
        return 60


def _save(r, dest: Path):
    """Escribe en .part, verifica que sea un ZIP/.osz y solo entonces lo renombra."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".osz.part")
    size, head = 0, b""
    with open(part, "wb") as fh:
        for chunk in r.iter_content(65536):
            if not head:
                head = chunk[:4]
            fh.write(chunk)
            size += len(chunk)
    if head != b"PK\x03\x04" or size < MIN_SIZE:
        part.unlink(missing_ok=True)
        return False, "respuesta inválida (no es un .osz)"
    os.replace(part, dest)
    return True, size


def _notes(trail):
    return ", ".join(f"{n}:{c}" for n, c, _ in trail)


def fetch_set(set_id, dest: Path, pool: MirrorPool, session, rounds=4):
    """Devuelve (estado, detalle, mirror, notas, segundos_de_descarga). Nunca lanza errores de red."""
    trail, gone, counted = [], set(), set()     # trail: intentos fallidos (mirror, código, texto)

    def bad(m, code, text):
        trail.append((m["name"], code, text))
        if m["name"] not in counted:        # un mismo mapa defectuoso castiga al mirror una sola vez
            counted.add(m["name"])
            pool.failure(m)

    for rnd in range(rounds):
        for m in pool.ordered():
            if m["name"] in gone:
                continue
            wait = pool.reserve(m)
            if wait is None:
                continue
            if wait:
                time.sleep(wait)
            t0 = time.monotonic()
            try:
                r = session.get(m["url"].format(id=set_id), headers={"User-Agent": UA},
                                stream=True, timeout=TIMEOUT)
            except (requests.RequestException, OSError) as e:
                bad(m, "red", f"{m['name']}: sin conexión ({type(e).__name__})")
                continue
            try:
                with r:
                    code = r.status_code
                    if code == 200:
                        ok, info = _save(r, dest)
                        if ok:
                            pool.success(m, gone)
                            return "done", info, m["name"], _notes(trail), time.monotonic() - t0
                        bad(m, "inválido", f"{m['name']}: {info}")
                    elif code in (404, 410):
                        gone.add(m["name"])
                        pool.bump(m, "n_404")
                        trail.append((m["name"], str(code), f"{m['name']}: no tiene el mapa"))
                    elif code in (429, 502, 503, 504):
                        pool.penalize(m, _retry_after(r))
                        trail.append((m["name"], str(code), f"{m['name']}: pide esperar (HTTP {code})"))
                    else:
                        bad(m, f"HTTP{code}", f"{m['name']}: HTTP {code}")
            except (requests.RequestException, OSError) as e:       # corte a mitad de la descarga
                dest.with_suffix(".osz.part").unlink(missing_ok=True)
                bad(m, "corte", f"{m['name']}: se cortó la descarga ({type(e).__name__})")
        if len(gone) == len(pool.items):
            return "not_found", "ningún mirror tiene este mapa", None, _notes(trail), 0.0
        if rnd < rounds - 1:         # espera a que se libere algún mirror y reintenta
            time.sleep(min(max(pool.soonest(), 1.0), 60.0))
    return "failed", "; ".join(t[2] for t in trail[-4:]) or "sin respuesta", None, _notes(trail), 0.0


def probe(url_template, set_id, session, max_secs=60):
    """Prueba un mirror con un mapa (sin guardarlo). Devuelve un diccionario con el veredicto."""
    t0, size, head = time.monotonic(), 0, b""
    try:
        with session.get(url_template.format(id=set_id), headers={"User-Agent": UA},
                         stream=True, timeout=TIMEOUT) as r:
            if r.status_code != 200:
                v = "no tiene este mapa" if r.status_code in (404, 410) else f"respondió HTTP {r.status_code}"
                return {"verdict": v, "secs": time.monotonic() - t0, "size": 0}
            for chunk in r.iter_content(65536):
                head = head or chunk[:4]
                size += len(chunk)
                if time.monotonic() - t0 > max_secs:
                    return {"verdict": f"demasiado lento (cortado a los {max_secs} s)", "secs": time.monotonic() - t0, "size": size}
    except (requests.RequestException, OSError) as e:
        return {"verdict": f"error ({type(e).__name__})", "secs": time.monotonic() - t0, "size": size}
    ok = head == b"PK\x03\x04" and size >= MIN_SIZE
    return {"verdict": "OK" if ok else "respuesta inválida", "secs": time.monotonic() - t0, "size": size}


def safe_name(artist, title, sid):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", f"{sid} {artist} - {title}").strip()
    return name[:120].rstrip(". ") + ".osz"


def dest_for(out_dir, row):
    return Path(out_dir) / f"bloque_{row['block']:03d}" / safe_name(row["artist"], row["title"], row["id"])


def _fmt_time(secs):
    secs = int(secs)
    h, r = divmod(secs, 3600)
    m, sec = divmod(r, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{sec:02d}s" if m else f"{sec}s")


def run(cfg, state, rows, stop: threading.Event, say=print):
    """Descarga las filas con N hilos. Cada resultado se guarda en la base apenas termina.
    Devuelve (conteos, estadísticas)."""
    pool = MirrorPool(cfg["mirrors"], cfg["no_video"], cfg["min_interval"])
    local = threading.local()
    total, done_n = len(rows), 0
    counts = {"done": 0, "not_found": 0, "failed": 0}
    stats = {"bytes": 0, "secs": 0.0, "pool": []}
    t0 = time.monotonic()

    def job(row):
        if stop.is_set():
            return row, None
        try:
            dest = dest_for(cfg["output_dir"], row)
            if dest.exists() and dest.stat().st_size >= MIN_SIZE:      # ya estaba en disco
                return row, ("done", dest.stat().st_size, "disco", "", 0.0)
            if not hasattr(local, "s"):
                local.s = requests.Session()
            return row, fetch_set(row["id"], dest, pool, local.s)
        except Exception as e:      # último recurso: un mapa con problemas nunca debe tumbar la descarga entera
            return row, ("failed", f"error inesperado: {type(e).__name__}: {e}", None, "", 0.0)

    ex = ThreadPoolExecutor(max_workers=max(1, int(cfg["concurrency"])))
    try:
        for fut in as_completed([ex.submit(job, r) for r in rows]):
            row, res = fut.result()
            if res is None:
                continue
            st, info, mirror, notes, dl = res
            done_n += 1
            counts[st] += 1
            dest = str(dest_for(cfg["output_dir"], row))
            before = f"  [antes: {notes}]" if notes else ""
            if st == "done":
                state.mark(row["id"], "done", path=dest, size=info, mirror=mirror, secs=round(dl, 2))
                stats["bytes"] += info
                mb = info / 1048576
                speed = f", {mb / dl:.2f} MB/s" if dl > 0.05 else ""
                extra = f"  ({mb:.1f} MB, {mirror}{speed}){before}"
            else:
                state.mark(row["id"], st, error=info)
                extra = f"  ({info})"
            elapsed = time.monotonic() - t0
            eta = f"  ~{_fmt_time((total - done_n) * elapsed / done_n)} restantes" if 3 <= done_n < total else ""
            tag = {"done": "OK ", "not_found": "NO ", "failed": "ERR"}[st]
            say(f"[{done_n}/{total}] {tag} {row['id']} {row['artist']} - {row['title']}{extra}{eta}")
            for ev in pool.pop_events():
                say(ev)
    except KeyboardInterrupt:
        stop.set()
        say("\nInterrumpido. Lo descargado queda guardado; vuelve a ejecutar 'download' para continuar.")
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    stats["secs"] = time.monotonic() - t0
    stats["pool"] = [(m["name"], m["n_ok"], m["n_err"], m["n_404"]) for m in pool.items]
    return counts, stats
