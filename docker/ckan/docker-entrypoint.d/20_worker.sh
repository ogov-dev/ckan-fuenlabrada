#!/bin/bash
# Worker de tareas en segundo plano (XLoader) dentro del mismo contenedor,
# compartiendo el ckan.ini ya inicializado. Se puede desactivar con
# CKAN_RUN_WORKER=false si se despliega un worker independiente.
if [ "${CKAN_RUN_WORKER:-true}" = "true" ]; then
    echo "[ogov] Arrancando worker de tareas (ckan jobs worker)"
    ( while true; do ckan -c "$CKAN_INI" jobs worker default; echo "[ogov] worker reiniciado"; sleep 5; done ) &
fi
