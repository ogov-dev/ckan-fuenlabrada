"""Tests unitarios del conector Portal de Transparencia -> CKAN (sin red)."""
import json

import pytest

import sync as t

BASE = "https://transp.example"


@pytest.fixture
def src():
    return t.Source(BASE)


@pytest.mark.parametrize("valor,esperado", [
    (None, ""), (True, ""), (3, "3"), ("  x  ", "x"),
    ({"es": "Hola", "cat": "Hola"}, "Hola"),
    ('{"es":"Sí"}', "Sí"),
    ({"image": "/download/a.pdf", "path": "a.pdf"}, BASE + "/download/a.pdf"),
    ({"file1": {"image": "/d/1.pdf"}, "file2": {"image": "/d/2.pdf"}}, f"{BASE}/d/1.pdf | {BASE}/d/2.pdf"),
    ({"value": "74813.48", "isCurrency": False}, "74813.48"),
    ({"id": "u", "name": {"es": "2023/2027"}}, "2023/2027"),
    ({"id": "x", "label": {"es": "Opción"}, "name": ""}, "Opción"),
    ([{"es": "a"}, "", {"es": "b"}], "a | b"),
])
def test_flat(src, valor, esperado):
    assert src.flat(valor) == esperado


def test_flat_ordena_ficheros_numericamente(src):
    v = {f"file{i}": {"image": f"/d/{i}"} for i in (10, 2, 1)}
    assert src.flat(v).split(" | ") == [f"{BASE}/d/1", f"{BASE}/d/2", f"{BASE}/d/10"]


def test_table_usa_etiquetas_y_omite_vacias(src):
    records = [{"data": {"k1": {"es": "2025"}, "k2": "", "k3": {"image": "/d/a.pdf"}}},
               {"data": {"k1": {"es": "2024"}, "k3": ""}}]
    schema = [("k1", "Ejercicio"), ("k2", "Vacía"), ("k3", "PDF")]
    labels, rows = t.table(src, records, schema)
    assert labels == ["Ejercicio", "PDF"]
    assert rows == [["2025", BASE + "/d/a.pdf"], ["2024", ""]]


def test_table_sin_esquema_y_etiquetas_duplicadas(src):
    records = [{"data": {"a": "1", "b": "2", "submit": True}}]
    assert t.table(src, records, [])[0] == ["a", "b"]
    labels, _ = t.table(src, records, [("a", "Fecha"), ("b", "Fecha")])
    assert labels == ["Fecha", "Fecha (2)"]


def test_slugify():
    assert t.slugify("Órganos de Gobierno / 2026") == "organos-de-gobierno-2026"


def test_pick_primera_regla():
    rules = [{"si_contiene": ["presupuest"], "grupo": "hacienda", "tematica": "hacienda"},
             {"si_contiene": ["plan"], "grupo": "urbanismo", "tematica": "urbanismo-infraestructuras"}]
    assert t.pick(rules, "Plan de Presupuestos", "grupo", "x") == "hacienda"
    assert t.pick(rules, "Planes Parciales", "tematica", "x") == "urbanismo-infraestructuras"
    assert t.pick(rules, "Otra cosa", "grupo", "x") == "x"


class _Resp:
    def __init__(self, text):
        self.text = text


class _Session:
    def __init__(self, html):
        self.html, self.headers = html, {}

    def get(self, url, **kw):
        return _Resp(self.html)


def _html(view):
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(view)}</script>'


def test_schema_desde_visor(src):
    view = {"props": {"pageProps": {"initialView": {"form_schema": {
        "dynamic_entity_uuid": "U",
        "structure": {"fields": {"components": [
            {"key": "builderInputComponent", "type": "builderInputComponent", "labelTag": {"es": "Ejercicio"}},
            {"key": "submit", "type": "button", "label": "Submit"},
            {"key": "doc", "type": "documentUploadComponent", "label": None, "labelTag": {"en": "PDF"}},
        ]}}}}}}}
    src.s = _Session(_html(view))
    assert src.schema("/ruta", "U") == [("builderInputComponent", "Ejercicio"), ("doc", "PDF")]
    assert src.schema("/ruta", "OTRA") == []          # el visor es de otra entidad


def test_schema_html_inesperado(src):
    src.s = _Session("<html>sin datos</html>")
    assert src.schema("/ruta", "U") == []


# ------------------------------------------------- publicación en CKAN ---
from ckanapi import NotFound  # noqa: E402

CFG = {"organization": "ayto", "license_id": "odc-by", "dataset_prefix": "transparencia-",
       "excluir": ["Política de cookies"], "grupo_por_defecto": "sector-publico",
       "clasificacion": [{"si_contiene": ["presupuest"], "grupo": "hacienda", "tematica": "hacienda"}]}


