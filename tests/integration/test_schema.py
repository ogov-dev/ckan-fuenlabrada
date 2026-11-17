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

    def test_esquema_es_el_comun(self):
        schema = helpers.call_action("scheming_dataset_schema_show", type="dataset")
        assert "ogov" in schema["about"].lower()
        nombres = [f["field_name"] for f in schema["dataset_fields"]]
        for campo in ("theme", "language", "frequency", "temporal_coverage", "spatial_coverage", "contact"):
            assert campo in nombres
        theme = next(f for f in schema["dataset_fields"] if f["field_name"] == "theme")
        assert len(theme["choices"]) == 22                          # sectores NTI-RISP


@pytest.mark.usefixtures("with_plugins")
def test_licencias():
    ids = {l["id"] for l in helpers.call_action("license_list")}
    assert {"CC-BY-4.0", "CC0-1.0", "CC-BY-SA-4.0", "ODbL-1.0", "odc-by"} <= ids
