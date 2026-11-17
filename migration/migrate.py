#!/usr/bin/env python3
"""Migración estándar de un portal CKAN existente a la baseline oGov.tech.

Repetible e idempotente. Tres fases, cada una deja un informe (JSON y
Markdown) y termina con código distinto de 0 si hay errores:

    python3 migrate.py export portales/<ayto>.yml   # descarga y valida (solo lectura en origen)
    python3 migrate.py import portales/<ayto>.yml   # carga en el portal nuevo
    python3 migrate.py verify portales/<ayto>.yml   # compara origen y destino

``import`` y ``verify`` necesitan ``CKAN_URL`` y ``CKAN_API_TOKEN`` (sysadmin).

Alcance (propuesta, apdo. 1.4.2): organizaciones, grupos, conjuntos de datos,
recursos compatibles con el esquema común y páginas de ckanext-pages. Se
conservan los UUID de datasets y recursos para que las URLs de descarga
antiguas sigan funcionando.

Los volcados e informes se escriben en ``$OGOV_TOOLS_DATA_DIR/migration``
(en la imagen: ``/var/lib/ckan/ogov-tools/migration``) o, si no está definida,
junto a este script. Nunca se versionan.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

import requests
import yaml

HERE = Path(__file__).resolve().parent
NTI = "http://datos.gob.es/kos/sector-publico/sector/"
EU_LANG = "http://publications.europa.eu/resource/authority/language/"
UA = "ogov-ckan-migration/2.0"
CHUNK = 1 << 20

ERROR, WARNING = "error", "warning"


# =============================================================== informe ===
@dataclasses.dataclass
class Issue:
    level: str
    stage: str
    message: str
    dataset: str = ""
    resource: str = ""


class Report:
    """Acumula incidencias y estadísticas; se guarda como JSON y Markdown."""

    def __init__(self, stage: str, portal: str):
        self.stage = stage
        self.portal = portal
        self.started = dt.datetime.now(dt.timezone.utc)
        self.issues: list[Issue] = []
        self.stats: dict[str, int] = {}

    def add(self, level: str, message: str, dataset: str = "", resource: str = "") -> None:
        self.issues.append(Issue(level, self.stage, message, dataset, resource))

    def error(self, message: str, **kw: str) -> None:
        self.add(ERROR, message, **kw)

    def warning(self, message: str, **kw: str) -> None:
        self.add(WARNING, message, **kw)

    def count(self, key: str, n: int = 1) -> None:
        self.stats[key] = self.stats.get(key, 0) + n

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "portal": self.portal,
            "started": self.started.isoformat(),
            "finished": dt.datetime.now(dt.timezone.utc).isoformat(),
            "result": "OK" if self.ok else "ERROR",
            "stats": self.stats,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "issues": [dataclasses.asdict(i) for i in self.issues],
        }

    def to_markdown(self) -> str:
        d = self.as_dict()
        lines = [
            f"# Informe de migración — {self.stage} — {self.portal}",
            "",
            f"- Resultado: **{d['result']}**",
            f"- Inicio: {d['started']}  ·  Fin: {d['finished']}",
            f"- Errores: {d['errors']}  ·  Avisos: {d['warnings']}",
            "",
            "| Métrica | Valor |",
            "|---|---|",
            *[f"| {k} | {v} |" for k, v in sorted(self.stats.items())],
        ]
        if self.issues:
            lines += ["", "| Nivel | Dataset | Recurso | Detalle |", "|---|---|---|---|"]
            for i in self.issues:
                msg = i.message.replace("|", "\\|")
                lines.append(f"| {i.level} | {i.dataset} | {i.resource} | {msg} |")
        return "\n".join(lines) + "\n"

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        stamp = self.started.strftime("%Y%m%dT%H%M%SZ")
        base = directory / f"{self.stage}-{stamp}"
        base.with_suffix(".json").write_text(json.dumps(self.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        base.with_suffix(".md").write_text(self.to_markdown(), encoding="utf-8")
        return base.with_suffix(".md")

    def summary(self) -> str:
        stats = ", ".join(f"{k}={v}" for k, v in sorted(self.stats.items()))
        return (f"[{self.stage}] {'OK' if self.ok else 'ERROR'} — "
                f"{len(self.errors)} errores, {len(self.warnings)} avisos — {stats}")


# ============================================================= utilidades ===
def data_dir() -> Path:
    env = os.environ.get("OGOV_TOOLS_DATA_DIR")
    return Path(env) / "migration" if env else HERE


def load_cfg(path: str) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    for key in ("source", "organization"):
        if key not in cfg:
            raise SystemExit(f"Falta '{key}' en {path}")
    for key in ("from", "name"):
        if key not in cfg["organization"]:
            raise SystemExit(f"Falta 'organization.{key}' en {path}")
    cfg["_slug"] = Path(path).stem
    cfg["_dump"] = data_dir() / "dumps" / cfg["_slug"]
    cfg["_reports"] = data_dir() / "reports" / cfg["_slug"]
    return cfg


def jsonl_write(path: Path, rows: Iterable[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def jsonl_read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "fichero"


def log(msg: str) -> None:
    print(msg, flush=True)


# ================================================= validación de ficheros ===
def check_source_hash(path: Path, declared: str) -> str | None:
    """Compara con el ``hash`` declarado en CKAN (``algoritmo:valor`` o md5/sha1 sin prefijo).

    Devuelve un mensaje de error o ``None`` si coincide o no se puede comprobar.
    """
    if not declared:
        return None
    algo, _, value = declared.partition(":")
    if not value:
        value = algo
        algo = {32: "md5", 40: "sha1", 64: "sha256"}.get(len(value), "")
    algo = algo.lower()
    if algo not in hashlib.algorithms_available or not re.fullmatch(r"[0-9a-fA-F]+", value):
        return None
    h = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    if h.hexdigest().lower() != value.lower():
        return f"hash {algo} distinto al declarado en origen"
    return None


def validate_download(path: Path, res: dict, content_type: str, content_length: int | None) -> list[tuple[str, str]]:
    """Valida un fichero descargado. Devuelve [(nivel, mensaje)]."""
    problems: list[tuple[str, str]] = []
    size = path.stat().st_size
    fmt = (res.get("format") or "").strip().lower()
    if size == 0:
        problems.append((ERROR, "fichero vacío (0 bytes)"))
    if content_length is not None and content_length != size:
        problems.append((ERROR, f"descarga incompleta: {size} de {content_length} bytes"))
    if "text/html" in (content_type or "").lower() and fmt not in ("html", "htm"):
        problems.append((ERROR, f"el servidor devolvió una página HTML en lugar de un fichero {fmt or 'de datos'}"))
    declared = res.get("size")
    if isinstance(declared, int) and declared > 0 and declared != size:
        problems.append((WARNING, f"tamaño {size} B distinto al declarado en origen ({declared} B)"))
    hash_problem = check_source_hash(path, res.get("hash") or "")
    if hash_problem:
        problems.append((ERROR, hash_problem))
    return problems


def download(session: requests.Session, url: str, target: Path, retries: int = 3) -> tuple[str, int | None]:
    """Descarga atómica (fichero .part + rename). Devuelve (content-type, content-length)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with session.get(url, stream=True, timeout=120, allow_redirects=True) as r:
                r.raise_for_status()
                ctype = r.headers.get("Content-Type", "")
                clen = r.headers.get("Content-Length")
                encoding = r.headers.get("Content-Encoding", "")
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(CHUNK):
                        f.write(chunk)
            tmp.replace(target)
            # Content-Length no es comparable si la respuesta venía comprimida.
            return ctype, (int(clen) if clen and clen.isdigit() and not encoding else None)
        except (requests.RequestException, OSError) as e:
            last = e
            time.sleep(min(2 ** attempt, 10))
    if tmp.exists():
        tmp.unlink()
    raise RuntimeError(f"descarga fallida tras {retries} intentos: {last}")


