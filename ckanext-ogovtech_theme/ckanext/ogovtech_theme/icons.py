"""Iconos de temáticas/grupos (Heroicons outline, MIT — ver icons/LICENSE).

Mismo estilo que los "ámbitos" de los portales de transparencia de oGov.
El icono se elige por el nombre (slug) del grupo, de modo que funciona igual
en todos los ayuntamientos sin configurar nada. Si un grupo tiene imagen
propia subida en CKAN, la plantilla usa esa imagen en su lugar.
"""
from __future__ import annotations

import os
import unicodedata
from functools import lru_cache

from markupsafe import Markup

_DIR = os.path.join(os.path.dirname(__file__), "icons")

# (fragmento del slug, icono). Se recorre en orden: lo más específico primero.
RULES: list[tuple[str, str]] = [
    ("participacion", "chat-bubble-left-right"),
    ("urbanismo-infraestructura", "building-office-2"),
    ("infraestructura", "building-office-2"),
    ("urbanismo", "map"),
    ("ciencia", "beaker"),
    ("tecnologia", "beaker"),
    ("comercio", "shopping-bag"),
    ("cultura", "ticket"),
    ("ocio", "ticket"),
    ("demografia", "users"),
    ("poblacion", "users"),
    ("deporte", "trophy"),
    ("economia", "chart-bar"),
    ("educacion", "academic-cap"),
    ("empleo", "briefcase"),
    ("energia", "bolt"),
    ("hacienda", "banknotes"),
    ("presupuesto", "banknotes"),
    ("industria", "wrench-screwdriver"),
    ("legislacion", "scale"),
    ("justicia", "scale"),
    ("medio-rural", "sun"),
    ("pesca", "sun"),
    ("medio-ambiente", "globe-europe-africa"),
    ("salud", "heart"),
    ("sector-publico", "building-library"),
    ("seguridad", "shield-check"),
    ("sociedad", "user-group"),
    ("bienestar", "user-group"),
    ("transporte", "truck"),
    ("movilidad", "truck"),
    ("turismo", "camera"),
    ("vivienda", "home"),
]
DEFAULT = "folder"


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return value.lower().replace(" ", "-").replace("_", "-")


def icon_name_for(group_name: str) -> str:
    slug = _norm(group_name)
    for fragment, icon in RULES:
        if fragment in slug:
            return icon
    return DEFAULT


@lru_cache(maxsize=64)
def _svg(icon: str) -> str:
    with open(os.path.join(_DIR, icon + ".svg"), encoding="utf-8") as f:
        return f.read().strip()


def ogovtech_group_icon(group_name: str, css_class: str = "ogov-icon") -> Markup:
    svg = _svg(icon_name_for(group_name))
    return Markup(svg.replace("<svg ", f'<svg class="{css_class}" ', 1))
