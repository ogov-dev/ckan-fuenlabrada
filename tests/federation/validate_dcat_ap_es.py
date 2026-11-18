#!/usr/bin/env python3
"""Valida el catálogo DCAT de una instancia contra las formas SHACL oficiales
de DCAT-AP-ES (datos.gob.es, repositorio datosgobes/DCAT-AP-ES).

Reproduce la validación del propio repositorio oficial (tests/test.ini):
mismas formas (perfil base, sin HVD), inferencia ``rdfs`` y avisos permitidos.
Falla (código 1) solo si hay **violaciones**.

    python3 validate_dcat_ap_es.py --url http://host:puerto      # recorre todas las páginas
    python3 validate_dcat_ap_es.py --file catalogo.ttl            # o un fichero
    python3 validate_dcat_ap_es.py --official-examples            # comprueba el validador
"""
from __future__ import annotations

import argparse
import collections
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from pyshacl import validate
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

# Versión fijada de las formas oficiales (commit de datosgobes/DCAT-AP-ES).
COMMIT = "c6ab5330edd33b24350429fc2c17906506f04fa1"
BASE = f"https://raw.githubusercontent.com/datosgobes/DCAT-AP-ES/{COMMIT}"
SHAPES = ["shacl_mdr-vocabularies.shape.ttl", "shacl_common_shapes.ttl", "shacl_catalog_shape.ttl",
          "shacl_dataservice_shape.ttl", "shacl_dataset_shape.ttl", "shacl_distribution_shape.ttl"]
EXAMPLES = {"E_DCAT-AP-ES_minimal.ttl": "warnings", "E_DCAT-AP-ES_full.ttl": "conformant"}
SH = Namespace("http://www.w3.org/ns/shacl#")
HYDRA = Namespace("http://www.w3.org/ns/hydra/core#")
# Copia local de las formas (versionada en el repositorio, ver shapes/ATTRIBUTION.md).
CACHE = Path(__file__).with_name("shapes")


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ogov-dcat-validation/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def cached(path: str) -> Path:
    target = CACHE / path
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(fetch(f"{BASE}/{path}"))
    return target


def shapes_graph() -> Graph:
    g = Graph()
    for name in SHAPES:
        g.parse(cached(f"shacl/1.0.0/{name}"), format="turtle")
    return g


def catalog_from_url(url: str) -> Graph:
    """Descarga el catálogo completo siguiendo la paginación hydra."""
    g, page, pages = Graph(), url.rstrip("/") + "/catalog.ttl", 0
    while page and pages < 200:
        part = Graph().parse(data=fetch(page).decode("utf-8"), format="turtle")
        nxt = [str(o) for o in part.objects(None, HYDRA.nextPage)]
        for t in part:
            if t[0].__class__.__name__ != "URIRef" or "PagedCollection" not in str(t[0]):
                g.add(t)
        pages += 1
        # La página siguiente se pide a la misma URL base (la pública puede no ser accesible).
        if nxt:
            parts = urllib.parse.urlsplit(nxt[0])
            nxt_url = url.rstrip("/") + parts.path + ("?" + parts.query if parts.query else "")
            page = nxt_url if nxt_url != page else None
        else:
            page = None
    print(f"catálogo: {pages} página(s), {len(g)} triples")
    return g


def run(data: Graph, sg: Graph) -> tuple[int, int, collections.Counter]:
    conforms, report, _ = validate(data, shacl_graph=sg, inference="rdfs", abort_on_first=False,
                                   allow_warnings=True, meta_shacl=False)
    counts = collections.Counter()
    details = collections.Counter()
    for r in report.subjects(RDF.type, SH.ValidationResult):
        sev = str(report.value(r, SH.resultSeverity)).rsplit("#", 1)[-1]
        counts[sev] += 1
        path = report.value(r, SH.resultPath)
        focus_type = next(iter(data.objects(report.value(r, SH.focusNode), RDF.type)), None)
        details[(sev, str(focus_type).rsplit("#", 1)[-1] if focus_type else "?",
                 str(path).rsplit("/", 1)[-1] if path else "-",
                 str(report.value(r, SH.resultMessage))[:110])] += 1
    return counts["Violation"], counts["Warning"], details


def print_details(details: collections.Counter) -> None:
    for (sev, cls, path, msg), n in sorted(details.items(), key=lambda x: (x[0][0] != "Violation", -x[1])):
        print(f"  {sev:9} x{n:<4} {cls:12} {path:22} {msg}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url")
    g.add_argument("--file")
    g.add_argument("--official-examples", action="store_true")
    a = ap.parse_args()
    sg = shapes_graph()
    print(f"formas SHACL DCAT-AP-ES @ {COMMIT[:12]}: {len(sg)} triples")

    if a.official_examples:
        ok = True
        for name, expect in EXAMPLES.items():
            data = Graph().parse(cached(f"examples/ttl/1.0.0/{name}"), format="turtle")
            v, w, d = run(data, sg)
            good = v == 0 and (w == 0 if expect == "conformant" else True)
            ok &= good
            print(f"{'OK ' if good else 'MAL'} {name}: {v} violaciones, {w} avisos (esperado: {expect})")
        return 0 if ok else 1

    data = catalog_from_url(a.url) if a.url else Graph().parse(a.file)
    v, w, details = run(data, sg)
    print(f"\nResultado: {v} violaciones, {w} avisos")
    print_details(details)
    return 1 if v else 0


if __name__ == "__main__":
    sys.exit(main())
