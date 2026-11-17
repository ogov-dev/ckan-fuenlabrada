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
