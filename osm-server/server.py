#!/usr/bin/env python3
"""Servidor Local de Teselas OpenStreetMap (Nativo Python).

Almacena y sirve teselas cartográficas en formato XYZ (/tile/{z}/{x}/{y}.png).
Utiliza SQLite como almacenamiento persistente y caché local de alto rendimiento.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import re
import socketserver
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# Constantes del servicio
PORT: int = 8550
HOST: str = "0.0.0.0"  # nosec B104
BASE_DIR: Path = Path(__file__).resolve().parent
CACHE_DIR: Path = BASE_DIR / "cache"
DB_PATH: Path = CACHE_DIR / "tiles.db"
START_TIME: float = time.time()
USER_AGENT: str = "OSMLocalServer/1.0 (devops@local.osm; Local Map Server)"

# Patrón de coincidencia para endpoints de teselas XYZ
TILE_REGEX: re.Pattern[str] = re.compile(r"^/tile/(\d+)/(\d+)/(\d+)\.png$")

# Conexión thread-safe a SQLite por cada hilo de atención HTTP
_local = threading.local()


def validate_tile_coords(z: int, x: int, y: int) -> bool:
    """Valida si las coordenadas XYZ de una tesela son numéricamente válidas.

    En la proyección Web Mercator (Slippy Map), el nivel de zoom z debe estar
    en el rango [0, 19] para OpenStreetMap estándar, y las coordenadas x e y
    deben estar dentro del rango [0, 2^z - 1].

    Args:
        z: Nivel de zoom (entero no negativo).
        x: Columna de la tesela (0 a 2^z - 1).
        y: Fila de la tesela (0 a 2^z - 1).

    Returns:
        True si las coordenadas están dentro de los límites válidos, False en caso contrario.

    Example:
        >>> validate_tile_coords(0, 0, 0)
        True
        >>> validate_tile_coords(1, 2, 0)
        False
    """
    if z < 0 or z > 19:
        return False
    max_index = (1 << z) - 1
    if x < 0 or x > max_index:
        return False
    return not (y < 0 or y > max_index)


def init_db(db_path: Path | None = None) -> None:
    """Inicializa el esquema de la base de datos SQLite para almacenamiento de teselas.

    Crea la tabla `tiles` y su índice primario si no existen.

    Args:
        db_path: Ruta al archivo SQLite donde se almacenarán las teselas. Si es None, usa DB_PATH.

    Example:
        >>> from pathlib import Path
        >>> init_db(Path("/tmp/test_tiles.db"))
    """
    target_path = db_path if db_path is not None else DB_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_path))
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tiles (
                    z INTEGER NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (z, x, y)
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tiles_zxy ON tiles(z, x, y);")
    finally:
        conn.close()


def get_db(db_path: Path | None = None) -> sqlite3.Connection:
    """Retorna una conexión SQLite reutilizable para el hilo actual.

    Aplica configuración de alto rendimiento con `journal_mode=WAL`
    y `synchronous=NORMAL`. Si cambia la ruta solicitada, renueva la conexión.

    Args:
        db_path: Ruta al archivo de base de datos SQLite. Si es None, usa DB_PATH actual.

    Returns:
        Instancia de `sqlite3.Connection` vinculada al hilo actual.

    Example:
        >>> conn = get_db()
        >>> isinstance(conn, sqlite3.Connection)
        True
    """
    target_path = db_path if db_path is not None else DB_PATH
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    cached_path: Path | None = getattr(_local, "db_path", None)
    if conn is None or cached_path != target_path:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(target_path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        # Asegurar tabla existente
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tiles (
                    z INTEGER NOT NULL,
                    x INTEGER NOT NULL,
                    y INTEGER NOT NULL,
                    data BLOB NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (z, x, y)
                );
            """)
        _local.conn = conn
        _local.db_path = target_path
    return conn


def close_db() -> None:
    """Cierra y limpia la conexión SQLite del hilo actual si está activa.

    Example:
        >>> close_db()
    """
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None:
        with contextlib.suppress(Exception):
            conn.close()
        _local.conn = None
        _local.db_path = None


def get_tile_from_cache(conn: sqlite3.Connection, z: int, x: int, y: int) -> bytes | None:
    """Consulta una tesela binaria PNG desde la caché local SQLite.

    Args:
        conn: Conexión SQLite activa.
        z: Nivel de zoom de la tesela.
        x: Columna de la tesela.
        y: Fila de la tesela.

    Returns:
        Contenido binario (bytes) de la tesela si existe en caché, None en caso contrario.

    Example:
        >>> conn = get_db()
        >>> tile_bytes = get_tile_from_cache(conn, 6, 31, 39)
    """
    cur = conn.cursor()
    cur.execute("SELECT data FROM tiles WHERE z=? AND x=? AND y=?;", (z, x, y))
    row = cur.fetchone()
    if row and isinstance(row[0], (bytes, memoryview)):
        return bytes(row[0])
    return None


