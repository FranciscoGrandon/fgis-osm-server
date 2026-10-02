# Servidor Local de OpenStreetMap (Zona Centro-Sur de Chile)

Stack de servidor cartográfico local optimizado para servir teselas (tiles) en formato XYZ (`/tile/{z}/{x}/{y}.png`) para las regiones de **O'Higgins, Maule, Ñuble y Biobío**.

Este proyecto cuenta con dos modalidades de despliegue:
1. **Modo Nativo Python (Operativo sin Docker):** Servidor HTTP multihilo de alto rendimiento con almacenamiento persistente SQLite (`tiles.db`) en modo WAL y visor interactivo Leaflet.js.
2. **Modo Contenedores Docker Compose:** Configuración lista para entornos de producción o servidores con Docker Engine.

---

## 1. Arquitectura y Endpoints

El servidor expone los siguientes endpoints HTTP en el puerto `8550`:

| Endpoint | Método | Descripción |
| :--- | :---: | :--- |
| `http://localhost:8550/` | `GET` | **Visor Web Interactivo:** Aplicación moderna con Leaflet.js centrada en la zona Centro-Sur, con accesos directos a Rancagua, Talca, Chillán y Concepción, y panel de métricas en tiempo real. |
| `http://localhost:8550/tile/{z}/{x}/{y}.png` | `GET` | **Endpoint Estándar de Teselas:** Retorna imágenes PNG (256x256 px). Si la tesela está en la base de datos local SQLite, se entrega en < 2 ms (`X-Tile-Source: CACHE_HIT`). Si no, se descarga y almacena automáticamente. |
| `http://localhost:8550/api/status` | `GET` | **API de Métricas:** Retorna JSON con número de teselas almacenadas, tamaño en MB de la base de datos, desglose por nivel de zoom y uptime. |
| `http://localhost:8550/api/health` | `GET` | **Healthcheck:** Verificación rápida de estado del servicio (`{"status":"ok"}`). |

---

## 2. Diagrama Técnico de Arquitectura y Flujo de Datos

El siguiente diagrama ilustra el flujo de peticiones, el mecanismo de almacenamiento en caché persistente en disco y el pipeline de pre-carga:

```mermaid
flowchart TD
    subgraph Clientes ["1. Capa de Clientes"]
        Browser["Navegador Web (index.html / Leaflet)"]
        GIS["Sistemas GIS / QGIS / Apps Móviles"]
        Curl["Herramientas CLI (curl.exe)"]
    end

    subgraph ServerStack ["2. Servidor Local Python (server.py en puerto 8550)"]
        Router{"Enrutador HTTP (do_GET / do_HEAD)"}
        Viewer["Servidor de Archivos (index.html)"]
        Metrics["API de Métricas (/api/status & /api/health)"]
        TileService["Servicio de Teselas (/tile/{z}/{x}/{y}.png)"]
        CacheCheck{"¿Existe la tesela en tiles.db?"}
    end

    subgraph Storage ["3. Almacenamiento Persistente en Disco"]
        SQLite[("Base de Datos SQLite WAL (cache/tiles.db)")]
    end

    subgraph SeederCLI ["4. Pipeline de Pre-Carga y Mantenimiento"]
        Seeder["Herramienta CLI Seeder (seed_region.py)"]
    end

    subgraph External ["5. Servidores Upstream (Internet)"]
        OSM["OpenStreetMap Public CDN (tile.openstreetmap.org)"]
    end

    Browser -->|Petición HTTP| Router
    GIS -->|Petición /tile/{z}/{x}/{y}.png| Router
    Curl -->|Validación /api/health| Router

    Router -->|Ruta /| Viewer
    Router -->|Ruta /api/*| Metrics
    Router -->|Ruta /tile/*| TileService

    Metrics -.->|Consulta conteo y tamaño| SQLite
    TileService --> CacheCheck

    CacheCheck -->|"SÍ: CACHE_HIT (< 2ms)"| SQLite
    SQLite -->|Retorna binario PNG| TileService
    TileService -->|HTTP 200 (image/png)| Clientes

    CacheCheck -->|"NO: CACHE_MISS"| OSM
    OSM -->|Descarga tesela 256x256 px| TileService
    TileService -->|Guarda con TIMESTAMP| SQLite

    Seeder -->|Pre-descarga masiva Z6..Z12 / TTL| SQLite
```

---

## 3. Instrucciones de Uso (Modo Nativo Python)

### 3.1 Iniciar el Servidor
En una consola PowerShell dentro del directorio `osm-server`:
```powershell
python server.py
```
El servidor quedará escuchando en `http://localhost:8550`.

### 3.2 Ejecutar en Segundo Plano (Background en Windows)
Para dejar el servidor corriendo silenciosamente sin mantener una consola abierta:
```powershell
Start-Process pythonw.exe -ArgumentList "server.py" -WorkingDirectory "D:\OSM_LOCAL\osm-server"
```

Para detener el proceso en segundo plano:
```powershell
Get-Process pythonw | Where-Object { $_.CommandLine -like "*server.py*" } | Stop-Process
```