class FakeCKAN:
    """CKAN en memoria con lo justo para publish()."""

    def __init__(self, fail_on: str | None = None):
        self.pkgs, self.calls, self.fail_on = {}, [], fail_on
        self.missing_orgs: set[str] = set()
        self.groups: set[str] = {"hacienda", "sector-publico"}
        self.action = self

    def __getattr__(self, name):
        return lambda **kw: self.call_action(name, kw)

    def call_action(self, name, data, files=None):
        self.calls.append(name)
        if name == self.fail_on:
            raise RuntimeError("fallo simulado")
        if name == "organization_show":
            if data["id"] in self.missing_orgs:
                raise NotFound()
            return {"name": data["id"]}
        if name == "group_list":
            return sorted(self.groups)
        if name == "package_search":
            return {"results": [{"name": n} for n, p in self.pkgs.items() if p.get("state") != "deleted"]}
        if name == "package_delete":
            self.pkgs[data["id"]]["state"] = "deleted"
            return None
        if name == "package_show":
            if data["id"] not in self.pkgs:
                raise NotFound()
            return self.pkgs[data["id"]]
        if name == "package_create":
            self.pkgs[data["name"]] = {**data, "resources": []}
        elif name == "package_patch":
            self.pkgs[data["id"]].update({k: v for k, v in data.items() if k != "id"})
        elif name == "resource_create":
            res = {**data, "id": f"r{len(self.calls)}"}
            if files:
                res["_content"] = files["upload"][1].read()
            self.pkgs[data["package_id"]]["resources"].append(res)
        elif name == "resource_update":
            res = next(r for r in self.pkgs[data["package_id"]]["resources"] if r["id"] == data["id"])
            res.update(data)
            if files:
                res["_content"] = files["upload"][1].read()


def test_build_package_clasifica():
    pkg = t.build_package("Presupuestos", "presupuestos", "/presupuestos", BASE, CFG)
    assert pkg["name"] == "transparencia-presupuestos"
    assert pkg["groups"] == [{"name": "hacienda"}]
    assert pkg["theme"] == [t.NTI + "hacienda"] and pkg["url"] == BASE + "/presupuestos"
    otro = t.build_package("Organigrama", "organigrama", "/organigrama", BASE, CFG)
    assert otro["groups"] == [{"name": "sector-publico"}] and otro["theme"] == [t.NTI + "sector-publico"]


def test_serialize_csv_con_bom_y_json():
    csv_b, json_b = t.serialize(["Año", "Nombre"], [["2025", "Ñandú, S.A."]])
    assert csv_b.startswith(b"\xef\xbb\xbf")
    assert csv_b.decode("utf-8-sig").splitlines() == ["Año,Nombre", '2025,"Ñandú, S.A."']
    assert json.loads(json_b) == [{"Año": "2025", "Nombre": "Ñandú, S.A."}]


def test_publish_crea_y_luego_actualiza_sin_duplicar():
    dst = FakeCKAN()
    pkg = t.build_package("Contratos", "contratos", "/contratos", BASE, CFG)
    t.publish(dst, pkg, "contratos", ["A"], [["1"]])
    res = dst.pkgs["transparencia-contratos"]["resources"]
    assert [r["name"] for r in res] == ["Contratos (CSV)", "Contratos (JSON)", t.VIEWER_RESOURCE]
    t.publish(dst, pkg, "contratos", ["A"], [["1"], ["2"]])
    res = dst.pkgs["transparencia-contratos"]["resources"]
    assert len(res) == 3                                                 # idempotente
    assert res[0]["_content"].decode("utf-8-sig").splitlines() == ["A", "1", "2"]
    assert "package_patch" in dst.calls


def test_publish_no_resube_si_no_cambia():
    dst = FakeCKAN()
    pkg = t.build_package("Contratos", "contratos", "/contratos", BASE, CFG)
    assert t.publish(dst, pkg, "contratos", ["A"], [["1"]]) == 2
    dst.calls.clear()
    assert t.publish(dst, pkg, "contratos", ["A"], [["1"]]) == 0          # mismo contenido
    assert "resource_update" not in dst.calls and "resource_create" not in dst.calls
    assert t.publish(dst, pkg, "contratos", ["A"], [["1"], ["2"]]) == 2   # cambia -> CSV y JSON
    res = dst.pkgs["transparencia-contratos"]["resources"]
    assert all(len(r[t.HASH_FIELD]) == 64 for r in res[:2])