# ================================================================ export ===
def cmd_export(cfg: dict, src=None, session: requests.Session | None = None) -> Report:
    from ckanapi import RemoteCKAN

    rep = Report("export", cfg["_slug"])
    src = src or RemoteCKAN(cfg["source"], user_agent=UA)
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", UA)
    dump = cfg["_dump"]
    (dump / "files").mkdir(parents=True, exist_ok=True)
    manifest_path = dump / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    log(f"[export] {cfg['source']} -> {dump}")
    orgs = [src.action.organization_show(id=o, include_datasets=False) for o in src.action.organization_list()]
    if not any(o["name"] == cfg["organization"]["from"] for o in orgs):
        rep.error(f"la organización '{cfg['organization']['from']}' no existe en origen")
    groups = [src.action.group_show(id=g, include_datasets=False) for g in src.action.group_list()]

    datasets: list[dict] = []
    start, total = 0, None
    while total is None or start < total:
        page = src.action.package_search(q="*:*", rows=100, start=start, sort="name asc", include_private=True)
        total = page["count"]
        datasets += page["results"]
        start += 100
    if len(datasets) != total:
        rep.error(f"package_search anunció {total} datasets pero se recibieron {len(datasets)}")

    pages: list[dict] = []
    if cfg.get("pages"):
        try:
            pages = [src.action.ckanext_pages_show(page=p["name"]) for p in src.action.ckanext_pages_list()]
        except Exception as e:  # el portal de origen puede no tener ckanext-pages
            rep.warning(f"no se pudieron leer las páginas: {e}")

    for ds in datasets:
        rep.count("datasets")
        org_name = (ds.get("organization") or {}).get("name")
        if org_name != cfg["organization"]["from"]:
            rep.warning(f"pertenece a la organización '{org_name}', no a '{cfg['organization']['from']}': "
                        f"se importará en '{cfg['organization']['name']}'", dataset=ds["name"])
        for res in ds["resources"]:
            rep.count("recursos")
            res.pop("_file", None)
            res.pop("_sha256", None)
            res["_export"] = {"status": "ok", "issues": []}
            if res.get("url_type") != "upload":
                rep.count("recursos_externos")
                continue
            rep.count("ficheros")
            fname = safe_name(res["url"].rsplit("/", 1)[-1])
            target = dump / "files" / res["id"] / fname
            known = manifest.get(res["id"])
            try:
                if target.exists() and known and sha256_file(target) == known["sha256"]:
                    rep.count("ficheros_reutilizados")
                    ctype, clen = known.get("content_type", ""), None
                else:
                    ctype, clen = download(session, res["url"], target)
                    rep.count("ficheros_descargados")
            except RuntimeError as e:
                res["_export"] = {"status": ERROR, "issues": [str(e)]}
                rep.error(str(e), dataset=ds["name"], resource=res.get("name") or res["id"])
                continue
            problems = validate_download(target, res, ctype, clen)
            for level, msg in problems:
                rep.add(level, msg, dataset=ds["name"], resource=res.get("name") or res["id"])
            status = ERROR if any(l == ERROR for l, _ in problems) else ("warning" if problems else "ok")
            sha = sha256_file(target)
            res["_file"] = str(target.relative_to(dump))
            res["_sha256"] = sha
            res["_export"] = {"status": status, "issues": [m for _, m in problems]}
            manifest[res["id"]] = {"sha256": sha, "size": target.stat().st_size, "content_type": ctype}
            if status == ERROR:
                rep.count("ficheros_con_error")

    jsonl_write(dump / "organizations.jsonl", orgs)
    jsonl_write(dump / "groups.jsonl", groups)
    jsonl_write(dump / "datasets.jsonl", datasets)
    jsonl_write(dump / "pages.jsonl", pages)
    manifest_path.write_text(json.dumps(manifest, indent=1))
    rep.stats.update({"organizaciones": len(orgs), "grupos": len(groups), "paginas": len(pages)})
    return rep


