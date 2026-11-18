"""Tests unitarios de migration/migrate.py (sin red ni CKAN real)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import requests
from ckanapi import NotFound

import migrate as m

NTI = m.NTI


# ------------------------------------------------------------- dobles ---
class FakeResponse:
    def __init__(self, body: bytes = b"", status: int = 200, headers: dict | None = None):
        self.body, self.status_code, self.headers = body, status, headers or {}

    @property
    def text(self):
        return self.body.decode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            yield self.body[i:i + size]


class FakeSession:
    """Devuelve respuestas por URL; una lista se consume en orden (para reintentos)."""

    def __init__(self, routes: dict):
        self.routes, self.headers, self.calls = routes, {}, []

    def get(self, url, **kw):
        self.calls.append(url)
        r = self.routes[url]
        if isinstance(r, list):
            r = r.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _Actions:
    def __init__(self, ckan):
        self._ckan = ckan

    def __getattr__(self, name):
        return lambda **kw: self._ckan.call_action(name, kw)


class FakeCKAN:
    def __init__(self, handlers: dict | None = None):
        self.handlers = handlers or {}
        self.calls: list[tuple[str, dict, dict | None]] = []
        self.action = _Actions(self)

    def call_action(self, name, data=None, files=None, **kw):
        self.calls.append((name, data or {}, files))
        h = self.handlers.get(name)
        if callable(h):
            return h(data or {})
        return h


@pytest.fixture
def cfg(tmp_path):
    return {
        "_slug": "prueba",
        "_dump": tmp_path / "dump",
        "_reports": tmp_path / "reports",
        "source": "https://origen.example",
        "organization": {"from": "vieja", "name": "nueva", "title": "Nueva"},
        "licenses": {"replace": {"uk-ogl": "odc-by"}, "if_empty": "odc-by"},
        "theme_by_group": {"urbanismo": "urbanismo-infraestructuras", "infra": "urbanismo-infraestructuras",
                           "hacienda": "hacienda"},
        "theme_default": "sector-publico",
        "language_default": "SPA",
        "site_about": False,          # la página "Acerca de" tiene sus propios tests
    }


def dataset(**kw):
    base = {"id": "d1", "name": "ds-uno", "title": "Uno", "notes": "Notas", "license_id": "odc-by",
            "tags": [{"name": "a"}], "groups": [{"name": "urbanismo"}], "resources": [],
            "organization": {"name": "vieja"}, "type": "dataset", "state": "active", "private": False}
    base.update(kw)
    return base


# ------------------------------------------------------------- informe ---
def test_report_ok_y_errores(tmp_path):
    rep = m.Report("export", "p")
    rep.count("x", 2)
    rep.warning("aviso | con barra", dataset="d")
    assert rep.ok
    rep.error("fallo")
    assert not rep.ok and len(rep.errors) == 1 and len(rep.warnings) == 1
    md_path = rep.save(tmp_path)
    data = json.loads(md_path.with_suffix(".json").read_text())
    assert data["result"] == "ERROR" and data["stats"] == {"x": 2} and data["errors"] == 1
    assert "aviso \\| con barra" in md_path.read_text()


# ---------------------------------------------------- validación ficheros ---
@pytest.mark.parametrize("declared,ok", [
    ("md5:{md5}", True), ("{sha1}", True), ("sha256:{sha256}", True), ("md5:" + "0" * 32, False),
    ("", True), ("raro:zzz", True),
])
def test_check_source_hash(tmp_path, declared, ok):
    f = tmp_path / "f.csv"
    f.write_bytes(b"a,b\n1,2\n")
    raw = f.read_bytes()
    declared = declared.format(md5=hashlib.md5(raw).hexdigest(), sha1=hashlib.sha1(raw).hexdigest(),
                               sha256=hashlib.sha256(raw).hexdigest())
    assert (m.check_source_hash(f, declared) is None) is ok


def _file(tmp_path, body: bytes) -> Path:
    p = tmp_path / "x.csv"
    p.write_bytes(body)
    return p


def test_validate_download_correcto(tmp_path):
    p = _file(tmp_path, b"a,b\n")
    assert m.validate_download(p, {"format": "CSV", "size": 4}, "text/csv", 4) == []


@pytest.mark.parametrize("body,res,ctype,clen,level,frag", [
    (b"", {"format": "CSV"}, "text/csv", 0, m.ERROR, "vacío"),
    (b"abc", {"format": "CSV"}, "text/csv", 10, m.ERROR, "incompleta"),
    (b"<html>", {"format": "CSV"}, "text/html; charset=utf-8", None, m.ERROR, "HTML"),
    (b"abc", {"format": "CSV", "size": 99}, "text/csv", None, m.WARNING, "declarado"),
])
def test_validate_download_detecta(tmp_path, body, res, ctype, clen, level, frag):
    problems = m.validate_download(_file(tmp_path, body), res, ctype, clen)
    assert any(lvl == level and frag in msg for lvl, msg in problems), problems


def test_validate_download_html_permitido_si_formato_html(tmp_path):
    assert m.validate_download(_file(tmp_path, b"<html>"), {"format": "HTML"}, "text/html", None) == []


def test_download_reintenta_y_es_atomico(tmp_path, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    target = tmp_path / "f" / "a.csv"
    sess = FakeSession({"u": [requests.ConnectionError("caído"),
                              FakeResponse(b"datos", headers={"Content-Type": "text/csv", "Content-Length": "5"})]})
    ctype, clen = m.download(sess, "u", target)
    assert target.read_bytes() == b"datos" and (ctype, clen) == ("text/csv", 5)
    assert not list(target.parent.glob("*.part"))


def test_download_fallido_no_deja_restos(tmp_path, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    target = tmp_path / "a.csv"
    sess = FakeSession({"u": [FakeResponse(status=500)] * 3})
    with pytest.raises(RuntimeError, match="3 intentos"):
        m.download(sess, "u", target)
    assert not target.exists() and not list(tmp_path.glob("*.part"))


def test_download_no_compara_longitud_si_va_comprimido(tmp_path):
    sess = FakeSession({"u": FakeResponse(b"xx", headers={"Content-Length": "1", "Content-Encoding": "gzip"})})
    assert m.download(sess, "u", tmp_path / "a")[1] is None


# --------------------------------------------------------- transformación ---
def test_transform_dataset(cfg):
    ds = dataset(license_id="uk-ogl", groups=[{"name": "urbanismo"}, {"name": "infra"}],
                 maintainer="Ana", maintainer_email="ana@example.org")
    out = m.transform_dataset(ds, cfg)
    assert out["owner_org"] == "nueva"
    assert out["license_id"] == "odc-by"
    assert out["theme"] == [NTI + "urbanismo-infraestructuras"]           # sin duplicados
    assert out["groups"] == [{"name": "urbanismo"}, {"name": "infra"}]   # grupos intactos
    assert out["language"] == [m.EU_LANG + "SPA"]
    assert out["contact"] == [{"name": "Ana", "email": "ana@example.org"}]
    assert out["id"] == "d1"                                              # UUID conservado


def test_transform_licencia_vacia_y_tema_por_defecto(cfg):
    out = m.transform_dataset(dataset(license_id="", groups=[], notes=""), cfg)
    assert out["license_id"] == "odc-by"
    assert out["theme"] == [NTI + "sector-publico"]
    assert out["notes"] == "Uno" and "contact" not in out


def test_load_cfg_valida_claves(tmp_path):
    p = tmp_path / "x.yml"
    p.write_text("source: https://a\norganization: {from: a}\n")
    with pytest.raises(SystemExit, match="organization.name"):
        m.load_cfg(str(p))


def test_load_cfg_usa_directorio_de_datos(tmp_path, monkeypatch):
    p = tmp_path / "ayto.yml"
    p.write_text("source: https://a\norganization: {from: a, name: b}\n")
    monkeypatch.setenv("OGOV_TOOLS_DATA_DIR", str(tmp_path / "data"))
    cfg = m.load_cfg(str(p))
    assert cfg["_dump"] == tmp_path / "data" / "migration" / "dumps" / "ayto"


# ------------------------------------------------------------------ export ---
def _source_ckan(resources):
    return FakeCKAN({
        "organization_list": ["vieja"],
        "organization_show": lambda d: {"name": d["id"], "title": "Vieja"},
        "group_list": ["urbanismo"],
        "group_show": lambda d: {"name": d["id"], "title": "Urbanismo"},
        "package_search": lambda d: {"count": 1, "results": [dataset(resources=resources)]},
    })


def test_export_detecta_fallos_y_reutiliza(cfg, monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    resources = [
        {"id": "r1", "name": "bueno", "format": "CSV", "url_type": "upload", "url": "https://o/r1/bueno.csv"},
        {"id": "r2", "name": "html", "format": "CSV", "url_type": "upload", "url": "https://o/r2/malo.csv"},
        {"id": "r3", "name": "caido", "format": "XLSX", "url_type": "upload", "url": "https://o/r3/c.xlsx"},
        {"id": "r4", "name": "externo", "format": "API", "url": "https://api.example"},
    ]
    sess = FakeSession({
        "https://o/r1/bueno.csv": FakeResponse(b"a,b\n", headers={"Content-Type": "text/csv"}),
        "https://o/r2/malo.csv": FakeResponse(b"<html>error</html>", headers={"Content-Type": "text/html"}),
        "https://o/r3/c.xlsx": [FakeResponse(status=404)] * 3,
    })
    rep = m.cmd_export(cfg, src=_source_ckan(resources), session=sess)
    assert not rep.ok
    assert {i.resource for i in rep.errors} == {"html", "caido"}
    assert rep.stats["recursos"] == 4 and rep.stats["recursos_externos"] == 1
    saved = {r["id"]: r for r in m.jsonl_read(cfg["_dump"] / "datasets.jsonl")[0]["resources"]}
    assert saved["r1"]["_export"]["status"] == "ok" and saved["r1"]["_sha256"]
    assert saved["r2"]["_export"]["status"] == m.ERROR
    assert saved["r3"]["_export"]["status"] == m.ERROR and "_file" not in saved["r3"]

    # Segunda ejecución: el fichero válido no se vuelve a descargar.
    sess.calls.clear()
    sess.routes["https://o/r3/c.xlsx"] = [FakeResponse(status=404)] * 3
    rep2 = m.cmd_export(cfg, src=_source_ckan(resources), session=sess)
    assert "https://o/r1/bueno.csv" not in sess.calls
    assert rep2.stats["ficheros_reutilizados"] >= 1


# --------------------------------------------------------------- selección ---
def _paged_source(pages, declared=None):
    """Origen cuyo package_search devuelve las páginas indicadas según "start"."""
    calls = []

    def search(d):
        calls.append(d)
        return pages[d.get("start", 0) // m.PAGE]
    src = FakeCKAN({"package_search": search,
                    "organization_show": lambda d: {"name": d["id"], "package_count": declared}})
    return src, calls


def _ds(i, **kw):
    return dataset(id=f"id{i}", name=f"ds-{i:03d}", **kw)


def test_seleccion_filtra_por_organizacion_y_tipo():
    src, calls = _paged_source([{"count": 1, "results": [_ds(1)]}], declared=1)
    rep = m.Report("export", "p")
    out = m.select_datasets(src, "vieja", rep, authenticated=False)
    assert [d["id"] for d in out] == ["id1"]
    assert calls[0]["fq"] == 'organization:"vieja" +dataset_type:dataset'
    assert "include_private" not in calls[0]
    assert rep.ok and any("SOURCE_API_TOKEN" in w.message for w in rep.warnings)


def test_seleccion_con_token_incluye_privados_y_borradores():
    src, calls = _paged_source([{"count": 2, "results": [_ds(1, private=True), _ds(2, state="draft")]}])
    rep = m.Report("export", "p")
    m.select_datasets(src, "vieja", rep, authenticated=True)
    assert calls[0]["include_private"] is True and calls[0]["include_drafts"] is True
    assert rep.stats["datasets_privados"] == 1 and rep.stats["datasets_borrador"] == 1
    assert not rep.warnings


def test_seleccion_pagina_y_elimina_duplicados():
    page1 = {"count": 101, "results": [_ds(i) for i in range(100)]}
    page2 = {"count": 101, "results": [_ds(99), _ds(100)]}     # el 99 se repite (el origen cambió)
    src, calls = _paged_source([page1, page2], declared=101)
    rep = m.Report("export", "p")
    out = m.select_datasets(src, "vieja", rep, authenticated=False)
    assert len(out) == 101 and len({d["id"] for d in out}) == 101
    assert [c["start"] for c in calls] == [0, 100] and rep.ok


def test_seleccion_detecta_totales_que_no_cuadran():
    src, _ = _paged_source([{"count": 3, "results": [_ds(1), _ds(2)]}, {"count": 3, "results": []}], declared=5)
    rep = m.Report("export", "p")
    m.select_datasets(src, "vieja", rep, authenticated=False)
    msgs = " ".join(e.message for e in rep.errors)
    assert "anunció 3" in msgs and "declara 5" in msgs


def test_seleccion_descarta_otra_organizacion_u_otro_tipo():
    res = [_ds(1), _ds(2, organization={"name": "otra"}), _ds(3, type="showcase")]
    src, _ = _paged_source([{"count": 3, "results": res}], declared=None)
    rep = m.Report("export", "p")
    out = m.select_datasets(src, "vieja", rep, authenticated=False)
    assert [d["id"] for d in out] == ["id1"]
    msgs = " ".join(e.message for e in rep.errors)
    assert "otra organización" in msgs and "otro tipo" in msgs


def test_export_avisa_de_recurso_sin_url(cfg):
    src, _ = _paged_source([{"count": 1, "results": [_ds(1, resources=[{"id": "r", "name": "vacío", "url": ""}])]}],
                           declared=1)
    src.handlers.update({"organization_list": ["vieja"], "group_list": []})
    rep = m.cmd_export(cfg, src=src, session=FakeSession({}))
    assert rep.ok and any("sin URL" in w.message and w.resource == "vacío" for w in rep.warnings)


def test_export_usa_la_seleccion(cfg):
    src, calls = _paged_source([{"count": 1, "results": [_ds(1)]}], declared=1)
    src.handlers.update({"organization_list": ["vieja"], "group_list": []})
    rep = m.cmd_export(cfg, src=src, session=FakeSession({}))
    assert rep.ok and rep.stats["datasets"] == 1
    assert calls[0]["fq"].startswith('organization:"vieja"')


def test_export_error_si_falta_organizacion(cfg):
    cfg["organization"]["from"] = "otra"
    rep = m.cmd_export(cfg, src=_source_ckan([]), session=FakeSession({}))
    assert any("no existe en origen" in i.message for i in rep.errors)


# ------------------------------------------------------------------ import ---
def _write_dump(cfg, resources, file_body=b"a,b\n"):
    dump = cfg["_dump"]
    (dump / "files" / "r1").mkdir(parents=True)
    (dump / "files" / "r1" / "bueno.csv").write_bytes(file_body)
    m.jsonl_write(dump / "datasets.jsonl", [dataset(resources=resources)])
    m.jsonl_write(dump / "organizations.jsonl", [{"name": "vieja", "title": "Vieja"}])
    m.jsonl_write(dump / "groups.jsonl", [{"name": "urbanismo", "title": "Urbanismo"}])
    m.jsonl_write(dump / "pages.jsonl", [])


def _missing(d):
    raise NotFound()


def test_import_no_sube_ficheros_fallidos(cfg):
    resources = [
        {"id": "r1", "name": "bueno", "format": "CSV", "url_type": "upload", "_file": "files/r1/bueno.csv",
         "_export": {"status": "ok"}},
        {"id": "r2", "name": "malo", "format": "CSV", "url_type": "upload", "url": "https://viejo/r2",
         "_export": {"status": "error"}},
        {"id": "r4", "name": "externo", "format": "API", "url": "https://api.example"},
    ]
    _write_dump(cfg, resources)
    created = {}
    dst = FakeCKAN({
        "organization_show": _missing, "group_show": _missing,
        "package_show": lambda d: created.get(d["id"]) or _missing(d),
        "package_create": lambda d: created.setdefault(d["id"], {**d, "resources": []}),
    })
    rep = m.cmd_import(cfg, dst=dst)
    assert not rep.ok and rep.stats["datasets_incompletos"] == 1
    assert [i.resource for i in rep.errors] == ["malo"]
    uploads = [c for c in dst.calls if c[0] == "resource_create"]
    assert {c[1]["name"] for c in uploads} == {"bueno", "externo"}
    assert next(c for c in uploads if c[1]["name"] == "bueno")[2]["upload"] is not None
    assert not any(c[1].get("url") == "https://viejo/r2" for c in uploads)   # nunca enlaza al portal viejo
    assert ("organization_create", {"name": "nueva", "title": "Nueva", "description": ""}, None) in dst.calls


def test_import_sin_volcado(cfg):
    rep = m.cmd_import(cfg, dst=FakeCKAN())
    assert not rep.ok and "export" in rep.errors[0].message


# ------------------------------------------------------------------ verify ---
def _dest_from(cfg, ds, **override):
    new = {**m.transform_dataset(ds, cfg), "organization": {"name": "nueva"}}
    new["resources"] = [{**r, "url": f"http://publico:8090/dataset/d1/resource/{r['id']}/download/f"}
                        for r in ds["resources"]]
    new.update(override)
    return new


def test_compare_dataset_identico(cfg):
    ds = dataset(resources=[{"id": "r1", "name": "a", "format": "CSV", "url_type": "upload"}])
    assert m.compare_dataset(ds, _dest_from(cfg, ds), cfg) == []


def test_compare_dataset_detecta_diferencias(cfg):
    ds = dataset(resources=[{"id": "r1", "name": "a", "format": "CSV", "url_type": "upload"},
                            {"id": "r2", "name": "b", "format": "JSON", "url": "https://x"}])
    new = _dest_from(cfg, ds, title="Otro", groups=[], theme=[])
    new["resources"][0]["format"] = "XLSX"
    new["resources"] = new["resources"][:1] + [{"id": "r9", "name": "extra"}]
    msgs = {(lvl, msg.split(":")[0]) for lvl, _, msg in m.compare_dataset(ds, new, cfg)}
    assert (m.ERROR, "title") in msgs
    assert (m.ERROR, "grupos distintos") in msgs
    assert (m.ERROR, "sin temática NTI (obligatoria para datos.gob.es)") in msgs
    assert (m.ERROR, "formato") in msgs
    assert (m.ERROR, "recurso no encontrado en destino") in msgs
    assert (m.WARNING, "recurso adicional que no está en origen") in msgs


def test_internal_url():
    assert m.internal_url("http://publico:8090/dataset/x/resource/y/download/f.csv", "http://ckan:5000/") == \
        "http://ckan:5000/dataset/x/resource/y/download/f.csv"


def test_verify_detecta_fichero_alterado(cfg, monkeypatch):
    monkeypatch.setenv("CKAN_URL", "http://ckan:5000")
    original = b"a,b\n"
    ds = dataset(resources=[{"id": "r1", "name": "a", "format": "CSV", "url_type": "upload",
                             "_sha256": hashlib.sha256(original).hexdigest()}])
    m.jsonl_write(_mkdir(cfg["_dump"]) / "datasets.jsonl", [ds])
    dst = FakeCKAN({"package_show": lambda d: _dest_from(cfg, ds),
                    "package_search": lambda d: {"count": 1}})
    url = "http://ckan:5000/dataset/d1/resource/r1/download/f"
    assert m.cmd_verify(cfg, dst=dst, session=FakeSession({url: FakeResponse(original)})).ok
    rep = m.cmd_verify(cfg, dst=dst, session=FakeSession({url: FakeResponse(b"alterado")}))
    assert [i.message for i in rep.errors] == ["el fichero en destino no coincide byte a byte con el de origen"]


def test_fetch_sha256_reintenta_cortes(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    sess = FakeSession({"u": [requests.ConnectionError("reset"), FakeResponse(status=502), FakeResponse(b"ok")]})
    assert m.fetch_sha256(sess, "u") == hashlib.sha256(b"ok").hexdigest()


def test_fetch_sha256_no_reintenta_404(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    resp = FakeResponse(status=404)

    def raise_404():
        e = requests.HTTPError("404")
        e.response = resp
        raise e
    resp.raise_for_status = raise_404
    sess = FakeSession({"u": [resp, FakeResponse(b"no debe llegar")]})
    with pytest.raises(requests.HTTPError):
        m.fetch_sha256(sess, "u")
    assert len(sess.calls) == 1


def test_verify_detecta_dataset_ausente(cfg):
    ds = dataset()
    m.jsonl_write(_mkdir(cfg["_dump"]) / "datasets.jsonl", [ds])
    dst = FakeCKAN({"package_show": _missing, "package_search": lambda d: {"count": 0}})
    rep = m.cmd_verify(cfg, dst=dst, session=FakeSession({}))
    msgs = [i.message for i in rep.errors]
    assert "dataset no encontrado en destino" in msgs
    assert any("tiene 0 datasets" in x for x in msgs)


def _mkdir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def test_main_codigo_salida(cfg, tmp_path, monkeypatch):
    p = tmp_path / "prueba.yml"
    p.write_text("source: https://a\norganization: {from: vieja, name: nueva}\n")
    monkeypatch.setenv("OGOV_TOOLS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(m, "target_ckan", lambda: FakeCKAN())
    assert m.main(["import", str(p)]) == 1                      # sin volcado -> error
    assert m.main(["import", str(p), "--allow-errors"]) == 0
    assert list((tmp_path / "data" / "migration" / "reports" / "prueba").glob("import-*.md"))


# ------------------------------------------- fechas y página "Acerca de" ---
def test_transform_conserva_fechas_originales(cfg):
    ds = dataset(metadata_created="2022-02-07T10:11:12.123456", metadata_modified="2022-06-28T08:00:00")
    out = m.transform_dataset(ds, cfg)
    assert out["issued"] == "2022-02-07T10:11:12.123456"
    assert out["modified"] == "2022-06-28T08:00:00"


def test_transform_sin_fechas_no_inventa(cfg):
    out = m.transform_dataset(dataset(), cfg)
    assert "issued" not in out and "modified" not in out


@pytest.mark.parametrize("res,esperado", [
    ({"id": "r", "created": "2021-01-01T00:00:00", "last_modified": "2021-02-01T00:00:00",
      "metadata_modified": "2021-03-01T00:00:00"},
     {"id": "r", "issued": "2021-01-01T00:00:00", "modified": "2021-02-01T00:00:00"}),
    ({"id": "r", "metadata_modified": "2021-03-01T00:00:00"}, {"id": "r", "modified": "2021-03-01T00:00:00"}),
    ({"id": "r", "name": "", "format": "CSV"}, {"id": "r", "format": "CSV"}),
])
def test_resource_fields_fechas(res, esperado):
    assert m.resource_fields(res) == esperado


@pytest.mark.parametrize("a,b,igual", [
    ("2022-02-07T10:11:12.123456", "2022-02-07 10:11:12", True),
    ("2022-02-07T10:11:12Z", "2022-02-07T10:11:12+00:00", True),
    ("2022-02-07T10:11:12", "2022-02-07T10:11:13", False),
    ("", "2022-02-07", False),
    (None, None, False),
])
def test_same_date(a, b, igual):
    assert m.same_date(a, b) is igual


def test_extract_about_parrafos_y_listas():
    html = ('<html><article class="module"><div class="module-content">\n'
            '  <p>Primera línea</p>\n  Segunda línea\n'
            '  <ul>\n<li>uno</li>\n<li>dos</li>\n</ul>\n  Final\n'
            '</div></article></html>')
    assert m.extract_about(html) == ("<p>Primera línea</p>\n\nSegunda línea\n\n"
                                     "<ul>\n<li>uno</li>\n<li>dos</li>\n</ul>\n\nFinal")


def test_extract_about_lista_en_una_linea_y_sin_contenido():
    html = '<article class="module"><div class="module-content"><ol><li>a</li></ol></div></article>'
    assert m.extract_about(html) == "<ol><li>a</li></ol>"
    assert m.extract_about('<article class="module"><div class="module-content">  </div></article>') is None
    assert m.extract_about("<html>sin bloque</html>") is None


def test_export_guarda_acerca_de(cfg):
    cfg["site_about"] = True
    src, _ = _paged_source([{"count": 1, "results": [_ds(1)]}], declared=1)
    src.handlers.update({"organization_list": ["vieja"], "group_list": []})
    about = '<article class="module"><div class="module-content">Texto del portal</div></article>'
    rep = m.cmd_export(cfg, src=src, session=FakeSession(
        {"https://origen.example/about": FakeResponse(about.encode())}))
    assert json.loads((cfg["_dump"] / "site.json").read_text()) == {"about": "Texto del portal"}
    assert rep.stats["acerca_de_caracteres"] == len("Texto del portal")


def test_export_sin_acerca_de_avisa_y_sigue(cfg):
    cfg["site_about"] = True
    src, _ = _paged_source([{"count": 1, "results": [_ds(1)]}], declared=1)
    src.handlers.update({"organization_list": ["vieja"], "group_list": []})
    rep = m.cmd_export(cfg, src=src, session=FakeSession(
        {"https://origen.example/about": FakeResponse(status=500)}))
    assert rep.ok and not (cfg["_dump"] / "site.json").exists()
    assert any("Acerca de" in w.message for w in rep.warnings)
