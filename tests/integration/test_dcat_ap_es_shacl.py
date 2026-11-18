"""Conformidad con las formas SHACL oficiales de DCAT-AP-ES (datos.gob.es).

El catálogo que genera CKAN con la cadena de perfiles real no debe tener
ninguna violación (los avisos de propiedades recomendadas se permiten, como en
el propio repositorio oficial).
"""
import sys
from pathlib import Path

import pytest
from rdflib import Graph

from ckan.tests import factories
from ckanext.dcat.processors import RDFSerializer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "federation"))
import validate_dcat_ap_es as shacl  # noqa: E402

PROFILES = ["euro_dcat_ap_2", "euro_dcat_ap_scheming", "dcat_ap_es"]
NTI = "http://datos.gob.es/kos/sector-publico/sector/"
SPA = "http://publications.europa.eu/resource/authority/language/SPA"


@pytest.fixture(scope="module")
def shapes():
    return shacl.shapes_graph()


def _catalog_graph(datasets):
    s = RDFSerializer(profiles=PROFILES)
    return Graph().parse(data=s.serialize_catalog({}, dataset_dicts=datasets, _format="turtle"), format="turtle")


@pytest.mark.usefixtures("with_plugins", "clean_db", "clean_index")
@pytest.mark.ckan_config("ckanext.ogovtech_theme.contact_email", "datos@ayto.es")
@pytest.mark.ckan_config("ckanext.ogovtech.catalog_issued", "")        # instalación mínima
@pytest.mark.ckan_config("ckan.site_description", "")
def test_catalogo_representativo_sin_violaciones(shapes):
    org = factories.Organization()
    common = dict(owner_org=org["id"], license_id="odc-by", language=[SPA])
    datasets = [
        factories.Dataset(**common, title="Municipio (se sustituye)", theme=[NTI + "economia"],
                          spatial_coverage=[{"uri": "http://datos.gob.es/recurso/sector-publico/territorio/Municipio/Fuenlabrada"}],
                          resources=[{"url": "https://e.org/m.csv", "format": "CSV", "name": "Datos"}]),
        factories.Dataset(**common, title="Mínimo", theme=[NTI + "economia"],
                          resources=[{"url": "https://e.org/a.csv", "format": "CSV", "name": "Datos"}]),
        factories.Dataset(**common, title="Completo", theme=[NTI + "medio-ambiente", NTI + "salud"],
                          tags=[{"name": "aire"}, {"name": "calidad"}],
                          frequency="http://publications.europa.eu/resource/authority/frequency/DAILY",
                          temporal_coverage=[{"start": "2020-01-01", "end": "2025-12-31"}],
                          spatial_coverage=[{"uri": "http://publications.europa.eu/resource/authority/country/ESP"}],
                          contact=[{"name": "Servicio de datos"}],
                          resources=[
                              {"url": "https://e.org/a.xlsx", "format": "XLSX", "name": "Hoja", "description": "Año"},
                              {"url": "https://e.org/a.prj", "format": "PRJ", "name": "Proyección"},
                              {"url": "https://e.org/a.json", "format": "JSON", "name": "API", "hash": "c" * 40},
                              {"url": "https://e.org/b.csv", "format": "CSV", "name": "MD5", "hash": "d" * 32},
                              {"url": "", "format": "CSV", "name": "Sin URL"},
                          ]),
    ]
    violations, warnings, details = shacl.run(_catalog_graph(datasets), shapes)
    assert violations == 0, "\n".join(f"{k} x{v}" for k, v in details.items() if k[0] == "Violation")


def test_el_validador_reproduce_los_ejemplos_oficiales(shapes):
    for name, expect in shacl.EXAMPLES.items():
        data = Graph().parse(shacl.cached(f"examples/ttl/1.0.0/{name}"), format="turtle")
        v, w, _ = shacl.run(data, shapes)
        assert v == 0 and (w == 0 if expect == "conformant" else w > 0), name
