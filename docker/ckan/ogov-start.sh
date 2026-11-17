#!/bin/bash
# Arranque de la baseline: valida los secretos ANTES de que prerun.py cree el
# administrador o toque la base de datos, y después cede a start_ckan.sh.
#   ogov-start.sh           valida y arranca
#   ogov-start.sh --check   solo valida (código 0 = correcto)
# OGOV_ENV=development rebaja los errores a avisos (nunca en producción).
set -u

errors=0
fail() {
    if [ "${OGOV_ENV:-production}" = "development" ]; then
        echo "[ogov] AVISO (development): $1" >&2
    else
        echo "[ogov] ERROR: $1" >&2
        errors=$((errors + 1))
    fi
}

check_secret() {   # nombre longitud_mínima
    local name=$1 min=$2 value="${!1:-}"
    if [ -z "$value" ]; then
        fail "$name no está definida"
    elif [ "${#value}" -lt "$min" ]; then
        fail "$name es demasiado corta (${#value} < $min caracteres)"
    elif printf '%s' "$value" | grep -qiE 'cambia|changeme|example|password|secret'; then
        fail "$name contiene un valor de ejemplo"
    fi
}

check_secret CKAN_SECRET_KEY 32
check_secret CKAN_JWT_SECRET 32
check_secret CKAN_SYSADMIN_PASSWORD 12
for url_var in CKAN_SQLALCHEMY_URL CKAN_DATASTORE_WRITE_URL CKAN_DATASTORE_READ_URL; do
    if printf '%s' "${!url_var:-}" | grep -qiE ':(cambia|changeme|pass|password)[^@]*@'; then
        fail "$url_var usa una contraseña de ejemplo"
    fi
done

if [ "$errors" -gt 0 ]; then
    echo "[ogov] $errors problema(s) de secretos: no se arranca. Revisa el .env de la instancia." >&2
    exit 1
fi
[ "${1:-}" = "--check" ] && { echo "[ogov] secretos correctos"; exit 0; }
exec /srv/app/start_ckan.sh
