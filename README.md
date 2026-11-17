# ogov-ckan — baseline de portales de datos abiertos de oGov.tech

Portal CKAN estándar, común y replicable para todos los ayuntamientos, federable
con datos.gob.es (DCAT-AP / DCAT-AP-ES).

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
  dumps/                    # volcados descargados — ignorado por git
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
Idempotente, pensado para ejecutarse periódicamente (cron).

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
```

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
- `CKAN_SECRET_KEY` y `CKAN_JWT_SECRET` fijos por instancia: los tokens de API y las
  sesiones sobreviven a los redespliegues.

Recursos de terceros incluidos en el tema: fuente Montserrat (SIL OFL 1.1) e
iconos Heroicons (MIT); sus licencias están junto a los ficheros.
