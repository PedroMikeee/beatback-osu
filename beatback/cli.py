# SPDX-License-Identifier: GPL-3.0-or-later
"""Interfaz de línea de comandos de BeatBack."""
import argparse
import csv
import getpass
import threading
from pathlib import Path

from . import __version__, config, downloader
from .api import ApiError, OsuClient
from .db import State
from .planner import aggregate, plan

AVG_MB = 5  # tamaño medio supuesto de un .osz sin video; se reemplaza por el real cuando hay datos


def fmt_size(mb):
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.0f} MB"


def _state():
    d = config.config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return State(d / "beatback.db")


def _need_creds(cfg):
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise SystemExit("Faltan las credenciales de la API. Ejecuta primero: beatback init")


def cmd_init(args):
    cfg = config.load(use_env=False)
    print("Configuración de BeatBack (Enter conserva el valor actual).")
    cfg["client_id"] = input(f"Client ID [{cfg['client_id']}]: ").strip() or cfg["client_id"]
    secret = getpass.getpass("Client Secret (no se muestra al escribir): ").strip()
    cfg["client_secret"] = secret or cfg["client_secret"]
    cfg["username"] = input(f"Tu nombre de usuario de osu! [{cfg['username']}]: ").strip() or cfg["username"]
    cfg["output_dir"] = input(f"Carpeta de descarga [{cfg['output_dir']}]: ").strip() or cfg["output_dir"]
    f = config.save(cfg)
    print(f"Guardado en {f} (solo tú puedes leerlo).")
    try:
        user = OsuClient(cfg).find_user(cfg["username"])
        print(f"Conexión correcta. Jugador: {user['username']} (id {user['id']}).")
    except ApiError as e:
        print(f"Se guardó, pero la prueba de conexión falló: {e}")


def cmd_plan(args):
    cfg = config.load()
    _need_creds(cfg)
    name = args.usuario or cfg["username"]
    if not name:
        raise SystemExit("Indica tu usuario con --usuario o ejecuta: beatback init")
    try:
        client = OsuClient(cfg)
        user = client.find_user(name)
        print(f"Jugador: {user['username']} (id {user['id']})")
        items = []
        for it in client.most_played(user["id"], limit=args.prueba):
            items.append(it)
            if len(items) % 50 == 0:
                print(f"  leyendo mapas más jugados... {len(items)}", end="\r", flush=True)
    except ApiError as e:
        raise SystemExit(f"Error: {e}")
    print(f"Mapas (dificultades) leídos: {len(items)}            ")
    reported = user.get("beatmap_playcounts_count")
    if reported and not args.prueba:
        print(f"Tu perfil reporta {reported} mapas distintos jugados; BeatBack leyó {len(items)} entradas de esa lista.")
    sets = aggregate(items, args.modo)
    rows, unknown = plan(sets, args.min_jugadas, args.estados, args.desde, args.hasta, cfg["block_size"])
    st = _state()
    st.save_plan(rows)
    blocks = max((r["block"] for r in rows), default=0)
    print(f"Beatmapsets únicos a descargar: {len(rows)} en {blocks} bloque(s) de hasta {cfg['block_size']}.")
    avg, n = st.avg_done_mb()
    if n >= 20:
        print(f"Tamaño estimado: ~{fmt_size(len(rows) * avg)} (según tus {n} descargas reales).")
    else:
        print(f"Tamaño estimado: ~{fmt_size(len(rows) * AVG_MB)} (aproximado, "
              f"{'sin' if cfg['no_video'] else 'con'} video; el real varía).")
    if unknown:
        print(f"Aviso: {unknown} mapas no traen año de publicación y se conservaron aunque filtraras por año.")
    print("Ver la lista sin descargar: beatback list   |   Descargar: beatback download (prueba: --limite 5)")


