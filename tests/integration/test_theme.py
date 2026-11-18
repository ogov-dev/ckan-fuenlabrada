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


@pytest.mark.parametrize("valor", ["https://ayto.es/fachada.jpg", "/uploads/hero.png", "", None])
def test_safe_url_valida(valor):
    assert theme.safe_url(valor, {}) == (valor.strip() if valor else valor)


@pytest.mark.parametrize("valor", [
    "x'); background:url(https://mal.example/x", 'https://a.es/"onload', "javascript:alert(1)",
    "https://a.es/con espacio.jpg", "data:image/png;base64,AAAA", "https://a.es/x)y",
])
def test_safe_url_rechaza_inyeccion(valor):
    with pytest.raises(tk.Invalid):
        theme.safe_url(valor, {})


@pytest.mark.parametrize("valor,ok", [("info@ayto.es", True), ("", True), ("sin-arroba", False), ("a@b", False)])
def test_email(valor, ok):
    if ok:
        assert theme.email(valor, {}) == valor
    else:
        with pytest.raises(tk.Invalid):
            theme.email(valor, {})


@pytest.mark.usefixtures("with_plugins", "clean_db")
def test_config_rechaza_hero_malicioso():
    sysadmin = factories.Sysadmin()
    with pytest.raises(tk.ValidationError) as e:
        helpers.call_action("config_option_update", {"user": sysadmin["name"]},
                            **{"ckanext.ogovtech_theme.hero_image": "x'); color:red; ('"})
    assert "ckanext.ogovtech_theme.hero_image" in e.value.error_dict


@pytest.mark.usefixtures("with_plugins", "clean_db")
@pytest.mark.ckan_config("WTF_CSRF_ENABLED", False)
def test_formulario_admin_muestra_error(app):
    sysadmin = factories.SysadminWithToken()
    r = app.post("/ckan-admin/config", headers={"Authorization": sysadmin["token"]},
                 data={"ckanext.ogovtech_theme.color_primary": "azul", "save": ""})
    assert "Introduce un color hexadecimal" in r.body


@pytest.mark.usefixtures("with_plugins")
def test_solo_espanol_sin_selector_de_idioma(app):
    body = app.get("/").body
    assert 'lang="es"' in body
    assert "field-lang-select" not in body


@pytest.mark.usefixtures("with_plugins")
def test_selector_de_idioma_si_hay_varios(app, monkeypatch):
    """CKAN calcula los idiomas al arrancar: se simula que hay dos."""
    import ckan.lib.i18n as i18n
    locales = []
    for code in ("es", "ca"):
        loc = i18n.Locale.parse(code)
        loc.short_name = loc.identifier = code
        locales.append(loc)
    monkeypatch.setattr(i18n, "available_locales", locales)
    assert "field-lang-select" in app.get("/").body


@pytest.mark.usefixtures("with_plugins", "clean_db", "clean_index")
def test_ficha_de_grupo_con_icono(app):
    factories.Group(name="demografia", title="Demografía", image_url="")
    body = app.get("/group/demografia").body
    assert "ogov-group-badge--info" in body and 'class="ogov-icon"' in body


@pytest.mark.usefixtures("with_plugins", "clean_db", "clean_index")
def test_ficha_de_grupo_con_imagen_propia(app):
    factories.Group(name="hacienda", title="Hacienda", image_url="https://example.org/h.png")
    body = app.get("/group/hacienda").body
    assert "https://example.org/h.png" in body and "ogov-group-badge--info" not in body


@pytest.mark.usefixtures("with_plugins", "clean_db", "clean_index")
def test_titulo_de_recurso_sin_etiqueta_xloader_para_el_publico(app):
    org = factories.Organization()
    ds = factories.Dataset(owner_org=org["id"], license_id="odc-by",
                           theme=["http://datos.gob.es/kos/sector-publico/sector/economia"],
                           language=["http://publications.europa.eu/resource/authority/language/SPA"],
                           resources=[{"url": "https://e.org/a.csv", "format": "CSV", "name": "Datos 2026"}])
    body = app.get(f"/dataset/{ds['name']}").body
    assert "Datos 2026" in body and 'data-format="csv"' in body
    assert "loader-badge" not in body
