"""Pruebas unitarias exhaustivas para server.py (OSM Local Tile Server)."""

from __future__ import annotations

import io
import json
import sqlite3
import sys
import time
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Asegurar importación de osm-server
SERVER_DIR = Path(__file__).resolve().parent.parent / "osm-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import server  # noqa: E402


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Fixture que provee una base de datos SQLite temporal y limpia."""
    db_file = tmp_path / "test_tiles.db"
    server.init_db(db_file)
    return db_file


@pytest.fixture
def sqlite_conn(temp_db: Path) -> Iterator[sqlite3.Connection]:
    """Fixture que retorna una conexión SQLite a la base de datos temporal."""
    server.close_db()
    conn = sqlite3.connect(str(temp_db))
    yield conn
    conn.close()
    server.close_db()


# ============================================================================
# 1. Pruebas de Validación de Coordenadas de Tesela
# ============================================================================


def test_validate_tile_coords_valid() -> None:
    """Verifica coordenadas válidas en varios niveles de zoom."""
    assert server.validate_tile_coords(0, 0, 0) is True
    assert server.validate_tile_coords(1, 0, 0) is True
    assert server.validate_tile_coords(1, 1, 1) is True
    assert server.validate_tile_coords(6, 31, 39) is True
    assert server.validate_tile_coords(19, (1 << 19) - 1, (1 << 19) - 1) is True


def test_validate_tile_coords_zoom_out_of_bounds() -> None:
    """Verifica que niveles de zoom negativos o excesivos sean rechazados."""
    assert server.validate_tile_coords(-1, 0, 0) is False
    assert server.validate_tile_coords(20, 0, 0) is False
    assert server.validate_tile_coords(99, 0, 0) is False


def test_validate_tile_coords_xy_out_of_bounds() -> None:
    """Verifica que coordenadas X o Y negativas o superiores a 2^z - 1 sean rechazadas."""
    assert server.validate_tile_coords(0, 1, 0) is False
    assert server.validate_tile_coords(0, 0, 1) is False
    assert server.validate_tile_coords(1, -1, 0) is False
    assert server.validate_tile_coords(1, 0, -1) is False
    assert server.validate_tile_coords(2, 4, 1) is False
    assert server.validate_tile_coords(2, 1, 4) is False


# ============================================================================
# 2. Pruebas de Persistencia y Caché SQLite
# ============================================================================


def test_init_db_creates_table_and_index(tmp_path: Path) -> None:
    """Verifica que init_db cree la tabla tiles y el índice correspondiente."""
    db_file = tmp_path / "subdir" / "new_tiles.db"
    server.init_db(db_file)
    assert db_file.exists()

    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tiles';")
    assert cur.fetchone() is not None

    cur.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_tiles_zxy';")
    assert cur.fetchone() is not None
    conn.close()


def test_init_db_idempotent(temp_db: Path) -> None:
    """Verifica que llamar a init_db varias veces no genere errores."""
    server.init_db(temp_db)
    server.init_db(temp_db)
    assert temp_db.exists()


def test_save_and_get_tile_cache(sqlite_conn: sqlite3.Connection) -> None:
    """Verifica el ciclo completo de guardado y lectura de tesela binaria."""
    # Inicialmente la tesela no existe
    assert server.get_tile_from_cache(sqlite_conn, 6, 18, 38) is None
    assert server.get_tile_size_from_cache(sqlite_conn, 6, 18, 38) is None

    # Guardar tesela
    tile_png = b"\x89PNG\r\n\x1a\nFakeTileBinaryData"
    success = server.save_tile_to_cache(sqlite_conn, 6, 18, 38, tile_png)
    assert success is True

    # Recuperar tesela
    retrieved = server.get_tile_from_cache(sqlite_conn, 6, 18, 38)
    assert retrieved == tile_png

    # Tamaño en bytes
    size = server.get_tile_size_from_cache(sqlite_conn, 6, 18, 38)
    assert size == len(tile_png)


def test_save_tile_replace_existing(sqlite_conn: sqlite3.Connection) -> None:
    """Verifica que guardar una tesela con las mismas coordenadas reemplace el contenido."""
    v1 = b"DataVersion1"
    v2 = b"DataVersion2Updated"
    server.save_tile_to_cache(sqlite_conn, 7, 36, 76, v1)
    assert server.get_tile_from_cache(sqlite_conn, 7, 36, 76) == v1

    server.save_tile_to_cache(sqlite_conn, 7, 36, 76, v2)
    assert server.get_tile_from_cache(sqlite_conn, 7, 36, 76) == v2


def test_save_tile_to_cache_db_error() -> None:
    """Verifica manejo de errores de base de datos en save_tile_to_cache."""
    mock_conn = MagicMock()
    mock_conn.__enter__.side_effect = sqlite3.OperationalError("Database locked")
    result = server.save_tile_to_cache(mock_conn, 1, 1, 1, b"data")
    assert result is False


# ============================================================================
# 3. Pruebas de Métricas y Estado
# ============================================================================


def test_get_metrics_empty(sqlite_conn: sqlite3.Connection, temp_db: Path) -> None:
    """Verifica estructura de métricas con base de datos vacía."""
    start_time = time.time() - 10.0
    metrics = server.get_metrics(sqlite_conn, start_time, temp_db)

    assert metrics["status"] == "online"
    assert metrics["uptime_seconds"] >= 10
    assert metrics["cached_tiles"] == 0
    assert metrics["zoom_levels"] == {}
    assert "endpoints" in metrics
    assert metrics["endpoints"]["viewer"] == "/"


def test_get_metrics_populated(sqlite_conn: sqlite3.Connection, temp_db: Path) -> None:
    """Verifica conteo y desglose por niveles de zoom en las métricas."""
    server.save_tile_to_cache(sqlite_conn, 6, 10, 10, b"t1")
    server.save_tile_to_cache(sqlite_conn, 6, 11, 10, b"t2")
    server.save_tile_to_cache(sqlite_conn, 7, 20, 20, b"t3")

    metrics = server.get_metrics(sqlite_conn, time.time(), temp_db)
    assert metrics["cached_tiles"] == 3
    assert metrics["zoom_levels"] == {"zoom_6": 2, "zoom_7": 1}
    assert metrics["database_size_mb"] >= 0.0


# ============================================================================
# 4. Pruebas de Descarga Upstream (fetch_upstream_tile)
# ============================================================================


def test_fetch_upstream_tile_invalid_coords() -> None:
    """Verifica que coordenadas inválidas levanten ValueError de inmediato."""
    with pytest.raises(ValueError, match="Coordenadas de tesela inválidas"):
        server.fetch_upstream_tile(-1, 0, 0)

    with pytest.raises(ValueError, match="Coordenadas de tesela inválidas"):
        server.fetch_upstream_tile(2, 10, 0)


@patch("urllib.request.urlopen")
def test_fetch_upstream_tile_success(mock_urlopen: MagicMock) -> None:
    """Verifica descarga exitosa de tesela upstream."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"\x89PNG\r\n\x1a\nMockUpstreamData"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    data = server.fetch_upstream_tile(6, 18, 38)
    assert data == b"\x89PNG\r\n\x1a\nMockUpstreamData"
    mock_urlopen.assert_called_once()


