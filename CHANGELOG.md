# Cambios

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
