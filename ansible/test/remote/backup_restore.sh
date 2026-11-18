#!/bin/bash
# Prueba los comandos de copia y restauración de docs/ACTUALIZACION.md sobre la
# instancia del servidor de pruebas (tras ansible/test/remote/run.sh).
#   ansible/test/remote/backup_restore.sh <token-sysadmin> <huella-antes.json>
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SERVER=ogov-remote-test
INST=ogov-remoto
URL=http://localhost:8093
TOKEN=$1
BEFORE=$2
on_server() { docker exec "$SERVER" sh -c "cd /opt/ogov-ckan/$INST && $*"; }

echo "== 1. Copia de seguridad (comandos de la guía, paso 2.1)"
on_server 'B=/backup/prueba; mkdir -p $B
  for db in ckandb datastore; do docker compose exec -T db pg_dump -U postgres -Fc "$db" > $B/$db.dump; done
  docker run --rm -v '"$INST"'_ckan_storage:/data:ro -v $B:/backup alpine tar czf /backup/storage.tgz -C /data .
  ls -l $B | awk "NR>1{print \$5, \$9}"'

echo "== 2. Cambio posterior a la copia (un dataset nuevo)"
curl -s -H "Authorization: $TOKEN" -H 'Content-Type: application/json' "$URL/api/3/action/package_create" \
  -d '{"name":"creado-despues-de-la-copia","title":"Posterior","notes":"x","owner_org":"org-actualizacion","license_id":"CC-BY-4.0","theme":["http://datos.gob.es/kos/sector-publico/sector/economia"],"language":["http://publications.europa.eu/resource/authority/language/SPA"]}' \
  | python3 -c "import sys,json;print('dataset posterior creado:', json.load(sys.stdin)['success'])"

echo "== 3. Restauración (comandos de la guía, paso 2.2)"
on_server 'B=/backup/prueba; docker compose stop ckan nginx >/dev/null 2>&1
  for db in ckandb datastore; do
    docker compose exec -T db dropdb -U postgres --if-exists "$db"
    docker compose exec -T db createdb -U postgres -O ckandbuser "$db"
    docker compose exec -T db pg_restore -U postgres -d "$db" --no-owner --role=ckandbuser < $B/$db.dump
  done
  docker run --rm -v '"$INST"'_ckan_storage:/data -v $B:/backup alpine sh -c "rm -rf /data/* && tar xzf /backup/storage.tgz -C /data"
  docker compose up -d --force-recreate ckan nginx >/dev/null 2>&1'
for _ in $(seq 90); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/3/action/status_show")" = 200 ] && break; sleep 10
done
on_server 'docker compose exec -T ckan ckan -c /srv/app/ckan.ini search-index rebuild >/dev/null 2>&1' && echo "índice reconstruido"

echo "== 4. Estado tras restaurar"
code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/3/action/package_show?id=creado-despues-de-la-copia")
[ "$code" = 404 ] && echo "el dataset posterior a la copia ya no existe (correcto)" || { echo "FALLO: sigue existiendo ($code)"; exit 1; }
python3 "$HERE/data.py" compare "$URL" "$TOKEN" "$BEFORE"
echo "tokens de XLoader activos: $(docker exec "$SERVER" docker exec $INST-ckan-1 ckan -c /srv/app/ckan.ini user token list ckan_admin 2>/dev/null | grep -c '] xloader -')"
