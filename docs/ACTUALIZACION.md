# Actualizar CKAN desde GitHub sin modificar datos de producción

## Principios

- **Los datos viven en volúmenes**, no en la imagen: `pg_data` (PostgreSQL:
  CKAN y DataStore), `ckan_storage` (ficheros subidos, volcados de migración) y
  `solr_data` (índice, reconstruible). Actualizar es **cambiar la imagen**; los
  volúmenes no se tocan.
- **Imágenes inmutables y versionadas**: cada versión se publica una vez
  (`scripts/release-image.sh`) y se despliega por digest.
- **Nunca se prueba en producción**: toda versión pasa antes por una
  preproducción con una **copia** de los datos de producción.
- **Única operación que modifica la base de datos**: al arrancar una versión con
  un CKAN o extensiones más nuevos, `ckan db init` aplica migraciones de esquema
  (no borra datos, pero no se deshace sola). Por eso la copia de seguridad previa
  es obligatoria y la prueba en preproducción va primero.

> Probado con `ansible/test/remote/run.sh`: instalación de la versión 1.0.0,
> carga de datos, actualización a 1.1.0 con el mismo inventario y comprobación de
> que metadatos, fichero (SHA-256), filas de DataStore y usuarios son idénticos.

## 1. Preparar la nueva versión (GitHub)

1. Los cambios entran en `main` mediante *pull request* revisado.
2. En local o CI, con el commit exacto que se va a publicar:
   ```bash
   docker compose -f tests/docker-compose.yml run --rm tests      # unitarios + integración + SHACL
   docker compose -f tests/docker-compose.yml down -v
   ansible/test/run.sh && ansible/test/run.sh --down              # rol de Ansible
   ```
3. Etiquetar y publicar la imagen:
   ```bash
   git tag -a v1.2.0 -m "v1.2.0" && git push origin v1.2.0
   git checkout v1.2.0
   scripts/release-image.sh 1.2.0 --push ghcr.io/ogov-dev/ogov-ckan
   ```
   Anotar el digest que imprime.

### Si la versión cambia CKAN o una extensión

En `docker/ckan/Dockerfile`: `ARG CKAN_IMAGE` (etiqueta **y** digest) y las
versiones de extensiones (`SCHEMING_REF`, `DCAT_VERSION`, `XLOADER_VERSION`,
`PAGES_REF`). Revisar sus notas de versión (cambios de configuración o de
plantillas) y pasar la batería completa. Actualizar la tabla de versiones del
README.

## 2. Probar en preproducción con una copia de producción

1. **Copia de seguridad de producción** (solo lectura sobre producción). Se
   copian las dos bases de datos (sin usuarios ni contraseñas) y los ficheros:
   ```bash
   cd /opt/ogov-ckan/<instancia>
   F=$(date +%Y%m%d-%H%M); B=/backup/<instancia>-$F; sudo mkdir -p $B
   for db in ckandb datastore; do
     sudo docker compose exec -T db pg_dump -U postgres -Fc "$db" | sudo tee $B/$db.dump >/dev/null
   done
   sudo docker run --rm -v <instancia>_ckan_storage:/data:ro -v $B:/backup alpine \
       tar czf /backup/storage.tgz -C /data .
   sudo ls -l $B          # ckandb.dump, datastore.dump y storage.tgz, ninguno vacío
   ```
   (La política y la custodia de las copias son de la infraestructura; anexo
   técnico, apdo. 3.) Los nombres `ckandb`/`datastore`/`ckandbuser` son los
   valores por defecto de `ckan_db_name`, `ckan_datastore_db` y `ckan_db_user`.
2. **Restaurar la copia en preproducción** (otra instancia, desplegada antes con
   la versión **actual** de producción y con sus propios secretos):
   ```bash
   cd /opt/ogov-ckan/<preproduccion>
   sudo docker compose stop ckan nginx
   for db in ckandb datastore; do
     sudo docker compose exec -T db dropdb -U postgres --if-exists "$db"
     sudo docker compose exec -T db createdb -U postgres -O ckandbuser "$db"
     sudo docker compose exec -T db pg_restore -U postgres -d "$db" --no-owner --role=ckandbuser < $B/$db.dump
   done
   sudo docker run --rm -v <preproduccion>_ckan_storage:/data -v $B:/backup alpine \
       sh -c 'rm -rf /data/* && tar xzf /backup/storage.tgz -C /data'
   # Recrear (no solo arrancar) CKAN: regenera su token de XLoader, que no
   # existe en la base restaurada, y reaplica los permisos de DataStore.
   sudo docker compose up -d --force-recreate ckan nginx
   sudo docker compose exec ckan ckan -c /srv/app/ckan.ini search-index rebuild
   ```
   Los usuarios de CKAN vienen en la copia; sus tokens de API de producción no
   valen en preproducción (están firmados con otros secretos), lo que es correcto.
3. **Desplegar la versión nueva en preproducción**: cambiar `ckan_image` en su
   inventario y ejecutar el playbook con `--limit <preproduccion>`.
4. **Verificar**: aceptación 19/19, SHACL con 0 violaciones, revisión visual y,
   si hubo migración de base de datos, que los datos siguen ahí.

## 3. Actualizar producción

1. Nueva copia de seguridad de producción (paso 2.1), justo antes.
2. En el repositorio de despliegues, cambiar **solo** `ckan_image` al digest
   nuevo (y `ckan_baseline_dir` al checkout de la misma etiqueta). Commit.
3. Desplegar:
   ```bash
   ansible-playbook playbooks/deploy.yml -i <inventario> --limit <instancia> --ask-vault-pass
   ```
   El rol recrea solo el contenedor de CKAN (y los que cambien), espera a que
   responda y comprueba la configuración.
4. Verificar: aceptación y SHACL contra el portal; versión en ejecución:
   ```bash
   sudo docker exec <instancia>-ckan-1 cat /srv/app/OGOV_VERSION
   ```

## 4. Vuelta atrás

- **Sin cambios de esquema** (misma versión de CKAN y extensiones): volver a
  poner el `ckan_image` anterior y desplegar.
- **Con cambios de esquema**: parar `ckan`, restaurar la copia del paso 3.1
  (igual que en 2.2, pero en producción) y desplegar la imagen anterior.

## Lo que este procedimiento NO hace

- No toca los volúmenes ni ejecuta comandos sobre los datos de producción,
  salvo leerlos para la copia de seguridad.
- No gestiona copias programadas, monitorización ni alta disponibilidad
  (responsabilidad de la infraestructura; anexo técnico, apdos. 3 y 9).
