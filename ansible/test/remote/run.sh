#!/bin/bash
# Prueba de despliegue REMOTO con Ansible (SSH) y de ACTUALIZACIÓN con datos.
#
#   ansible/test/remote/run.sh           # ejecuta la prueba completa
#   ansible/test/remote/run.sh --down    # elimina servidor y datos de prueba
#
# Requisitos: registro en localhost:5050 con ogov/ckan:$FROM_VERSION y :$TO_VERSION
# (por defecto 1.0.0 y 1.3.0). Deja el servidor en marcha para sync_test.sh.
# (scripts/release-image.sh ... --push localhost:5050/ogov/ckan).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
SERVER=ogov-remote-test
PORT_SSH=2222
PORT_HTTP=8093
REGISTRY=host.docker.internal:5050
FROM_VERSION=${FROM_VERSION:-1.0.0}
TO_VERSION=${TO_VERSION:-1.3.0}
WORK=$(mktemp -d)
KEEP=${KEEP_WORK:-/tmp/ogov-remote-work}
trap 'rm -rf "$KEEP" && cp -R "$WORK" "$KEEP" && rm -rf "$WORK"' EXIT

if [ "${1:-}" = "--down" ]; then docker rm -f -v "$SERVER" >/dev/null 2>&1 || true; exit 0; fi

step() { printf '\n== %s\n' "$*"; }

step "0. Imágenes publicadas en el registro"
for v in "$FROM_VERSION" "$TO_VERSION"; do
  docker manifest inspect --insecure "localhost:5050/ogov/ckan:$v" >/dev/null || { echo "Falta ogov/ckan:$v en el registro"; exit 1; }
  echo "ogov/ckan:$v disponible"
done

step "1. Servidor remoto (SSH + Docker propio)"
ssh-keygen -q -t ed25519 -N "" -f "$WORK/id_ed25519" -C ansible-test
mkdir -p "$WORK/keys" && cp "$WORK/id_ed25519.pub" "$WORK/keys/authorized_keys"
docker build -q -t ogov/remote-server:test "$HERE" >/dev/null
docker rm -f -v "$SERVER" >/dev/null 2>&1 || true
docker run -d --name "$SERVER" --privileged -e REGISTRY="$REGISTRY" \
  -p "$PORT_SSH:22" -p "$PORT_HTTP:$PORT_HTTP" -v "$WORK/keys:/keys:ro" ogov/remote-server:test >/dev/null
for _ in $(seq 60); do docker exec "$SERVER" docker info >/dev/null 2>&1 && break; sleep 2; done
echo "servidor listo: $(docker exec "$SERVER" docker version --format 'Docker {{.Server.Version}}')"

docker build -q -t ogov/ansible-runner:test "$REPO/ansible" -f "$REPO/ansible/test/Dockerfile" >/dev/null

# Secretos aleatorios cifrados con ansible-vault (nada se guarda en el repo)
python3 -c 'import secrets;print(secrets.token_urlsafe(32))' > "$WORK/vault-pass"
mkdir -p "$WORK/host_vars/remoto"
python3 - "$WORK/host_vars/remoto/vault.yml" <<'PY'
import secrets, sys
keys = ["vault_ckan_secret_key", "vault_ckan_jwt_secret", "vault_ckan_sysadmin_password",
        "vault_postgres_password", "vault_ckan_db_password", "vault_datastore_ro_password"]
open(sys.argv[1], "w").write("---\n" + "".join(f"{k}: {secrets.token_urlsafe(36)}\n" for k in keys))
PY
cat > "$WORK/hosts.yml" <<YML
---
all:
  children:
    ckan_instances:
      hosts:
        remoto:
          ansible_host: host.docker.internal
          ansible_port: $PORT_SSH
          ansible_user: despliegue
          ansible_ssh_private_key_file: /work/id_ed25519
          ansible_ssh_common_args: "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
          ansible_become: true
          ansible_python_interpreter: /usr/bin/python3
          ckan_baseline_dir: /baseline
          ckan_instance_name: ogov-remoto
          ckan_http_port: $PORT_HTTP
          ckan_site_url: http://localhost:$PORT_HTTP
          ckan_site_title: Instancia remota de prueba
          ckan_site_id: remoto
          ckan_sysadmin_email: admin@example.org
          ckan_dir3_code: L01000003
          ckan_spatial_uri: http://datos.gob.es/recurso/sector-publico/territorio/Provincia/Madrid
