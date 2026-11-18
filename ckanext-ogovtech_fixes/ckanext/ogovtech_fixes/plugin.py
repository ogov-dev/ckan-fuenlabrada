"""Contenedor único del código propio de la baseline oGov.tech.

* Declara las opciones de federación (DIR3, ámbito territorial, licencia
  del catálogo) que cada implantación rellena por variables de entorno.
* Registra el perfil RDF ``dcat_ap_es`` (ver ``profiles.py`` y setup.py).
* Quita los bloques repetibles vacíos al guardar (``composites.py``).
* Corrige el usuario vacío de XLoader al recargar recursos (``internal_user.py``).
* Aporta el esquema común de metadatos (``schemas/dataset.yaml``) y la
  lista de licencias (``schemas/licenses.json``).
"""
import ckan.plugins as p
import ckan.plugins.toolkit as tk

from ckanext.ogovtech_fixes import composites, internal_user
from ckanext.ogovtech_fixes.profiles import publisher_uri


@tk.blanket.config_declarations
class OgovtechFixesPlugin(p.SingletonPlugin):
    p.implements(p.ITemplateHelpers)
    p.implements(p.IActions)

    def get_helpers(self):
        return {"ogovtech_publisher_uri": publisher_uri}

    # IActions: sin bloques repetibles vacíos (ver composites.py)
    def get_actions(self):
        actions = {"package_create": composites.package_create,
                   "package_update": composites.package_update}
        if p.plugin_loaded("datastore"):
            # Recargas de XLoader con ckanext-activity (ver internal_user.py)
            actions["datastore_create"] = internal_user.datastore_create
            actions["datastore_delete"] = internal_user.datastore_delete
        return actions