def cmd_download(args):
    cfg = config.load()
    if args.concurrencia:
        cfg["concurrency"] = args.concurrencia
    st = _state()
    rows = st.queue(args.bloque, args.reintentar, args.limite)
    if not rows:
        print("No hay nada pendiente. Usa 'beatback status' para ver el estado o --reintentar para los fallidos.")
        return
    print(f"Descargando {len(rows)} mapas en {cfg['output_dir']} ... (Ctrl+C para pausar)")
    counts, stats = downloader.run(cfg, st, rows, threading.Event())
    print(f"Listo: {counts['done']} descargados, {counts['not_found']} no disponibles, {counts['failed']} con error.")
    if stats["bytes"]:
        mirrors = ", ".join(f"{k}: {v}" for k, v in sorted(stats["mirrors"].items()))
        print(f"Total: {fmt_size(stats['bytes'] / 1048576)} en {downloader._fmt_time(stats['secs'])}. Por mirror -> {mirrors}")


def cmd_status(args):
    st = _state()
    c = st.counts()
    if not c:
        print("Aún no hay plan. Ejecuta: beatback plan")
        return
    print("Estado: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
    print("Bloque  total  hechos  problemas")
    for b in st.per_block():
        print(f"{b['block']:>6}  {b['total']:>5}  {b['done'] or 0:>6}  {b['problems'] or 0:>9}")


def cmd_list(args):
    st = _state()
    rows, total = st.list_rows(args.estado, args.bloque, args.orden, args.limite)
    if not total:
        print("No hay mapas en la lista. Ejecuta primero: beatback plan")
        return
    print(f"{'id':>8}  {'jugadas':>7}  {'año':>4}  {'★':>4}  {'estado':<9}  artista - título")
    for r in rows:
        year = r["year"] or "?"
        title = f"{r['artist']} - {r['title']}"
        print(f"{r['id']:>8}  {r['playcount']:>7}  {year:>4}  {r['stars']:>4.1f}  {r['state']:<9}  {title[:70]}")
    print(f"Mostrando {len(rows)} de {total}. (usa --limite 0 para verlos todos, o: beatback export lista.csv)")


def cmd_export(args):
    rows = _state().all_rows()
    out = Path(args.archivo)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        cols = ["id", "artist", "title", "creator", "status", "year", "playcount", "stars", "block",
                "state", "mirror", "bytes", "path", "updated"]
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
    print(f"Exportadas {len(rows)} filas a {out}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="beatback", description="Recupera los mapas de osu! que has jugado.")
    p.add_argument("--version", action="version", version=f"BeatBack {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="guarda tus credenciales y opciones").set_defaults(fn=cmd_init)

    a = sub.add_parser("plan", help="lee tu historial y arma la lista (no descarga nada)")
    a.add_argument("--usuario", help="nombre de usuario de osu! (por defecto, el de init)")
    a.add_argument("--modo", default="osu", choices=["osu", "taiko", "fruits", "mania", "todos"])
    a.add_argument("--min-jugadas", type=int, default=1, help="mínimo de veces jugado")
    a.add_argument("--estados", nargs="+", help="ej: ranked loved approved graveyard")
    a.add_argument("--desde", type=int, help="año de publicación del mapa, desde")
    a.add_argument("--hasta", type=int, help="año de publicación del mapa, hasta")
    a.add_argument("--prueba", type=int, help="solo los N más jugados (para probar)")
    a.set_defaults(fn=cmd_plan)

    d = sub.add_parser("download", help="descarga lo pendiente, por bloques")
    d.add_argument("--bloque", type=int, help="solo este bloque")
    d.add_argument("--limite", type=int, help="solo N mapas (para probar)")
    d.add_argument("--reintentar", action="store_true", help="reintentar también los fallidos")
    d.add_argument("--concurrencia", type=int, help="descargas simultáneas")
    d.set_defaults(fn=cmd_download)

    sub.add_parser("status", help="muestra el progreso").set_defaults(fn=cmd_status)

    l = sub.add_parser("list", help="muestra en pantalla la lista de mapas (no descarga nada)")
    l.add_argument("--estado", choices=["pending", "done", "failed", "not_found"])
    l.add_argument("--bloque", type=int)
    l.add_argument("--orden", default="jugadas", choices=["jugadas", "id", "anio", "estrellas"])
    l.add_argument("--limite", type=int, default=50, help="cuántos mostrar (0 = todos)")
    l.set_defaults(fn=cmd_list)

    e = sub.add_parser("export", help="exporta la lista a CSV")
    e.add_argument("archivo", nargs="?", default="beatback.csv")
    e.set_defaults(fn=cmd_export)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
