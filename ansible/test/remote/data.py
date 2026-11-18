#!/usr/bin/env python3
"""Datos de prueba para verificar que una actualización no modifica datos.

    data.py seed    URL TOKEN fichero.json   # crea datos y guarda su huella
    data.py compare URL TOKEN fichero.json   # comprueba que siguen idénticos

Solo usa la biblioteca estándar (se ejecuta en el equipo de quien lanza la prueba).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
import uuid

CSV = b"codigo,nombre,valor\n1,Uno,10.5\n2,Dos,20\n3,Tres,30.25\n"
NTI = "http://datos.gob.es/kos/sector-publico/sector/"
SPA = "http://publications.europa.eu/resource/authority/language/SPA"


def call(url, token, action, data=None, files=None):
    full = f"{url}/api/3/action/{action}"
    headers = {"Authorization": token}
    if files:
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in (data or {}).items():
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        name, content = files
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="upload"; filename="{name}"\r\n'
                 f"Content-Type: text/csv\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(full, data=body, headers=headers)
    elif data is not None:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(full, data=json.dumps(data).encode(), headers=headers)
    else:
        req = urllib.request.Request(full, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        body = json.load(r)
    if not body.get("success"):
        raise SystemExit(f"{action}: {body.get('error')}")
    return body["result"]


def fingerprint(url, token) -> dict:
    ds = call(url, token, "package_show?" + urllib.parse.urlencode({"id": "prueba-actualizacion"}))
    res = ds["resources"][0]
    path = "/" + res["url"].split("/", 3)[3]
    with urllib.request.urlopen(urllib.request.Request(url + path, headers={"Authorization": token})) as r:
        sha = hashlib.sha256(r.read()).hexdigest()
    rows = call(url, token, "datastore_search?" + urllib.parse.urlencode({"resource_id": res["id"]}))
    return {
        "dataset_id": ds["id"], "title": ds["title"], "notes": ds["notes"], "license_id": ds["license_id"],
        "theme": ds["theme"], "metadata_modified": ds["metadata_modified"],
        "resource_id": res["id"], "file_sha256": sha,
        "datastore_total": rows["total"], "datastore_records": [{k: v for k, v in r.items() if k != "_id"}
                                                                for r in rows["records"]],
        "users": sorted(u["name"] for u in call(url, token, "user_list")),
        "datasets_total": call(url, token, "package_search?rows=0")["count"],
    }


def seed(url, token, out):
    call(url, token, "organization_create", {"name": "org-actualizacion", "title": "Organización de prueba"})
    call(url, token, "package_create", {
        "name": "prueba-actualizacion", "title": "Dato que debe sobrevivir a la actualización",
        "notes": "Creado con la versión anterior.", "owner_org": "org-actualizacion", "license_id": "CC-BY-4.0",
        "theme": [NTI + "sector-publico"], "language": [SPA]})
    call(url, token, "resource_create", {"package_id": "prueba-actualizacion", "name": "datos.csv", "format": "CSV"},
         files=("datos.csv", CSV))
    deadline = time.time() + 600
    while time.time() < deadline:
        res = call(url, token, "package_show?id=prueba-actualizacion")["resources"][0]
        if res.get("datastore_active"):
            break
        time.sleep(5)
    else:
        raise SystemExit("XLoader no cargó el CSV en DataStore a tiempo")
    fp = fingerprint(url, token)
    json.dump(fp, open(out, "w"), indent=1, ensure_ascii=False)
    print(f"datos creados: dataset {fp['dataset_id'][:8]}…, fichero sha256 {fp['file_sha256'][:12]}…, "
          f"{fp['datastore_total']} filas en DataStore, {fp['datasets_total']} datasets")


def compare(url, token, before_file):
    before = json.load(open(before_file))
    after = fingerprint(url, token)
    diffs = [k for k in before if before[k] != after[k]]
    for k in before:
        print(f"  {'✓' if k not in diffs else '✗'} {k}")
    if diffs:
        raise SystemExit(f"FALLO: cambiaron {diffs}")
    print("idénticos: metadatos, fichero (sha256), filas de DataStore, usuarios y número de datasets")


if __name__ == "__main__":
    cmd, url, token, path = sys.argv[1:5]
    {"seed": seed, "compare": compare}[cmd](url.rstrip("/"), token, path)
