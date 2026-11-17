"""Tema visual común de oGov.tech.

Solo encapsula la capa de presentación. Lo único configurable por
ayuntamiento (desde /ckan-admin/config) es:

* logotipo (opción nativa ``ckan.site_logo``),
* colores corporativos,
* textos básicos (título, lema, texto de portada y texto del pie),
* enlaces del pie de página y datos de contacto.

No se añaden maquetaciones por ayuntamiento ni se toca el core.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import ckan.plugins as p
import ckan.plugins.toolkit as tk

from ckanext.ogovtech_theme.icons import ogovtech_group_icon

log = logging.getLogger(__name__)

PREFIX = "ckanext.ogovtech_theme."

COLOR_KEYS = ("color_primary", "color_secondary", "color_accent", "color_text_on_primary")
TEXT_KEYS = (
    "hero_image", "footer_text", "footer_links", "footer_legal_links",
    "contact_email", "contact_phone", "contact_address",
)
EDITABLE_KEYS = COLOR_KEYS + TEXT_KEYS

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


def hex_color(value: Any, context: Any) -> Any:
    """Validador: acepta vacío o un color hexadecimal (#rgb / #rrggbb)."""
    if value in (None, ""):
        return value
    value = str(value).strip()
    if not _HEX.match(value):
        raise tk.Invalid(tk._("Introduce un color hexadecimal, p. ej. #004a8f"))
    return value.lower()


def footer_links_validator(value: Any, context: Any) -> Any:
    """Valida que cada línea sea ``Texto|URL``."""
    if not value:
        return value
    for n, line in enumerate(str(value).splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        if "|" not in line:
            raise tk.Invalid(
                tk._("Línea {n}: usa el formato Texto|URL").format(n=n)
            )
        url = line.split("|", 1)[1].strip()
        if not (url.startswith(("http://", "https://", "/", "mailto:"))):
            raise tk.Invalid(
                tk._("Línea {n}: la URL debe empezar por http(s)://, / o mailto:").format(n=n)
            )
    return value


_SAFE_URL = re.compile(r"^(https?://|/)[^\s'\"()<>\\]+$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def safe_url(value: Any, context: Any) -> Any:
    """URL http(s) o ruta local, sin caracteres que permitan salir de un
    atributo HTML o de ``url()`` en CSS (se usa en el estilo del hero)."""
    if value in (None, ""):
        return value
    value = str(value).strip()
    if not _SAFE_URL.match(value):
        raise tk.Invalid(tk._("Introduce una URL http(s):// o una ruta que empiece por /, sin espacios ni comillas"))
    return value


def email(value: Any, context: Any) -> Any:
    if value in (None, ""):
        return value
    value = str(value).strip()
    if not _EMAIL.match(value):
        raise tk.Invalid(tk._("Introduce un correo electrónico válido"))
    return value


# --- helpers -----------------------------------------------------------------

def ogovtech_theme_get(key: str, default: str = "") -> str:
    return tk.config.get(PREFIX + key) or default


def ogovtech_theme_colors() -> dict[str, str]:
    return {k: tk.config.get(PREFIX + k) for k in COLOR_KEYS}


def ogovtech_theme_footer_links(kind: str = "footer_links") -> list[dict[str, str]]:
    raw = tk.config.get(PREFIX + kind) or ""
    links = []
    for line in raw.splitlines():
        if "|" in line:
            text, url = (part.strip() for part in line.split("|", 1))
            if text and url:
                links.append({"text": text, "url": url})
    return links


def ogovtech_theme_home() -> dict[str, Any]:
    """Datos para la portada: cifras, temáticas (grupos) y últimos datasets."""
    ctx: dict[str, Any] = {}
    act = tk.get_action
    try:
        res = act("package_search")(dict(ctx), {
            "q": "*:*", "rows": 6, "sort": "metadata_modified desc",
            "facet.field": ["res_format"], "facet.limit": -1})
        datasets = res["results"]
        stats = {"dataset_count": res["count"]}
        formats = len(res["search_facets"].get("res_format", {}).get("items", []))
    except Exception:  # p. ej. Solr caído: la portada no debe romperse, pero se registra
        log.exception("ogovtech_theme: no se pudieron obtener los datasets de la portada")
        datasets, stats, formats = [], {"dataset_count": 0}, 0
    try:
        groups = act("group_list")(dict(ctx), {"all_fields": True, "sort": "title asc", "limit": 30})
        stats["group_count"] = len(act("group_list")(dict(ctx), {}))
        stats["organization_count"] = len(act("organization_list")(dict(ctx), {}))
    except Exception:
        log.exception("ogovtech_theme: no se pudieron obtener grupos/organizaciones de la portada")
        groups = []
        stats.setdefault("group_count", 0)
        stats.setdefault("organization_count", 0)
    return {"stats": stats, "datasets": datasets, "groups": groups, "formats": formats}


@tk.blanket.config_declarations
class OgovtechThemePlugin(p.SingletonPlugin):
    p.implements(p.IConfigurer)
    p.implements(p.ITemplateHelpers)
    p.implements(p.IValidators)

    # IConfigurer
    def update_config(self, config: Any) -> None:
        tk.add_template_directory(config, "templates")
        tk.add_public_directory(config, "public")
        tk.add_resource("assets", "ogovtech_theme")

    def update_config_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        ignore_missing = tk.get_validator("ignore_missing")
        unicode_safe = tk.get_validator("unicode_safe")
        for key in COLOR_KEYS:
            schema[PREFIX + key] = [ignore_missing, unicode_safe, hex_color]
        for key in TEXT_KEYS:
            schema[PREFIX + key] = [ignore_missing, unicode_safe]
        schema[PREFIX + "footer_links"].append(footer_links_validator)
        schema[PREFIX + "footer_legal_links"].append(footer_links_validator)
        schema[PREFIX + "hero_image"].append(safe_url)
        schema[PREFIX + "contact_email"].append(email)
        return schema

    # ITemplateHelpers
    def get_helpers(self) -> dict[str, Any]:
        return {
            "ogovtech_theme_get": ogovtech_theme_get,
            "ogovtech_theme_colors": ogovtech_theme_colors,
            "ogovtech_theme_footer_links": ogovtech_theme_footer_links,
            "ogovtech_theme_home": ogovtech_theme_home,
            "ogovtech_group_icon": ogovtech_group_icon,
        }

    # IValidators
    def get_validators(self) -> dict[str, Any]:
        return {
            "ogovtech_hex_color": hex_color,
            "ogovtech_footer_links": footer_links_validator,
            "ogovtech_safe_url": safe_url,
            "ogovtech_email": email,
        }

