# ogov-ckan — baseline de portales de datos abiertos de oGov.tech

Portal CKAN estándar, común y replicable para todos los ayuntamientos, federable
con datos.gob.es (DCAT-AP / DCAT-AP-ES).

## Documentación

- [Primera instalación](docs/INSTALACION.md)
- [Actualizar CKAN desde GitHub sin modificar datos de producción](docs/ACTUALIZACION.md)

## Organización de repositorios

| Repositorio | Qué contiene | Cuántos |
|---|---|---|
| **`ogov-ckan`** (este) | Código común: imagen Docker, extensiones `ckanext-ogovtech_theme` y `ckanext-ogovtech_fixes`, herramienta de migración, Ansible. Versionado con etiquetas (`v1.0.0`, …). | **Uno** para todos |
| **`ogov-ckan-despliegues`** | Una carpeta por ayuntamiento: inventario Ansible, variables (`.env` sin secretos), secretos cifrados con `ansible-vault`, mapeo de migración (`migracion.yml`) y versión de la baseline que usa. | **Uno** para todos |
| *(fuera de git)* | Datos: volcados de migración, ficheros subidos, base de datos y copias de seguridad. | — |

No se crea un repositorio por ayuntamiento: la propuesta exige que todos los
portales compartan el mismo código. Lo único que cambia entre portales es
configuración, y esa configuración vive en una carpeta por ayuntamiento:

```
ogov-ckan-despliegues/
  fuenlabrada/
    inventario.yml        # servidor(es) y versión de la baseline (p. ej. v1.0.0)
    vars.yml              # URL, título, DIR3, territorio…
    vault.yml             # contraseñas y tokens (cifrado)
    migracion.yml         # mapeo del portal antiguo (si aplica)
  otro-ayuntamiento/
    …
```

**Los datos nunca viajan en git**: son binarios, cambian, crecen con cada
ayuntamiento y pueden contener datos personales. La migración se reproduce con
`migration/migrate.py` a partir del portal de origen.

## Estructura de este repositorio

```
docker/                     # imagen CKAN, compose, nginx, init de PostgreSQL
  docker-compose.yml        # despliegue
  docker-compose.dev.yml    # desarrollo: monta el código de las extensiones
ckanext-ogovtech_theme/     # tema visual común (configurable en /ckan-admin/config)
ckanext-ogovtech_fixes/     # esquema de metadatos, licencias, perfil DCAT-AP-ES
connectors/transparencia/   # sincroniza datos del Portal de Transparencia oGov -> CKAN
  sync.py
  portales/<ayto>.yml
migration/                  # migración estándar de portales existentes
  migrate.py
  portales/<ayto>.yml       # (en producción, en ogov-ckan-despliegues)
                            # volcados e informes: $OGOV_TOOLS_DATA_DIR/migration
                            # (en la imagen, dentro del volumen /var/lib/ckan); nunca en git
ansible/                    # despliegue de instancias independientes (rol "ckan")
tests/
  unit/                     # tests unitarios (migración, conector, iconos, arranque)
  integration/              # tests contra CKAN real (tema, esquema, perfil DCAT)
  acceptance/acceptance.py  # aceptación de una instancia desplegada (anexo, apdo. 8)
  docker-compose.yml        # entorno de tests aislado
```

## Arranque local

```bash
cd docker
cp .env.example .env        # rellenar
docker compose up -d --build
# portal en http://localhost:${NGINX_PORT}
```

## Migración de un portal existente

```bash
docker exec -w /srv/app/migration <ckan> python3 migrate.py export portales/<ayto>.yml
docker exec -w /srv/app/migration -e CKAN_URL=http://localhost:5000 -e CKAN_API_TOKEN=… \
    <ckan> python3 migrate.py import portales/<ayto>.yml
docker exec … python3 migrate.py verify portales/<ayto>.yml
```

Se conservan los identificadores de datasets y recursos: las URLs de descarga
antiguas siguen funcionando si el dominio apunta al portal nuevo.

