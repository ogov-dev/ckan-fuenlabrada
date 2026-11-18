#!/usr/bin/env python3
"""Conector Portal de Transparencia oGov -> CKAN.

Lee la API pública de lectura de un portal de transparencia de oGov
(``/api/v1``) y publica cada tabla (entidad dinámica) como un conjunto de
datos en CKAN, con:

* un CSV y un JSON con columnas legibles (etiquetas del visor, no los
  nombres internos ``builderInputComponent…``),
* los documentos adjuntos (PDF, XLSX…) como enlaces dentro de las columnas,
* un recurso que enlaza al visor original del portal de transparencia.

Es idempotente: cada ejecución actualiza los mismos conjuntos de datos (solo
resube lo que cambia) y retira, con salvaguardas, los de tablas que ya no
existen. Se programa por instancia con Ansible (ver docs/INSTALACION.md).

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
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

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
        except Exception as e:
            log(f"  aviso: sin esquema del visor {route} ({type(e).__name__}); se usan claves internas")
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


def build_package(name: str, slug: str, route: str, src_base: str, cfg: dict) -> dict:
    rules = cfg.get("clasificacion", [])
    group = pick(rules, name, "grupo", cfg.get("grupo_por_defecto"))
    theme = pick(rules, name, "tematica", cfg.get("tematica_por_defecto", "sector-publico"))
    return {
        "name": (cfg.get("dataset_prefix", "transparencia-") + slug)[:100],
        "title": name,
        "notes": (f"{name}. Información publicada en el Portal de Transparencia "
                  f"({src_base}). Actualizado automáticamente desde su API pública; "
                  f"los documentos enlazados se sirven desde el propio portal de transparencia."),
        "owner_org": cfg["organization"],
        "license_id": cfg.get("license_id", "odc-by"),
        "tags": [{"name": "transparencia"}],
        "theme": [NTI + theme],
        "language": [EU_LANG],
        "url": src_base + route,
        "groups": [{"name": group}] if group else [],
    }


def serialize(labels: list[str], rows: list[list[str]]) -> tuple[bytes, bytes]:
    """CSV (UTF-8 con BOM, legible en Excel) y JSON (lista de objetos)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(labels)
    w.writerows(rows)
    as_json = json.dumps([dict(zip(labels, r)) for r in rows], ensure_ascii=False, indent=1)
    return buf.getvalue().encode("utf-8-sig"), as_json.encode("utf-8")


VIEWER_RESOURCE = "Visor en el Portal de Transparencia"
# Huella del contenido publicado. Campo propio: XLoader sobrescribe "hash".
HASH_FIELD = "ogov_content_sha256"


def publish(dst, pkg: dict, slug: str, labels: list[str], rows: list[list[str]]) -> int:
    """Crea o actualiza el dataset y sus tres recursos (idempotente: por nombre).

    Solo sube CSV/JSON si su contenido cambió (huella SHA-256 en ``HASH_FIELD``).
    Devuelve el número de ficheros subidos."""
    try:
        current_pkg = dst.action.package_show(id=pkg["name"])
        # Si la tabla se retiró en una ejecución anterior y vuelve a existir,
        # se reactiva (sin esto quedaría borrada para siempre).
        state = {"state": "active"} if current_pkg.get("state") == "deleted" else {}
        dst.action.package_patch(id=pkg["name"], **pkg, **state)
    except NotFound:
        dst.action.package_create(**pkg)
    current = {r["name"]: r for r in dst.action.package_show(id=pkg["name"])["resources"]}
    csv_bytes, json_bytes = serialize(labels, rows)
    title = pkg["title"]
    uploaded = 0
    for rname, fmt, fname, content in ((f"{title} (CSV)", "CSV", f"{slug}.csv", csv_bytes),
                                       (f"{title} (JSON)", "JSON", f"{slug}.json", json_bytes)):
        digest = hashlib.sha256(content).hexdigest()
        existing = current.get(rname)
        if existing and existing.get(HASH_FIELD) == digest:
            continue  # sin cambios: no se resube ni se reprocesa en XLoader
        data = {"package_id": pkg["name"], "name": rname, "format": fmt, "url": fname, HASH_FIELD: digest}
        if existing:
            data["id"] = existing["id"]
        dst.call_action("resource_update" if existing else "resource_create", data,
                        files={"upload": (fname, io.BytesIO(content))})
        uploaded += 1
    vdata = {"package_id": pkg["name"], "name": VIEWER_RESOURCE, "format": "HTML", "url": pkg["url"]}
    viewer = current.get(VIEWER_RESOURCE)
    if not viewer:
        dst.action.resource_create(**vdata)
    elif viewer.get("url") != pkg["url"]:
        dst.action.resource_update(id=viewer["id"], **vdata)
    return uploaded


