#!/bin/bash
# Genera (una sola vez por contenedor) el token de API que usa XLoader.
if [ -z "$CKANEXT__XLOADER__API_TOKEN" ] && ! grep -q "^ckanext.xloader.api_token" "$CKAN_INI"; then
    echo "[ogov] Generando token de API para XLoader"
    TOKEN=$(ckan -c "$CKAN_INI" user token add "$CKAN_SYSADMIN_NAME" xloader | tail -n 1 | tr -d '\t ')
    ckan config-tool "$CKAN_INI" "ckanext.xloader.api_token=$TOKEN"
fi
