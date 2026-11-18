#!/usr/bin/env python3
"""Rastreo completo de un portal: recorre todas las páginas internas enlazadas
y comprueba enlaces, recursos estáticos, vistas, descargas y textos.

    python3 crawl.py http://localhost:8090 [--max-pages 3000] [--workers 4]
    CRAWL_PASSWORD=… python3 crawl.py http://localhost:8090 --login usuario [--forms]

Con --login se navega con sesión iniciada (contraseña en CRAWL_PASSWORD) y con
--forms se abren también los formularios de edición y alta (solo GET: nunca se
envía un formulario ni se borra nada; el cierre de sesión y los borrados se
excluyen siempre).

Solo biblioteca estándar. Código de salida 1 si hay incidencias de nivel ERROR.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import http.cookiejar
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = "ogov-crawl/1.0"
# Rutas que no se rastrean (acciones, formularios o combinaciones infinitas).
SKIP = re.compile(r"/(user/(login|logout|register|reset)|api/|dataset/new|organization/new|group/new|"
                  r"ckan-admin|dashboard|.*/(edit|delete|new_resource|follow|unfollow|followers|"
                  r"resource/new|views)/?)|/webassets/|/base/|/ogovtech_theme/|\.(rdf|ttl|jsonld|xml|n3)$")
# Con --forms solo se excluyen las rutas que cambian estado o cierran la sesión.
SKIP_FORMS = re.compile(r"/(user/(logout|_logout|reset)|api/|.*/(delete|follow|unfollow|purge|trash|reset_config|reset)/?)|"
                        r"/webassets/|/base/|/ogovtech_theme/|\.(rdf|ttl|jsonld|xml|n3)$")
# Rutas de descarga: se comprueban con HEAD, no se rastrean.
DOWNLOAD = re.compile(r"/download/")
ERROR_TEXTS = {
    "Internal server error": "error interno",
    "Error 500": "error 500",
    "There are no views created for this resource yet": "recurso sin vista (mensaje del core)",
    "Todavía no existen vistas creadas para este recurso": "recurso sin vista (mensaje del core)",
    "Traceback (most recent call last)": "traza de error",
}
# Textos en inglés que no deberían verse en un portal solo en español.
ENGLISH = ["There is no description", " Member", " Followers", "Datasets", "Read more", "View ",
           "Explore", "Download", "No description", "Last Updated", "Created", "Format",
           "Navigate to", "Close", "Remove", "Delete", "Publish", "Search datasets", "Filter by",
           "Open details", "Activity Stream", "Changes", "Members", "Edit", "Add", "Unpublish",
           "active", "draft", "deleted", "Previous", "Next", "Loading", "Submit", "Cancel"]
# Atributos que leen los lectores de pantalla o aparecen como ayuda emergente.
TEXT_ATTRS = ("aria-label", "title", "placeholder", "alt")


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.assets, self.iframes, self.text = [], [], [], []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self._skip += 1
        for name in TEXT_ATTRS:
            if a.get(name) and a[name].strip() and tag not in ("script", "style", "link", "meta"):
                self.text.append(a[name].strip())
        if tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "img" and a.get("src"):
            self.assets.append(a["src"])
        elif tag == "link" and a.get("href") and a.get("rel") in ("stylesheet", "icon", "shortcut icon"):
            self.assets.append(a["href"])
        elif tag == "script" and a.get("src"):
            self.assets.append(a["src"])
        elif tag == "iframe" and a.get("src"):
            self.iframes.append(a["src"])

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.text.append(data.strip())


OPENER = urllib.request.build_opener()


def login(base: str, user: str, password: str) -> None:
    """Inicia sesión con el formulario de CKAN; la cookie queda en OPENER."""
    global OPENER
    jar = http.cookiejar.CookieJar()
    OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    _, _, body = fetch(base + "/user/login")
    m = re.search(r'name="_csrf_token"\s+(?:value|content)="([^"]+)"', body)
    data = urllib.parse.urlencode({"login": user, "password": password,
                                   "_csrf_token": m.group(1) if m else ""}).encode()
    req = urllib.request.Request(base + "/user/login", data=data, headers={"User-Agent": UA})
    OPENER.open(req, timeout=60).read()
    _, _, home = fetch(base + "/dashboard/datasets")
    if "/user/_logout" not in home and "/user/logout" not in home:
        raise SystemExit(f"no se pudo iniciar sesión como {user}")


def fetch(url: str, method: str = "GET") -> tuple[int, str, str]:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    try:
        with OPENER.open(req, timeout=60) as r:
            body = r.read().decode("utf-8", "replace") if method == "GET" else ""
            return r.status, r.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", ""
    except Exception as e:  # noqa: BLE001
        return 0, "", str(e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("--max-pages", type=int, default=3000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--login", help="usuario con el que navegar (contraseña en CRAWL_PASSWORD)")
    ap.add_argument("--forms", action="store_true", help="abrir también formularios de edición y alta")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    if a.login:
        login(base, a.login, os.environ["CRAWL_PASSWORD"])
    skip = SKIP_FORMS if a.forms else SKIP
    host = urllib.parse.urlsplit(base).netloc

    def internal(u: str, page_url: str) -> str | None:
        full = urllib.parse.urljoin(page_url, u)
        p = urllib.parse.urlsplit(full)
        if p.scheme not in ("http", "https") or p.netloc != host:
            return None
        q = urllib.parse.parse_qs(p.query)
        # Solo se siguen consultas de paginación y búsqueda simple
        if p.query and not (set(q) <= {"page", "q"}):
            return None
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))

    issues: list[tuple[str, str, str]] = []           # (nivel, url, detalle)
    seen, queue = set(), [base + "/", base + "/dataset/", base + "/group/", base + "/organization/",
                          base + "/pages", base + "/about"]
    if a.login:
        queue += [base + "/dashboard/datasets", base + "/user/" + a.login]
    if a.forms:
        queue += [base + "/dataset/new", base + "/ckan-admin/config", base + "/ckan-admin"]
    assets, downloads, iframes = {}, {}, {}
    statuses = collections.Counter()
    english = collections.Counter()
    english_where: dict[str, str] = {}

    with cf.ThreadPoolExecutor(a.workers) as pool:
        while queue and len(seen) < a.max_pages:
            batch = [u for u in dict.fromkeys(queue) if u not in seen][: a.workers * 4]
            queue = [u for u in queue if u not in batch]
            seen.update(batch)
            for url, (status, ctype, body) in zip(batch, pool.map(fetch, batch)):
                statuses[status] += 1
                if status != 200:
                    issues.append(("ERROR", url, f"HTTP {status}"))
                    continue
                if "html" not in ctype:
                    continue
                pg = Page()
                pg.feed(body)
                text = " ".join(pg.text)
                for needle, label in ERROR_TEXTS.items():
                    if needle in text:
                        issues.append(("ERROR" if "error" in label or "traza" in label else "AVISO", url, label))
                for word in ENGLISH:
                    if re.search(rf"\b{re.escape(word.strip())}\b", text):
                        english[word.strip()] += 1
                        english_where.setdefault(word.strip(), url)
                for href in pg.links:
                    full = urllib.parse.urljoin(url, href)
                    if DOWNLOAD.search(full) and urllib.parse.urlsplit(full).netloc == host:
                        downloads.setdefault(full, url)
                        continue
                    u = internal(href, url)
                    if u and u not in seen and not skip.search(urllib.parse.urlsplit(u).path):
                        queue.append(u)
                for src in pg.assets:
                    full = urllib.parse.urljoin(url, src)
                    assets.setdefault(full, url)
                for src in pg.iframes:
                    full = urllib.parse.urljoin(url, src)
                    iframes.setdefault(full, url)

        def check(items: dict, method: str, label: str):
            urls = list(items)
            for u, (status, _, _) in zip(urls, pool.map(lambda x: fetch(x, method), urls)):
                if status != 200:
                    issues.append(("ERROR", items[u], f"{label} {status}: {u}"))
            return len(urls)

        n_assets = check(assets, "GET", "recurso estático")
        n_iframes = check(iframes, "GET", "vista incrustada")
        n_downloads = check(downloads, "HEAD", "descarga")

    errors = [i for i in issues if i[0] == "ERROR"]
    print(f"Páginas rastreadas: {len(seen)}  (códigos: {dict(statuses)})")
    print(f"Recursos estáticos: {n_assets} · vistas incrustadas: {n_iframes} · descargas: {n_downloads}")
    print(f"Incidencias: {len(errors)} errores, {len(issues) - len(errors)} avisos")
    by_kind = collections.defaultdict(list)
    for level, url, detail in issues:
        by_kind[(level, re.sub(r"https?://\S+", "", detail).strip())].append(url)
    for (level, detail), urls in sorted(by_kind.items(), key=lambda x: (x[0][0] != "ERROR", -len(x[1]))):
        print(f"  {level:5} x{len(urls):<4} {detail}  · p. ej. {urls[0]}")
    if english:
        print("Textos en inglés visibles (páginas):", dict(english.most_common()))
        for word, where in english_where.items():
            print(f"  '{word}' p. ej. en {where}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