def publish_with_retry(dst, pkg: dict, slug: str, labels, rows, retries: int = 3) -> int:
    """publish() con reintentos ante cortes de red transitorios (CKAN ocupado,
    p. ej. procesando XLoader). Los errores de validación no se reintentan."""
    for attempt in range(1, retries + 1):
        try:
            return publish(dst, pkg, slug, labels, rows)
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == retries:
                raise
            log(f"  reintento {attempt} de {pkg['name']} tras error de red: {e}")
            time.sleep(5 * attempt)
    raise AssertionError("inalcanzable")


def existing_datasets(dst, cfg: dict) -> list[str]:
    """Datasets del conector ya publicados (por organización, etiqueta y prefijo)."""
    prefix = cfg.get("dataset_prefix", "transparencia-")
    res = dst.action.package_search(fq=f'organization:"{cfg["organization"]}" +tags:transparencia',
                                    rows=1000, include_private=True)
    return sorted(d["name"] for d in res["results"] if d["name"].startswith(prefix))


def plan_retirement(existing: list[str], expected: set[str], stats: dict, cfg: dict) -> tuple[list[str], str | None]:
    """Qué datasets retirar y, si hay que bloquear la retirada, por qué.

    Nunca se retira nada si la ejecución no es fiable: errores, origen sin
    tablas, o una retirada masiva (por encima de ``max_retirada``, por defecto
    20 %) que apunta a un fallo del origen y no a un cambio real."""
    to_retire = [n for n in existing if n not in expected]
    if not to_retire:
        return [], None
    if stats["errores"]:
        return to_retire, "hubo errores en la ejecución"
    if not expected:
        return to_retire, "el origen no devolvió ninguna tabla"
    limit = float(cfg.get("max_retirada", 0.2))
    if existing and len(to_retire) / len(existing) > limit:
        return to_retire, f"se retirarían {len(to_retire)} de {len(existing)} (más del {limit:.0%})"
    return to_retire, None


