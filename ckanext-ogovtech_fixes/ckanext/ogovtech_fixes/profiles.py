"""Perfil DCAT-AP-ES para la federación con datos.gob.es.

Referencia: formas SHACL oficiales de DCAT-AP-ES 1.0.0 (datosgobes/DCAT-AP-ES),
validadas con ``tests/federation/validate_dcat_ap_es.py``.

Se ejecuta DESPUÉS de ``euro_dcat_ap_2`` y ``euro_dcat_ap_scheming``
(ver ``ckanext.dcat.rdf.profiles``) y ajusta lo que DCAT-AP-ES exige:

Catálogo
  * ``dct:publisher``  -> URI DIR3 del ayuntamiento.
  * ``dct:spatial``    -> URI de territorio de datos.gob.es.
  * ``dcat:themeTaxonomy`` -> ``http://datos.gob.es/kos/sector-publico/sector`` (convención 10).
  * ``dct:license``, ``dct:issued``, ``dct:identifier``.
  * ``dct:language``   -> URIs de la EU Publications Office.
  * ``dct:description`` -> si el portal no tiene lema, se genera (es obligatoria).
  * ``dct:issued``     -> ``ckanext.ogovtech.catalog_issued`` o, si falta, la fecha de
    instalación del portal (es obligatoria).

Conjunto de datos
  * ``dct:publisher``  -> el mismo DIR3 (si el dataset no trae uno propio).
  * ``dct:license``    -> URL de la licencia (las distribuciones la heredan con
    ``ckanext.dcat.resource.inherit.license = true``).
  * ``dct:spatial``    -> solo territorios de vocabularios admitidos (datos.gob.es:
    país, comunidad o provincia; EU country/continent/place; GeoNames); si no
    queda ninguno, el del catálogo. Los
    territorios por URI no se tipan como ``dct:Location`` (exigiría geometría).
  * ``dct:language``   -> por defecto español si no se ha indicado.
  * ``dcat:contactPoint`` -> se completa con el correo de contacto del portal o
    se omite (exige ``vcard:hasEmail`` o ``vcard:hasURL``).

Distribución
  * ``dct:format``     -> IRI del vocabulario europeo de tipos de fichero; si el
    formato no existe en él (p. ej. PRJ, SHX), se omite (es solo recomendado).
  * ``dcat:mediaType`` -> tipo IANA.
  * ``spdx:checksum``  -> solo SHA-1 en hexadecimal; otros se omiten.
  * Recursos sin URL: no se publican como distribución (``dcat:accessURL`` es obligatorio).

Textos (título, descripción, palabras clave, nombres de contacto): siempre con
etiqueta de idioma ``@es`` (convención de literales multilingües).
"""
from __future__ import annotations

import mimetypes
import re

from rdflib import BNode, Literal, URIRef

import ckan.plugins.toolkit as tk
from ckanext.dcat.profiles import DCAT, DCT, FOAF, LOCN, RDF, RDFProfile, SPDX, VCARD, XSD
from ckanext.dcat.utils import resource_uri

DIR3_BASE = "http://datos.gob.es/recurso/sector-publico/org/Organismo/"
THEME_TAXONOMY = "http://datos.gob.es/kos/sector-publico/sector"
EU_LANG = "http://publications.europa.eu/resource/authority/language/"
EU_FILE_TYPE = "http://publications.europa.eu/resource/authority/file-type/"
LANG_MAP = {"es": "SPA", "ca": "CAT", "eu": "EUS", "gl": "GLG", "en": "ENG"}
SHA1 = URIRef("http://spdx.org/rdf/terms#checksumAlgorithm_sha1")

