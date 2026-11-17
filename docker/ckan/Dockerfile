# Baseline CKAN oGov.tech
# Todas las versiones están fijadas: ver VERSIONS.md en la raíz del repositorio.
ARG CKAN_IMAGE=ckan/ckan-base:2.12.1
FROM ${CKAN_IMAGE}

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

# Scripts de arranque (se ejecutan tras prerun.py, antes de uwsgi)
COPY docker/ckan/docker-entrypoint.d/ /docker-entrypoint.d/

# --- Configuración común de la baseline (idéntica en todos los portales) ----
# ckan.plugins lo escribe prerun.py a partir de esta variable.
ENV CKAN__PLUGINS="ogovtech_theme ogovtech_fixes activity stats scheming_datasets dcat pages datastore xloader datatables_view text_view image_view envvars"

# Resto de opciones comunes: se fijan en ckan.ini durante la construcción.
# Las opciones propias de cada ayuntamiento (URL, DIR3, credenciales…)
# llegan por variables de entorno (ver docker/.env.example).
RUN ckan config-tool ${CKAN_INI} \
        "ckan.views.default_views=datatables_view text_view image_view" \
        "ckan.locale_default=es" \
        "ckan.locales_offered=es en" \
        "ckan.auth.public_user_details=false" \
        "licenses_group_url=file://${SRC_DIR}/ckanext-ogovtech_fixes/ckanext/ogovtech_fixes/schemas/licenses.json" \
        "scheming.dataset_schemas=ckanext.ogovtech_fixes:schemas/dataset.yaml" \
        "scheming.presets=ckanext.scheming:presets.json ckanext.dcat.schemas:presets.yaml" \
        "scheming.dataset_fallback=false" \
        "ckanext.dcat.rdf.profiles=euro_dcat_ap_2 euro_dcat_ap_scheming dcat_ap_es" \
        "ckanext.dcat.enable_content_negotiation=true" \
        "ckanext.pages.allow_html=false" \
        "ckanext.pages.editor=ckeditor"

RUN chown ckan:ckan-sys ${CKAN_INI}
USER ckan
