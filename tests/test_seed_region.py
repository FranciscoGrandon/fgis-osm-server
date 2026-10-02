"""Pruebas unitarias exhaustivas para seed_region.py (Pre-carga de teselas OSM)."""

from __future__ import annotations

import sqlite3
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Asegurar importación de osm-server
SERVER_DIR = Path(__file__).resolve().parent.parent / "osm-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import seed_region  # noqa: E402


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Fixture que provee una base de datos SQLite limpia con el esquema listo."""
    db_file = tmp_path / "seed_tiles.db"
    conn = sqlite3.connect(str(db_file))
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
    conn.close()
    return db_file


# ============================================================================
# 1. Pruebas de deg2num y num2deg (Proyección Web Mercator)
# ============================================================================


def test_deg2num_equator_greenwich() -> None:
    """Verifica conversión en origen (0°, 0°) a zoom 0."""
    xtile, ytile = seed_region.deg2num(0.0, 0.0, 0)
    assert (xtile, ytile) == (0, 0)


def test_deg2num_regional_cities() -> None:
    """Verifica que las coordenadas de ciudades chilenas retornen índices válidos."""
    # Concepción
    x, y = seed_region.deg2num(-36.8270, -73.0503, 6)
    assert x >= 0 and y >= 0
    assert x < (1 << 6) and y < (1 << 6)

    # Talca
    x, y = seed_region.deg2num(-35.4264, -71.6554, 8)
    assert x >= 0 and y >= 0
    assert x < (1 << 8) and y < (1 << 8)


def test_deg2num_invalid_zoom() -> None:
    """Verifica que niveles de zoom inválidos levanten ValueError."""
    with pytest.raises(ValueError, match="Nivel de zoom inválido"):
        seed_region.deg2num(0.0, 0.0, -1)
    with pytest.raises(ValueError, match="Nivel de zoom inválido"):
        seed_region.deg2num(0.0, 0.0, 31)


def test_deg2num_invalid_latitude() -> None:
    """Verifica que latitudes fuera de [-85.05112878, 85.05112878] levanten ValueError."""
    with pytest.raises(ValueError, match="Latitud fuera de rango"):
        seed_region.deg2num(86.0, 0.0, 5)
    with pytest.raises(ValueError, match="Latitud fuera de rango"):
        seed_region.deg2num(-86.0, 0.0, 5)


def test_deg2num_invalid_longitude() -> None:
    """Verifica que longitudes fuera de [-180.0, 180.0] levanten ValueError."""
    with pytest.raises(ValueError, match="Longitud fuera de rango"):
        seed_region.deg2num(0.0, 181.0, 5)
    with pytest.raises(ValueError, match="Longitud fuera de rango"):
        seed_region.deg2num(0.0, -181.0, 5)


def test_num2deg_zoom_0() -> None:
    """Verifica conversión inversa de tesela 0/0/0 a grados geográficos."""
    lat, lon = seed_region.num2deg(0, 0, 0)
    assert round(lat, 3) == pytest.approx(85.051, abs=0.01)
    assert round(lon, 1) == -180.0


def test_num2deg_invalid_inputs() -> None:
    """Verifica validación de rangos en num2deg."""
    with pytest.raises(ValueError, match="Nivel de zoom inválido"):
        seed_region.num2deg(0, 0, -1)
    with pytest.raises(ValueError, match="fuera de rango"):
        seed_region.num2deg(2, 0, 1)  # x=2 excede 2^1 - 1
    with pytest.raises(ValueError, match="fuera de rango"):
        seed_region.num2deg(0, 2, 1)  # y=2 excede 2^1 - 1


# ============================================================================
# 2. Pruebas de Bounding Box y Límites de Teselas
# ============================================================================


def test_calculate_tile_bounds_valid() -> None:
    """Verifica cálculo correcto de rangos X e Y para un bounding box válido."""
    bbox = {
        "min_lat": -38.5,
        "max_lat": -33.8,
        "min_lon": -74.0,
        "max_lon": -70.0,
    }
    x_start, x_end, y_start, y_end = seed_region.calculate_tile_bounds(bbox, 6)

    assert x_start <= x_end
    assert y_start <= y_end
    assert x_start >= 0 and x_end < (1 << 6)
    assert y_start >= 0 and y_end < (1 << 6)


def test_calculate_tile_bounds_missing_keys() -> None:
    """Verifica error cuando el bbox carece de llaves requeridas."""
    invalid_bbox = {"min_lat": -38.0, "max_lat": -34.0}
    with pytest.raises(ValueError, match="El BBOX debe contener las llaves"):
        seed_region.calculate_tile_bounds(invalid_bbox, 6)


def test_calculate_tile_bounds_inverted() -> None:
    """Verifica rechazo de coordenadas invertidas (min > max)."""
    with pytest.raises(ValueError, match="min_lat no puede ser superior"):
        seed_region.calculate_tile_bounds(
            {"min_lat": -30.0, "max_lat": -35.0, "min_lon": -74.0, "max_lon": -70.0}, 6
        )
    with pytest.raises(ValueError, match="min_lon no puede ser superior"):
        seed_region.calculate_tile_bounds(
            {"min_lat": -38.0, "max_lat": -34.0, "min_lon": -60.0, "max_lon": -70.0}, 6
        )


# ============================================================================
# 3. Pruebas de download_tile y Políticas de Caché / TTL
# ============================================================================


@patch("urllib.request.urlopen")
def test_download_tile_new(mock_urlopen: MagicMock, temp_db: Path) -> None:
    """Verifica descarga e inserción exitosa de una tesela nueva."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"FakePngBinaryContent"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    conn = sqlite3.connect(str(temp_db))
    downloaded = seed_region.download_tile(conn, 6, 18, 38)
    conn.close()

    assert downloaded is True

    # Verificar presencia en BD
    conn = sqlite3.connect(str(temp_db))
    cur = conn.cursor()
    cur.execute("SELECT data FROM tiles WHERE z=6 AND x=18 AND y=38;")
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] == b"FakePngBinaryContent"


