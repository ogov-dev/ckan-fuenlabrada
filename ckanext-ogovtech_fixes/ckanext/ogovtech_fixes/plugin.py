"""Contenedor único del código propio de la baseline oGov.tech.

* Declara las opciones de federación (DIR3, ámbito territorial, licencia
  del catálogo) que cada implantación rellena por variables de entorno.
* Registra el perfil RDF ``dcat_ap_es`` (ver ``profiles.py`` y setup.py).
* Aporta el esquema común de metadatos (``schemas/dataset.yaml``) y la
  lista de licencias (``schemas/licenses.json``).
"""
import ckan.plugins as p
import ckan.plugins.toolkit as tk

from ckanext.ogovtech_fixes.profiles import publisher_uri


@tk.blanket.config_declarations
class OgovtechFixesPlugin(p.SingletonPlugin):
    p.implements(p.ITemplateHelpers)

    def get_helpers(self):
        return {"ogovtech_publisher_uri": publisher_uri}
