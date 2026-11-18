"""Usuario de las llamadas internas a ``datastore_create`` y ``datastore_delete``.

ckanext-xloader 2.6.0 (``loader.py``) crea y borra las tablas del DataStore con
un contexto ``ignore_auth`` sin usuario (``"user": ""`` o sin clave). Cuando
eso cambia ``datastore_active`` del recurso, CKAN hace un ``resource_patch`` y
ckanext-activity intenta registrar la actividad con ese usuario vacío:
"User not found". Resultado: fallan cargas y **recargas** de recursos (p. ej.
una tabla de transparencia que cambia).

Solo en llamadas internas (``ignore_auth`` y sin usuario) se usa el usuario
del sitio, que es el que ya usa XLoader para el resto de sus operaciones.
"""
from __future__ import annotations

import ckan.plugins.toolkit as tk


def _with_site_user(context: dict) -> None:
    if context.get("ignore_auth") and not context.get("user"):
        site_user = tk.get_action("get_site_user")({"ignore_auth": True}, {})
        context["user"] = site_user["name"]


@tk.chained_action
def datastore_create(original, context, data_dict):
    _with_site_user(context)
    return original(context, data_dict)


@tk.chained_action
def datastore_delete(original, context, data_dict):
    _with_site_user(context)
    return original(context, data_dict)