def test_download_tile_skip_existing(temp_db: Path) -> None:
    """Verifica que una tesela existente no se vuelva a descargar (force=False)."""
    conn = sqlite3.connect(str(temp_db))
    with conn:
        conn.execute(
            "INSERT INTO tiles (z, x, y, data, created_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP);",
            (6, 18, 38, b"AlreadyCached"),
        )

    # No debe llamar a urlopen
    with patch("urllib.request.urlopen") as mock_urlopen:
        downloaded = seed_region.download_tile(conn, 6, 18, 38, force=False)
        mock_urlopen.assert_not_called()
    conn.close()

    assert downloaded is False


@patch("urllib.request.urlopen")
def test_download_tile_force_overwrite(mock_urlopen: MagicMock, temp_db: Path) -> None:
    """Verifica que force=True fuerce la re-descarga de una tesela existente."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"FreshlyUpdatedContent"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    conn = sqlite3.connect(str(temp_db))
    with conn:
        conn.execute(
            "INSERT INTO tiles (z, x, y, data, created_at) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP);",
            (6, 18, 38, b"OldContent"),
        )

    downloaded = seed_region.download_tile(conn, 6, 18, 38, force=True)
    assert downloaded is True

    cur = conn.cursor()
    cur.execute("SELECT data FROM tiles WHERE z=6 AND x=18 AND y=38;")
    assert cur.fetchone()[0] == b"FreshlyUpdatedContent"
    conn.close()


def test_download_tile_ttl_unexpired(temp_db: Path) -> None:
    """Verifica que una tesela dentro de la ventana de TTL sea omitida."""
    conn = sqlite3.connect(str(temp_db))
    with conn:
        conn.execute(
            "INSERT INTO tiles (z, x, y, data, created_at) VALUES (?, ?, ?, ?, datetime('now', '-2 days'));",
            (6, 18, 38, b"ValidData"),
        )

    # TTL de 7 días: antigüedad de 2 días está vigente
    with patch("urllib.request.urlopen") as mock_urlopen:
        downloaded = seed_region.download_tile(conn, 6, 18, 38, max_age_days=7)
        mock_urlopen.assert_not_called()

    conn.close()
    assert downloaded is False


@patch("urllib.request.urlopen")
def test_download_tile_ttl_expired(mock_urlopen: MagicMock, temp_db: Path) -> None:
    """Verifica que una tesela fuera de la ventana de TTL sea re-descargada."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"RenewedTileData"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    conn = sqlite3.connect(str(temp_db))
    with conn:
        conn.execute(
            "INSERT INTO tiles (z, x, y, data, created_at) VALUES (?, ?, ?, ?, datetime('now', '-10 days'));",
            (6, 18, 38, b"ExpiredData"),
        )

    # TTL de 7 días: antigüedad de 10 días debe disparar re-descarga
    downloaded = seed_region.download_tile(conn, 6, 18, 38, max_age_days=7)
    conn.close()

    assert downloaded is True


