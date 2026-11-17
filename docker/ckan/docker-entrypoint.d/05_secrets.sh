#!/bin/bash
# Claves secretas estables. La imagen base genera claves nuevas en cada
# contenedor, lo que invalida todos los tokens de API y las sesiones en cada
# redespliegue. Si se definen en el entorno, se fijan en ckan.ini.
if [ -n "$CKAN_SECRET_KEY" ]; then
    ckan config-tool "$CKAN_INI" "SECRET_KEY=$CKAN_SECRET_KEY" "WTF_CSRF_SECRET_KEY=$CKAN_SECRET_KEY"
fi
if [ -n "$CKAN_JWT_SECRET" ]; then
    ckan config-tool "$CKAN_INI" \
        "api_token.jwt.encode.secret=string:$CKAN_JWT_SECRET" \
        "api_token.jwt.decode.secret=string:$CKAN_JWT_SECRET"
fi