@patch("urllib.request.urlopen")
def test_fetch_upstream_tile_http_error(mock_urlopen: MagicMock) -> None:
    """Verifica propagación de error HTTP upstream (ej. 404)."""
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://tile.openstreetmap.org/6/18/38.png",
        code=404,
        msg="Not Found",
        hdrs={},  # type: ignore
        fp=None,
    )

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        server.fetch_upstream_tile(6, 18, 38)
    assert exc_info.value.code == 404


@patch("urllib.request.urlopen")
def test_fetch_upstream_tile_url_error(mock_urlopen: MagicMock) -> None:
    """Verifica propagación de fallo de conexión de red upstream."""
    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

    with pytest.raises(urllib.error.URLError):
        server.fetch_upstream_tile(6, 18, 38)


# ============================================================================
# 5. Pruebas del Manejador HTTP (TileServerHandler)
# ============================================================================


class MockSocket:
    """Mock socket para instanciar HTTPRequestHandlers en memoria."""

    def __init__(self, request_bytes: bytes) -> None:
        self.rfile = io.BytesIO(request_bytes)
        self.wfile = io.BytesIO()

    def makefile(
        self, mode: str = "r", *args: Any, **kwargs: Any
    ) -> io.BytesIO | io.TextIOWrapper:
        del args, kwargs
        if "b" in mode:
            if "r" in mode:
                return self.rfile
            return self.wfile
        return io.TextIOWrapper(self.rfile if "r" in mode else self.wfile)

    def sendall(self, data: bytes) -> None:
        self.wfile.write(data)


