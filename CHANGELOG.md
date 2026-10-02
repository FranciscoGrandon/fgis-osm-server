# Changelog

Todas las modificaciones notables a este proyecto serán documentadas en este archivo.

El formato está basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.0.0/),
y este proyecto se adhiere a [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Añadido
- Publicación del repositorio público oficial en GitHub: `https://github.com/FranciscoGrandon/fgis-osm-server`.
- Integración completa del cliente web autónomo **F-GIS 4.0** (`fgis4/`) con capa resiliente (`OsmLocalResilientLayer`), conmutación automática de teselas (fallback), librerías locales (`lib/`) y herramientas geoanalíticas de terreno.
- Archivo `.gitignore` integral para exclusión estricta de cachés de mapas (`cache/`, `tiles.db*`, `*.mbtiles`), entornos virtuales y artefactos de cobertura.
- `README.md` principal unificado con arquitectura del stack completo, instrucciones de ejecución local y documentación de endpoints.
- Archivo de configuración centralizado `pyproject.toml` con estándares de calidad para `ruff`, `mypy` (modo strict), `pytest`, `pytest-cov` (umbral >90%) y `bandit`.
- Suite integral de pruebas unitarias en `tests/test_server.py` y `tests/test_seed_region.py` con cobertura de casos felices, límites geográficos y de zoom, persistencia SQLite, expiración TTL y excepciones de red.
- Funciones modulares y testeables en `server.py` (`validate_tile_coords`, `get_tile_from_cache`, `get_tile_size_from_cache`, `save_tile_to_cache`, `fetch_upstream_tile`, `get_metrics`).
- Funciones matemáticas geográficas adicionales en `seed_region.py` (`num2deg`, `calculate_tile_bounds`) con validación estricta de bordes en proyección Web Mercator EPSG:3857.
- Servidor local de teselas cartográficas nativo en Python (`server.py`) con soporte multihilo (`ThreadingHTTPServer`) en puerto 8080.
- Caché persistente de teselas en SQLite con modo WAL (`cache/tiles.db`).
- Endpoint estándar de teselas cartográficas `/tile/{z}/{x}/{y}.png` compatible con especificación XYZ Slippy Map.
- Endpoint de monitoreo y métricas en tiempo real `/api/status` y healthcheck `/api/health`.
- Visor web cartográfico interactivo (`index.html`) con Leaflet.js, botones de acceso rápido para Rancagua, Talca, Chillán y Concepción, polígono delimitador de la zona y panel de estadísticas de caché.
- Script de pre-carga y seeding masivo (`seed_region.py`) para almacenamiento offline de las 4 regiones (O'Higgins, Maule, Ñuble, Biobío).
- Archivo de configuración Docker Compose (`docker-compose.yml`) con imagen `overv/openstreetmap-tile-server` para soporte multiplataforma.
- Manual de uso y operaciones detallado en `README.md`.
- Sección de Estrategia DevOps Recomendada de Actualización y Mantenimiento en `README.md` (cadencias recomendadas, política de OSM y 4 métodos operativos).
- Flags CLI `--ttl-days` y `--force` en `seed_region.py` para refresco incremental de teselas expiradas.
- Diagrama técnico de arquitectura y flujo de datos en formato Mermaid integrado en `README.md`.

### Cambiado
- Refactorización completa de `server.py` y `seed_region.py` con tipado estricto (`type hints` compatibles con Python 3.10+ y `mypy --strict`), docstrings estandarizados en formato Google con ejemplos de uso `doctest`.
- Validación y sanitización rigurosa de coordenadas y URLs para mitigar riesgos de seguridad (SSRF) conforme a auditoría `bandit`.
- Migración del puerto local de escucha predeterminado desde el saturado `8080` al puerto limpio y dedicado `8550` en `server.py`, `index.html`, `docker-compose.yml` y `README.md`.

### Corregido
- Fuga de estado en conexiones thread-local SQLite en `server.py` (`get_db`) que retenía conexiones obsoletas entre solicitudes y suites de prueba; se implementó tracking de `db_path` por hilo y función `close_db()`.
- Error de evaluación temprana de argumentos por defecto para `DB_PATH` en firmas de funciones de persistencia y métricas.
- Violaciones de tipado estricto en `tests/test_server.py` (`sqlite_conn` tipado como `Iterator[sqlite3.Connection]`, `MockSocket.makefile` completamente anotado).
- Eliminación de importación sin uso de `os` y simplificación de validación de coordenadas (regla Ruff SIM103).
- Sustitución de bloques `try-except-pass` por `contextlib.suppress(Exception)` en `server.py`, subsanando hallazgos de seguridad Bandit (B110) y linter (SIM105).
- 5 tests fallidos en la suite unitaria causados por fuga de conexión a la base de datos productiva.
- Cobertura de código insuficiente (77.06% previo), elevada a 97.61% con nuevas pruebas para rutas HEAD, archivos estáticos, errores de red y seeding urbano.
