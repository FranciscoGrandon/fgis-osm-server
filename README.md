# F-GIS 4.0 & OpenStreetMap Local Tile Server

> **Autor:** Creado por Francisco Grandón Vergara  
> **Plataforma:** Geoanalítica de Terreno + Servidor Cartográfico Local de Alto Rendimiento

Plataforma geoespacial integrada para visualización, geoanalítica de terreno y servicio de teselas cartográficas locales (XYZ Slippy Map) optimizado para la Zona Centro-Sur de Chile (Regiones de O'Higgins, Maule, Ñuble y Biobío).

---

## 🚀 Componentes del Proyecto

### 1. F-GIS 4.0 (`fgis4/`)
Visor cartográfico web y geoanalítico avanzado con arquitectura cliente ligera y modo offline/local:
- **Capa Base Resiliente (`OsmLocalResilientLayer`):** Conexión nativa con el servidor local de teselas en `http://localhost:8550/tile/{z}/{x}/{y}.png` con conmutación por tesela (fallback) transparente hacia el servidor de OpenStreetMap ante ausencia de caché o desconexión.
- **Herramientas de Terreno:** Soporte multicapa, dibujo vectorial (Leaflet Draw), importación/exportación de datos (GeoJSON, XLSX, CSV), análisis espacial y clustering.
- **Libre de dependencias externas en tiempo de ejecución:** Incluye librerías locales en `fgis4/lib/` (Leaflet 1.9+, Leaflet Draw, SheetJS, geometrías regionales).

### 2. OSM Local Tile Server (`osm-server/`)
Servidor cartográfico local en Python puro de alto rendimiento:
- **Servicio Multihilo:** Endpoints HTTP en el puerto `8550`:
  - `GET /tile/{z}/{x}/{y}.png`: Servicio de teselas cartográficas estándar (256x256 px). Si existe en la base SQLite entrega en < 2ms (`X-Tile-Source: CACHE_HIT`), o bien descarga upstream y cachea.
  - `GET /api/status`: Métricas en tiempo real (teselas almacenadas, tamaño en MB, desglose por zoom).
  - `GET /api/health`: Healthcheck del servicio (`{"status":"ok"}`).
  - `GET /`: Visor integrado de monitoreo.
- **Base de Datos SQLite WAL:** Almacenamiento eficiente con modo WAL (Write-Ahead Logging).
- **Herramienta Seeder CLI (`seed_region.py`):** Descarga masiva controlada de teselas con cadencia respetuosa de la política de OSM, soporte de TTL y flags de actualización incremental.
- **Docker Compose:** Soporte de despliegue contenerizado mediante `docker-compose.yml`.

---

## 🛠️ Instalación y Uso Rápido

### Prerrequisitos
- Python 3.10+ (probado en Python 3.12)
- Navegador Web moderno (Edge, Chrome, Firefox)

### Ejecutar Servidor de Teselas
```powershell
cd osm-server
python server.py
```
El servidor iniciará en `http://localhost:8550`.

### Abrir F-GIS 4.0
Abra directamente en su navegador el archivo:
`fgis4/index.html`

F-GIS 4.0 detectará de forma reactiva la presencia de `http://localhost:8550` y activará la capa local de teselas con el indicador verde en la cabecera.

---

## 🧪 Pruebas Unitarias y Calidad de Código
El proyecto cuenta con una cobertura de pruebas unitarias superior al 95%:
```powershell
python -m pytest tests --cov=osm-server
```

---

## 📄 Licencia y Reconocimientos
- Cartografía: © Colaboradores de [OpenStreetMap](https://www.openstreetmap.org/copyright) (ODbL).
- Código desarrollado por Francisco Grandón Vergara.
