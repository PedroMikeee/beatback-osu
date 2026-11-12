# BeatBack

Recupera los mapas de **osu!** que has jugado a lo largo del tiempo, sin ir uno por uno.
Escribes tu usuario, BeatBack lee tu lista de mapas más jugados y los descarga **sin video**,
en **carpetas por bloques**, listas para importar en osu!lazer.

> Proyecto no oficial. No está afiliado a osu! ni a ppy. Es una herramienta de copia de seguridad personal.

## Instalación (Ubuntu / Debian)

```bash
sudo apt install python3 python3-venv python3-pip git
cd beatback
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
```

(En Ubuntu 24.04 hay que usar el entorno virtual `.venv`; instalar paquetes de Python "a pelo" está bloqueado.)

## Credenciales de la API (una sola vez)

1. Entra a <https://osu.ppy.sh/home/account/edit>, sección **OAuth**, y pulsa **New OAuth Application**.
2. Nombre: el que quieras. Callback URL: `http://localhost`.
3. Copia el **Client ID** y el **Client Secret**. El secreto es privado: no lo compartas ni lo subas a Git.
4. Ejecuta `beatback init` y pega los datos. Se guardan en `~/.config/beatback/config.json`, legible solo por ti.

## Uso

```bash
beatback init                      # credenciales y carpeta de descarga
beatback plan                      # lee tu historial y arma la lista (no descarga)
beatback download --limite 5       # PRUEBA con 5 mapas antes de lanzar todo
beatback download                  # descarga todo lo pendiente (Ctrl+C pausa; vuelve a ejecutar para seguir)
beatback status                    # progreso por bloque
beatback export lista.csv          # lista de todo en CSV
```

Filtros de `plan`: `--modo osu|taiko|fruits|mania|todos` (por defecto `osu`), `--min-jugadas N`,
`--estados ranked loved graveyard ...`, `--desde AÑO`, `--hasta AÑO`, `--prueba N`.

**Sobre los años:** la lista de "más jugados" de osu! no indica *cuándo* jugaste cada mapa. `--desde/--hasta`
filtran por el año de **publicación del mapa**, que solo es una aproximación.

## Importar en osu!lazer

Los mapas quedan en `bloque_001`, `bloque_002`, ... dentro de tu carpeta de descarga. Importa un bloque a la vez
(en lazer: arrastrar los `.osz` a la ventana, o usar el importador de archivos del juego; el comportamiento exacto
con muchos archivos está **pendiente de verificar** en tu instalación).

## Configuración

Edita `~/.config/beatback/config.json`: tamaño de bloque, con/sin video, descargas simultáneas, pausa entre peticiones
y la lista de mirrors. Los mirrors son servicios de terceros y pueden cambiar o caerse; BeatBack prueba uno tras otro
y respeta sus límites de velocidad. **No subas la concurrencia**: es la forma más rápida de que te bloqueen.

## Límites conocidos

- Solo ve lo que expone la API oficial (más jugados). No recupera un historial completo con fechas.
- Mapas eliminados del juego pueden no estar en ningún mirror (se marcan `not_found`).
- Los mirrors y el sufijo de "sin video" de `catboy` están **por verificar** con pruebas reales.

## Pruebas

```bash
python3 -m unittest discover -s tests -t . -v
```
Usan una API y unos mirrors **falsos** locales; no tocan internet.

## Licencias

- **Código de BeatBack:** GPL-3.0-or-later (ver `LICENSE`). *Provisional hasta que el grupo confirme.*
- **Documentación:** CC BY-SA 4.0 (propuesta).
- **Dependencias:**

| Componente | Uso | Licencia | Estado |
|---|---|---|---|
| Python (biblioteca estándar) | `argparse`, `sqlite3`, `unittest`... | PSF | a verificar |
| `requests` | peticiones HTTP | Apache-2.0 | a verificar |

- **Contenido de osu!** (mapas y música): pertenece a sus autores. BeatBack no lo aloja ni lo redistribuye; solo
  facilita descargar desde mirrors públicos lo que ya jugaste. Respeta los términos de la API de osu! y de cada mirror.