def simulate_http_request(request_text: str, db_path: Path) -> tuple[int, dict[str, str], bytes]:
    """Ejecuta una petición simulada en TileServerHandler y retorna (status, headers, body)."""
    server.close_db()
    with patch.object(server, "DB_PATH", db_path):
        sock: Any = MockSocket(request_text.encode("utf-8"))
        server_obj: Any = None
        server.TileServerHandler(
            sock,
            ("127.0.0.1", 12345),
            server_obj,
        )

        response_bytes = sock.wfile.getvalue()
        # Parsear respuesta HTTP
        header_part, _, body_part = response_bytes.partition(b"\r\n\r\n")
        lines = header_part.decode("iso-8859-1").split("\r\n")
        status_line = lines[0]
        status_code = int(status_line.split()[1]) if len(lines) > 0 and len(status_line.split()) >= 2 else 0

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        server.close_db()
        return status_code, headers, body_part


def test_handler_get_health(temp_db: Path) -> None:
    """Verifica endpoint GET /api/health."""
    req = "GET /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, headers, body = simulate_http_request(req, temp_db)

    assert code == 200
    assert headers.get("content-type") == "application/json"
    assert json.loads(body.decode("utf-8")) == {"status": "ok"}


def test_handler_get_status(temp_db: Path) -> None:
    """Verifica endpoint GET /api/status con datos JSON estructurados."""
    req = "GET /api/status HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, headers, body = simulate_http_request(req, temp_db)

    assert code == 200
    assert "application/json" in headers.get("content-type", "")
    payload = json.loads(body.decode("utf-8"))
    assert payload["status"] == "online"
    assert "cached_tiles" in payload


def test_handler_get_tile_cache_hit(temp_db: Path, sqlite_conn: sqlite3.Connection) -> None:
    """Verifica respuesta 200 y cabecera CACHE_HIT cuando la tesela existe en SQLite."""
    tile_data = b"ExistingCachedTilePNGData"
    server.save_tile_to_cache(sqlite_conn, 6, 18, 38, tile_data)

    req = "GET /tile/6/18/38.png HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, headers, body = simulate_http_request(req, temp_db)

    assert code == 200
    assert headers.get("content-type") == "image/png"
    assert headers.get("x-tile-source") == "CACHE_HIT"
    assert body == tile_data


def test_handler_get_tile_invalid_coords(temp_db: Path) -> None:
    """Verifica respuesta 400 Bad Request ante coordenadas de tesela fuera de rango."""
    req = "GET /tile/2/99/99.png HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, _, _ = simulate_http_request(req, temp_db)
    assert code == 400


