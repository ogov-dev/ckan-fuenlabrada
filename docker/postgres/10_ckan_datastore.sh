#!/bin/bash
# Crea las bases de datos y usuarios de CKAN y DataStore.
# Las contraseñas se pasan como variables de psql (:'var' = literal escapado)
# y los nombres se validan: ningún valor del .env se interpola en el SQL.
set -euo pipefail

for name in CKAN_DB CKAN_DB_USER DATASTORE_DB DATASTORE_READONLY_USER; do
    if ! [[ "${!name}" =~ ^[a-z_][a-z0-9_]{0,62}$ ]]; then
        echo "[ogov] $name='${!name}' no es un identificador válido (minúsculas, dígitos y _)" >&2
        exit 1
    fi
done

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" \
     -v ckan_db="$CKAN_DB" -v ckan_user="$CKAN_DB_USER" -v ckan_pw="$CKAN_DB_PASSWORD" \
     -v ds_db="$DATASTORE_DB" -v ro_user="$DATASTORE_READONLY_USER" -v ro_pw="$DATASTORE_READONLY_PASSWORD" <<'SQL'
CREATE ROLE :"ckan_user" LOGIN PASSWORD :'ckan_pw';
CREATE DATABASE :"ckan_db" OWNER :"ckan_user" ENCODING 'utf-8';
CREATE ROLE :"ro_user" LOGIN PASSWORD :'ro_pw';
CREATE DATABASE :"ds_db" OWNER :"ckan_user" ENCODING 'utf-8';
SQL