YML

ansible() {
  docker run --rm -v "$REPO:/baseline:ro" -v "$WORK:/work" \
    -e ANSIBLE_CONFIG=/baseline/ansible/ansible.cfg -e ANSIBLE_ROLES_PATH=/baseline/ansible/roles \
    -e ANSIBLE_FORCE_COLOR=0 -e ANSIBLE_DEPRECATION_WARNINGS=0 -w /baseline/ansible ogov/ansible-runner:test "$@"
}
deploy() {   # $1 = versión
  ansible ansible-playbook -i /work/hosts.yml --vault-password-file /work/vault-pass \
    -e "ckan_image=$REGISTRY/ogov/ckan:$1" playbooks/deploy.yml > "$WORK/deploy-$1.log" 2>&1 \
    || { tail -30 "$WORK/deploy-$1.log"; echo "FALLO: despliegue $1"; exit 1; }
  grep -A2 "PLAY RECAP" "$WORK/deploy-$1.log" | grep remoto
}
inner() { docker exec "$SERVER" docker "$@"; }
ckan_version() { inner exec ogov-remoto-ckan-1 sh -c 'if [ -f /srv/app/OGOV_VERSION ]; then head -1 /srv/app/OGOV_VERSION; else echo "anterior a 1.1.0 (sin fichero de versión)"; fi'; }

ansible sh -c 'ansible-vault encrypt --vault-password-file /work/vault-pass /work/host_vars/remoto/vault.yml >/dev/null'
step "2. Conectividad SSH y privilegios (ansible -m ping, become)"
ansible ansible -i /work/hosts.yml --vault-password-file /work/vault-pass remoto -m ping -o | sed 's/=>.*/=> pong/'
ansible ansible -i /work/hosts.yml --vault-password-file /work/vault-pass remoto -b -m command -a whoami -o | sed 's/.*stdout) //'

step "3. Primera instalación: versión $FROM_VERSION desde el registro"
deploy "$FROM_VERSION"
echo "versión en ejecución: $(ckan_version)"

step "4. Datos de prueba (organización, dataset y CSV en DataStore)"
TOKEN=$(inner exec ogov-remoto-ckan-1 ckan -c /srv/app/ckan.ini user token add ckan_admin prueba 2>/dev/null | tail -n1 | tr -d '\t ')
python3 "$HERE/data.py" seed "http://localhost:$PORT_HTTP" "$TOKEN" "$WORK/antes.json"
cp "$WORK/antes.json" "${HUELLA_OUT:-/tmp/ogov-remote-huella.json}"; echo "$TOKEN" > "${TOKEN_OUT:-/tmp/ogov-remote-token}"

step "5. Actualización a $TO_VERSION (mismo inventario, solo cambia la imagen)"
deploy "$TO_VERSION"
echo "versión en ejecución: $(ckan_version)"

step "6. Los datos no han cambiado"
python3 "$HERE/data.py" compare "http://localhost:$PORT_HTTP" "$TOKEN" "$WORK/antes.json"

step "7. Idempotencia tras la actualización"
deploy "$TO_VERSION" | grep -q "changed=0" && echo "segunda ejecución: changed=0"

step "8. Aceptación sobre la instancia actualizada"
docker run --rm --platform linux/amd64 -v "$REPO/tests/acceptance:/acc" -w /acc --entrypoint python3 ogov/ckan:test \
  acceptance.py --url "http://host.docker.internal:$PORT_HTTP" --token "$TOKEN" --report-dir /acc/reports | tail -3

step "9. Federación: catálogo validado con las formas SHACL oficiales"
docker run --rm -v "$REPO/tests/federation:/v" -w /v python:3.12-slim sh -c \
  "pip install -q -r requirements.txt 2>/dev/null >/dev/null; python validate_dcat_ap_es.py --url http://host.docker.internal:$PORT_HTTP 2>/dev/null" \
  | grep -E "^catálogo|^Resultado|Violation"
