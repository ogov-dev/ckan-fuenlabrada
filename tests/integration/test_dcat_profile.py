"""Perfil DCAT-AP-ES (ckanext-ogovtech_fixes) con la cadena de perfiles real."""
from __future__ import annotations

import json

import pytest
from rdflib import URIRef
from rdflib.namespace import RDF, RDFS

from ckan.tests import factories
from ckanext.dcat.processors import RDFParser, RDFSerializer
from ckanext.dcat.profiles import DCAT, DCT

from ckanext.ogovtech_fixes.profiles import DIR3_BASE, THEME_TAXONOMY

PROFILES = ["euro_dcat_ap_2", "euro_dcat_ap_scheming", "dcat_ap_es"]
NTI = "http://datos.gob.es/kos/sector-publico/sector/"
SPA = URIRef("http://publications.europa.eu/resource/authority/language/SPA")
MADRID = URIRef("http://datos.gob.es/recurso/sector-publico/territorio/Provincia/Madrid")
PUBLISHER = URIRef(DIR3_BASE + "L01289999")   # ver docker/ckan/test.ini


def _dataset(**kw):
    org = factories.Organization()
    data = dict(
        owner_org=org["id"], license_id="odc-by",
        theme=[NTI + "medio-ambiente"],
        language=["http://publications.europa.eu/resource/authority/language/SPA"],
        resources=[{"url": "https://example.org/aire.csv", "format": "CSV", "name": "Aire"}],
    )
    data.update(kw)
    return factories.Dataset(**data)


def _graph(dataset):
    s = RDFSerializer(profiles=PROFILES)
    ref = s.graph_from_dataset(dataset)
    return s.g, ref


@pytest.mark.usefixtures("with_plugins", "clean_db", "clean_index")
class TestDataset:
    def test_publicador_dir3_unico(self):
        g, ref = _graph(_dataset())
        assert list(g.objects(ref, DCT.publisher)) == [PUBLISHER]

    def test_tematica_nti_como_uri(self):
        g, ref = _graph(_dataset())
        assert URIRef(NTI + "medio-ambiente") in set(g.objects(ref, DCAT.theme))

    def test_idioma_uri_eu(self):
        g, ref = _graph(_dataset())
        assert SPA in set(g.objects(ref, DCT.language))

    def test_spatial_por_defecto(self):
        g, ref = _graph(_dataset())
        assert list(g.objects(ref, DCT.spatial)) == [MADRID]

    def test_spatial_propio_no_se_duplica(self):
        propio = "http://datos.gob.es/recurso/sector-publico/territorio/Municipio/Fuenlabrada"
        g, ref = _graph(_dataset(spatial_coverage=[{"uri": propio, "text": "Fuenlabrada"}]))
        uris = [o for o in g.objects(ref, DCT.spatial) if isinstance(o, URIRef)]
        assert uris == [URIRef(propio)]

    def test_formato_imt(self):
        g, ref = _graph(_dataset())
        dist = next(g.objects(ref, DCAT.distribution))
        imt = next(g.objects(dist, DCT["format"]))
        assert (imt, RDF.type, DCT.IMT) in g
        assert str(next(g.objects(imt, RDF.value))) == "text/csv"
        assert str(next(g.objects(imt, RDFS.label))) == "CSV"

    def test_licencia_en_dataset_y_distribucion(self):
        odc_by = URIRef("https://opendatacommons.org/licenses/by/1-0/")
        g, ref = _graph(_dataset())
        assert list(g.objects(ref, DCT.license)) == [odc_by]                      # NTI-RISP
        dist = next(g.objects(ref, DCAT.distribution))
        assert list(g.objects(dist, DCT.license)) == [odc_by]                     # DCAT-AP (herencia)

    def test_parseo_no_se_altera(self):
        """dcat_ap_es solo serializa: el ida y vuelta conserva los campos del esquema."""
        s = RDFSerializer(profiles=PROFILES)
        ttl = s.serialize_dataset(_dataset(), _format="turtle")
        p = RDFParser(profiles=PROFILES)
        p.parse(ttl, _format="turtle")
        parsed = next(p.datasets())
        theme = parsed["theme"]   # el perfil scheming lo entrega como JSON, formato que acepta el validador
        assert (json.loads(theme) if isinstance(theme, str) else theme) == [NTI + "medio-ambiente"]
        assert parsed["title"]


@pytest.mark.usefixtures("with_plugins", "clean_db", "clean_index")
class TestCatalogo:
    def _catalog(self):
        s = RDFSerializer(profiles=PROFILES)
        ref = s.graph_from_catalog({})
        return s.g, ref

    def test_propiedades_obligatorias_datos_gob_es(self):
        g, ref = self._catalog()
        assert list(g.objects(ref, DCT.publisher)) == [PUBLISHER]
        assert list(g.objects(ref, DCAT.themeTaxonomy)) == [URIRef(THEME_TAXONOMY)]
        assert list(g.objects(ref, DCT.spatial)) == [MADRID]
        assert list(g.objects(ref, DCT.language)) == [SPA]
        assert list(g.objects(ref, DCT.license)) == [URIRef("https://creativecommons.org/licenses/by/4.0/")]
        assert list(g.objects(ref, DCT.issued))

    def test_endpoint_catalogo(self, app):
        _dataset(name="aire")
        res = app.get("/catalog.ttl")
        assert res.status_code == 200
        assert "dcat:themeTaxonomy" in res.body and "aire" in res.body


def test_orden_de_perfiles_configurado(ckan_config):
    profiles = ckan_config["ckanext.dcat.rdf.profiles"]
    assert (profiles.split() if isinstance(profiles, str) else list(profiles)) == PROFILES


def test_dcat_antes_que_scheming(ckan_config):
    plugins = ckan_config["ckan.plugins"]
    plugins = plugins.split() if isinstance(plugins, str) else list(plugins)
    assert plugins.index("dcat") < plugins.index("scheming_datasets")