## Conector Portal de Transparencia → CKAN

Publica en CKAN cada tabla del portal de transparencia de oGov (índice `/data/5`
y visores) usando su API pública de lectura: un dataset por tabla con CSV y JSON
de columnas legibles, enlaces a los documentos y enlace al visor original.
Idempotente; retira con salvaguardas las tablas que desaparecen. Ansible lo
programa por instancia (por defecto, mensual): ver docs/INSTALACION.md, apdo. 7.

```bash
docker exec -w /srv/app/connectors/transparencia -e CKAN_URL=http://localhost:5000 -e CKAN_API_TOKEN=… \
    <ckan> python3 sync.py portales/<ayto>.yml [--dry-run] [--solo <ruta>]
```

> Nota de alcance: la propuesta de Solucionex considera las integraciones con
> sistemas externos fuera de la oferta base (bolsa de horas). Este conector es
> código propio de oGov y no forma parte de la baseline de Solucionex.

## Pruebas

```bash
# Unitarias + integración con CKAN (entorno aislado, se destruye al terminar)
docker compose -f tests/docker-compose.yml run --rm tests
docker compose -f tests/docker-compose.yml down -v

# Aceptación contra una instancia en marcha (crea y borra sus datos de prueba)
docker run --rm -v "$PWD/tests/acceptance:/acc" -w /acc --entrypoint python3 ogov/ckan:test \
    acceptance.py --url http://<host>:<puerto> --token <token sysadmin> --report-dir /acc/reports

# Rol de Ansible: lint, despliegue de 2 instancias, idempotencia y aislamiento
ansible/test/run.sh && ansible/test/run.sh --down

# Despliegue REMOTO por SSH desde un registro, actualización 1.0.0 -> 1.1.0 con
# datos, copia de seguridad y restauración (servidor simulado con Docker propio)
ansible/test/remote/run.sh
ansible/test/remote/backup_restore.sh <token> <huella.json>
ansible/test/remote/run.sh --down

# Federación: catálogo de una instancia contra las formas SHACL oficiales de DCAT-AP-ES
docker run --rm -v "$PWD/tests/federation:/v" -w /v python:3.12-slim \
    sh -c 'pip install -q -r requirements.txt && python validate_dcat_ap_es.py --url http://<host>:<puerto>'
```

## Publicar una versión de la imagen

```bash
scripts/release-image.sh 1.2.0 --push <registro>/ogov-ckan
```

Solo desde un árbol git limpio; prueba la imagen antes de publicarla, no
sobrescribe versiones existentes e imprime el digest para el inventario.

## Despliegue con Ansible

Una entrada del inventario por instancia; varias pueden compartir servidor.
La infraestructura (Docker y Compose) la aporta el cliente: el rol lo comprueba,
no lo instala.

```bash
cd ansible
ansible-galaxy collection install -r requirements.yml
cp inventories/vault.ejemplo.yml inventories/<entorno>/host_vars/<ayto>/vault.yml   # rellenar
ansible-vault encrypt inventories/<entorno>/host_vars/<ayto>/vault.yml
ansible-playbook playbooks/deploy.yml -i inventories/<entorno>/hosts.yml --ask-vault-pass [--limit <ayto>]
```

El rol valida variables (`meta/argument_specs.yml`) y la fortaleza de los secretos,
genera el `.env` (0600), levanta la instancia con `docker compose`, espera a que
responda y comprueba URL, título y extensiones.

## Versiones fijadas

