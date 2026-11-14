# Cambios

## 0.2.2
- **catboy.best vuelve a funcionar:** exige que la aplicación se identifique. Ahora BeatBack envía
  `BeatBack/VERSIÓN (+https://github.com/PedroMikeee/beatback-osu)` (se puede cambiar con `user_agent` en `config.json`).
- Mirrors con **niveles de preferencia** (`tier`): catboy primero; sayobot, osu.direct y nerinyan como respaldo.
  Un mirror libre en pocos segundos se prefiere a uno de nivel más bajo en pausa, para no quedarse esperando.
- Nuevo mirror **osu.direct** (entrega el mapa con video). BeatBack lee sus cupos (descargas y datos), los muestra
  en el resumen y se pausa antes de agotarlos.
- **Migración automática de la configuración:** si `config.json` tenía la lista antigua de mirrors sin personalizar,
  se reemplaza por la nueva. La lista por defecto ya no se congela en `config.json`.
- `beatback mirrors` prueba los mirrors de respaldo con un solo mapa (para no gastar su cupo) y muestra el cupo.

## 0.2.1
- **Corrección:** un corte o timeout a mitad de una descarga ya no cierra el programa con un traceback; se reintenta con otro mirror.
- Un mirror que falla 3 mapas seguidos (o dice "no lo tengo" a mapas que otros sí tienen) se aparta unos minutos, con aviso en pantalla.
- Un mapa defectuoso castiga al mirror una sola vez; con un único mirror configurado no se aparta nunca.
- Espera máxima por falta de datos: 30 s (antes 60 s), para que un mirror colgado cueste menos.
- Cada línea muestra los mirrors que fallaron antes (`[antes: nerinyan:404]`); la velocidad (MB/s) mide solo la descarga, sin esperas.
- Nuevo `beatback mirrors`: diagnóstico de cada mirror. Nuevo `beatback download --mirror NOMBRE`.
- Resumen final por mirror: descargados, errores y "no lo tengo".

## 0.2.0
- Las descargas se reparten entre los mirrors en lugar de usar siempre el primero (más rápido, misma cortesía por mirror).
- Se registra qué mirror sirvió cada archivo y en cuánto tiempo; la salida muestra MB/s y tiempo restante.
- Nuevo comando `beatback list`: ver la lista de mapas en pantalla sin descargar nada.
- `export` incluye mirror, tamaño y fecha.
- `plan` compara con el total que reporta tu perfil (si la API lo da) y estima el tamaño con tus descargas reales.
- La estimación de tamaño muestra MB cuando son pocos mapas.
- Concurrencia por defecto: 3. La base de datos de la 0.1 se migra sola.

## 0.1.0
- Núcleo inicial: `init`, `plan`, `download` (por bloques, reanudable), `status`, `export`.
