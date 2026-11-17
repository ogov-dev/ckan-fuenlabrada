#!/bin/bash
# Prueba del script de inicialización de PostgreSQL (docker/postgres) con
# contraseñas hostiles y nombres inválidos. Uso: tests/infra/test_postgres_init.sh
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PG=$(grep -m1 -o 'postgres:16[^ ]*' "$ROOT/docker/docker-compose.yml")
NAME=ogov-pgtest-$$
PW_CKAN="ab'c\$d;DROP ROLE x;--"
PW_RO="r'o\"1 \\x"
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --name "$NAME" -e POSTGRES_PASSWORD=x -e CKAN_DB=ckan -e CKAN_DB_USER=ckanuser \
  -e CKAN_DB_PASSWORD="$PW_CKAN" -e DATASTORE_DB=ds -e DATASTORE_READONLY_USER=ro \
  -e DATASTORE_READONLY_PASSWORD="$PW_RO" -v "$ROOT/docker/postgres:/docker-entrypoint-initdb.d:ro" "$PG" >/dev/null
for _ in $(seq 60); do docker exec "$NAME" pg_isready -h 127.0.0.1 -U postgres >/dev/null 2>&1 && break; sleep 1; done
sleep 2
docker exec -e PGPASSWORD="$PW_CKAN" "$NAME" psql -h 127.0.0.1 -U ckanuser -d ckan -tAc "select 1" | grep -qx 1
docker exec -e PGPASSWORD="$PW_RO" "$NAME" psql -h 127.0.0.1 -U ro -d ds -tAc "select 1" | grep -qx 1
owner=$(docker exec "$NAME" psql -U postgres -tAc "select pg_get_userbyid(datdba) from pg_database where datname='ds'")
[ "$owner" = ckanuser ]
echo "OK contraseñas con comillas, \$ y ; se guardan literalmente; propietario de ds=$owner"

out=$(docker run --rm -e POSTGRES_PASSWORD=x -e "CKAN_DB=ckan;drop" -e CKAN_DB_USER=u -e CKAN_DB_PASSWORD=p \
  -e DATASTORE_DB=ds -e DATASTORE_READONLY_USER=ro -e DATASTORE_READONLY_PASSWORD=p \
  -v "$ROOT/docker/postgres:/docker-entrypoint-initdb.d:ro" "$PG" 2>&1 || true)
echo "$out" | grep -q "no es un identificador válido"
echo "OK nombre inválido rechazado"
