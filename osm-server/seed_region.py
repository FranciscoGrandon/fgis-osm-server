#!/usr/bin/env python3
"""Script de Pre-Carga / Pre-Caché (Seeding) de Teselas Cartográficas.

Descarga y almacena en la base de datos local SQLite las teselas de la
Macro-Zona Centro-Sur de Chile (O'Higgins, Maule, Ñuble y Biobío).
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_DIR: Path = Path(__file__).resolve().parent
CACHE_DIR: Path = BASE_DIR / "cache"
DB_PATH: Path = CACHE_DIR / "tiles.db"
USER_AGENT: str = "OSMLocalServer-Seeder/1.0 (devops@local.osm; Pre-cache Tool)"

# Bounding Box Macro-Zona Centro-Sur de Chile
BBOX: dict[str, float] = {
    "min_lat": -38.5,  # Límite sur de Biobío
    "max_lat": -33.8,  # Límite norte de O'Higgins
    "min_lon": -74.0,  # Costa del Pacífico
    "max_lon": -70.0,  # Cordillera
}

# Puntos de interés específicos para zooms detallados (10 a 12)
CITIES: list[dict[str, Any]] = [
    {"name": "Concepción", "lat": -36.8270, "lon": -73.0503, "radius_tiles": 3},
    {"name": "Talca", "lat": -35.4264, "lon": -71.6554, "radius_tiles": 2},
    {"name": "Chillán", "lat": -36.6066, "lon": -72.1034, "radius_tiles": 2},
    {"name": "Rancagua", "lat": -34.1708, "lon": -70.7444, "radius_tiles": 2},
]


def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> tuple[int, int]:
    """Convierte coordenadas geográficas WGS84 (latitud, longitud) a índices de tesela (x, y).

    Aplica la fórmula matemática oficial de la proyección Web Mercator (EPSG:3857).

    Args:
        lat_deg: Latitud en grados decimales (entre -85.05112878 y 85.05112878).
        lon_deg: Longitud en grados decimales (entre -180.0 y 180.0).
        zoom: Nivel de zoom (entero entre 0 y 30).

    Returns:
        Tupla con las coordenadas de la tesela (xtile, ytile).

    Raises:
        ValueError: Si las coordenadas o el nivel de zoom están fuera del rango admisible.

    Example:
        >>> deg2num(0.0, 0.0, 0)
        (0, 0)
        >>> deg2num(-36.8270, -73.0503, 6)
        (18, 38)
    """
    if zoom < 0 or zoom > 30:
        raise ValueError(f"Nivel de zoom inválido: {zoom}. Debe ser entre 0 y 30.")
    if not (-85.05112878 <= lat_deg <= 85.05112878):
        raise ValueError(
            f"Latitud fuera de rango para proyección Web Mercator: {lat_deg}. "
            "Debe estar entre -85.05112878 y 85.05112878."
        )
    if not (-180.0 <= lon_deg <= 180.0):
        raise ValueError(f"Longitud fuera de rango: {lon_deg}. Debe estar entre -180.0 y 180.0.")

    lat_rad = math.radians(lat_deg)
    n = 1 << zoom
    xtile = int((lon_deg + 180.0) / 360.0 * n)
    ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)

    # Clamping defensivo ante extremos de redondeo en bordes exactos
    xtile = min(n - 1, max(0, xtile))
    ytile = min(n - 1, max(0, ytile))
    return (xtile, ytile)


def num2deg(xtile: int, ytile: int, zoom: int) -> tuple[float, float]:
    """Convierte índices de tesela (x, y) a coordenadas geográficas (latitud, longitud).

    Retorna la esquina noroeste (top-left) de la tesela en grados decimales.

    Args:
        xtile: Coordenada X de la tesela.
        ytile: Coordenada Y de la tesela.
        zoom: Nivel de zoom (entero entre 0 y 30).

    Returns:
        Tupla con (latitud_deg, longitud_deg) de la esquina noroeste de la tesela.

    Raises:
        ValueError: Si los índices o el nivel de zoom están fuera de rango.

    Example:
        >>> lat, lon = num2deg(0, 0, 0)
        >>> round(lat, 4), round(lon, 4)
        (85.0511, -180.0)
    """
    if zoom < 0 or zoom > 30:
        raise ValueError(f"Nivel de zoom inválido: {zoom}. Debe ser entre 0 y 30.")
    n = 1 << zoom
    if xtile < 0 or xtile >= n:
        raise ValueError(f"Coordenada xtile={xtile} fuera de rango para zoom={zoom} [0..{n-1}].")
    if ytile < 0 or ytile >= n:
        raise ValueError(f"Coordenada ytile={ytile} fuera de rango para zoom={zoom} [0..{n-1}].")

    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1.0 - 2.0 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return (lat_deg, lon_deg)


def calculate_tile_bounds(bbox: dict[str, float], zoom: int) -> tuple[int, int, int, int]:
    """Calcula el rango inclusivo de índices de teselas (X e Y) para un bounding box geográfico.

    Args:
        bbox: Diccionario con llaves 'min_lat', 'max_lat', 'min_lon', 'max_lon'.
        zoom: Nivel de zoom a calcular.

    Returns:
        Tupla con (x_start, x_end, y_start, y_end).

    Raises:
        ValueError: Si el bounding box carece de llaves requeridas o si min > max.

    Example:
        >>> bounds = calculate_tile_bounds({"min_lat": -38.0, "max_lat": -34.0, "min_lon": -74.0, "max_lon": -70.0}, 6)
        >>> len(bounds)
        4
    """
    required_keys = {"min_lat", "max_lat", "min_lon", "max_lon"}
    if not required_keys.issubset(bbox.keys()):
        raise ValueError(f"El BBOX debe contener las llaves: {required_keys}")
    if bbox["min_lat"] > bbox["max_lat"]:
        raise ValueError("min_lat no puede ser superior a max_lat")
    if bbox["min_lon"] > bbox["max_lon"]:
        raise ValueError("min_lon no puede ser superior a max_lon")

    x_min, y_min = deg2num(bbox["max_lat"], bbox["min_lon"], zoom)
    x_max, y_max = deg2num(bbox["min_lat"], bbox["max_lon"], zoom)

    x_start, x_end = min(x_min, x_max), max(x_min, x_max)
    y_start, y_end = min(y_min, y_max), max(y_min, y_max)
    return (x_start, x_end, y_start, y_end)


def download_tile(
    conn: sqlite3.Connection,
    z: int,
    x: int,
    y: int,
    max_age_days: int | None = None,
    force: bool = False,
    timeout: float = 10.0,
    user_agent: str = USER_AGENT,
) -> bool:
    """Descarga y almacena una tesela cartográfica si no existe o ha expirado.

    Args:
        conn: Conexión SQLite activa.
        z: Nivel de zoom de la tesela.
        x: Columna de la tesela.
        y: Fila de la tesela.
        max_age_days: Cantidad máxima de días de antigüedad permitidos antes de refrescar.
        force: Si es True, descarga la tesela ignorando la caché existente.
        timeout: Tiempo máximo de espera en segundos para la petición HTTP.
        user_agent: Cabecera User-Agent a enviar al servidor de teselas.

    Returns:
        True si la tesela fue descargada e insertada/actualizada, False si fue omitida o falló.

    Example:
        >>> # conn = sqlite3.connect(":memory:")
        >>> # download_tile(conn, 6, 18, 38)
    """
    cur = conn.cursor()
    if not force:
        if max_age_days is not None:
            cur.execute(
                "SELECT 1 FROM tiles WHERE z=? AND x=? AND y=? AND created_at >= datetime('now', ?);",
                (z, x, y, f"-{int(max_age_days)} days"),
            )
            if cur.fetchone():
                return False  # Tesela vigente dentro del período TTL
        else:
            cur.execute("SELECT 1 FROM tiles WHERE z=? AND x=? AND y=?;", (z, x, y))
            if cur.fetchone():
                return False  # Ya existe en la base de datos

    url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "image/png",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            if resp.status == 200:
                data = resp.read()
                with conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO tiles (z, x, y, data, created_at) "
                        "VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP);",
                        (z, x, y, data),
                    )
                return True
            return False
    except Exception as e:
        sys.stderr.write(f"  [!] Error descargando tesela {z}/{x}/{y}: {e}\n")
        sys.stderr.flush()
        return False


def seed(
    min_zoom: int = 6,
    max_zoom: int = 8,
    include_cities: bool = True,
    max_age_days: int | None = None,
    force: bool = False,
    db_path: Path = DB_PATH,
    delay_seconds: float = 0.05,
) -> dict[str, int]:
    """Ejecuta el proceso completo de pre-carga y refresco de teselas en la base de datos.

    Args:
        min_zoom: Nivel mínimo de zoom para el área regional general.
        max_zoom: Nivel máximo de zoom para el área regional general.
        include_cities: Si es True, incluye zooms de detalle (10 a 12) en capitales regionales.
        max_age_days: Días máximos de antigüedad permitidos antes de forzar re-descarga.
        force: Si es True, re-descarga todas las teselas sin importar su antigüedad.
        db_path: Ruta a la base de datos SQLite.
        delay_seconds: Pausa de cortesía entre peticiones para cumplir la política de OSM.

    Returns:
        Diccionario con conteos: 'downloaded', 'skipped' y 'total'.

    Example:
        >>> # stats = seed(min_zoom=6, max_zoom=6, include_cities=False)
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
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

    print("=== Iniciando Pre-Carga / Refresco de Teselas (Centro-Sur de Chile) ===")
    print(f"Base de Datos: {db_path}")
    print(f"Rango de Zoom General: {min_zoom} a {max_zoom}")
    if max_age_days is not None:
        print(f"Política TTL: Actualizar teselas con más de {max_age_days} días de antigüedad")
    if force:
        print("Modo Forzado: Re-descargar todas las teselas")

    total_downloaded = 0
    total_skipped = 0

    try:
        # 1. Descarga del área general por niveles de zoom
        for z in range(min_zoom, max_zoom + 1):
            x_start, x_end, y_start, y_end = calculate_tile_bounds(BBOX, z)
            num_tiles = (x_end - x_start + 1) * (y_end - y_start + 1)
            print(
                f"\n[Zoom {z}] Procesando cuadrante: X[{x_start}..{x_end}], Y[{y_start}..{y_end}] ({num_tiles} teselas)..."
            )

            for x in range(x_start, x_end + 1):
                for y in range(y_start, y_end + 1):
                    downloaded = download_tile(
                        conn, z, x, y, max_age_days=max_age_days, force=force
                    )
                    if downloaded:
                        total_downloaded += 1
                        if delay_seconds > 0:
                            time.sleep(delay_seconds)
                    else:
                        total_skipped += 1

        # 2. Descarga de alta resolución para capitales regionales
        if include_cities:
            print("\n[Alta Resolución] Descargando centros urbanos (Zoom 10 a 12)...")
            for city in CITIES:
                city_name = str(city["name"])
                print(f" -> Ciudad: {city_name}")
                for z in [10, 11, 12]:
                    cx, cy = deg2num(float(city["lat"]), float(city["lon"]), z)
                    r = int(city["radius_tiles"])
                    for x in range(cx - r, cx + r + 1):
                        for y in range(cy - r, cy + r + 1):
                            downloaded = download_tile(
                                conn, z, x, y, max_age_days=max_age_days, force=force
                            )
                            if downloaded:
                                total_downloaded += 1
                                if delay_seconds > 0:
                                    time.sleep(delay_seconds)
                            else:
                                total_skipped += 1
    finally:
        conn.close()

    total_evaluated = total_downloaded + total_skipped
    print("\n=======================================================")
    print(" Proceso de Pre-Carga / Refresco Finalizado")
    print(f" Teselas nuevas/actualizadas: {total_downloaded}")
    print(f" Teselas omitidas (vigentes): {total_skipped}")
    print(f" Total evaluadas:             {total_evaluated}")
    print("=======================================================")

    return {
        "downloaded": total_downloaded,
        "skipped": total_skipped,
        "total": total_evaluated,
    }


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de línea de comandos (CLI) para pre-carga de teselas.

    Args:
        argv: Lista de argumentos de consola (None para usar sys.argv[1:]).

    Returns:
        Código de salida del proceso (0 para éxito).
    """
    parser = argparse.ArgumentParser(
        description="Pre-carga y actualización de teselas OSM para Centro-Sur de Chile."
    )
    parser.add_argument(
        "--min-zoom", type=int, default=6, help="Nivel de zoom mínimo regional (default: 6)"
    )
    parser.add_argument(
        "--max-zoom", type=int, default=8, help="Nivel de zoom máximo regional (default: 8)"
    )
    parser.add_argument(
        "--no-cities", action="store_true", help="Omitir centros urbanos detallados"
    )
    parser.add_argument(
        "--ttl-days",
        type=int,
        default=None,
        help="Días máximos de antigüedad para refrescar teselas",
    )
    parser.add_argument(
        "--force", action="store_true", help="Forzar re-descarga de todas las teselas"
    )
    args = parser.parse_args(argv)

    seed(
        min_zoom=args.min_zoom,
        max_zoom=args.max_zoom,
        include_cities=not args.no_cities,
        max_age_days=args.ttl_days,
        force=args.force,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
