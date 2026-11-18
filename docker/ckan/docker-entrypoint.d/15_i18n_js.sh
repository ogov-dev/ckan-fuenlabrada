#!/bin/bash
# Traducciones de la interfaz JavaScript (botones y mensajes generados por JS,
# p. ej. "Agregar filtro" en las vistas de recursos). CKAN las sirve en
# /api/i18n/<idioma> a partir de ficheros que hay que generar; se guardan en el
# volumen de almacenamiento (ckan.storage_path/i18n/js), por eso se generan al
# arrancar y no al construir la imagen. Es idempotente y tarda unos segundos.
# Se ejecuta con "source" desde start_ckan.sh: sin "set -e"; si falla, solo
# se avisa (la interfaz seguiría funcionando, con esos textos en inglés).
if ckan -c "$CKAN_INI" translation js >/dev/null 2>&1; then
    echo "[ogov] traducciones JavaScript generadas"
else
    echo "[ogov] AVISO: no se pudieron generar las traducciones JavaScript" >&2
fi
