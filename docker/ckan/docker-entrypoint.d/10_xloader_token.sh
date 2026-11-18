#!/bin/bash
# Token de API de XLoader. Cada contenedor nuevo genera uno; antes se revocan
# los anteriores con el mismo nombre para no acumular credenciales de
# administrador válidas en cada redespliegue.
TOKEN_NAME=xloader
if [ -z "${CKANEXT__XLOADER__API_TOKEN:-}" ] && ! grep -q "^ckanext.xloader.api_token" "$CKAN_INI"; then
    for id in $(ckan -c "$CKAN_INI" user token list "$CKAN_SYSADMIN_NAME" 2>/dev/null \
                | sed -nE "s/^[[:space:]]*\[([^]]+)\] ${TOKEN_NAME} - .*/\1/p"); do
        ckan -c "$CKAN_INI" user token revoke "$id" >/dev/null 2>&1 && echo "[ogov] Token $TOKEN_NAME anterior revocado"
    done
    echo "[ogov] Generando token de API para XLoader"
    TOKEN=$(ckan -c "$CKAN_INI" user token add "$CKAN_SYSADMIN_NAME" "$TOKEN_NAME" | tail -n 1 | tr -d '\t ')
    ckan config-tool "$CKAN_INI" "ckanext.xloader.api_token=$TOKEN"
fi
