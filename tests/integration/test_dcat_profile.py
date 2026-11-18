"""Perfil DCAT-AP-ES (ckanext-ogovtech_fixes) con la cadena de perfiles real."""
from __future__ import annotations

import json

import pytest
from rdflib import URIRef
from rdflib.namespace import RDF, RDFS

from ckan.tests import factories
from ckanext.dcat.processors import RDFParser, RDFSerializer
from ckanext.dcat.profiles import DCAT, DCT, SPDX, VCARD, XSD

from ckanext.ogovtech_fixes.profiles import DIR3_BASE, THEME_TAXONOMY, clean_mime

EU_FT = "http://publications.europa.eu/resource/authority/file-type/"
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
        propio = "http://publications.europa.eu/resource/authority/country/ESP"
        g, ref = _graph(_dataset(spatial_coverage=[{"uri": propio, "text": "Fuenlabrada"}]))
        uris = [o for o in g.objects(ref, DCT.spatial) if isinstance(o, URIRef)]
        assert uris == [URIRef(propio)]

    def test_territorio_no_admitido_se_sustituye_por_el_del_portal(self):
        municipio = URIRef("http://datos.gob.es/recurso/sector-publico/territorio/Municipio/Fuenlabrada")
        g, ref = _graph(_dataset(spatial_coverage=[{"uri": str(municipio)}]))
        assert list(g.objects(ref, DCT.spatial)) == [MADRID]
        assert not list(g.predicate_objects(municipio))

    def test_territorio_por_uri_no_es_location_sin_geometria(self):
        uri = "http://datos.gob.es/recurso/sector-publico/territorio/Autonomia/Comunidad-Madrid"
        g, ref = _graph(_dataset(spatial_coverage=[{"uri": uri}]))
        assert (URIRef(uri), RDF.type, DCT.Location) not in g
        assert URIRef(uri) in set(g.objects(ref, DCT.spatial))

    def test_formato_vocabulario_europeo(self):
        g, ref = _graph(_dataset())
        dist = next(g.objects(ref, DCAT.distribution))
        assert list(g.objects(dist, DCT["format"])) == [URIRef(EU_FT + "CSV")]
        assert not list(g.subjects(RDF.type, DCT.IMT))                     # nada de IMT (NTI-RISP)

    def test_formato_sin_codigo_europeo_se_omite(self):
        g, ref = _graph(_dataset(resources=[{"url": "https://e.org/a.prj", "format": "PRJ", "name": "Proyección"}]))
        dist = next(g.objects(ref, DCAT.distribution))
        assert not list(g.objects(dist, DCT["format"]))

    def test_textos_con_etiqueta_es(self):
        ds = _dataset(title="Calidad del aire", notes="Mediciones", tags=[{"name": "aire"}])
        g, ref = _graph(ds)
        for p in (DCT.title, DCT.description, DCAT.keyword):
            vals = list(g.objects(ref, p))
            assert vals and all(v.language == "es" for v in vals), p
        dist = next(g.objects(ref, DCAT.distribution))
        assert all(v.language == "es" for v in g.objects(dist, DCT.title))

    def test_checksum_solo_sha1(self):
        sha1 = "a" * 40
        ds = _dataset(resources=[{"url": "https://e.org/a.csv", "format": "CSV", "name": "A", "hash": sha1},
                                 {"url": "https://e.org/b.csv", "format": "CSV", "name": "B", "hash": "b" * 32}])
        g, ref = _graph(ds)
        checksums = list(g.subjects(SPDX.checksumValue, None))
        assert len(checksums) == 1
        assert g.value(checksums[0], SPDX.algorithm) == URIRef("http://spdx.org/rdf/terms#checksumAlgorithm_sha1")
        assert g.value(checksums[0], SPDX.checksumValue).datatype == XSD.hexBinary

    def test_recurso_sin_url_no_se_federa(self):
        ds = _dataset(resources=[{"url": "https://e.org/a.csv", "format": "CSV", "name": "A"},
                                 {"url": "", "format": "CSV", "name": "Sin URL"}])
        g, ref = _graph(ds)
        dists = list(g.objects(ref, DCAT.distribution))
        assert len(dists) == 1 and all(list(g.objects(d, DCAT.accessURL)) for d in dists)

    def test_contacto_sin_correo_se_omite(self):
        g, ref = _graph(_dataset(contact=[{"name": "Servicio de datos"}]))
        assert not list(g.objects(ref, DCAT.contactPoint))

    @pytest.mark.ckan_config("ckanext.ogovtech_theme.contact_email", "datos@ayto.es")
    def test_contacto_sin_correo_usa_el_del_portal(self):
        g, ref = _graph(_dataset(contact=[{"name": "Servicio de datos"}]))
        cp = next(g.objects(ref, DCAT.contactPoint))
        assert list(g.objects(cp, VCARD.hasEmail)) == [URIRef("mailto:datos@ayto.es")]
        assert all(v.language == "es" for v in g.objects(cp, VCARD.fn))

    def test_mime_con_parametros_y_api(self):
        ds = _dataset(resources=[
            {"url": "https://example.org/a.csv", "format": "CSV", "name": "A", "mimetype": "text/csv; charset=utf-8"},
            {"url": "https://api.example.org/v1", "format": "API", "name": "B"},
        ])
        g, ref = _graph(ds)
        media = {str(o) for o in g.objects(None, DCAT.mediaType)}
        assert media == {"https://www.iana.org/assignments/media-types/text/csv"}
        assert all(" " not in m and ";" not in m for m in media)

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

    @pytest.mark.ckan_config("ckan.site_description", "")
    def test_descripcion_del_catalogo_siempre_presente(self):
        g, ref = self._catalog()
        descs = list(g.objects(ref, DCT.description))
        assert len(descs) == 1 and descs[0].language == "es"

    def test_endpoint_catalogo(self, app):
        _dataset(name="aire")
        res = app.get("/catalog.ttl")
        assert res.status_code == 200
        assert "dcat:themeTaxonomy" in res.body and "aire" in res.body
        assert "<http://datos.gob.es/kos/sector-publico/sector>" in res.body


def test_orden_de_perfiles_configurado(ckan_config):
    profiles = ckan_config["ckanext.dcat.rdf.profiles"]
    assert (profiles.split() if isinstance(profiles, str) else list(profiles)) == PROFILES


def test_dcat_antes_que_scheming(ckan_config):
    plugins = ckan_config["ckan.plugins"]
    plugins = plugins.split() if isinstance(plugins, str) else list(plugins)
    assert plugins.index("dcat") < plugins.index("scheming_datasets")


@pytest.mark.parametrize("entrada,salida", [
    ("text/csv", "text/csv"), ("text/csv; charset=utf-8", "text/csv"), ("Application/JSON", "application/json"),
    ("", None), (None, None), ("no es mime", None), ("text/ html", None),
])
def test_clean_mime(entrada, salida):
    assert clean_mime(entrada) == salida
