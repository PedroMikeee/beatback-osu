# SPDX-License-Identifier: GPL-3.0-or-later
"""Convierte la lista de 'más jugados' en una lista de beatmapsets sin duplicados, filtrada y en bloques."""


def _year(bs: dict):
    date = bs.get("ranked_date") or bs.get("submitted_date") or ""
    return int(date[:4]) if date[:4].isdigit() else None


def aggregate(items, modo="osu"):
    """Une varias dificultades del mismo set en una sola fila (un .osz se descarga una vez)."""
    sets = {}
    for it in items:
        bm, bs = it.get("beatmap") or {}, it.get("beatmapset") or {}
        sid = bs.get("id") or bm.get("beatmapset_id")
        if not sid:
            continue
        if modo != "todos" and bm.get("mode") not in (None, modo):
            continue
        s = sets.setdefault(sid, {
            "id": int(sid), "artist": bs.get("artist") or "?", "title": bs.get("title") or "?",
            "creator": bs.get("creator") or "?", "status": (bs.get("status") or bm.get("status") or "?").lower(),
            "year": _year(bs), "playcount": 0, "stars": 0.0})
        s["playcount"] += int(it.get("count") or 0)
        s["stars"] = max(s["stars"], float(bm.get("difficulty_rating") or 0))
    return list(sets.values())


def plan(sets, min_playcount=1, estados=None, desde=None, hasta=None, block_size=150):
    """Aplica filtros y asigna bloques (los más jugados primero). Devuelve (filas, sin_anio)."""
    estados = {e.lower() for e in estados} if estados else None
    out, unknown_year = [], 0
    for s in sets:
        if s["playcount"] < min_playcount:
            continue
        if estados and s["status"] not in estados:
            continue
        if (desde or hasta) and s["year"] is None:
            unknown_year += 1          # no sabemos el año: se conserva y se avisa
        elif s["year"] is not None and ((desde and s["year"] < desde) or (hasta and s["year"] > hasta)):
            continue
        out.append(s)
    out.sort(key=lambda s: (-s["playcount"], s["id"]))
    for i, s in enumerate(out):
        s["block"] = i // block_size + 1
    return out, unknown_year