# ============================================================ transformación ===
def map_license(license_id: str, cfg: dict) -> str:
    lic_cfg = cfg.get("licenses", {})
    lic = license_id or lic_cfg.get("if_empty") or ""
    return lic_cfg.get("replace", {}).get(lic, lic)


def themes_for(group_names: list[str], cfg: dict) -> list[str]:
    themes: list[str] = []
    for g in group_names:
        t = cfg.get("theme_by_group", {}).get(g)
        if t and NTI + t not in themes:
            themes.append(NTI + t)
    return themes or [NTI + cfg.get("theme_default", "sector-publico")]


def transform_dataset(ds: dict, cfg: dict) -> dict:
    group_names = [g["name"] for g in ds.get("groups", [])]
    out = {
        "id": ds["id"],
        "name": ds["name"],
        "title": ds["title"],
        "notes": ds.get("notes") or ds["title"],
        "owner_org": cfg["organization"]["name"],
        "private": bool(ds.get("private", False)),
        "license_id": map_license(ds.get("license_id") or "", cfg),
        "tags": [{"name": t["name"]} for t in ds.get("tags", [])],
        "groups": [{"name": g} for g in group_names],
        "theme": themes_for(group_names, cfg),
        "language": [EU_LANG + cfg.get("language_default", "SPA")],
    }
    for key in ("url", "version", "author", "author_email", "maintainer", "maintainer_email"):
        out[key] = ds.get(key) or ""
    contact = {k: v for k, v in (("name", ds.get("maintainer")), ("email", ds.get("maintainer_email"))) if v}
    if contact:
        out["contact"] = [contact]
    return out