# Formato de CKAN -> código del vocabulario europeo de tipos de fichero
# (todos comprobados en publications.europa.eu/resource/authority/file-type).
FILE_TYPES = {
    "CSV": "CSV", "TSV": "TSV", "JSON": "JSON", "GEOJSON": "GEOJSON", "XML": "XML",
    "RDF": "RDF_XML", "RDF/XML": "RDF_XML", "TTL": "RDF_TURTLE", "TURTLE": "RDF_TURTLE",
    "JSON-LD": "JSON_LD", "JSONLD": "JSON_LD", "N3": "N3",
    "XLS": "XLS", "XLSX": "XLSX", "ODS": "ODS", "ODT": "ODT", "DOC": "DOC", "DOCX": "DOCX",
    "PDF": "PDF", "ZIP": "ZIP", "TXT": "TXT", "HTML": "HTML", "KML": "KML",
    "SHP": "SHP", "DBF": "DBF", "DBASE": "DBF", "GTFS": "GTFS",
}

# Formatos habituales -> tipo MIME (IANA) para dcat:mediaType.
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
    "TSV": "text/tab-separated-values",
}
_MIME = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_SHA1_HEX = re.compile(r"^[0-9a-fA-F]{40}$")
# Vocabularios de dct:spatial admitidos por DCAT-AP-ES (Spatial_Shape).
ALLOWED_SPATIAL = re.compile(
    r"^(http://datos\.gob\.es/recurso/sector-publico/territorio/(Pais|Autonomia|Provincia)/.+"
    r"|http://publications\.europa\.eu/resource/authority/(country|continent|place)/.+"
    r"|https?://sws\.geonames\.org/.+)$")
TEXT_PREDICATES = (DCT.title, DCT.description, DCAT.keyword, VCARD.fn,
                   VCARD["organization-name"], FOAF.name)


def file_type_uri(fmt: str | None) -> URIRef | None:
    code = FILE_TYPES.get((fmt or "").strip().upper())
    return URIRef(EU_FILE_TYPE + code) if code else None


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


def _install_date() -> str | None:
    """Fecha de instalación del portal: creación del usuario interno (site user)."""
    try:
        site_user = tk.get_action("get_site_user")({"ignore_auth": True}, {})
        user = tk.get_action("user_show")({"ignore_auth": True}, {"id": site_user["name"]})
        return (user.get("created") or "")[:10] or None
    except Exception:  # sin base de datos (p. ej. serialización aislada)
        return None


def _tag_spanish(g, subject, predicates=TEXT_PREDICATES) -> None:
    """Literales de texto sin idioma -> ``@es`` (DCAT-AP-ES exige literales multilingües)."""
    for p in predicates:
        for o in list(g.objects(subject, p)):
            if isinstance(o, Literal) and o.language is None and o.datatype is None:
                g.remove((subject, p, o))
                if str(o).strip():
                    g.add((subject, p, Literal(str(o), lang="es")))


