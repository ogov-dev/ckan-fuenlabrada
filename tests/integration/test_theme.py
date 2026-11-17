"""Tema común (ckanext-ogovtech_theme): validadores, helpers, configuración y plantillas."""
import pytest

import ckan.plugins.toolkit as tk
from ckan.tests import factories, helpers

from ckanext.ogovtech_theme import plugin as theme


@pytest.mark.parametrize("valor,esperado", [("#ABC", "#abc"), ("#003f80", "#003f80"), ("", ""), (None, None)])
def test_hex_color_valido(valor, esperado):
    assert theme.hex_color(valor, {}) == esperado


@pytest.mark.parametrize("valor", ["rojo", "#12", "003f80", "#gggggg", "#0000000"])
def test_hex_color_invalido(valor):
    with pytest.raises(tk.Invalid):
        theme.hex_color(valor, {})


def test_footer_links_validator():
    ok = "Aviso legal|https://a.es\n\nAccesibilidad|/pages/accesibilidad\nCorreo|mailto:a@b.es"
    assert theme.footer_links_validator(ok, {}) == ok
    for malo in ("Sin separador", "Texto|javascript:alert(1)", "Texto|ftp://x"):
        with pytest.raises(tk.Invalid):
            theme.footer_links_validator(malo, {})


@pytest.mark.ckan_config("ckanext.ogovtech_theme.footer_links", "A|https://a\nmal formado\n B | /b \n|/vacío")
@pytest.mark.usefixtures("with_plugins")
def test_helper_enlaces():
    assert theme.ogovtech_theme_footer_links() == [{"text": "A", "url": "https://a"}, {"text": "B", "url": "/b"}]


@pytest.mark.usefixtures("with_plugins", "clean_db")
class TestConfiguracionEditable:
    def _update(self, **cfg):
        sysadmin = factories.Sysadmin()
        return helpers.call_action("config_option_update", {"user": sysadmin["name"]}, **cfg)

    def test_guarda_y_aplica(self, app):
        self._update(**{"ckanext.ogovtech_theme.color_primary": "#7A1F3D",
                        "ckanext.ogovtech_theme.footer_text": "Ayuntamiento de Prueba",
                        "ckanext.ogovtech_theme.footer_legal_links": "Cookies|/pages/cookies"})
        assert tk.config["ckanext.ogovtech_theme.color_primary"] == "#7a1f3d"
        body = app.get("/").body
        assert "--ogov-primary: #7a1f3d" in body
        assert "Ayuntamiento de Prueba" in body and "/pages/cookies" in body

    def test_rechaza_color_invalido(self):
        with pytest.raises(tk.ValidationError) as e:
            self._update(**{"ckanext.ogovtech_theme.color_primary": "azul"})
        assert "ckanext.ogovtech_theme.color_primary" in e.value.error_dict

    def test_formulario_admin(self, app):
        sysadmin = factories.SysadminWithToken()
        body = app.get("/ckan-admin/config", headers={"Authorization": sysadmin["token"]}).body
        for campo in ("field-ogov-color_primary", "field-ogov-footer-links", "field-ogov-hero-image"):
            assert campo in body


@pytest.mark.usefixtures("with_plugins", "clean_db", "clean_index")
class TestPlantillas:
    def test_portada_con_grupos_e_iconos(self, app):
        factories.Group(name="medio-ambiente", title="Medio ambiente", image_url="")
        body = app.get("/").body
        assert "ogov-hero" in body and "ogov-stats" in body
        assert 'class="ogov-tile__icon"' in body and "Medio ambiente" in body

    def test_pie_ckan_y_ogov(self, app):
        body = app.get("/").body
        assert "basado en" in body and "ckan.org" in body
        assert "Desarrollado por" in body and "ogov-logo.png" in body

    def test_grupo_con_imagen_propia_la_respeta(self, app):
        factories.Group(name="economia", title="Economía", image_url="https://example.org/logo.png")
        body = app.get("/group/").body
        assert "https://example.org/logo.png" in body and "ogov-group-badge" not in body

    def test_listado_grupos_con_icono(self, app):
        factories.Group(name="hacienda", title="Hacienda", image_url="")
        assert "ogov-group-badge" in app.get("/group/").body

    def test_fuente_alojada_localmente(self, app):
        assert app.get("/ogovtech_theme/fonts/montserrat-latin-700.woff2").status_code == 200
        assert "fonts.googleapis.com" not in app.get("/").body


@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_portada_registra_errores_sin_romperse(app, monkeypatch, caplog):
    """Si la búsqueda falla, la portada responde y el error queda en el log."""
    original = tk.get_action

    def falla(name):
        if name == "package_search":
            def boom(*a, **kw):
                raise RuntimeError("solr caído")
            return boom
        return original(name)
    monkeypatch.setattr(theme.tk, "get_action", falla)
    with caplog.at_level("ERROR"):
        assert app.get("/").status_code == 200
    assert "no se pudieron obtener los datasets" in caplog.text