RESOURCE_FIELDS = ("id", "name", "description", "format", "mimetype")


def resource_fields(res: dict) -> dict:
    return {k: res[k] for k in RESOURCE_FIELDS if res.get(k)}


# ================================================================ import ===
def target_ckan():
    from ckanapi import RemoteCKAN

    url, token = os.environ.get("CKAN_URL"), os.environ.get("CKAN_API_TOKEN")
    if not url or not token:
        raise SystemExit("Define CKAN_URL y CKAN_API_TOKEN (token de un sysadmin del portal nuevo).")
    return RemoteCKAN(url, apikey=token, user_agent=UA)


def upsert(dst, kind: str, data: dict) -> None:
    from ckanapi import NotFound

    try:
        dst.call_action(f"{kind}_show", {"id": data["name"]})
        dst.call_action(f"{kind}_patch", {**data, "id": data["name"]})
    except NotFound:
        dst.call_action(f"{kind}_create", data)


def import_dataset(dst, ds: dict, cfg: dict, dump: Path, rep: Report) -> bool:
    """Crea/actualiza un dataset y sus recursos. Devuelve True si todo fue bien."""
    from ckanapi import NotFound

    data = transform_dataset(ds, cfg)
    try:
        existing = dst.action.package_show(id=ds["id"])
        dst.action.package_update(**data, resources=existing["resources"])
    except NotFound:
        dst.action.package_create(**data)
    existing_ids = {r["id"] for r in dst.action.package_show(id=ds["id"])["resources"]}
    ok = True
    for res in ds["resources"]:
        rname = res.get("name") or res["id"]
        fields = {**resource_fields(res), "package_id": ds["id"]}
        action = "resource_update" if res["id"] in existing_ids else "resource_create"
        if res.get("url_type") == "upload":
            exported = res.get("_export", {})
            file_rel = res.get("_file")
            if exported.get("status") == ERROR or not file_rel or not (dump / file_rel).exists():
                rep.error("fichero no disponible o inválido en el volcado; recurso NO importado "
                          "(se evita dejar un enlace al portal antiguo)", dataset=ds["name"], resource=rname)
                ok = False
                continue
            with (dump / file_rel).open("rb") as fh:
                dst.call_action(action, {**fields, "url": Path(file_rel).name}, files={"upload": fh})
            rep.count("ficheros_subidos")
        else:
            dst.call_action(action, {**fields, "url": res["url"]})
            rep.count("recursos_externos")
    return ok