@patch("urllib.request.urlopen")
def test_handler_get_tile_cache_miss_upstream_success(
    mock_urlopen: MagicMock, temp_db: Path
) -> None:
    """Verifica descarga upstream y persistencia automática ante CACHE_MISS."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"NewUpstreamTileBytes"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    req = "GET /tile/6/18/39.png HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, headers, body = simulate_http_request(req, temp_db)

    assert code == 200
    assert headers.get("content-type") == "image/png"
    assert headers.get("x-tile-source") == "CACHE_MISS_UPSTREAM"
    assert body == b"NewUpstreamTileBytes"

    # Verificar que quedó almacenada en base de datos
    conn = sqlite3.connect(str(temp_db))
    cached = server.get_tile_from_cache(conn, 6, 18, 39)
    conn.close()
    assert cached == b"NewUpstreamTileBytes"


@patch("urllib.request.urlopen")
def test_handler_get_tile_upstream_404(mock_urlopen: MagicMock, temp_db: Path) -> None:
    """Verifica manejo adecuado de error 404 proveniente de upstream."""
    mock_urlopen.side_effect = urllib.error.HTTPError(
        url="https://tile.openstreetmap.org/6/18/39.png",
        code=404,
        msg="Not Found",
        hdrs={},  # type: ignore
        fp=None,
    )

    req = "GET /tile/6/18/39.png HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, _, _ = simulate_http_request(req, temp_db)
    assert code == 404


@patch("urllib.request.urlopen")
def test_handler_get_tile_upstream_502(mock_urlopen: MagicMock, temp_db: Path) -> None:
    """Verifica código 502 ante fallos de conexión hacia OpenStreetMap."""
    mock_urlopen.side_effect = urllib.error.URLError("Connection timed out")

    req = "GET /tile/6/18/39.png HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, _, _ = simulate_http_request(req, temp_db)
    assert code == 502


def test_handler_head_request(temp_db: Path, sqlite_conn: sqlite3.Connection) -> None:
    """Verifica que peticiones HEAD retornen cabeceras correctas sin cuerpo."""
    server.save_tile_to_cache(sqlite_conn, 6, 18, 38, b"12345678")

    req = "HEAD /tile/6/18/38.png HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, headers, body = simulate_http_request(req, temp_db)

    assert code == 200
    assert headers.get("content-type") == "image/png"
    assert headers.get("content-length") == "8"
    assert headers.get("x-tile-source") == "CACHE_HIT"
    assert body == b""  # Sin cuerpo en HEAD


def test_handler_head_status(temp_db: Path) -> None:
    """Verifica petición HEAD sobre /api/status."""
    req = "HEAD /api/status HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, headers, body = simulate_http_request(req, temp_db)
    assert code == 200
    assert headers.get("content-type") == "application/json"
    assert body == b""


def test_get_db_switch_paths(temp_db: Path, tmp_path: Path) -> None:
    """Verifica cambio dinámico de conexión SQLite al recibir una ruta diferente."""
    conn1 = server.get_db(temp_db)
    assert conn1 is not None

    second_db = tmp_path / "second.db"
    server.init_db(second_db)
    conn2 = server.get_db(second_db)
    assert conn2 is not None

    # Cerrar conexión formalmente
    server.close_db()
    # Doble cierre no debe fallar
    server.close_db()


@patch("urllib.request.urlopen")
def test_fetch_upstream_tile_status_not_200(mock_urlopen: MagicMock) -> None:
    """Verifica que respuestas upstream con código distinto a 200 lancen HTTPError."""
    mock_resp = MagicMock()
    mock_resp.status = 503
    mock_resp.headers = {}
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    with pytest.raises(urllib.error.HTTPError):
        server.fetch_upstream_tile(6, 18, 38)


def test_handler_get_index_and_root(temp_db: Path) -> None:
    """Verifica que GET / y GET /index.html sirvan el visor web con código 200."""
    for path in ["/", "/index.html"]:
        req = f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n"
        code, headers, body = simulate_http_request(req, temp_db)
        assert code == 200
        assert "text/html" in headers.get("content-type", "")
        assert b"<!DOCTYPE html>" in body or len(body) > 0


def test_handler_head_index_and_health(temp_db: Path) -> None:
    """Verifica peticiones HEAD para visor web y health check."""
    for path in ["/", "/index.html"]:
        req = f"HEAD {path} HTTP/1.1\r\nHost: localhost\r\n\r\n"
        code, headers, body = simulate_http_request(req, temp_db)
        assert code == 200
        assert "text/html" in headers.get("content-type", "")
        assert body == b""

    req_health = "HEAD /api/health HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, headers, body = simulate_http_request(req_health, temp_db)
    assert code == 200
    assert headers.get("content-type") == "application/json"
    assert body == b""


def test_handler_head_tile_invalid_coords(temp_db: Path) -> None:
    """Verifica respuesta 400 ante HEAD con coordenadas fuera de rango."""
    req = "HEAD /tile/99/0/0.png HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, _, _ = simulate_http_request(req, temp_db)
    assert code == 400


def test_handler_head_tile_cache_miss(temp_db: Path) -> None:
    """Verifica respuesta 200 sin cuerpo ante HEAD de tesela no existente en caché."""
    req = "HEAD /tile/6/18/38.png HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, headers, body = simulate_http_request(req, temp_db)
    assert code == 200
    assert headers.get("content-type") == "image/png"
    assert "x-tile-source" not in headers
    assert body == b""


def test_handler_serve_file_not_found(temp_db: Path) -> None:
    """Verifica error 404 al intentar servir un archivo inexistente."""
    req = "GET /archivo_que_no_existe_xyz.txt HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, _, _ = simulate_http_request(req, temp_db)
    assert code == 404


@patch("server.get_metrics")
def test_handler_serve_status_error(mock_metrics: MagicMock, temp_db: Path) -> None:
    """Verifica respuesta 500 ante excepciones al generar métricas en /api/status."""
    mock_metrics.side_effect = RuntimeError("Database read failed")
    req = "GET /api/status HTTP/1.1\r\nHost: localhost\r\n\r\n"
    code, _, _ = simulate_http_request(req, temp_db)
    assert code == 500