def test_publish_ignora_hash_sobrescrito_por_xloader():
    """XLoader reescribe "hash"; la comparación usa el campo propio."""
    dst = FakeCKAN()
    pkg = t.build_package("Contratos", "contratos", "/contratos", BASE, CFG)
    t.publish(dst, pkg, "contratos", ["A"], [["1"]])
    for r in dst.pkgs["transparencia-contratos"]["resources"]:
        r["hash"] = "otro-valor-de-xloader"
    assert t.publish(dst, pkg, "contratos", ["A"], [["1"]]) == 0


class _FakeSource(t.Source):
    def __init__(self, ents, recs):
        super().__init__(BASE)
        self._ents, self._recs = ents, recs

    def entities(self, module):
        return self._ents

    def records(self, uuid):
        return self._recs.get(uuid, [])

    def schema(self, route, uuid):
        return [("k", "Columna")]


ENTS = [{"uuid": "1", "name": {"es": "Presupuestos"}, "route": "/presupuestos"},
        {"uuid": "2", "name": {"es": "Vacía"}, "route": "/vacia"},
        {"uuid": "3", "name": {"es": "Política de cookies"}, "route": "/politica-de-cookies"}]
RECS = {"1": [{"data": {"k": {"es": "x"}}}]}


def test_run_estadisticas(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    stats = t.run(CFG, _FakeSource(ENTS, RECS), FakeCKAN())
    assert {k: stats[k] for k in ("publicadas", "vacias", "excluidas", "errores", "ficheros_subidos", "retiradas")} \
        == {"publicadas": 1, "vacias": 1, "excluidas": 1, "errores": 0, "ficheros_subidos": 2, "retiradas": 0}


def test_run_dry_run_no_escribe():
    assert t.run(CFG, _FakeSource(ENTS, RECS), None)["publicadas"] == 1


def test_run_solo_una_tabla(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    stats = t.run(CFG, _FakeSource(ENTS, RECS), FakeCKAN(), solo="vacia")
    assert (stats["publicadas"], stats["vacias"], stats["retiradas"]) == (0, 1, 0)


def test_run_sigue_si_una_tabla_falla(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    stats = t.run(CFG, _FakeSource(ENTS, RECS), FakeCKAN(fail_on="package_create"))
    assert stats["errores"] == 1 and stats["vacias"] == 1


def test_main_exige_credenciales(tmp_path, monkeypatch):
    p = tmp_path / "c.yml"
    p.write_text("source: https://x\norganization: a\n")
    monkeypatch.delenv("CKAN_URL", raising=False)
    with pytest.raises(SystemExit, match="CKAN_URL"):
        t.main([str(p)])


def test_main_codigo_de_salida(tmp_path, monkeypatch):
    p = tmp_path / "c.yml"
    p.write_text("source: https://x\norganization: a\n")
    monkeypatch.setattr(t, "run", lambda *a, **k: {"publicadas": 0, "vacias": 0, "excluidas": 0, "errores": 2, "ficheros_subidos": 0,
                                       "retiradas": 0, "retirada_bloqueada": 0})
    assert t.main([str(p), "--dry-run"]) == 1
    monkeypatch.setattr(t, "run", lambda *a, **k: {"publicadas": 3, "vacias": 0, "excluidas": 0, "errores": 0, "ficheros_subidos": 6,
                                       "retiradas": 0, "retirada_bloqueada": 0})
    assert t.main([str(p), "--dry-run"]) == 0


class _JsonSession:
    def __init__(self, payloads):
        self.payloads, self.headers = payloads, {}

    def get(self, url, params=None, timeout=None):
        p = self.payloads.pop(0)
        if isinstance(p, Exception):
            raise p

        class R:
            def raise_for_status(self_inner):
                pass

            def json(self_inner):
                return p
        return R()


def test_source_api_y_reintentos(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    import requests
    s = t.Source(BASE)
    s.s = _JsonSession([requests.ConnectionError("x"), {"data": [{"uuid": "u"}]},
                        {"data": {"data": [{"data": {}}]}}])
    assert s.entities(5) == [{"uuid": "u"}]
    assert s.records("u") == [{"data": {}}]
    s.s = _JsonSession([requests.ConnectionError("x")] * 3)
    with pytest.raises(RuntimeError, match="No se pudo leer"):
        s.entities(5)



# --------------------------------------------------- retirada y salvaguardas ---
def _existing(dst, *names):
    for n in names:
        dst.pkgs[n] = {"name": n, "resources": []}


ENTS10 = [{"uuid": str(i), "name": {"es": f"Tabla {i}"}, "route": f"/tabla-{i}"} for i in range(10)]
RECS10 = {str(i): [{"data": {"k": {"es": "x"}}}] for i in range(10)}


def test_retira_tablas_desaparecidas(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    dst = FakeCKAN()
    _existing(dst, "transparencia-tabla-99")                       # ya no existe en el origen
    stats = t.run(CFG, _FakeSource(ENTS10, RECS10), dst)
    assert stats["retiradas"] == 1 and stats["detalle"]["retiradas"] == ["transparencia-tabla-99"]
    assert dst.pkgs["transparencia-tabla-99"]["state"] == "deleted"   # borrado recuperable
    assert stats["retirada_bloqueada"] == 0


def test_retira_tablas_vaciadas_o_excluidas(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    dst = FakeCKAN()
    recs = {**RECS10, "3": []}                                      # la 3 se ha vaciado
    cfg = {**CFG, "excluir": ["tabla-5"]}                           # la 5 se excluye
    t.run(CFG, _FakeSource(ENTS10, RECS10), dst)                    # primera publicación (10)
    stats = t.run(cfg, _FakeSource(ENTS10, recs), dst)
    assert sorted(stats["detalle"]["retiradas"]) == ["transparencia-tabla-3", "transparencia-tabla-5"]


def test_no_retira_si_el_origen_viene_vacio(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    dst = FakeCKAN()
    _existing(dst, "transparencia-a", "transparencia-b")
    stats = t.run(CFG, _FakeSource(ENTS10, {}), dst)                # API devuelve tablas sin registros
    assert stats["retiradas"] == 0 and stats["retirada_bloqueada"] == 2
    assert "ningún" in stats["detalle"]["bloqueo"][0] or "ninguna" in stats["detalle"]["bloqueo"][0]
    assert all(p.get("state") != "deleted" for p in dst.pkgs.values())


def test_no_retira_si_seria_masivo(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    dst = FakeCKAN()
    t.run(CFG, _FakeSource(ENTS10, RECS10), dst)                    # 10 publicadas
    stats = t.run(CFG, _FakeSource(ENTS10[:5], RECS10), dst)        # desaparece el 50 %
    assert stats["retiradas"] == 0 and stats["retirada_bloqueada"] == 5
    assert "más del 20%" in stats["detalle"]["bloqueo"][0]


def test_no_retira_si_hubo_errores(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    dst = FakeCKAN()
    _existing(dst, "transparencia-vieja")
    real = t.publish
    monkeypatch.setattr(t, "publish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("caída")))
    stats = t.run(CFG, _FakeSource(ENTS10, RECS10), dst)
    monkeypatch.setattr(t, "publish", real)
    assert stats["errores"] == 10 and stats["retiradas"] == 0 and stats["retirada_bloqueada"] == 1


def test_main_falla_si_retirada_bloqueada(tmp_path, monkeypatch):
    p = tmp_path / "c.yml"
    p.write_text("source: https://x\norganization: a\n")
    monkeypatch.setattr(t, "run", lambda *a, **k: {"publicadas": 3, "vacias": 0, "excluidas": 0, "errores": 0,
                                                   "ficheros_subidos": 0, "retiradas": 0, "retirada_bloqueada": 4})
    assert t.main([str(p), "--dry-run"]) == 1


def test_organizacion_inexistente_detiene(monkeypatch):
    dst = FakeCKAN()
    dst.missing_orgs = {"ayto"}
    with pytest.raises(SystemExit, match="no existe en CKAN"):
        t.run(CFG, _FakeSource(ENTS, RECS), dst)


def test_grupo_inexistente_se_omite_con_aviso(monkeypatch):
    monkeypatch.setattr(t.time, "sleep", lambda s: None)
    dst = FakeCKAN()
    dst.groups = {"sector-publico"}                                  # falta "hacienda"
    stats = t.run(CFG, _FakeSource(ENTS, RECS), dst)
    assert stats["errores"] == 0
    assert dst.pkgs["transparencia-presupuestos"]["groups"] == []
    assert any("hacienda" in a for a in stats["detalle"]["avisos"])


def test_informe_de_ejecucion(tmp_path, monkeypatch):
    monkeypatch.setenv("OGOV_TOOLS_DATA_DIR", str(tmp_path))
    stats = {"publicadas": 1, "vacias": 0, "excluidas": 0, "errores": 0, "ficheros_subidos": 0,
             "retiradas": 0, "retirada_bloqueada": 2, "detalle": {"bloqueo": ["x"]}}
    path = t.save_report(stats, "/cfg/fuenlabrada.yml")
    data = json.loads(path.read_text())
    assert data["resultado"] == "ERROR" and data["retirada_bloqueada"] == 2
    assert (path.parent / "fuenlabrada-ultimo.json").exists()
