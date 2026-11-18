#!/bin/bash
# Cookies de sesión y de "Recordarme". El ckan.ini por defecto las emite sin
# "Secure", con "Domain=" vacío y la de "Recordarme" con SameSite=None (que los
# navegadores rechazan si no es Secure). Si el portal se publica por HTTPS
# (CKAN_SITE_URL https://…), las cookies solo viajan cifradas.
# Se ejecuta con "source" desde start_ckan.sh: sin "set -e" ni "exit".
if [ -n "${CKAN_INI:-}" ] && [ -f "$CKAN_INI" ]; then
    case "${CKAN_SITE_URL:-}" in
        https://*) ogov_cookie_secure=true ;;
        *)         ogov_cookie_secure=false ;;
    esac
    # Domain vacío: sin la línea, Flask no envía el atributo (solo el host del
    # portal). Se reescribe el fichero en su sitio (no "sed -i"): el usuario
    # ckan puede escribir ckan.ini pero no crear ficheros en /srv/app.
    if grep -q '^SESSION_COOKIE_DOMAIN *= *$' "$CKAN_INI"; then
        ogov_ini=$(sed '/^SESSION_COOKIE_DOMAIN *= *$/d' "$CKAN_INI") \
            && printf '%s\n' "$ogov_ini" > "$CKAN_INI"
        unset ogov_ini
    fi
    if ckan config-tool "$CKAN_INI" \
            "SESSION_COOKIE_SECURE=$ogov_cookie_secure" \
            "REMEMBER_COOKIE_SECURE=$ogov_cookie_secure" \
            "SESSION_COOKIE_SAMESITE=Lax" \
            "REMEMBER_COOKIE_SAMESITE=Lax" >/dev/null; then
        echo "[ogov] cookies: Secure=$ogov_cookie_secure, SameSite=Lax"
    else
        echo "[ogov] AVISO: no se pudo ajustar la configuración de cookies" >&2
    fi
    unset ogov_cookie_secure
fi
