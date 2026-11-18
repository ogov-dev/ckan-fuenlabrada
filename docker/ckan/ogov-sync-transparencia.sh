#!/bin/bash
# Sincronización Portal de Transparencia -> CKAN, pensada para la tarea
# programada (la instala Ansible). Se ejecuta DENTRO del contenedor de CKAN:
#   docker compose exec -T ckan /srv/app/ogov-sync-transparencia.sh [config.yml]
# Genera un token de API temporal, lo usa y lo revoca al terminar (también si
# falla): no hay credenciales guardadas para la tarea programada.
set -euo pipefail
CFG=${1:-/srv/app/config/transparencia.yml}
if [ ! -f "$CFG" ]; then
    echo "[ogov] Sin configuración del conector en $CFG: no se sincroniza." >&2
    exit 2
fi
TOKEN_NAME="sync-transparencia-$(date +%Y%m%d%H%M%S)"
revoke() {
    for id in $(ckan -c "$CKAN_INI" user token list "$CKAN_SYSADMIN_NAME" 2>/dev/null \
                | sed -nE "s/^[[:space:]]*\[([^]]+)\] ${TOKEN_NAME} - .*/\1/p"); do
        ckan -c "$CKAN_INI" user token revoke "$id" >/dev/null 2>&1
    done
}
trap revoke EXIT
TOKEN=$(ckan -c "$CKAN_INI" user token add "$CKAN_SYSADMIN_NAME" "$TOKEN_NAME" 2>/dev/null | tail -n 1 | tr -d '\t ')
echo "[ogov] $(date -u +%FT%TZ) sincronización de transparencia ($CFG)"
cd /srv/app/connectors/transparencia
CKAN_URL=http://localhost:5000 CKAN_API_TOKEN="$TOKEN" python3 sync.py "$CFG"
