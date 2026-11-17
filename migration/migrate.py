#!/usr/bin/env python3
"""Migración estándar de un portal CKAN existente a la baseline oGov.tech.

Repetible e idempotente: se puede ensayar tantas veces como haga falta.

    # 1) Descarga metadatos y ficheros del portal antiguo (solo lectura)
    python3 migrate.py export portales/fuenlabrada.yml

    # 2) Carga en el portal nuevo (requiere token de sysadmin)
    CKAN_URL=http://localhost:5000 CKAN_API_TOKEN=... \
        python3 migrate.py import portales/fuenlabrada.yml

    # 3) Comprueba que todo está y que los ficheros coinciden
    CKAN_URL=... CKAN_API_TOKEN=... python3 migrate.py verify portales/fuenlabrada.yml

Alcance (propuesta, apdo. 1.4.2): organizaciones, grupos, conjuntos de datos
y recursos compatibles con el esquema común, y páginas de ckanext-pages.
Se conservan los identificadores (UUID) de datasets y recursos, de modo que
las URLs de descarga antiguas siguen funcionando si el dominio pasa al nuevo
portal.

Los datos descargados van a ``dumps/<portal>/`` y NUNCA se versionan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from ckanapi import NotFound, RemoteCKAN, ValidationError

HERE = Path(__file__).resolve().parent
NTI = "http://datos.gob.es/kos/sector-publico/sector/"
EU_LANG = "http://publications.europa.eu/resource/authority/language/"
UA = "ogov-ckan-migration/1.0"


# --------------------------------------------------------------------- util
def load_cfg(path: str) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cfg["_slug"] = Path(path).stem
    cfg["_dump"] = HERE / "dumps" / cfg["_slug"]
    return cfg


def jsonl_write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def jsonl_read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name) or "fichero"


def log(msg: str) -> None:
    print(msg, flush=True)


# ------------------------------------------------------------------- export
def cmd_export(cfg: dict) -> None:
    src = RemoteCKAN(cfg["source"], user_agent=UA)
    dump = cfg["_dump"]
    (dump / "files").mkdir(parents=True, exist_ok=True)

    log(f"[export] {cfg['source']}")
    orgs = [src.action.organization_show(id=o, include_datasets=False)
            for o in src.action.organization_list()]
    groups = [src.action.group_show(id=g, include_datasets=False)
              for g in src.action.group_list()]

    datasets, start = [], 0
    while True:
        page = src.action.package_search(q="*:*", rows=100, start=start, sort="name asc")
        datasets += page["results"]
        start += 100
        if start >= page["count"]:
            break

    pages = []
    if cfg.get("pages"):
        try:
            for p in src.action.ckanext_pages_list():
                pages.append(src.action.ckanext_pages_show(page=p["name"]))
        except Exception as e:  # el portal origen puede no tener ckanext-pages
            log(f"[export] páginas no disponibles: {e}")

    # Descarga de ficheros subidos (url_type=upload)
    session = requests.Session()
    session.headers["User-Agent"] = UA
    n_files = 0
    for ds in datasets:
        for res in ds["resources"]:
            if res.get("url_type") != "upload":
                continue
            target = dump / "files" / res["id"] / safe_name(res["url"].rsplit("/", 1)[-1])
            if target.exists() and target.stat().st_size > 0:
                res["_file"], res["_sha256"] = str(target.relative_to(dump)), sha256(target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            for attempt in range(3):
                try:
                    with session.get(res["url"], stream=True, timeout=120, allow_redirects=True) as r:
                        r.raise_for_status()
                        with target.open("wb") as f:
                            for chunk in r.iter_content(1 << 20):
                                f.write(chunk)
                    break
                except requests.RequestException as e:
                    log(f"[export]   reintento {attempt + 1} {res['url']}: {e}")
                    time.sleep(2)
            else:
                log(f"[export] ERROR no se pudo descargar {res['url']}")
                continue
            res["_file"], res["_sha256"] = str(target.relative_to(dump)), sha256(target)
            n_files += 1

    jsonl_write(dump / "organizations.jsonl", orgs)
    jsonl_write(dump / "groups.jsonl", groups)
    jsonl_write(dump / "datasets.jsonl", datasets)
    jsonl_write(dump / "pages.jsonl", pages)
    log(f"[export] {len(orgs)} organizaciones, {len(groups)} grupos, {len(datasets)} datasets, "
        f"{sum(len(d['resources']) for d in datasets)} recursos ({n_files} ficheros nuevos), "
        f"{len(pages)} páginas -> {dump}")


# -------------------------------------------------------------- transform
def transform_dataset(ds: dict, cfg: dict) -> dict:
    org_cfg = cfg["organization"]
    lic_cfg = cfg.get("licenses", {})
    lic = ds.get("license_id") or lic_cfg.get("if_empty") or ""
    lic = lic_cfg.get("replace", {}).get(lic, lic)

    group_names = [g["name"] for g in ds.get("groups", [])]
    themes = []
    for g in group_names:
        t = cfg.get("theme_by_group", {}).get(g)
        if t and NTI + t not in themes:
            themes.append(NTI + t)
    if not themes:
        themes = [NTI + cfg.get("theme_default", "sector-publico")]

    out = {
        "id": ds["id"],
        "name": ds["name"],
        "title": ds["title"],
        "notes": ds.get("notes") or ds["title"],
        "owner_org": org_cfg["name"],
        "private": ds.get("private", False),
        "license_id": lic,
        "tags": [{"name": t["name"]} for t in ds.get("tags", [])],
        "groups": [{"name": g} for g in group_names],
        "theme": themes,
        "language": [EU_LANG + cfg.get("language_default", "SPA")],
        "url": ds.get("url") or "",
        "version": ds.get("version") or "",
        "author": ds.get("author") or "",
        "author_email": ds.get("author_email") or "",
        "maintainer": ds.get("maintainer") or "",
        "maintainer_email": ds.get("maintainer_email") or "",
    }
    contact = {k: v for k, v in (("name", ds.get("maintainer")), ("email", ds.get("maintainer_email"))) if v}
    if contact:
        out["contact"] = [contact]
    return out


def resource_fields(res: dict) -> dict:
    keep = ("id", "name", "description", "format", "mimetype")
    return {k: res[k] for k in keep if res.get(k)}


# ------------------------------------------------------------------ import
def target() -> RemoteCKAN:
    url, token = os.environ.get("CKAN_URL"), os.environ.get("CKAN_API_TOKEN")
    if not url or not token:
        sys.exit("Define CKAN_URL y CKAN_API_TOKEN (token de un sysadmin del portal nuevo).")
    return RemoteCKAN(url, apikey=token, user_agent=UA)


def upsert(dst: RemoteCKAN, kind: str, data: dict) -> None:
    try:
        dst.call_action(f"{kind}_show", {"id": data["name"]})
        dst.call_action(f"{kind}_patch", {**data, "id": data["name"]})
    except NotFound:
        dst.call_action(f"{kind}_create", data)


def cmd_import(cfg: dict) -> None:
    dst, dump = target(), cfg["_dump"]
    org_cfg = cfg["organization"]

    # Organización (renombrada)
    src_org = next((o for o in jsonl_read(dump / "organizations.jsonl") if o["name"] == org_cfg["from"]), {})
    upsert(dst, "organization", {
        "name": org_cfg["name"], "title": org_cfg.get("title") or src_org.get("title"),
        "description": src_org.get("description", "")})
    log(f"[import] organización {org_cfg['name']}")

    # Grupos (tal cual)
    for g in jsonl_read(dump / "groups.jsonl"):
        data = {"name": g["name"], "title": g["title"], "description": g.get("description", "")}
        if cfg.get("groups", {}).get("migrate_images"):
            data["image_url"] = g.get("image_url", "")
        upsert(dst, "group", data)
    log("[import] grupos ok")

    ok = fail = 0
    for ds in jsonl_read(dump / "datasets.jsonl"):
        data = transform_dataset(ds, cfg)
        try:
            try:
                existing = dst.action.package_show(id=ds["id"])
                dst.action.package_update(**data, resources=existing["resources"])
            except NotFound:
                dst.action.package_create(**data)
            existing_ids = {r["id"] for r in dst.action.package_show(id=ds["id"])["resources"]}
            for res in ds["resources"]:
                fields = {**resource_fields(res), "package_id": ds["id"]}
                file_rel = res.get("_file")
                action = "resource_update" if res["id"] in existing_ids else "resource_create"
                if res.get("url_type") == "upload" and file_rel:
                    with (dump / file_rel).open("rb") as fh:
                        dst.call_action(action, {**fields, "url": Path(file_rel).name},
                                        files={"upload": fh})
                else:
                    dst.call_action(action, {**fields, "url": res["url"]})
            ok += 1
            log(f"[import]   ✓ {ds['name']} ({len(ds['resources'])} recursos)")
        except ValidationError as e:
            fail += 1
            log(f"[import]   ✗ {ds['name']}: {e.error_dict}")

    for p in jsonl_read(dump / "pages.jsonl"):
        dst.action.ckanext_pages_update(
            page=p["name"], name=p["name"], title=p["title"], content=p.get("content", ""),
            private=str(p.get("private")).lower() == "true", order=p.get("order") or "",
            page_type=p.get("page_type") or "page")
    log(f"[import] {ok} datasets ok, {fail} con errores")
    if fail:
        sys.exit(1)


# ------------------------------------------------------------------ verify
def cmd_verify(cfg: dict) -> None:
    dst, dump = target(), cfg["_dump"]
    src = jsonl_read(dump / "datasets.jsonl")
    problems = 0
    for ds in src:
        try:
            new = dst.action.package_show(id=ds["id"])
        except NotFound:
            log(f"[verify] FALTA dataset {ds['name']}")
            problems += 1
            continue
        new_res = {r["id"]: r for r in new["resources"]}
        for res in ds["resources"]:
            if res["id"] not in new_res:
                log(f"[verify] FALTA recurso {ds['name']} / {res.get('name')}")
                problems += 1
            elif res.get("_sha256"):
                # Descarga por CKAN_URL (la URL pública puede no ser accesible
                # desde donde se ejecuta el script, p. ej. dentro del contenedor).
                path = "/" + new_res[res["id"]]["url"].split("/", 3)[3]
                r = requests.get(os.environ["CKAN_URL"].rstrip("/") + path, timeout=120,
                                 headers={"Authorization": os.environ["CKAN_API_TOKEN"]})
                if hashlib.sha256(r.content).hexdigest() != res["_sha256"]:
                    log(f"[verify] FICHERO DISTINTO {ds['name']} / {res.get('name')}")
                    problems += 1
        if {g["name"] for g in new["groups"]} != {g["name"] for g in ds["groups"]}:
            log(f"[verify] GRUPOS DISTINTOS {ds['name']}")
            problems += 1
    n_res = sum(len(d["resources"]) for d in src)
    log(f"[verify] {len(src)} datasets / {n_res} recursos comprobados: "
        + ("todo correcto" if not problems else f"{problems} problemas"))
    sys.exit(1 if problems else 0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["export", "import", "verify"])
    ap.add_argument("config", help="portales/<ayuntamiento>.yml")
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    {"export": cmd_export, "import": cmd_import, "verify": cmd_verify}[a.command](cfg)


if __name__ == "__main__":
    main()