def get_tile_size_from_cache(conn: sqlite3.Connection, z: int, x: int, y: int) -> int | None:
    """Obtiene el tamaño en bytes de una tesela en caché sin cargar su contenido completo.

    Args:
        conn: Conexión SQLite activa.
        z: Nivel de zoom de la tesela.
        x: Columna de la tesela.
        y: Fila de la tesela.

    Returns:
        Longitud en bytes si la tesela existe en caché, None en caso contrario.

    Example:
        >>> conn = get_db()
        >>> size = get_tile_size_from_cache(conn, 6, 31, 39)
    """
    cur = conn.cursor()
    cur.execute("SELECT length(data) FROM tiles WHERE z=? AND x=? AND y=?;", (z, x, y))
    row = cur.fetchone()
    if row and row[0] is not None:
        return int(row[0])
    return None


def save_tile_to_cache(conn: sqlite3.Connection, z: int, x: int, y: int, data: bytes) -> bool:
    """Almacena o actualiza una tesela binaria en la caché SQLite local.

    Args:
        conn: Conexión SQLite activa.
        z: Nivel de zoom de la tesela.
        x: Columna de la tesela.
        y: Fila de la tesela.
        data: Contenido binario PNG de la tesela.

    Returns:
        True si se almacenó exitosamente, False si ocurrió un error de bloqueo o base de datos.

    Example:
        >>> conn = get_db()
        >>> success = save_tile_to_cache(conn, 6, 31, 39, b"test_data")
        >>> success
        True
    """
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO tiles (z, x, y, data, created_at) "
                "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP);",
                (z, x, y, data),
            )
        return True
    except sqlite3.Error:
        return False


