#!/usr/bin/env python3
"""Pruebas de aceptación de una instancia desplegada de la baseline (anexo técnico, apdo. 8).

Se ejecutan contra una instancia en marcha, crean sus propios datos de prueba
(organización y dataset con prefijo ``aceptacion-``) y los eliminan al final.

    python3 acceptance.py --url http://host:puerto --token <token sysadmin> [--report-dir DIR]

Código de salida 0 solo si todas las comprobaciones pasan. Genera un informe
JSON y Markdown con el resultado y la evidencia de cada comprobación.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import time
import traceback
import uuid
from pathlib import Path

import requests
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

DCAT = Namespace("http://www.w3.org/ns/dcat#")
DCT = Namespace("http://purl.org/dc/terms/")
HYDRA = Namespace("http://www.w3.org/ns/hydra/core#")
NTI = "http://datos.gob.es/kos/sector-publico/sector/"
SPA = "http://publications.europa.eu/resource/authority/language/SPA"
EXPECTED_PLUGINS = {"ogovtech_theme", "ogovtech_fixes", "activity", "stats", "dcat", "scheming_datasets",
                    "pages", "datastore", "xloader", "datatables_view", "text_view", "image_view"}
CSV = "codigo,nombre,valor\n1,Uno,10.5\n2,Dos,20\n3,Tres,30.25\n"


class Acceptance:
    def __init__(self, url: str, token: str, timeout: int = 300):
        self.url = url.rstrip("/")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": token, "User-Agent": "ogov-acceptance/1.0"})
        self.timeout = timeout
        self.run_id = uuid.uuid4().hex[:8]
        self.org = f"aceptacion-org-{self.run_id}"
        self.ds = f"aceptacion-ds-{self.run_id}"
        self.results: list[dict] = []
        self.ctx: dict = {}

    # ----------------------------------------------------------- utilidades
    def api(self, action: str, method: str = "GET", **data):
        if method == "GET":
            r = self.s.get(f"{self.url}/api/3/action/{action}", params=data, timeout=60)
        elif "files" in data:
            files = data.pop("files")
            r = self.s.post(f"{self.url}/api/3/action/{action}", data=data, files=files, timeout=120)
        else:
            r = self.s.post(f"{self.url}/api/3/action/{action}", json=data, timeout=60)
        body = r.json()
        if not body.get("success"):
            raise AssertionError(f"{action}: {body.get('error')}")
        return body["result"]

    def check(self, area: str, name: str, fn) -> None:
        t0 = time.monotonic()
        try:
            evidence = fn()
            self.results.append({"area": area, "check": name, "ok": True, "evidence": evidence,
                                 "seconds": round(time.monotonic() - t0, 1)})
            print(f"  ✓ [{area}] {name}: {evidence}", flush=True)
        except Exception as e:
            self.results.append({"area": area, "check": name, "ok": False,
                                 "evidence": f"{type(e).__name__}: {e}",
                                 "seconds": round(time.monotonic() - t0, 1)})
            print(f"  ✗ [{area}] {name}: {type(e).__name__}: {e}", flush=True)
            if "--debug" in sys.argv:
                traceback.print_exc()

    def graph(self, path: str, fmt: str, headers: dict | None = None) -> Graph:
        r = self.s.get(self.url + path, headers=headers or {}, timeout=60)
        r.raise_for_status()
        g = Graph()
        g.parse(data=r.text, format=fmt)
        return g

    # ------------------------------------------------------------- pruebas
    def ckan(self):
        def status():
            st = self.api("status_show")
            assert st["ckan_version"].startswith("2.12"), st["ckan_version"]
            missing = EXPECTED_PLUGINS - set(st["extensions"])
            assert not missing, f"faltan extensiones {missing}"
            return f"CKAN {st['ckan_version']}, {len(st['extensions'])} extensiones"
        self.check("CKAN", "estado, versión y extensiones", status)

        def home():
            r = self.s.get(self.url + "/", timeout=60)
            assert r.status_code == 200 and "ogov-hero" in r.text and "Desarrollado por" in r.text
            return "portada con tema oGov (200)"
        self.check("CKAN", "portada pública con el tema común", home)

        def admin():
            r = self.s.get(self.url + "/ckan-admin/config", timeout=60)
            assert r.status_code == 200 and "field-ogov-color_primary" in r.text
            return "configuración visual accesible al sysadmin"
        self.check("CKAN", "acceso administrativo y configuración visual", admin)

        def pages():
            r = self.s.get(self.url + "/pages", timeout=60)
            assert r.status_code == 200
            return "ckanext-pages responde (200)"
        self.check("CKAN", "ckanext-pages", pages)

    def api_and_schema(self):
        def org():
            o = self.api("organization_create", "POST", name=self.org, title="Organización de aceptación")
            return f"organización {o['name']} creada"
        self.check("API", "crear organización", org)

        def invalid():
            try:
                self.api("package_create", "POST", name=self.ds + "-invalido", title="X", notes="x",
                         owner_org=self.org, license_id="CC-BY-4.0")
            except AssertionError as e:
                assert "theme" in str(e), e
                return "rechazado por el esquema común (falta 'theme')"
            raise AssertionError("se aceptó un dataset sin temática NTI")
        self.check("Esquema", "validación del esquema común", invalid)

        def create():
            d = self.api("package_create", "POST", name=self.ds, title="Dataset de aceptación",
                         notes="Prueba automática de aceptación.", owner_org=self.org, license_id="CC-BY-4.0",
                         theme=[NTI + "sector-publico"], language=[SPA], tags=[{"name": "aceptacion"}])
            self.ctx["dataset_id"] = d["id"]
            return f"dataset {d['name']} creado"
        self.check("API", "crear dataset conforme al esquema", create)

        def upload():
            res = self.api("resource_create", "POST", package_id=self.ds, name="datos.csv", format="CSV",
                           files={"upload": ("datos.csv", io.BytesIO(CSV.encode()), "text/csv")})
            self.ctx["resource_id"] = res["id"]
            r = self.s.get(res["url"].replace(res["url"].split("/dataset/")[0], self.url), timeout=60)
            assert r.status_code == 200 and r.text == CSV, "la descarga no coincide con lo subido"
            return f"recurso subido y descargado íntegro ({len(CSV)} bytes)"
        self.check("API", "publicar y descargar recurso (URL persistente)", upload)

        def search():
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                hits = self.api("package_search", q=f"name:{self.ds}", include_private=True)["count"]
                if hits == 1:
                    break
                time.sleep(2)
            assert hits == 1, "no indexado"
            facets = self.api("package_search", fq=f"organization:{self.org}", **{"facet.field": '["res_format"]'})
            fmts = [i["name"] for i in facets["search_facets"]["res_format"]["items"]]
            assert "CSV" in fmts, fmts
            return "indexado en Solr; filtro por organización y faceta de formato correctos"
        self.check("Solr", "indexación, búsqueda y filtros", search)


    def xloader_datastore(self):
        def load():
            deadline = time.monotonic() + self.timeout
            status = None
            while time.monotonic() < deadline:
                try:
                    status = self.api("xloader_status", resource_id=self.ctx["resource_id"])
                except AssertionError:
                    status = None
                if status and status.get("status") in ("complete", "error"):
                    break
                time.sleep(3)
            assert status and status["status"] == "complete", f"estado XLoader: {status}"
            return f"XLoader completo en <{self.timeout}s"
        self.check("XLoader", "carga automática del CSV", load)

        def datastore():
            r = self.api("datastore_search", resource_id=self.ctx["resource_id"])
            fields = [f["id"] for f in r["fields"] if f["id"] != "_id"]
            assert r["total"] == 3 and fields == ["codigo", "nombre", "valor"], (r["total"], fields)
            q = self.api("datastore_search", resource_id=self.ctx["resource_id"], q="Dos")
            assert q["total"] == 1 and q["records"][0]["nombre"] == "Dos"
            return "3 filas y columnas codigo/nombre/valor; búsqueda de texto OK"
        self.check("DataStore", "consulta de datos cargados", datastore)

        def ro_api():
            anon = requests.get(f"{self.url}/api/3/action/datastore_search",
                                params={"resource_id": self.ctx["resource_id"], "limit": 1}, timeout=60).json()
            assert anon["success"] and anon["result"]["records"], anon
            return "API DataStore accesible sin autenticación (dataset público)"
        self.check("DataStore", "acceso público de lectura", ro_api)

        def views():
            v = self.api("resource_view_list", id=self.ctx["resource_id"])
            types = {x["view_type"] for x in v}
            assert "datatables_view" in types, types
            return f"vistas por defecto: {sorted(types)}"
        self.check("API", "vistas estándar de recursos (tras la carga en DataStore)", views)

    def dcat(self):
        def catalog():
            g = self.graph("/catalog.ttl", "turtle")
            cat = next(g.subjects(RDF.type, DCAT.Catalog))
            pub = list(g.objects(cat, DCT.publisher))
            assert pub and str(pub[0]).startswith("http://datos.gob.es/recurso/sector-publico/org/Organismo/"), pub
            assert list(g.objects(cat, DCAT.themeTaxonomy)) == [URIRef(NTI.rstrip("/"))]
            assert list(g.objects(cat, DCT.license)) and list(g.objects(cat, DCT.language))
            n = len(list(g.subjects(RDF.type, DCAT.Dataset)))
            return f"catálogo DCAT válido: publicador DIR3 {pub[0].split('/')[-1]}, {n} datasets en la página"
        self.check("DCAT", "endpoint del catálogo (Turtle)", catalog)

        def formats():
            sizes = {}
            for ext, fmt in (("rdf", "xml"), ("jsonld", "json-ld"), ("ttl", "turtle")):
                sizes[ext] = len(self.graph(f"/catalog.{ext}", fmt))
            assert len(set(sizes.values())) == 1, f"las serializaciones difieren: {sizes}"
            return f"RDF/XML, JSON-LD y Turtle equivalentes ({sizes['ttl']} triples)"
        self.check("DCAT", "serialización en los formatos configurados", formats)

        def dataset():
            g = self.graph(f"/dataset/{self.ds}.ttl", "turtle")
            ds = next(g.subjects(RDF.type, DCAT.Dataset))
            assert URIRef(NTI + "sector-publico") in set(g.objects(ds, DCAT.theme))
            assert URIRef(SPA) in set(g.objects(ds, DCT.language))
            assert list(g.objects(ds, DCT.license)), "sin dct:license en el dataset"
            dist = next(g.objects(ds, DCAT.distribution))
            assert list(g.objects(dist, DCT.license)), "sin dct:license en la distribución"
            fmt = next(g.objects(dist, DCT["format"]))
            assert str(fmt) == "http://publications.europa.eu/resource/authority/file-type/CSV", fmt
            titles = list(g.objects(ds, DCT.title))
            assert titles and all(t.language == "es" for t in titles), "título sin etiqueta @es"
            return "dataset con temática NTI, idioma, licencia (dataset y distribución), formato EU y textos @es"
        self.check("DCAT", "perfil de federación DCAT-AP-ES en un dataset", dataset)

        def negotiation():
            g = self.graph(f"/dataset/{self.ds}", "turtle", headers={"Accept": "text/turtle"})
            assert any(True for _ in g.subjects(RDF.type, DCAT.Dataset))
            return "negociación de contenido (Accept: text/turtle) OK"
        self.check("DCAT", "negociación de contenido", negotiation)

        def pagination():
            total = self.api("package_search", rows=0)["count"]
            g = self.graph("/catalog.ttl", "turtle")
            seen = len(list(g.subjects(RDF.type, DCAT.Dataset)))
            if total > seen:
                nxt = list(g.objects(None, HYDRA.nextPage))
                assert nxt, f"{total} datasets, la página trae {seen} y no hay hydra:nextPage"
                return f"paginado: {seen}/{total} por página con hydra:nextPage"
            return f"los {total} datasets caben en una página"
        self.check("DCAT", "paginación del catálogo para el agregador", pagination)

    def cleanup(self):
        def purge():
            if self.ctx.get("dataset_id"):
                self.api("dataset_purge", "POST", id=self.ds)
            self.api("organization_purge", "POST", id=self.org)
            return "datos de prueba eliminados"
        self.check("Limpieza", "eliminar datos de prueba", purge)

    def run(self) -> bool:
        print(f"Aceptación contra {self.url} (ejecución {self.run_id})")
        try:
            self.ckan()
            self.api_and_schema()
            self.xloader_datastore()
            self.dcat()
        finally:
            self.cleanup()
        return all(r["ok"] for r in self.results)

    def report(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ok = all(r["ok"] for r in self.results)
        data = {"url": self.url, "fecha": stamp, "resultado": "OK" if ok else "ERROR",
                "pasadas": sum(r["ok"] for r in self.results), "total": len(self.results),
                "comprobaciones": self.results}
        base = directory / f"aceptacion-{stamp}"
        base.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        md = [f"# Aceptación — {self.url}", "", f"Resultado: **{data['resultado']}** "
              f"({data['pasadas']}/{data['total']}) — {stamp}", "",
              "| Área | Comprobación | Resultado | Evidencia |", "|---|---|---|---|"]
        md += [f"| {r['area']} | {r['check']} | {'✅' if r['ok'] else '❌'} | {str(r['evidence']).replace('|', '/')} |"
               for r in self.results]
        base.with_suffix(".md").write_text("\n".join(md) + "\n")
        return base.with_suffix(".md")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--report-dir", default="reports")
    ap.add_argument("--xloader-timeout", type=int, default=300)
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    acc = Acceptance(a.url, a.token, a.xloader_timeout)
    ok = acc.run()
    path = acc.report(Path(a.report_dir))
    passed = sum(r["ok"] for r in acc.results)
    print(f"\nResultado: {'OK' if ok else 'ERROR'} — {passed}/{len(acc.results)} comprobaciones. Informe: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
