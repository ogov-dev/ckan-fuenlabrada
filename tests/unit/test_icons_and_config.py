"""Iconos de grupos y coherencia de la configuración de la baseline (sin CKAN)."""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "src" / "ckanext-ogovtech_theme" if (ROOT / "src").exists() else None


def _theme_pkg():
    import ckanext.ogovtech_theme.icons as icons
    return icons


NTI_SECTORS = ["ciencia-tecnologia", "comercio", "cultura-ocio", "demografia", "deporte", "economia",
               "educacion", "empleo", "energia", "hacienda", "industria", "legislacion-justicia",
               "medio-ambiente", "medio-rural-pesca", "salud", "sector-publico", "seguridad",
               "sociedad-bienestar", "transporte", "turismo", "urbanismo-infraestructuras", "vivienda"]


@pytest.mark.parametrize("sector", NTI_SECTORS)
def test_cada_sector_nti_tiene_icono_propio(sector):
    icons = _theme_pkg()
    assert icons.icon_name_for(sector) != icons.DEFAULT


@pytest.mark.parametrize("grupo,icono", [
    ("cultura-y-ocio", "ticket"), ("Participación ciudadana", "chat-bubble-left-right"),
    ("urbanismo", "map"), ("infraestructuras", "building-office-2"), ("algo-raro", "folder"),
])
def test_grupos_de_fuenlabrada(grupo, icono):
    assert _theme_pkg().icon_name_for(grupo) == icono


def test_todos_los_iconos_referenciados_existen():
    icons = _theme_pkg()
    for name in {i for _, i in icons.RULES} | {icons.DEFAULT}:
        svg = icons._svg(name)
        assert svg.startswith("<svg") and 'stroke="currentColor"' in svg


def test_icono_es_markup_con_clase():
    out = _theme_pkg().ogovtech_group_icon("economia", "x")
    assert str(out).startswith('<svg class="x"') and hasattr(out, "__html__")


def _dockerfile() -> str:
    for p in (ROOT / "docker" / "ckan" / "Dockerfile",):
        if p.exists():
            return p.read_text()
    pytest.skip("Dockerfile no disponible en este entorno")


def test_plugins_dcat_antes_que_scheming_en_imagen():
    plugins = re.search(r'CKAN__PLUGINS="([^"]+)"', _dockerfile()).group(1).split()
    assert plugins.index("dcat") < plugins.index("scheming_datasets")
    assert plugins[0] == "ogovtech_theme" and plugins[-1] == "envvars"


def test_perfiles_rdf_en_imagen():
    m = re.search(r'ckanext\.dcat\.rdf\.profiles=([^"]+)"', _dockerfile())
    assert m.group(1).split() == ["euro_dcat_ap_2", "euro_dcat_ap_scheming", "dcat_ap_es"]


def test_mapeo_migracion_fuenlabrada_es_coherente():
    cfg = yaml.safe_load((ROOT / "migration" / "portales" / "fuenlabrada.yml").read_text())
    assert set(cfg["theme_by_group"].values()) <= set(NTI_SECTORS)
    assert cfg["theme_default"] in NTI_SECTORS
    assert cfg["organization"]["name"] == "ayuntamiento-de-fuenlabrada"
    assert cfg["licenses"]["replace"] == {"uk-ogl": "odc-by"}
