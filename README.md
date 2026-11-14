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
beatback plan                      # lee tu historial y arma la lista (no descarga nada)
beatback list                      # VER la lista en pantalla (por defecto los 50 más jugados; --limite 0 = todos)
beatback mirrors                   # prueba cada mirror: ¿responde?, ¿a qué velocidad? (hazlo ANTES de descargar mucho)
beatback download --limite 5       # PRUEBA con 5 mapas antes de lanzar todo
beatback download                  # descarga todo lo pendiente (Ctrl+C pausa; vuelve a ejecutar para seguir)
beatback status                    # progreso por bloque
beatback export lista.csv          # lista de todo en CSV (estado, mirror, tamaño, fecha...)
```

`list` admite `--estado pending|done|failed|not_found`, `--bloque N` y `--orden jugadas|id|anio|estrellas`.

Filtros de `plan`: `--modo osu|taiko|fruits|mania|todos` (por defecto `osu`), `--min-jugadas N`,
`--estados ranked loved graveyard ...`, `--desde AÑO`, `--hasta AÑO`, `--prueba N`.

**Sobre los años:** la lista de "más jugados" de osu! no indica *cuándo* jugaste cada mapa. `--desde/--hasta`
filtran por el año de **publicación del mapa**, que solo es una aproximación.

## Si la descarga va lenta o falla

1. Ejecuta `beatback mirrors`: prueba cada mirror con mapas de tu lista y muestra si responde, cuánto tarda y a qué MB/s.
2. Si uno falla o es muy lento, descarga solo con los buenos: `beatback download --mirror catboy sayobot`
   (o cambia su `tier` en la lista `mirrors` de `config.json`).
3. Durante la descarga, cada línea indica qué mirror sirvió el archivo y, entre corchetes, los que fallaron antes
   (`[antes: catboy:corte]`). Un mirror que falla 3 mapas seguidos se aparta unos minutos y BeatBack avisa.
4. Un corte de conexión a mitad de archivo ya no detiene el programa: se reintenta con otro mirror.

## Mirrors y niveles de preferencia

Cada mirror tiene un `tier` (0 = primero). BeatBack usa los de nivel bajo mientras estén disponibles y recurre a los
de nivel alto solo cuando los anteriores están ocupados, en pausa o no tienen el mapa. Lista por defecto (desde 0.2.2):

| Nivel | Mirror | Notas (medidas en una conexión real, noviembre 2026) |
|---|---|---|
| 0 | catboy.best | Rápido (~2 MB/s). **Exige identificarse** con nombre, versión y dirección: BeatBack lo hace (`USER_AGENT`). |
| 1 | sayobot | Funciona, pero lento (0,07 a 0,2 MB/s) y con cortes ocasionales. |
| 2 | osu.direct | Rápido, pero entrega el mapa **con video** y informa cupos (descargas y datos). BeatBack los lee y se pausa solo. |
| 3 | nerinyan | En las pruebas respondió `not_found` para mapas que existen: falla de su lado. |

Por defecto la lista **no se guarda** en `config.json`, así las mejoras de cada versión llegan solas. Si la editas, tu
versión se respeta. Cada entrada: `name`, `tier`, `url_novideo`, `url_full` (con `{id}` donde va el número del set).
Si algún mirror devuelve 403, no intentes saltártelo: es su forma de decidir quién puede descargar.
Antes de una descarga masiva, revisa las condiciones de uso de cada mirror.

## Historial y velocidad

- **Historial:** cada mapa queda registrado (estado, archivo, tamaño, mirror que lo sirvió y fecha) en una base
  SQLite: `~/.config/beatback/beatback.db` en Linux, `C:\\Users\\TuUsuario\\.config\\beatback\\beatback.db` en Windows.
  Es **por equipo**: cada ordenador lleva el suyo. Consúltalo con `beatback status`, `beatback list` o `beatback export`.
- **Velocidad:** las descargas se reparten entre los mirrors (el que lleva más tiempo libre va primero) y cada uno
  respeta una pausa mínima (`min_interval`). Durante la descarga se muestra MB/s y el tiempo restante estimado.
  Si aparecen errores `429`, baja `concurrency` a 2 en la configuración.
- Si tienes menos de 20 mapas descargados, el tamaño estimado usa un promedio supuesto; después usa tus datos reales.

## Windows

Funciona con Python 3.9+. Si PowerShell bloquea la activación del entorno virtual:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. Evita rutas con símbolos como `!`. La configuración
no se comparte entre equipos: repite `beatback init` en cada uno.

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
