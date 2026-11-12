# SPDX-License-Identifier: GPL-3.0-or-later
"""Estado del progreso en SQLite: si se corta, la descarga continúa donde quedó."""
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS sets(
  id INTEGER PRIMARY KEY, artist TEXT, title TEXT, creator TEXT, status TEXT,
  year INTEGER, playcount INTEGER, stars REAL, block INTEGER,
  state TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed | not_found
  error TEXT, path TEXT, bytes INTEGER, updated TEXT)
"""


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute(SCHEMA)
        self.db.commit()

    def save_plan(self, rows):
        """Guarda el plan. Lo ya descargado no se toca; lo pendiente que ya no cumple los filtros se quita."""
        ids = {r["id"] for r in rows}
        for r in rows:
            self.db.execute(
                """INSERT INTO sets(id,artist,title,creator,status,year,playcount,stars,block,updated)
                   VALUES(:id,:artist,:title,:creator,:status,:year,:playcount,:stars,:block,datetime('now'))
                   ON CONFLICT(id) DO UPDATE SET artist=excluded.artist, title=excluded.title,
                     creator=excluded.creator, status=excluded.status, year=excluded.year,
                     playcount=excluded.playcount, stars=excluded.stars,
                     block=CASE WHEN sets.state='done' THEN sets.block ELSE excluded.block END""", r)
        for (sid,) in self.db.execute("SELECT id FROM sets WHERE state='pending'").fetchall():
            if sid not in ids:
                self.db.execute("DELETE FROM sets WHERE id=?", (sid,))
        self.db.commit()

    def queue(self, block=None, retry_failed=False, limit=None):
        states = ("pending", "failed") if retry_failed else ("pending",)
        q = f"SELECT * FROM sets WHERE state IN ({','.join('?' * len(states))})"
        args = list(states)
        if block:
            q += " AND block=?"
            args.append(block)
        q += " ORDER BY block, playcount DESC, id"
        if limit:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.db.execute(q, args)]

    def mark(self, sid, state, error=None, path=None, size=None):
        self.db.execute("UPDATE sets SET state=?, error=?, path=?, bytes=?, updated=? WHERE id=?",
                        (state, error, path, size, time.strftime("%Y-%m-%d %H:%M:%S"), sid))
        self.db.commit()

    def counts(self):
        return {r[0]: r[1] for r in self.db.execute("SELECT state, COUNT(*) FROM sets GROUP BY state")}

    def per_block(self):
        return [dict(r) for r in self.db.execute(
            "SELECT block, COUNT(*) total, SUM(state='done') done, SUM(state IN ('failed','not_found')) problems "
            "FROM sets GROUP BY block ORDER BY block")]

    def all_rows(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM sets ORDER BY block, playcount DESC, id")]
