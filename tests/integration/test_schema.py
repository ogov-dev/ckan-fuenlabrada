"""Esquema común de metadatos y licencias (ckanext-ogovtech_fixes)."""
import pytest

import ckan.logic as logic
from ckan.tests import factories, helpers

NTI = "http://datos.gob.es/kos/sector-publico/sector/"
SPA = "http://publications.europa.eu/resource/authority/language/SPA"


def _create(**kw):
    org = factories.Organization()
    sysadmin = factories.Sysadmin()
    data = dict(name="ds-prueba", title="Prueba", notes="Descripción", owner_org=org["id"],
                license_id="CC-BY-4.0", theme=[NTI + "economia"], language=[SPA])
    data.update(kw)
    return helpers.call_action("package_create", {"user": sysadmin["name"]}, **data)


@pytest.mark.usefixtures("with_plugins", "clean_db", "clean_index")
class TestEsquema:
    def test_dataset_valido(self):
        ds = _create()
        assert ds["theme"] == [NTI + "economia"] and ds["language"] == [SPA]

    @pytest.mark.parametrize("campo", ["theme", "notes", "language", "license_id"])
    def test_campos_obligatorios(self, campo):
        with pytest.raises(logic.ValidationError) as e:
            _create(**{campo: [] if campo in ("theme", "language") else ""})
        assert campo in e.value.error_dict

    def test_tematica_fuera_de_la_taxonomia(self):
        with pytest.raises(logic.ValidationError) as e:
            _create(theme=[NTI + "inventada"])
        assert "theme" in e.value.error_dict

    def test_frecuencia_fuera_de_lista(self):
        with pytest.raises(logic.ValidationError):
            _create(frequency="cada-tanto")

    def test_correo_de_contacto_validado(self):
        with pytest.raises(logic.ValidationError) as e:
            _create(contact=[{"name": "Ana", "email": "no-es-correo"}])
        assert "contact" in e.value.error_dict
        assert _create(name="ok-contacto", contact=[{"name": "Ana", "email": "ana@ayto.es"}])["contact"][0]["email"] == "ana@ayto.es"

    def test_esquema_es_el_comun(self):
        schema = helpers.call_action("scheming_dataset_schema_show", type="dataset")
        assert "ogov" in schema["about"].lower()
        assert schema["about_url"] == "https://github.com/ogov-dev/ckan-fuenlabrada"
        nombres = [f["field_name"] for f in schema["dataset_fields"]]
        for campo in ("theme", "language", "frequency", "temporal_coverage", "spatial_coverage", "contact"):
            assert campo in nombres
        theme = next(f for f in schema["dataset_fields"] if f["field_name"] == "theme")
        assert len(theme["choices"]) == 22                          # sectores NTI-RISP


@pytest.mark.usefixtures("with_plugins")
def test_licencias():
    ids = {l["id"] for l in helpers.call_action("license_list")}
    assert {"CC-BY-4.0", "CC0-1.0", "CC-BY-SA-4.0", "ODbL-1.0", "odc-by"} <= ids


# ------------------------------------------- bloques repetibles vacíos ---
from ckanext.ogovtech_fixes import composites  # noqa: E402


@pytest.mark.parametrize("entrada,esperado", [
    ({"contact": [{"name": "", "email": " ", "url": None}]}, {"contact": []}),
    ({"contact": [{"name": "Ana", "email": ""}, {"name": "", "email": ""}]}, {"contact": [{"name": "Ana", "email": ""}]}),
    ({"contact-0-name": "", "contact-0-email": "", "notes": "x"}, {"notes": "x"}),
    ({"contact-0-name": "Ana", "contact-0-email": ""}, {"contact-0-name": "Ana", "contact-0-email": ""}),
    ({"spatial_coverage": "no es lista"}, {"spatial_coverage": "no es lista"}),
])
def test_drop_empty_blocks(entrada, esperado):
    assert composites.drop_empty_blocks(dict(entrada), ["contact", "spatial_coverage"]) == esperado


@pytest.mark.usefixtures("with_plugins", "clean_db", "clean_index")
class TestBloquesVacios:
    def test_no_se_guardan_al_crear(self):
        ds = _create(contact=[{"name": "", "email": "", "url": ""}],
                     spatial_coverage=[{"uri": "", "text": ""}])
        assert not ds.get("contact") and not ds.get("spatial_coverage")

    def test_se_conservan_los_que_tienen_datos(self):
        ds = _create(contact=[{"name": "Ana", "email": ""}, {"name": "", "email": ""}])
        assert [c["name"] for c in ds["contact"]] == ["Ana"]

    def test_tambien_al_actualizar(self):
        ds = _create(contact=[{"name": "Ana", "email": "ana@ayto.es"}])
        sysadmin = factories.Sysadmin()
        ds = helpers.call_action("package_patch", {"user": sysadmin["name"]}, id=ds["id"],
                                 spatial_coverage=[{"uri": "", "text": ""}])
        assert not ds.get("spatial_coverage") and ds["contact"][0]["name"] == "Ana"


# -------------------------------- usuario de las llamadas internas (XLoader) ---
from ckanext.ogovtech_fixes import internal_user  # noqa: E402


@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_datastore_delete_interno_usa_el_usuario_del_sitio():
    visto = []
    original = lambda context, data_dict: visto.append(context["user"]) or "ok"   # noqa: E731
    sitio = helpers.call_action("get_site_user")["name"]
    # Llamada interna de XLoader: sin usuario y con ignore_auth
    assert internal_user.datastore_delete(original, {"user": "", "ignore_auth": True}, {}) == "ok"
    # Llamada normal: el usuario no se toca
    internal_user.datastore_delete(original, {"user": "editora"}, {})
    # Sin ignore_auth tampoco (no se eleva a nadie)
    internal_user.datastore_delete(original, {"user": ""}, {})
    assert visto == [sitio, "editora", ""]
    # datastore_create igual (XLoader lo llama con ignore_auth y sin clave "user")
    visto.clear()
    internal_user.datastore_create(original, {"ignore_auth": True}, {})
    assert visto == [sitio]