def cmd_import(cfg: dict, dst=None) -> Report:
    from ckanapi import ValidationError

    rep = Report("import", cfg["_slug"])
    dst = dst or target_ckan()
    dump = cfg["_dump"]
    datasets = jsonl_read(dump / "datasets.jsonl")
    if not datasets:
        rep.error(f"no hay volcado en {dump}: ejecuta antes 'export'")
        return rep
    org_cfg = cfg["organization"]
    src_org = next((o for o in jsonl_read(dump / "organizations.jsonl") if o["name"] == org_cfg["from"]), {})
    upsert(dst, "organization", {"name": org_cfg["name"], "title": org_cfg.get("title") or src_org.get("title"),
                                 "description": src_org.get("description", "")})
    for g in jsonl_read(dump / "groups.jsonl"):
        data = {"name": g["name"], "title": g["title"], "description": g.get("description", "")}
        if cfg.get("groups", {}).get("migrate_images"):
            data["image_url"] = g.get("image_url", "")
        upsert(dst, "group", data)
        rep.count("grupos")

    for ds in datasets:
        try:
            if import_dataset(dst, ds, cfg, dump, rep):
                rep.count("datasets_ok")
            else:
                rep.count("datasets_incompletos")
        except ValidationError as e:
            rep.error(f"validación rechazada: {e.error_dict}", dataset=ds["name"])
            rep.count("datasets_error")
        except Exception as e:  # registrar y seguir: el informe debe ser completo
            rep.error(f"{type(e).__name__}: {e}", dataset=ds["name"])
            rep.count("datasets_error")

    for p in jsonl_read(dump / "pages.jsonl"):
        dst.action.ckanext_pages_update(
            page=p["name"], name=p["name"], title=p["title"], content=p.get("content", ""),
            private=str(p.get("private")).lower() == "true", order=p.get("order") or "",
            page_type=p.get("page_type") or "page")
        rep.count("paginas")
    return rep


# ================================================================ verify ===
def compare_dataset(src: dict, new: dict, cfg: dict) -> list[tuple[str, str, str]]:
    """Compara metadatos y recursos. Devuelve [(nivel, recurso, mensaje)]."""
    out: list[tuple[str, str, str]] = []
    expected = transform_dataset(src, cfg)
    for key in ("name", "title", "notes", "license_id", "owner_org_name"):
        exp = expected["owner_org"] if key == "owner_org_name" else expected.get(key)
        got = (new.get("organization") or {}).get("name") if key == "owner_org_name" else new.get(key)
        if (exp or "") != (got or ""):
            out.append((ERROR, "", f"{key}: esperado {exp!r}, encontrado {got!r}"))
    if {t["name"] for t in expected["tags"]} != {t["name"] for t in new.get("tags", [])}:
        out.append((ERROR, "", "etiquetas distintas"))
    if {g["name"] for g in expected["groups"]} != {g["name"] for g in new.get("groups", [])}:
        out.append((ERROR, "", "grupos distintos"))
    if not new.get("theme"):
        out.append((ERROR, "", "sin temática NTI (obligatoria para datos.gob.es)"))

    new_res = {r["id"]: r for r in new.get("resources", [])}
    src_ids = {r["id"] for r in src["resources"]}
    for extra in set(new_res) - src_ids:
        out.append((WARNING, new_res[extra].get("name", extra), "recurso adicional que no está en origen"))
    for res in src["resources"]:
        rname = res.get("name") or res["id"]
        got = new_res.get(res["id"])
        if not got:
            out.append((ERROR, rname, "recurso no encontrado en destino"))
            continue
        for key in ("name", "description"):
            if (res.get(key) or "") != (got.get(key) or ""):
                out.append((ERROR, rname, f"{key} distinto"))
        if (res.get("format") or "").lower() != (got.get("format") or "").lower():
            out.append((ERROR, rname, f"formato: origen {res.get('format')!r}, destino {got.get('format')!r}"))
        if res.get("url_type") != "upload" and res.get("url") != got.get("url"):
            out.append((ERROR, rname, "URL externa distinta"))
        if res.get("url_type") == "upload" and got.get("url_type") != "upload":
            out.append((ERROR, rname, "en origen era un fichero subido y en destino no"))
    return out