def fetch_upstream_tile(
    z: int,
    x: int,
    y: int,
    timeout: float = 8.0,
    user_agent: str = USER_AGENT,
) -> bytes:
    """Descarga una tesela cartográfica directamente desde los servidores upstream de OSM.

    Valida previamente las coordenadas y la URL para prevenir vulnerabilidades de SSRF.

    Args:
        z: Nivel de zoom de la tesela.
        x: Columna de la tesela.
        y: Fila de la tesela.
        timeout: Tiempo máximo de espera en segundos.
        user_agent: Cabecera User-Agent para cumplir la política de uso de OSM.

    Returns:
        Contenido binario (bytes) de la imagen PNG de la tesela.

    Raises:
        ValueError: Si las coordenadas son inválidas.
        urllib.error.HTTPError: Si el servidor upstream retorna código HTTP >= 400.
        urllib.error.URLError: Si ocurre un fallo de red o resolución DNS.

    Example:
        >>> tile_bytes = fetch_upstream_tile(0, 0, 0, timeout=5.0)
    """
    if not validate_tile_coords(z, x, y):
        raise ValueError(f"Coordenadas de tesela inválidas: z={z}, x={x}, y={y}")

    upstream_url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    req = urllib.request.Request(
        upstream_url,
        headers={
            "User-Agent": user_agent,
            "Accept": "image/png,image/*;q=0.8,*/*;q=0.5",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        if resp.status == 200:
            content: bytes = resp.read()
            return content
        raise urllib.error.HTTPError(
            upstream_url, resp.status, f"Error upstream HTTP {resp.status}", resp.headers, None
        )


def get_metrics(
    conn: sqlite3.Connection,
    start_time: float,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Genera las métricas y estado operacional actual del servidor de teselas.

    Args:
        conn: Conexión SQLite activa.
        start_time: Timestamp de inicio del servidor (time.time()).
        db_path: Ruta al archivo SQLite para calcular el tamaño en disco. Si es None, usa DB_PATH.

    Returns:
        Diccionario con métricas de uptime, conteo de teselas, tamaño y desglose por zoom.

    Example:
        >>> conn = get_db()
        >>> stats = get_metrics(conn, time.time())
        >>> "cached_tiles" in stats
        True
    """
    target_path = db_path if db_path is not None else DB_PATH
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tiles;")
    row = cur.fetchone()
    total_tiles = int(row[0]) if row else 0

    db_size_mb = (target_path.stat().st_size / (1024 * 1024)) if target_path.exists() else 0.0

    cur.execute("SELECT z, COUNT(*) FROM tiles GROUP BY z ORDER BY z;")
    zoom_breakdown = {f"zoom_{r[0]}": int(r[1]) for r in cur.fetchall()}

    return {
        "status": "online",
        "uptime_seconds": max(0, int(time.time() - start_time)),
        "cached_tiles": total_tiles,
        "database_size_mb": round(db_size_mb, 2),
        "zoom_levels": zoom_breakdown,
        "endpoints": {
            "tile_template": "/tile/{z}/{x}/{y}.png",
            "status": "/api/status",
            "health": "/api/health",
            "viewer": "/",
        },
    }


class TileServerHandler(http.server.SimpleHTTPRequestHandler):
    """Manejador HTTP para el servidor de teselas OpenStreetMap.

    Procesa peticiones GET y HEAD para teselas XYZ, APIs de monitoreo
    y contenido web estático.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        """Emite mensajes de log concisos con timestamp a stdout."""
        sys.stdout.write(f"[{self.log_date_time_string()}] {self.address_string()} - {format % args}\n")
        sys.stdout.flush()

    def do_GET(self) -> None:
        """Enruta y procesa peticiones HTTP GET."""
        # 1. Endpoint raíz y visor web
        if self.path in ("/", "/index.html"):
            self.serve_file(BASE_DIR / "index.html", "text/html; charset=utf-8")
            return

        # 2. Endpoints de monitoreo y estado
        if self.path == "/api/status":
            self.serve_status()
            return

        if self.path == "/api/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        # 3. Endpoint de teselas /tile/{z}/{x}/{y}.png
        match = TILE_REGEX.match(self.path)
        if match:
            z, x, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
            self.serve_tile(z, x, y)
            return

        # 4. Archivos estáticos generales
        super().do_GET()

    def do_HEAD(self) -> None:
        """Enruta y procesa peticiones HTTP HEAD sin cuerpo de respuesta."""
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return

        if self.path in ("/api/status", "/api/health"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return

        match = TILE_REGEX.match(self.path)
        if match:
            z, x, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
            if not validate_tile_coords(z, x, y):
                self.send_error(400, f"Coordenadas de tesela fuera de rango: {z}/{x}/{y}")
                return

            conn = get_db()
            size = get_tile_size_from_cache(conn, z, x, y)
            if size is not None:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(size))
                self.send_header("X-Tile-Source", "CACHE_HIT")
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            return

        super().do_HEAD()

    def serve_file(self, filepath: Path, content_type: str) -> None:
        """Sirve un archivo local con las cabeceras HTTP correspondientes.

        Args:
            filepath: Ruta absoluta o relativa al archivo local.
            content_type: Tipo MIME para la cabecera Content-Type.
        """
        if not filepath.exists():
            self.send_error(404, f"Archivo no encontrado: {filepath.name}")
            return
        data = filepath.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def serve_status(self) -> None:
        """Sirve el endpoint JSON con estadísticas operacionales del servidor."""
        try:
            conn = get_db()
            metrics = get_metrics(conn, START_TIME, DB_PATH)
            body = json.dumps(metrics, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(500, f"Error al consultar métricas: {e}")

    def serve_tile(self, z: int, x: int, y: int) -> None:
        """Sirve una tesela cartográfica XYZ desde la caché local o descargándola upstream.

        Args:
            z: Nivel de zoom.
            x: Coordenada X (columna).
            y: Coordenada Y (fila).
        """
        if not validate_tile_coords(z, x, y):
            self.send_error(400, f"Coordenadas de tesela fuera de rango: {z}/{x}/{y}")
            return

        conn = get_db()

        # 1. Intentar obtener de la caché local SQLite
        cached_data = get_tile_from_cache(conn, z, x, y)
        if cached_data is not None:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(cached_data)))
            self.send_header("X-Tile-Source", "CACHE_HIT")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(cached_data)
            return

        # 2. Descargar de OpenStreetMap upstream
        try:
            upstream_data = fetch_upstream_tile(z, x, y, timeout=8.0, user_agent=USER_AGENT)
            save_tile_to_cache(conn, z, x, y, upstream_data)

            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(upstream_data)))
            self.send_header("X-Tile-Source", "CACHE_MISS_UPSTREAM")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=604800")
            self.end_headers()
            self.wfile.write(upstream_data)
        except urllib.error.HTTPError as e:
            self.send_error(e.code, f"Error upstream: {e.reason}")
        except Exception as e:
            self.send_error(502, f"Fallo de conexión upstream: {e}")


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Servidor HTTP multihilo no bloqueante."""

    daemon_threads = True
    allow_reuse_address = True


def run(host: str = HOST, port: int = PORT, db_path: Path = DB_PATH) -> None:
    """Inicia el bucle principal del servidor HTTP de teselas cartográficas.

    Args:
        host: Dirección IP o interfaz donde escuchar (ej. '0.0.0.0' o '127.0.0.1').
        port: Puerto TCP de escucha (ej. 8550).
        db_path: Ruta a la base de datos de caché SQLite.

    Example:
        >>> # run(host="127.0.0.1", port=8550)
    """
    init_db(db_path)
    server_address = (host, port)
    httpd = ThreadedHTTPServer(server_address, TileServerHandler)
    print("===============================================================")
    print(" Servidor Local de Teselas OpenStreetMap Activo")
    print(f" URL Base: http://localhost:{port}/")
    print(f" Endpoint Teselas: http://localhost:{port}/tile/{{z}}/{{x}}/{{y}}.png")
    print(f" Estado/Métricas: http://localhost:{port}/api/status")
    print(f" Base de Datos SQLite: {db_path}")
    print("===============================================================")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDeteniendo servidor...")
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":
    run()
