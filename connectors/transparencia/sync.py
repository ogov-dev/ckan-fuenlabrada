#!/usr/bin/env python3
"""Conector Portal de Transparencia oGov -> CKAN.

Lee la API pública de lectura de un portal de transparencia de oGov
(``/api/v1``) y publica cada tabla (entidad dinámica) como un conjunto de
datos en CKAN, con:

* un CSV y un JSON con columnas legibles (etiquetas del visor, no los
  nombres internos ``builderInputComponent…``),
* los documentos adjuntos (PDF, XLSX…) como enlaces dentro de las columnas,
* un recurso que enlaza al visor original del portal de transparencia.

Es idempotente: cada ejecución actualiza los mismos conjuntos de datos, así
que puede programarse (cron) para mantener CKAN sincronizado.

    CKAN_URL=http://localhost:5000 CKAN_API_TOKEN=... \
        python3 sync.py portales/fuenlabrada.yml [--solo contratos-menores] [--dry-run]

Fuentes que usa (todas públicas, sin autenticación):
    GET /api/v1/dynamic-entities/get-by-module?module=5   índice de tablas (/data/5)
    GET /api/v1/entity-record/index-all?dynamic_entity_uuid=…   registros
    GET <ruta del visor>  (HTML, __NEXT_DATA__.initialView.form_schema)   etiquetas
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import unicodedata

import requests
import yaml
from ckanapi import NotFound, RemoteCKAN

UA = "ogov-ckan-transparencia/1.0"
NTI = "http://datos.gob.es/kos/sector-publico/sector/"
EU_LANG = "http://publications.europa.eu/resource/authority/language/SPA"
SKIP_TYPES = {"button", "builderMarginComponent", "builderEmbedComponent",
              "titleComponent", "builderTitleComponent", "builderTextComponent"}


def log(msg: str) -> None:
    print(msg, flush=True)


def slugify(text: str, maxlen: int = 80) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:maxlen].strip("-")


class Source:
    def __init__(self, base: str, lang: str = "es"):
        self.base = base.rstrip("/")
        self.lang = lang
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA

    def get_json(self, path: str, **params):
        for attempt in range(3):
            try:
                r = self.s.get(f"{self.base}/api/v1/{path}", params=params, timeout=90)
                r.raise_for_status()
                return r.json()
            except requests.RequestException as e:
                log(f"  reintento {attempt + 1} {path}: {e}")
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"No se pudo leer {path}")

    def entities(self, module: int) -> list[dict]:
        return self.get_json("dynamic-entities/get-by-module", module=module)["data"]

    def records(self, uuid: str) -> list[dict]:
        d = self.get_json("entity-record/index-all", dynamic_entity_uuid=uuid,
                          fixed_filters="{}", exclude_filters="{}", active=1)
        data = d.get("data", d)
        return data if isinstance(data, list) else data.get("data", [])

    def schema(self, route: str, uuid: str) -> list[tuple[str, str]]:
        """[(clave_interna, etiqueta)] en el orden del formulario del visor."""
        try:
            html = self.s.get(self.base + route, timeout=90).text
            m = re.search(r'__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
            view = json.loads(m.group(1))["props"]["pageProps"].get("initialView") or {}
            fs = view.get("form_schema") or {}
            if fs.get("dynamic_entity_uuid") != uuid:
                return []
            comps = fs["structure"]["fields"]["components"]
        except Exception:
            return []
        out = []
        for c in comps:
            if c.get("type") in SKIP_TYPES or not c.get("key"):
                continue
            label = c.get("labelTag") or c.get("label") or c["key"]
            if isinstance(label, dict):
                label = label.get(self.lang) or next(iter(label.values()), c["key"])
            out.append((c["key"], str(label).strip()))
        return out

    # --- aplanado de valores -------------------------------------------------
    def flat(self, v) -> str:
        if v is None or isinstance(v, bool):
            return ""
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("{") and s.endswith("}"):
                try:
                    return self.flat(json.loads(s))
                except ValueError:
                    pass
            return s
        if isinstance(v, list):
            return " | ".join(x for x in (self.flat(i) for i in v) if x)
        if isinstance(v, dict):
            if "image" in v and isinstance(v["image"], str):          # documento subido
                return self.base + v["image"] if v["image"].startswith("/") else v["image"]
            if all(re.fullmatch(r"file\d+", k) for k in v):         # varios documentos
                return " | ".join(self.flat(v[k]) for k in sorted(v, key=lambda k: int(k[4:])))
            if "value" in v:                                           # número/moneda
                return self.flat(v["value"])
            if "name" in v and v["name"]:                              # relación
                return self.flat(v["name"])
            if "label" in v:                                           # selección
                return self.flat(v["label"])
            if self.lang in v:                                         # texto multilingüe
                return self.flat(v[self.lang])
            return self.flat(next(iter(v.values()), ""))
        return str(v)


def table(src: Source, records: list[dict], schema: list[tuple[str, str]]):
    keys_present = {k for r in records for k, v in r["data"].items() if src.flat(v)}
    cols = [(k, l) for k, l in schema if k in keys_present]
    if not cols:  # sin esquema: claves internas, en orden de aparición
        seen = []
        for r in records:
            for k in r["data"]:
                if k in keys_present and k != "submit" and k not in seen:
                    seen.append(k)
        cols = [(k, k) for k in seen]
    labels, used = [], {}
    for _, l in cols:
        used[l] = used.get(l, 0) + 1
        labels.append(l if used[l] == 1 else f"{l} ({used[l]})")
    rows = [[src.flat(r["data"].get(k)) for k, _ in cols] for r in records]
    return labels, rows


def pick(rules: list[dict], name: str, field: str, default):
    slug = slugify(name)
    for rule in rules:
        if any(w in slug for w in rule["si_contiene"]):
            return rule[field]
    return default


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--solo", help="procesar solo la tabla con esta ruta/slug")
    ap.add_argument("--dry-run", action="store_true", help="no escribe en CKAN")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config, encoding="utf-8"))
    src = Source(cfg["source"], cfg.get("lang", "es"))
    dst = None if a.dry_run else RemoteCKAN(os.environ["CKAN_URL"], apikey=os.environ["CKAN_API_TOKEN"], user_agent=UA)

    prefix = cfg.get("dataset_prefix", "transparencia-")
    excluded = {slugify(x) for x in cfg.get("excluir", [])}
    rules = cfg.get("clasificacion", [])
    ok = empty = skipped = 0

    for ent in src.entities(cfg.get("module", 5)):
        name = ent["name"].get(src.lang) or next(iter(ent["name"].values()))
        route = ent.get("route") or "/" + slugify(name)
        slug = slugify(route.strip("/") or name)
        if a.solo and a.solo not in (slug, route.strip("/")):
            continue
        if slug in excluded or slugify(name) in excluded:
            skipped += 1
            log(f"- {name}: excluida por configuración")
            continue
        records = src.records(ent["uuid"])
        if not records:
            empty += 1
            log(f"- {name}: sin registros, se omite")
            continue
        labels, rows = table(src, records, src.schema(route, ent["uuid"]))
        log(f"+ {name}: {len(rows)} registros, {len(labels)} columnas")
        if a.dry_run:
            log("    " + " · ".join(labels[:8]))
            ok += 1
            continue

        ds_name = (prefix + slug)[:100]
        group = pick(rules, name, "grupo", cfg.get("grupo_por_defecto"))
        theme = pick(rules, name, "tematica", cfg.get("tematica_por_defecto", "sector-publico"))
        viewer = src.base + route
        pkg = {
            "name": ds_name,
            "title": name,
            "notes": (f"{name}. Información publicada en el Portal de Transparencia "
                      f"({src.base}). Actualizado automáticamente desde su API pública; "
                      f"los documentos enlazados se sirven desde el propio portal de transparencia."),
            "owner_org": cfg["organization"],
            "license_id": cfg.get("license_id", "odc-by"),
            "tags": [{"name": "transparencia"}],
            "theme": [NTI + theme],
            "language": [EU_LANG],
            "url": viewer,
            "groups": [{"name": group}] if group else [],
        }
        try:
            dst.action.package_show(id=ds_name)
            dst.action.package_patch(id=ds_name, **pkg)
        except NotFound:
            dst.action.package_create(**pkg)
        current = {r["name"]: r["id"] for r in dst.action.package_show(id=ds_name)["resources"]}

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(labels)
        w.writerows(rows)
        payloads = [
            (f"{name} (CSV)", "CSV", f"{slug}.csv", buf.getvalue().encode("utf-8-sig")),
            (f"{name} (JSON)", "JSON", f"{slug}.json",
             json.dumps([dict(zip(labels, r)) for r in rows], ensure_ascii=False, indent=1).encode()),
        ]
        for rname, fmt, fname, content in payloads:
            data = {"package_id": ds_name, "name": rname, "format": fmt, "url": fname}
            action = "resource_update" if rname in current else "resource_create"
            if rname in current:
                data["id"] = current[rname]
            dst.call_action(action, data, files={"upload": (fname, io.BytesIO(content))})
        vname = "Visor en el Portal de Transparencia"
        vdata = {"package_id": ds_name, "name": vname, "format": "HTML", "url": viewer}
        if vname in current:
            dst.action.resource_update(id=current[vname], **vdata)
        else:
            dst.action.resource_create(**vdata)
        ok += 1
        time.sleep(0.2)

    log(f"[transparencia] {ok} tablas publicadas, {empty} vacías, {skipped} excluidas")


if __name__ == "__main__":
    main()