def fetch_sha256(session: requests.Session, url: str, retries: int = 3) -> str:
    """Huella del fichero servido por el destino. Reintenta cortes transitorios
    (p. ej. CKAN ocupado procesando XLoader); un 4xx no se reintenta."""
    for attempt in range(1, retries + 1):
        try:
            h = hashlib.sha256()
            with session.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                for chunk in r.iter_content(CHUNK):
                    h.update(chunk)
            return h.hexdigest()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code < 500:
                raise
            if attempt == retries:
                raise
        except requests.RequestException:
            if attempt == retries:
                raise
        time.sleep(min(2 ** attempt, 10))
    raise AssertionError("inalcanzable")


def internal_url(public_url: str, ckan_url: str) -> str:
    """La URL pública puede no ser accesible desde donde se ejecuta (p. ej. dentro del contenedor)."""
    path = "/" + public_url.split("/", 3)[3] if public_url.count("/") >= 3 else public_url
    return ckan_url.rstrip("/") + path


def cmd_verify(cfg: dict, dst=None, session: requests.Session | None = None) -> Report:
    from ckanapi import NotFound

    rep = Report("verify", cfg["_slug"])
    dst = dst or target_ckan()
    ckan_url = os.environ.get("CKAN_URL", "")
    session = session or requests.Session()
    session.headers.setdefault("User-Agent", UA)
    if os.environ.get("CKAN_API_TOKEN"):
        session.headers["Authorization"] = os.environ["CKAN_API_TOKEN"]

    src = jsonl_read(cfg["_dump"] / "datasets.jsonl")
    if not src:
        rep.error("no hay volcado: ejecuta antes 'export'")
        return rep
    for ds in src:
        rep.count("datasets")
        try:
            new = dst.action.package_show(id=ds["id"])
        except NotFound:
            rep.error("dataset no encontrado en destino", dataset=ds["name"])
            continue
        for level, rname, msg in compare_dataset(ds, new, cfg):
            rep.add(level, msg, dataset=ds["name"], resource=rname)
        new_res = {r["id"]: r for r in new.get("resources", [])}
        for res in ds["resources"]:
            rep.count("recursos")
            if res.get("url_type") != "upload" or res["id"] not in new_res:
                continue
            rname = res.get("name") or res["id"]
            if not res.get("_sha256"):
                rep.error("sin huella del fichero en el volcado (export falló)", dataset=ds["name"], resource=rname)
                continue
            try:
                got = fetch_sha256(session, internal_url(new_res[res["id"]]["url"], ckan_url))
            except requests.RequestException as e:
                rep.error(f"no se pudo descargar del destino: {e}", dataset=ds["name"], resource=rname)
                continue
            if got != res["_sha256"]:
                rep.error("el fichero en destino no coincide byte a byte con el de origen",
                          dataset=ds["name"], resource=rname)
            else:
                rep.count("ficheros_identicos")

    # Datasets en la organización destino que no vienen del origen
    org = cfg["organization"]["name"]
    found = dst.action.package_search(fq=f"organization:{org}", rows=0, include_private=True)["count"]
    rep.stats["datasets_en_destino_org"] = found
    if found < len(src):
        rep.error(f"la organización {org} tiene {found} datasets y el origen {len(src)}")
    return rep


# ================================================================== main ===
COMMANDS = {"export": cmd_export, "import": cmd_import, "verify": cmd_verify}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("config", help="portales/<ayuntamiento>.yml")
    ap.add_argument("--allow-errors", action="store_true",
                    help="termina con código 0 aunque haya errores (no recomendado)")
    a = ap.parse_args(argv)
    cfg = load_cfg(a.config)
    rep = COMMANDS[a.command](cfg)
    path = rep.save(cfg["_reports"])
    log(rep.summary())
    for issue in rep.errors[:20]:
        log(f"  ✗ {issue.dataset} / {issue.resource}: {issue.message}")
    if len(rep.errors) > 20:
        log(f"  … y {len(rep.errors) - 20} errores más")
    log(f"Informe: {path}")
    return 0 if rep.ok or a.allow_errors else 1


if __name__ == "__main__":
    sys.exit(main())