| Componente | Versión |
|---|---|
| CKAN | 2.12.1 (imagen `ckan/ckan-base:2.12.1`, Python 3.14) |
| ckanext-scheming | 3.1.0 |
| ckanext-dcat | 2.4.5 (corrige 2 avisos de seguridad de 2.4.4) |
| ckanext-xloader | 2.6.0 (2.4.0 no soporta CKAN 2.12) |
| ckanext-pages | commit `28eba99` (soporte CKAN 2.12, sin versión publicada) |
| PostgreSQL | 16.16 (`postgres:16.16-alpine@sha256:81e77a48…`) |
| Solr | 9.9.0 (`ckan/ckan-solr:2.12-solr9.9@sha256:c8208fdf…`) |
| Redis | 7.4.12 (`redis:7.4.12-alpine@sha256:7f7d30b7…`) |
| Nginx | 1.27.5 (`nginx:1.27.5-alpine@sha256:65645c7b…`) |

Todas las imágenes se fijan por etiqueta exacta **y digest** (`docker/docker-compose.yml`,
`tests/docker-compose.yml`, `ARG CKAN_IMAGE` del Dockerfile): no pueden cambiar sin un commit.

## Secretos

- Nunca dentro de la imagen ni en git: llegan por el `.env` de cada instancia (en
  Ansible, cifrado con `ansible-vault`). `.dockerignore` excluye `.env`, `.git` y volcados.
- `docker compose` no arranca si falta una contraseña obligatoria.
- `ogov-start.sh` valida los secretos **antes** de inicializar CKAN y se niega a arrancar
  con valores cortos o de ejemplo (`OGOV_ENV=development` los rebaja a avisos, solo en local).
- Ningún valor `CKAN_*` puede contener `%`: CKAN 2.12 interpola esas variables y no
  arrancaría. El arranque y el rol de Ansible lo rechazan; los textos con `%` se
  configuran desde el panel de administración.
- El token de XLoader se regenera en cada contenedor y se revocan los anteriores.
- `CKAN_SECRET_KEY` y `CKAN_JWT_SECRET` fijos por instancia: los tokens de API y las
  sesiones sobreviven a los redespliegues.

Recursos de terceros incluidos en el tema: fuente Montserrat (SIL OFL 1.1) e
iconos Heroicons (MIT); sus licencias están junto a los ficheros.

## Pendiente antes de producción

Decisiones de oGov / del ayuntamiento:

- Código **DIR3** oficial de cada ayuntamiento (`ckan_dir3_code`; vacío en el inventario de ejemplo).
- Si las tablas de transparencia con datos personales (Declaración de la Renta, de Bienes,
  Nóminas…) se publican también como datos abiertos (`excluir` en `connectors/.../portales/*.yml`).
- Licencia de los datos del conector de transparencia (ahora `odc-by`) y de los 2 datasets
  de Fuenlabrada que no tenían licencia en origen (ahora `odc-by`).
- Alcance del tema rehecho frente a la oferta base de Solucionex.
- Alta de cada portal en datos.gob.es y su periodicidad de federación (trámite del ayuntamiento).

Comprobaciones aún no realizadas:

- Federación real con datos.gob.es (el catálogo ya valida contra sus formas SHACL oficiales).
- Despliegue en un servidor real de preproducción (probado por SSH contra un servidor simulado).
- HTTPS / certificados (responsabilidad de la infraestructura según el anexo técnico).
- Registro de imágenes definitivo (p. ej. GHCR) y CI (trabajo nuevo, fuera de esta entrega).
- **Gráficos y mapas** en las vistas de recursos (`ckanext-charts`, `ckanext-geoview`…):
  fuera de la oferta base; evaluar compatibilidad con CKAN 2.12 antes de incluirlos.
- Calidad de metadatos para datos.gob.es: el catálogo no tiene violaciones DCAT-AP-ES, pero sí
  avisos de propiedades recomendadas (contacto, cobertura temporal, descripción de recursos).
- 39 de 87 hojas de cálculo de Fuenlabrada no se cargan en DataStore (no son tablas limpias:
  títulos, celdas combinadas…). Se descargan bien; falta la vista previa en tabla.
- Actualización a una versión de CKAN con migraciones de esquema (la actualización
  1.0.0 -> 1.1.0 con datos está probada, pero ambas usan CKAN 2.12.1).
