"""Perfil DCAT-AP-ES (compatible NTI-RISP) para la federación con datos.gob.es.

Se ejecuta DESPUÉS de ``euro_dcat_ap_2`` y ``euro_dcat_ap_scheming``
(ver ``ckanext.dcat.rdf.profiles``) y solo añade o sustituye lo que
datos.gob.es exige y CKAN no genera por sí mismo:

Catálogo
  * ``dct:publisher``  -> URI DIR3 del ayuntamiento.
  * ``dct:spatial``    -> URI de territorio de datos.gob.es.
  * ``dcat:themeTaxonomy`` -> taxonomía de sectores de datos.gob.es.
  * ``dct:license``, ``dct:issued``, ``dct:identifier``.
  * ``dct:language``   -> URIs de la EU Publications Office.

Conjunto de datos
  * ``dct:publisher``  -> el mismo DIR3 (si el dataset no trae uno propio).
  * ``dct:license``    -> URL de la licencia (las distribuciones la heredan con
    ``ckanext.dcat.resource.inherit.license = true``).
  * ``dct:spatial``    -> el del catálogo si el dataset no indica otro.
  * ``dct:language``   -> por defecto español si no se ha indicado.

Distribución
  * ``dct:format``     -> nodo ``dct:IMT`` con ``rdf:value`` (MIME) y ``rdfs:label``.
"""
from __future__ import annotations

import mimetypes

from rdflib import BNode, Literal, URIRef

import ckan.plugins.toolkit as tk
from ckanext.dcat.profiles import DCAT, DCT, RDF, RDFS, RDFProfile
from ckanext.dcat.utils import resource_uri

DIR3_BASE = "http://datos.gob.es/recurso/sector-publico/org/Organismo/"
THEME_TAXONOMY = "http://datos.gob.es/kos/sector-publico/sector/"
EU_LANG = "http://publications.europa.eu/resource/authority/language/"
LANG_MAP = {"es": "SPA", "ca": "CAT", "eu": "EUS", "gl": "GLG", "en": "ENG"}

# Formatos habituales -> tipo MIME (IANA), como pide la NTI-RISP.
FORMAT_MIME = {
    "CSV": "text/csv",
    "JSON": "application/json",
    "GEOJSON": "application/geo+json",
    "XML": "application/xml",
    "RDF": "application/rdf+xml",
    "XLS": "application/vnd.ms-excel",
    "XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ODS": "application/vnd.oasis.opendocument.spreadsheet",
    "PDF": "application/pdf",
    "ZIP": "application/zip",
    "TXT": "text/plain",
    "HTML": "text/html",
    "KML": "application/vnd.google-earth.kml+xml",
    "SHP": "application/x-shapefile",
    "TSV": "text/tab-separated-values",
}
_MIME = __import__("re").compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")


def clean_mime(value: str | None) -> str | None:
    """Tipo MIME sin parámetros (``text/csv; charset=utf-8`` -> ``text/csv``), o None si no es válido."""
    mime = (value or "").split(";")[0].strip().lower()
    return mime if _MIME.match(mime) else None


def _cfg(key: str, default: str = "") -> str:
    return (tk.config.get("ckanext.ogovtech." + key) or default).strip()


def publisher_uri() -> str | None:
    code = _cfg("dir3_code")
    return DIR3_BASE + code if code else None


def _lang_uri(code: str) -> URIRef:
    code = (code or "es").split("_")[0].lower()
    return URIRef(EU_LANG + LANG_MAP.get(code, code.upper()))


class DCATAPESProfile(RDFProfile):

    # ------------------------------------------------------------ catálogo
    def graph_from_catalog(self, catalog_dict, catalog_ref):
        g = self.g

        pub = publisher_uri()
        if pub:
            g.remove((catalog_ref, DCT.publisher, None))
            g.add((catalog_ref, DCT.publisher, URIRef(pub)))

        spatial = _cfg("spatial_uri")
        if spatial:
            g.remove((catalog_ref, DCT.spatial, None))
            g.add((catalog_ref, DCT.spatial, URIRef(spatial)))

        g.set((catalog_ref, DCAT.themeTaxonomy, URIRef(THEME_TAXONOMY)))

        site_url = tk.config.get("ckan.site_url", "").rstrip("/")
        if site_url:
            g.set((catalog_ref, DCT.identifier, Literal(site_url)))

        lic = _cfg("catalog_license", "https://creativecommons.org/licenses/by/4.0/")
        g.set((catalog_ref, DCT.license, URIRef(lic)))

        issued = _cfg("catalog_issued")
        if issued:
            self._add_date_triple(catalog_ref, DCT.issued, issued)

        # Idioma(s) del catálogo como URIs EU (sustituye el literal "es").
        g.remove((catalog_ref, DCT.language, None))
        for code in (tk.config.get("ckan.locale_default") or "es").split():
            g.add((catalog_ref, DCT.language, _lang_uri(code)))

    # ------------------------------------------------------------- dataset
    def graph_from_dataset(self, dataset_dict, dataset_ref):
        g = self.g

        # Publicador: DIR3 común salvo que el dataset traiga uno propio (URI).
        pub = publisher_uri()
        has_own = any(
            isinstance(o, URIRef) and str(o).startswith(DIR3_BASE)
            for o in g.objects(dataset_ref, DCT.publisher)
        )
        if pub and not has_own:
            for o in list(g.objects(dataset_ref, DCT.publisher)):
                g.remove((dataset_ref, DCT.publisher, o))
                if isinstance(o, BNode):
                    g.remove((o, None, None))
            g.add((dataset_ref, DCT.publisher, URIRef(pub)))

        # Ámbito geográfico por defecto.
        spatial = _cfg("spatial_uri")
        if spatial and not any(
            isinstance(o, URIRef) for o in g.objects(dataset_ref, DCT.spatial)
        ):
            g.add((dataset_ref, DCT.spatial, URIRef(spatial)))

        # Licencia a nivel de conjunto de datos (NTI-RISP). Las distribuciones
        # la heredan vía ckanext.dcat.resource.inherit.license (DCAT-AP).
        lic = dataset_dict.get("license_url")
        if lic and lic.startswith(("http://", "https://")) and not list(g.objects(dataset_ref, DCT.license)):
            g.add((dataset_ref, DCT.license, URIRef(lic)))

        # Idioma por defecto.
        if not list(g.objects(dataset_ref, DCT.language)):
            g.add((dataset_ref, DCT.language, _lang_uri("es")))

        # Formato de las distribuciones como dct:IMT.
        for res in dataset_dict.get("resources", []):
            dist = URIRef(resource_uri(res))
            fmt = (res.get("format") or "").strip()
            mime = clean_mime(res.get("mimetype")) or FORMAT_MIME.get(fmt.upper())
            if not mime and res.get("url"):
                mime = clean_mime(mimetypes.guess_type(res["url"])[0])
            if not mime:
                continue
            for o in list(g.objects(dist, DCT["format"])):
                g.remove((dist, DCT["format"], o))
                if isinstance(o, BNode):
                    g.remove((o, None, None))
            imt = BNode()
            g.add((dist, DCT["format"], imt))
            g.add((imt, RDF.type, DCT.IMT))
            g.add((imt, RDF.value, Literal(mime)))
            g.add((imt, RDFS.label, Literal(fmt.upper() or mime)))
            g.set((dist, DCAT.mediaType, URIRef("https://www.iana.org/assignments/media-types/" + mime)))