def run(cfg: dict, src: Source, dst=None, solo: str | None = None) -> dict:
    """Recorre las tablas del portal. Con ``dst=None`` no escribe (dry-run).

    Publica/actualiza las tablas con datos y retira (borrado recuperable) los
    datasets cuyas tablas ya no existen, están vacías o se han excluido."""
    excluded = {slugify(x) for x in cfg.get("excluir", [])}
    prefix = cfg.get("dataset_prefix", "transparencia-")
    stats = {"publicadas": 0, "vacias": 0, "excluidas": 0, "errores": 0, "ficheros_subidos": 0,
             "retiradas": 0, "retirada_bloqueada": 0}
    detail: dict[str, list] = {"actualizadas": [], "retiradas": [], "errores": [], "bloqueo": [], "avisos": []}
    expected: set[str] = set()
    groups: set[str] | None = None
    if dst is not None:
        try:
            dst.action.organization_show(id=cfg["organization"])
        except NotFound:
            raise SystemExit(f"La organización '{cfg['organization']}' no existe en CKAN: créala antes de sincronizar.")
        groups = set(dst.action.group_list())
    for ent in src.entities(cfg.get("module", 5)):
        name = ent["name"].get(src.lang) or next(iter(ent["name"].values()))
        route = ent.get("route") or "/" + slugify(name)
        slug = slugify(route.strip("/") or name)
        if solo and solo not in (slug, route.strip("/")):
            continue
        if slug in excluded or slugify(name) in excluded:
            stats["excluidas"] += 1
            log(f"- {name}: excluida por configuración")
            continue
        records = src.records(ent["uuid"])
        if not records:
            stats["vacias"] += 1
            log(f"- {name}: sin registros, se omite")
            continue
        expected.add((prefix + slug)[:100])
        labels, rows = table(src, records, src.schema(route, ent["uuid"]))
        log(f"+ {name}: {len(rows)} registros, {len(labels)} columnas")
        if dst is None:
            log("    " + " · ".join(labels[:8]))
            stats["publicadas"] += 1
            continue
        try:
            pkg = build_package(name, slug, route, src.base, cfg)
            missing = [g["name"] for g in pkg["groups"] if g["name"] not in groups]
            if missing:
                pkg["groups"] = [g for g in pkg["groups"] if g["name"] in groups]
                warning = f"{name}: el grupo {', '.join(missing)} no existe en CKAN; se publica sin él"
                if warning not in detail["avisos"]:
                    detail["avisos"].append(warning)
                    log(f"  aviso: {warning}")
            uploaded = publish_with_retry(dst, pkg, slug, labels, rows)
            stats["ficheros_subidos"] += uploaded
            stats["publicadas"] += 1
            if uploaded:
                detail["actualizadas"].append(name)
        except Exception as e:  # registrar y seguir con el resto de tablas
            stats["errores"] += 1
            detail["errores"].append(f"{name}: {type(e).__name__}: {e}")
            log(f"! {name}: {type(e).__name__}: {e}")
        time.sleep(0.2)

    if dst is not None and not solo:
        to_retire, blocked = plan_retirement(existing_datasets(dst, cfg), expected, stats, cfg)
        if blocked:
            stats["retirada_bloqueada"] = len(to_retire)
            detail["bloqueo"] = [blocked] + to_retire
            log(f"! retirada BLOQUEADA ({blocked}): {', '.join(to_retire)}")
        for ds_name in ([] if blocked else to_retire):
            dst.action.package_delete(id=ds_name)       # borrado recuperable (estado "deleted")
            stats["retiradas"] += 1
            detail["retiradas"].append(ds_name)
            log(f"- retirado {ds_name}: la tabla ya no existe, está vacía o se excluyó")
    stats["detalle"] = detail
    return stats


def save_report(stats: dict, cfg_path: str) -> Path | None:
    base = os.environ.get("OGOV_TOOLS_DATA_DIR")
    if not base:
        return None
    d = Path(base) / "connectors" / "transparencia" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ok = not stats["errores"] and not stats["retirada_bloqueada"]
    path = d / f"{Path(cfg_path).stem}-{stamp}.json"
    path.write_text(json.dumps({"fecha": stamp, "resultado": "OK" if ok else "ERROR", **stats},
                               ensure_ascii=False, indent=2))
    (d / f"{Path(cfg_path).stem}-ultimo.json").write_text(path.read_text())
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("config")
    ap.add_argument("--solo", help="procesar solo la tabla con esta ruta/slug")
    ap.add_argument("--dry-run", action="store_true", help="no escribe en CKAN")
    a = ap.parse_args(argv)
    with open(a.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    src = Source(cfg["source"], cfg.get("lang", "es"))
    dst = None
    if not a.dry_run:
        url, token = os.environ.get("CKAN_URL"), os.environ.get("CKAN_API_TOKEN")
        if not url or not token:
            raise SystemExit("Define CKAN_URL y CKAN_API_TOKEN (o usa --dry-run).")
        dst = RemoteCKAN(url, apikey=token, user_agent=UA)
    stats = run(cfg, src, dst, a.solo)
    report = None if a.dry_run else save_report(stats, a.config)
    log(f"[transparencia] {stats['publicadas']} tablas publicadas, {stats['vacias']} vacías, "
        f"{stats['excluidas']} excluidas, {stats['errores']} con error, "
        f"{stats['ficheros_subidos']} ficheros subidos (el resto sin cambios), "
        f"{stats['retiradas']} retiradas, {stats['retirada_bloqueada']} con retirada bloqueada")
    if report:
        log(f"Informe: {report}")
    return 1 if stats["errores"] or stats["retirada_bloqueada"] else 0


if __name__ == "__main__":
    sys.exit(main())