@patch("urllib.request.urlopen")
def test_download_tile_network_error(mock_urlopen: MagicMock, temp_db: Path) -> None:
    """Verifica manejo no bloqueante ante fallos de red al descargar tesela."""
    mock_urlopen.side_effect = urllib.error.URLError("DNS resolution failure")

    conn = sqlite3.connect(str(temp_db))
    downloaded = seed_region.download_tile(conn, 6, 18, 38)
    conn.close()

    assert downloaded is False


# ============================================================================
# 4. Pruebas de seed() y CLI
# ============================================================================


@patch("urllib.request.urlopen")
def test_seed_execution(mock_urlopen: MagicMock, temp_db: Path) -> None:
    """Verifica la ejecución del flujo completo de seeding sobre base de datos temporal."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"DummyTile"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    # Probar con un rango muy acotado para rapidez
    stats = seed_region.seed(
        min_zoom=6,
        max_zoom=6,
        include_cities=False,
        db_path=temp_db,
        delay_seconds=0.0,
    )

    assert "downloaded" in stats
    assert "skipped" in stats
    assert "total" in stats
    assert stats["downloaded"] > 0
    assert stats["total"] == stats["downloaded"] + stats["skipped"]


@patch("seed_region.seed")
def test_main_cli_args(mock_seed: MagicMock) -> None:
    """Verifica parsing de argumentos por línea de comandos."""
    mock_seed.return_value = {"downloaded": 10, "skipped": 0, "total": 10}

    code = seed_region.main(["--min-zoom", "6", "--max-zoom", "7", "--no-cities", "--ttl-days", "15"])
    assert code == 0
    mock_seed.assert_called_once_with(
        min_zoom=6,
        max_zoom=7,
        include_cities=False,
        max_age_days=15,
        force=False,
    )


@patch("urllib.request.urlopen")
def test_download_tile_status_not_200(mock_urlopen: MagicMock, temp_db: Path) -> None:
    """Verifica que status code != 200 en download_tile retorne False."""
    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    conn = sqlite3.connect(str(temp_db))
    downloaded = seed_region.download_tile(conn, 6, 18, 38)
    conn.close()

    assert downloaded is False


@patch("urllib.request.urlopen")
def test_seed_with_cities_and_force(mock_urlopen: MagicMock, temp_db: Path) -> None:
    """Verifica seed() con inclusión de centros urbanos, delay, política TTL y force."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b"CityTileData"
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    tiny_cities = [{"name": "TestCity", "lat": -36.8, "lon": -73.0, "radius_tiles": 0}]
    with patch.object(seed_region, "CITIES", tiny_cities):
        stats = seed_region.seed(
            min_zoom=6,
            max_zoom=6,
            include_cities=True,
            max_age_days=10,
            force=True,
            db_path=temp_db,
            delay_seconds=0.001,
        )

    assert stats["downloaded"] > 0
    assert stats["total"] >= stats["downloaded"]


@patch("seed_region.seed")
def test_main_cli_force(mock_seed: MagicMock) -> None:
    """Verifica invocación de CLI con bandera --force."""
    mock_seed.return_value = {"downloaded": 5, "skipped": 0, "total": 5}
    code = seed_region.main(["--force"])
    assert code == 0
    mock_seed.assert_called_once_with(
        min_zoom=6,
        max_zoom=8,
        include_cities=True,
        max_age_days=None,
        force=True,
    )