### 3.3 Pre-Carga de Teselas de la Región (Seeding Offline)
Para garantizar navegación 100% offline en las regiones de O'Higgins, Maule, Ñuble y Biobío:
```powershell
python seed_region.py
```
Este script pre-descarga las teselas de zoom 6 a 8 para toda la macro-zona y zoom 10 a 12 para los centros urbanos principales (Rancagua, Talca, Chillán, Concepción).

---

## 4. Pruebas de Validación (Curl)

### Probar una tesela representativa (Concepción - Zoom 12):
```powershell
curl.exe -I http://localhost:8550/tile/12/1216/2498.png
```
Respuesta esperada: `HTTP/1.1 200 OK`, `Content-Type: image/png`.

### Consultar estado de la base de datos:
```powershell
curl.exe http://localhost:8550/api/status
```

---

## 5. Despliegue con Docker Compose (Opcional)

Si en el futuro se despliega en una máquina con Docker Desktop o servidor Linux con Docker:
1. Descargar el extracto oficial de Chile:
   ```bash
   curl -O https://download.geofabrik.de/south-america/chile-latest.osm.pbf
   ```
2. Importar datos:
   ```bash
   docker compose run osm-tiles import
   ```
3. Iniciar el demonio:
   ```bash
   docker compose up -d
   ```
4. Monitorear logs:
   ```bash
   docker compose logs -f
   ```

---

## 6. Estrategia DevOps Recomendada de Actualización y Mantenimiento

### 6.1 Frecuencia Recomendada de Actualización

| Escenario | Frecuencia Recomendada | Criterio Técnico |
| :--- | :---: | :--- |
| **Operación GIS Corporativa / Sanitarias / Redes** *(Recomendado)* | **Cada 3 a 6 meses** *(Trimestral o Semestral)* | El trazado vial y límites comunales son estables. Permite incorporar loteos y nuevas urbanizaciones sin generar consumo excesivo de red ni variaciones imprevistas durante faenas operativas. |
| **Sectores de Rápida Expansión Urbana** | **Cada 1 a 2 meses** *(Bimestral)* | Aplicable si se requiere monitorear periódicamente sectores periurbanos en desarrollo intensivo. |
| **Respaldos Offline para Equipos en Terreno** | **Anual** | Suficiente para notebooks o terminales móviles de inspección con baja o nula conectividad. |

### 5.2 Política de Uso de OpenStreetMap (Tile Usage Policy)
OpenStreetMap es un servicio soportado por donaciones comunitarias. Para evitar bloqueos temporales de IP por saturación:
- No ejecutar scripts de scraping masivo de teselas de forma diaria ni semanal.
- Mantener siempre la cabecera `User-Agent` identificatoria configurada en `server.py` y `seed_region.py`.
- Utilizar pausas automáticas (50 ms) entre descargas sucesivas (incorporadas por defecto en el seeder).

### 5.3 Métodos de Actualización Disponibles

#### Método A: Actualización Incremental por TTL (Recomendado)
Refresca únicamente aquellas teselas cuya fecha de descarga supere un número determinado de días (ej. 90 días), respetando las que aún son vigentes:
```powershell
python seed_region.py --ttl-days 90
```

#### Método B: Forzado de Actualización Total
Sobrescribe todas las teselas de la macro-zona con la información más reciente de OSM:
```powershell
python seed_region.py --force
```

#### Método C: Automatización Programada en Windows (Background)
Para programar una actualización silenciosa trimestral sin ventanas emergentes (empleando `pythonw.exe` de acuerdo a los estándares del entorno):
```powershell
$Action = New-ScheduledTaskAction -Execute "pythonw.exe" -Argument '"D:\OSM_LOCAL\osm-server\seed_region.py" --ttl-days 90' -WorkingDirectory "D:\OSM_LOCAL\osm-server"
$Trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 12 -DaysOfWeek Sunday -At 02:00
Register-ScheduledTask -TaskName "OSM-Tiles-Quarterly-Update" -Action $Action -Trigger $Trigger -Description "Actualización trimestral de teselas OSM Centro-Sur"
```

#### Método D: Refresco Limpio / Purga de Caché
Para iniciar una base de datos limpia desde cero:
1. Detener el servidor.
2. Eliminar o respaldar la carpeta `cache/`.
3. Ejecutar nuevamente `python seed_region.py`.
4. Iniciar `python server.py`.

---

## 7. Ciclo de Calidad y Suite de Pruebas (QA)

El proyecto cuenta con un ciclo de aseguramiento de calidad automatizado conforme a los estándares de desarrollo sénior en Python:

```bash
# 1. Linting y formato
ruff check .

# 2. Comprobación estricta de tipos
mypy --strict osm-server

# 3. Pruebas unitarias con reporte de cobertura (>90%)
pytest --cov=osm-server --cov-report=term-missing

# 4. Auditoría de seguridad
bandit -c pyproject.toml -r osm-server
```

