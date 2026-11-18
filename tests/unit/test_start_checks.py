"""Validación de secretos en el arranque (docker/ckan/ogov-start.sh)."""
import os
import subprocess
from pathlib import Path

import pytest

CANDIDATES = [Path(__file__).resolve().parents[2] / "docker" / "ckan" / "ogov-start.sh",
              Path("/srv/app/ogov-start.sh")]
SCRIPT = next((p for p in CANDIDATES if p.exists()), None)
STRONG = "k" * 48
GOOD = {
    "CKAN_SECRET_KEY": STRONG, "CKAN_JWT_SECRET": "j" * 48, "CKAN_SYSADMIN_PASSWORD": "Unaclave-Robusta-2026",
    "CKAN_SQLALCHEMY_URL": "postgresql://ckan:Zq81Lm2vXy@db/ckan",
    "CKAN_DATASTORE_WRITE_URL": "postgresql://ckan:Zq81Lm2vXy@db/ds",
    "CKAN_DATASTORE_READ_URL": "postgresql://ro:Pk27Wn9Qa@db/ds",
}


def run(**env):
    if SCRIPT is None:
        pytest.skip("ogov-start.sh no disponible")
    full = {"PATH": os.environ["PATH"], **{k: v for k, v in env.items() if v is not None}}
    return subprocess.run(["bash", str(SCRIPT), "--check"], env=full, capture_output=True, text=True)


def test_secretos_correctos():
    r = run(**GOOD)
    assert r.returncode == 0, r.stderr
    assert "secretos correctos" in r.stdout


@pytest.mark.parametrize("cambio,mensaje", [
    ({"CKAN_SECRET_KEY": None}, "CKAN_SECRET_KEY no está definida"),
    ({"CKAN_JWT_SECRET": "corta"}, "CKAN_JWT_SECRET es demasiado corta"),
    ({"CKAN_SYSADMIN_PASSWORD": "cambia-esta-clave"}, "valor de ejemplo"),
    ({"CKAN_SQLALCHEMY_URL": "postgresql://ckan:cambia-ckan-db@db/ckan"}, "contraseña de ejemplo"),
    ({"CKAN_DATASTORE_READ_URL": "postgresql://ro:pass@db/ds"}, "contraseña de ejemplo"),
    ({"CKAN_DATASTORE_READ_URL": "postgresql://ro:cambia-datastore-ro@db/ds"}, "contraseña de ejemplo"),
])
def test_rechaza_secretos_debiles(cambio, mensaje):
    r = run(**{**GOOD, **cambio})
    assert r.returncode == 1
    assert mensaje in r.stderr and "no se arranca" in r.stderr


def test_no_hay_falsos_positivos_con_claves_aleatorias():
    """Una contraseña robusta que empieza por "pass" no debe confundirse con la de ejemplo."""
    r = run(**{**GOOD, "CKAN_SQLALCHEMY_URL": "postgresql://ckan:passW0rd-x9Kq81LmZ@db/ckan"})
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("entorno", ["production", "development"])
def test_porcentaje_siempre_es_error(entorno):
    """CKAN no arranca con '%' en variables CKAN_*: se corta antes, con un mensaje claro."""
    r = run(**{**GOOD, "CKAN__SITE_TITLE": "100% transparente", "OGOV_ENV": entorno})
    assert r.returncode == 1 and "CKAN__SITE_TITLE contiene '%'" in r.stderr


def test_desarrollo_solo_avisa():
    r = run(**{**GOOD, "CKAN_SYSADMIN_PASSWORD": "cambia", "OGOV_ENV": "development"})
    assert r.returncode == 0 and "AVISO (development)" in r.stderr


# ------------------------------------------------- cookies (06_cookies.sh) ---
COOKIES = next((p for p in [Path(__file__).resolve().parents[2] / "docker" / "ckan" / "docker-entrypoint.d" / "06_cookies.sh",
                            Path("/docker-entrypoint.d/06_cookies.sh")] if p.exists()), None)
INI = "[app:main]\nSESSION_COOKIE_NAME = ckan\nSESSION_COOKIE_DOMAIN = \nSESSION_COOKIE_SECURE = false\n"


def run_cookies(tmp_path, site_url):
    """Ejecuta el script con un `ckan` falso que registra los argumentos."""
    if COOKIES is None:
        pytest.skip("06_cookies.sh no disponible")
    app_dir = tmp_path / "app"                 # hace de /srv/app
    app_dir.mkdir()
    ini = app_dir / "ckan.ini"
    ini.write_text(INI)
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ckan").write_text('#!/bin/sh\necho "$@" >> "$OGOV_LOG"\n')
    (fake / "ckan").chmod(0o755)
    log = tmp_path / "args.log"
    env = {"PATH": f"{fake}:{os.environ['PATH']}", "CKAN_INI": str(ini), "OGOV_LOG": str(log),
           "CKAN_SITE_URL": site_url}
    app_dir.chmod(0o555)           # como /srv/app: se puede escribir ckan.ini, no crear ficheros
    try:
        r = subprocess.run(["bash", "-c", f'source "{COOKIES}"'], env=env, capture_output=True, text=True)
    finally:
        app_dir.chmod(0o755)
    assert r.returncode == 0 and not r.stderr, r.stderr
    return ini.read_text(), log.read_text(), r.stdout


def test_cookies_seguras_con_https(tmp_path):
    ini, args, out = run_cookies(tmp_path, "https://datos.example.org")
    assert "SESSION_COOKIE_SECURE=true" in args and "REMEMBER_COOKIE_SECURE=true" in args
    assert "REMEMBER_COOKIE_SAMESITE=Lax" in args and "SESSION_COOKIE_SAMESITE=Lax" in args
    assert "SESSION_COOKIE_DOMAIN" not in ini and "SESSION_COOKIE_NAME = ckan" in ini
    assert "Secure=true" in out


def test_cookies_sin_secure_en_http_local(tmp_path):
    _, args, out = run_cookies(tmp_path, "http://localhost:8090")
    assert "SESSION_COOKIE_SECURE=false" in args and "Secure=false" in out
