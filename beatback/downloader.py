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


class MirrorPool:
    """Reparte las peticiones entre mirrors respetando una pausa mínima por mirror."""

    def __init__(self, mirrors, no_video, min_interval):
        key = "url_novideo" if no_video else "url_full"
        self.items = [{"name": m["name"], "url": m[key], "next": 0.0} for m in mirrors]
        self.min_interval = float(min_interval)
        self.lock = threading.Lock()

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

    def soonest(self):
        with self.lock:
            return max(0.0, min(m["next"] for m in self.items) - time.monotonic())


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


def fetch_set(set_id, dest: Path, pool: MirrorPool, session, rounds=4):
    """Devuelve (estado, detalle): done | not_found | failed."""
    errors, gone = [], set()
    for rnd in range(rounds):
        for m in pool.items:
            if m["name"] in gone:
                continue
            wait = pool.reserve(m)
            if wait is None:
                continue
            if wait:
                time.sleep(wait)
            try:
                r = session.get(m["url"].format(id=set_id), headers={"User-Agent": UA},
                                stream=True, timeout=(10, 60))
            except requests.RequestException as e:
                pool.penalize(m, 10)
                errors.append(f"{m['name']}: red ({type(e).__name__})")
                continue
            with r:
                if r.status_code == 200:
                    ok, info = _save(r, dest)
                    if ok:
                        return "done", info
                    errors.append(f"{m['name']}: {info}")
                elif r.status_code in (404, 410):
                    gone.add(m["name"])
                    errors.append(f"{m['name']}: no tiene el mapa")
                elif r.status_code in (429, 502, 503, 504):
                    pool.penalize(m, _retry_after(r))
                    errors.append(f"{m['name']}: pide esperar (HTTP {r.status_code})")
                else:
                    errors.append(f"{m['name']}: HTTP {r.status_code}")
        if len(gone) == len(pool.items):
            return "not_found", "ningún mirror tiene este mapa"
        if rnd < rounds - 1:      # espera a que se libere algún mirror y reintenta
            time.sleep(min(max(pool.soonest(), 1.0), 300.0))
    return "failed", "; ".join(errors[-4:]) or "sin respuesta"


def safe_name(artist, title, sid):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", f"{sid} {artist} - {title}").strip()
    return name[:120].rstrip(". ") + ".osz"


def dest_for(out_dir, row):
    return Path(out_dir) / f"bloque_{row['block']:03d}" / safe_name(row["artist"], row["title"], row["id"])


def run(cfg, state, rows, stop: threading.Event, say=print):
    """Descarga las filas con N hilos. Cada resultado se guarda en la base apenas termina."""
    pool = MirrorPool(cfg["mirrors"], cfg["no_video"], cfg["min_interval"])
    local = threading.local()
    total, done_n, counts = len(rows), 0, {"done": 0, "not_found": 0, "failed": 0}

    def job(row):
        if stop.is_set():
            return row, None
        dest = dest_for(cfg["output_dir"], row)
        if dest.exists() and dest.stat().st_size >= MIN_SIZE:      # ya estaba en disco
            return row, ("done", dest.stat().st_size)
        if not hasattr(local, "s"):
            local.s = requests.Session()
        return row, fetch_set(row["id"], dest, pool, local.s)

    ex = ThreadPoolExecutor(max_workers=max(1, int(cfg["concurrency"])))
    try:
        for fut in as_completed([ex.submit(job, r) for r in rows]):
            row, res = fut.result()
            if res is None:
                continue
            st, info = res
            done_n += 1
            counts[st] += 1
            dest = str(dest_for(cfg["output_dir"], row))
            if st == "done":
                state.mark(row["id"], "done", path=dest, size=info)
            else:
                state.mark(row["id"], st, error=info)
            tag = {"done": "OK ", "not_found": "NO ", "failed": "ERR"}[st]
            extra = "" if st == "done" else f"  ({info})"
            say(f"[{done_n}/{total}] {tag} {row['id']} {row['artist']} - {row['title']}{extra}")
    except KeyboardInterrupt:
        stop.set()
        say("\nInterrumpido. Lo descargado queda guardado; vuelve a ejecutar 'download' para continuar.")
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    return counts
