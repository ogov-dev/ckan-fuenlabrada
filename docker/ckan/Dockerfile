# Baseline CKAN oGov.tech
# Todas las versiones están fijadas: ver "Versiones fijadas" en el README.
# Etiqueta exacta + digest: la imagen base no puede cambiar sin un commit.
ARG CKAN_IMAGE=ckan/ckan-base:2.12.1@sha256:da036b38a31f1e35c87ba1b099dd78d53c02e6585beafdac2cfe963571499c1c
FROM ${CKAN_IMAGE} AS baseline

ARG SCHEMING_REF=release-3.1.0
ARG DCAT_VERSION=2.4.5
ARG XLOADER_VERSION=2.6.0
ARG PAGES_REF=28eba99ad85d3b9037f5aaddad1f083300c69041

USER root

# --- Extensiones de comunidad (conjunto cerrado) ---------------------------
# Las que no están en PyPI se descargan de GitHub (revisión fija) y se instalan
# en modo editable: su paquete no incluye todos los ficheros de datos.
RUN for ext in "ckanext-scheming refs/tags/${SCHEMING_REF}" "ckanext-pages ${PAGES_REF}"; do \
        set -- $ext; mkdir -p ${SRC_DIR}/$1; \
        curl -fsSL "https://github.com/ckan/$1/archive/$2.tar.gz" | tar xz --strip-components=1 -C ${SRC_DIR}/$1; \
        pip install --no-cache-dir -e ${SRC_DIR}/$1; \
    done \
 && pip install --no-cache-dir \
        "ckanext-dcat==${DCAT_VERSION}" \
        "ckanext-xloader==${XLOADER_VERSION}"

# --- Extensiones propias oGov.tech -------------------------------------------
COPY ckanext-ogovtech_theme ${SRC_DIR}/ckanext-ogovtech_theme
COPY ckanext-ogovtech_fixes ${SRC_DIR}/ckanext-ogovtech_fixes
RUN pip install --no-cache-dir -e ${SRC_DIR}/ckanext-ogovtech_theme \
 && pip install --no-cache-dir -e ${SRC_DIR}/ckanext-ogovtech_fixes

# --- Herramientas: migración estándar y conectores ---------------------------
# Disponibles en todos los entornos (no solo en desarrollo), con dependencias
# fijadas. Los volcados e informes se escriben en el almacenamiento persistente
# ($OGOV_TOOLS_DATA_DIR, dentro del volumen de /var/lib/ckan), nunca en la imagen.
COPY tools-requirements.txt /srv/app/tools-requirements.txt
RUN pip install --no-cache-dir -r /srv/app/tools-requirements.txt
COPY migration /srv/app/migration
COPY connectors /srv/app/connectors
ENV OGOV_TOOLS_DATA_DIR=/var/lib/ckan/ogov-tools

# Scripts de arranque (se ejecutan tras prerun.py, antes de uwsgi)
COPY docker/ckan/docker-entrypoint.d/ /docker-entrypoint.d/

# --- Configuración común de la baseline (idéntica en todos los portales) ----
# ckan.plugins lo escribe prerun.py a partir de esta variable.
# Orden (documentación oficial de ckanext-dcat 2.4.5, apdo. "Schemas"):
#   - ogovtech_theme primero: sus plantillas tienen prioridad.
#   - dcat ANTES que scheming_datasets.
#   - envvars al final (documentación de ckanext-envvars).
ENV CKAN__PLUGINS="ogovtech_theme ogovtech_fixes activity stats dcat scheming_datasets pages datastore xloader datatables_view text_view image_view envvars"

# Perfiles RDF: euro_dcat_ap_2 porque DCAT-AP-ES 1.0 se basa en DCAT-AP 2.1.1;
# euro_dcat_ap_scheming tras él (docs oficiales); dcat_ap_es (propio) el último
# porque solo ajusta la SERIALIZACIÓN (no redefine parse_dataset, así que no
# altera la importación) y necesita ver el grafo ya completo: p. ej. solo añade
# el dct:spatial por defecto si el dataset no trae uno propio.
# Resto de opciones comunes: se fijan en ckan.ini durante la construcción.
# Las opciones propias de cada ayuntamiento (URL, DIR3, credenciales…)
# llegan por variables de entorno (ver docker/.env.example).
RUN ckan config-tool ${CKAN_INI} \
        "ckan.views.default_views=datatables_view text_view image_view" \
        "ckan.locale_default=es" \
        "ckan.locales_offered=es en" \
        "ckan.auth.public_user_details=false" \
        "ckan.max_resource_size=100" \
        "licenses_group_url=file://${SRC_DIR}/ckanext-ogovtech_fixes/ckanext/ogovtech_fixes/schemas/licenses.json" \
        "scheming.dataset_schemas=ckanext.ogovtech_fixes:schemas/dataset.yaml" \
        "scheming.presets=ckanext.scheming:presets.json ckanext.dcat.schemas:presets.yaml" \
        "scheming.dataset_fallback=false" \
        "ckanext.dcat.rdf.profiles=euro_dcat_ap_2 euro_dcat_ap_scheming dcat_ap_es" \
        "ckanext.dcat.enable_content_negotiation=true" \
        "ckanext.dcat.resource.inherit.license=true" \
        "ckanext.pages.allow_html=false" \
        "ckanext.pages.editor=ckeditor"

COPY --chmod=0755 docker/ckan/ogov-start.sh /srv/app/ogov-start.sh
RUN chown ckan:ckan-sys ${CKAN_INI}
USER ckan
CMD ["/srv/app/ogov-start.sh"]

# =============================================================================
# Etapa de pruebas (no se despliega): baseline + herramientas de test.
#   docker compose -f tests/docker-compose.yml run --rm tests
# =============================================================================
FROM baseline AS test
USER root
RUN pip install --no-cache-dir -r ${SRC_DIR}/ckan/dev-requirements.txt
COPY tests/federation/requirements.txt /tmp/federation-requirements.txt
RUN pip install --no-cache-dir -r /tmp/federation-requirements.txt
COPY pytest.ini /srv/app/pytest.ini
COPY tests /srv/app/tests
COPY docker/ckan/test.ini /srv/app/test.ini
COPY docker/ckan/Dockerfile /srv/app/docker/ckan/Dockerfile
RUN chown -R ckan:ckan-sys /srv/app/tests
USER ckan
WORKDIR /srv/app
