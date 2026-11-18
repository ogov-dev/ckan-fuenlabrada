"""Bloques repetibles vacíos (ckanext-scheming).

El formulario siempre envía un primer bloque de cada campo repetible
(cobertura geográfica, punto de contacto…) aunque se deje en blanco, y
scheming lo guarda tal cual: ``[{"uri": "", "text": ""}]``. Aquí se quitan
esos bloques antes de validar, tanto si llegan anidados (API, formulario ya
desplegado) como con claves planas (``campo-0-subcampo``).
"""
from __future__ import annotations

import re
from typing import Any

import ckan.plugins.toolkit as tk


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def composite_fields(dataset_type: str) -> list[str]:
    try:
        schema = tk.h.scheming_get_dataset_schema(dataset_type or "dataset")
    except Exception:  # noqa: BLE001  (sin scheming o tipo desconocido)
        return []
    return [f["field_name"] for f in (schema or {}).get("dataset_fields", [])
            if "repeating_subfields" in f]


def drop_empty_blocks(data_dict: dict, fields: list[str]) -> dict:
    """Quita de ``data_dict`` (en su sitio) los bloques cuyos subcampos están vacíos."""
    if not fields:
        return data_dict
    for name in fields:
        value = data_dict.get(name)
        if isinstance(value, list):
            data_dict[name] = [b for b in value
                               if not isinstance(b, dict) or not all(_blank(v) for v in b.values())]
    flat = re.compile(r"^(%s)-([^-]+)-(\w+)$" % "|".join(map(re.escape, fields)))
    blocks: dict[tuple[str, str], list[str]] = {}
    for key in list(data_dict):
        m = flat.match(key) if isinstance(key, str) else None
        if m:
            blocks.setdefault((m.group(1), m.group(2)), []).append(key)
    for keys in blocks.values():
        if all(_blank(data_dict[k]) for k in keys):
            for k in keys:
                del data_dict[k]
    return data_dict


def _chained(action_name: str):
    @tk.chained_action
    def action(original, context, data_dict):
        drop_empty_blocks(data_dict, composite_fields(data_dict.get("type") or "dataset"))
        return original(context, data_dict)
    action.__name__ = action_name
    return action


package_create = _chained("package_create")
package_update = _chained("package_update")