def _drop_node(g, subject, predicate, node) -> None:
    g.remove((subject, predicate, node))
    if isinstance(node, BNode):
        for p, o in list(g.predicate_objects(node)):
            g.remove((node, p, o))
            if isinstance(o, BNode):
                _drop_node(g, node, p, o)


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

        # dct:issued es obligatorio: la fecha configurada o, si falta, la de
        # instalación del portal (creación del usuario interno de CKAN).
        issued = _cfg("catalog_issued") or _install_date()
        if issued and not list(g.objects(catalog_ref, DCT.issued)):
            self._add_date_triple(catalog_ref, DCT.issued, issued)

        # Idioma(s) del catálogo como URIs EU (sustituye el literal "es").
        g.remove((catalog_ref, DCT.language, None))
        for code in (tk.config.get("ckan.locale_default") or "es").split():
            g.add((catalog_ref, DCT.language, _lang_uri(code)))

        # dct:title y dct:description son obligatorios: si el portal no tiene
        # lema configurado, se genera una descripción a partir del título.
        if not list(g.objects(catalog_ref, DCT.description)):
            title = tk.config.get("ckan.site_title") or "Portal de datos abiertos"
            g.add((catalog_ref, DCT.description, Literal(f"Catálogo de datos abiertos de {title}", lang="es")))

        _tag_spanish(g, catalog_ref)

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
                _drop_node(g, dataset_ref, DCT.publisher, o)
            g.add((dataset_ref, DCT.publisher, URIRef(pub)))

        # Territorios por URI: solo los vocabularios que admite DCAT-AP-ES
        # (datos.gob.es solo país/comunidad/provincia; no municipios).
        for loc in list(g.objects(dataset_ref, DCT.spatial)):
            if isinstance(loc, URIRef) and not ALLOWED_SPATIAL.match(str(loc)):
                _drop_node(g, dataset_ref, DCT.spatial, loc)
                for p_, o_ in list(g.predicate_objects(loc)):
                    g.remove((loc, p_, o_))

        # Ámbito geográfico por defecto.
        spatial = _cfg("spatial_uri")
        if spatial and not any(
            isinstance(o, URIRef) for o in g.objects(dataset_ref, DCT.spatial)
        ):
            g.add((dataset_ref, DCT.spatial, URIRef(spatial)))
        # Un territorio referenciado por URI (datos.gob.es) no se describe como
        # dct:Location: esa clase exige geometría, bbox o centroide.
        for loc in g.objects(dataset_ref, DCT.spatial):
            has_geometry = any(g.value(loc, p) is not None for p in (DCAT.bbox, DCAT.centroid, LOCN.geometry))
            if isinstance(loc, URIRef) and (loc, RDF.type, DCT.Location) in g and not has_geometry:
                g.remove((loc, RDF.type, DCT.Location))

        # Licencia a nivel de conjunto de datos. Las distribuciones la heredan
        # vía ckanext.dcat.resource.inherit.license.
        lic = dataset_dict.get("license_url")
        if lic and lic.startswith(("http://", "https://")) and not list(g.objects(dataset_ref, DCT.license)):
            g.add((dataset_ref, DCT.license, URIRef(lic)))

        # Idioma por defecto.
        if not list(g.objects(dataset_ref, DCT.language)):
            g.add((dataset_ref, DCT.language, _lang_uri("es")))

        # Puntos de contacto: deben tener correo o URL.
        fallback_email = (tk.config.get("ckanext.ogovtech_theme.contact_email") or "").strip()
        for cp in list(g.objects(dataset_ref, DCAT.contactPoint)):
            _tag_spanish(g, cp)
            if list(g.objects(cp, VCARD.hasEmail)) or list(g.objects(cp, VCARD.hasURL)):
                continue
            if fallback_email:
                g.add((cp, VCARD.hasEmail, URIRef("mailto:" + fallback_email)))
            else:
                _drop_node(g, dataset_ref, DCAT.contactPoint, cp)

        _tag_spanish(g, dataset_ref)

        # Distribuciones.
        for res in dataset_dict.get("resources", []):
            dist = URIRef(resource_uri(res))
            if not list(g.objects(dist, DCAT.accessURL)):
                # Recurso sin URL: dcat:accessURL es obligatorio; no se federa.
                _drop_node(g, dataset_ref, DCAT.distribution, dist)
                for p, o in list(g.predicate_objects(dist)):
                    _drop_node(g, dist, p, o)
                continue
            _tag_spanish(g, dist)
            fmt = (res.get("format") or "").strip()

            # dct:format: IRI del vocabulario europeo o nada.
            for o in list(g.objects(dist, DCT["format"])):
                _drop_node(g, dist, DCT["format"], o)
            ft = file_type_uri(fmt)
            if ft:
                g.add((dist, DCT["format"], ft))

            # dcat:mediaType: tipo IANA.
            mime = clean_mime(res.get("mimetype")) or FORMAT_MIME.get(fmt.upper())
            if not mime and res.get("url"):
                mime = clean_mime(mimetypes.guess_type(res["url"])[0])
            if mime:
                g.set((dist, DCAT.mediaType, URIRef("https://www.iana.org/assignments/media-types/" + mime)))

            # spdx:checksum: solo SHA-1 hexadecimal.
            for cs in list(g.objects(dist, SPDX.checksum)):
                value = g.value(cs, SPDX.checksumValue)
                if value is not None and _SHA1_HEX.match(str(value)):
                    g.set((cs, SPDX.algorithm, SHA1))
                    g.set((cs, SPDX.checksumValue, Literal(str(value).lower(), datatype=XSD.hexBinary)))
                else:
                    _drop_node(g, dist, SPDX.checksum, cs)
