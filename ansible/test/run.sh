#!/bin/bash
# Prueba del rol Ansible contra el Docker local: lint, despliegue de dos
# instancias independientes, idempotencia y aislamiento.
#   ansible/test/run.sh            # despliega y comprueba
#   ansible/test/run.sh --down     # elimina las instancias de prueba (y sus volúmenes)
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
DEPLOY_DIR=${OGOV_TEST_DEPLOY_DIR:-$REPO/.ansible-test-deploy}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if [ "${1:-}" = "--down" ]; then
  for i in ogov-prueba-a ogov-prueba-b; do
    [ -d "$DEPLOY_DIR/$i" ] && docker compose --project-directory "$DEPLOY_DIR/$i" -p "$i" down -v
  done
  rm -rf "$DEPLOY_DIR"; exit 0
fi

docker build -q -t ogov/ansible-runner:test "$REPO/ansible" -f "$HERE/Dockerfile" >/dev/null
mkdir -p "$DEPLOY_DIR"

# Secretos aleatorios por instancia, cifrados con ansible-vault con una clave
# temporal: nada sensible se guarda en el repositorio.
python3 -c 'import secrets;print(secrets.token_urlsafe(32))' > "$WORK/vault-pass"
for h in prueba-a prueba-b; do
  mkdir -p "$WORK/host_vars/$h"
  python3 - "$WORK/host_vars/$h/vault.yml" <<'PY'
import secrets, sys
keys = ["vault_ckan_secret_key", "vault_ckan_jwt_secret", "vault_ckan_sysadmin_password",
        "vault_postgres_password", "vault_ckan_db_password", "vault_datastore_ro_password"]
open(sys.argv[1], "w").write("---\n" + "".join(f"{k}: {secrets.token_urlsafe(36)}\n" for k in keys))
PY
done
cp "$HERE/hosts.yml" "$WORK/hosts.yml"

run() {
  docker run --rm -t \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$REPO:/baseline:ro" -v "$WORK:/work" -v "$DEPLOY_DIR:$DEPLOY_DIR" \
    -e OGOV_TEST_DEPLOY_DIR="$DEPLOY_DIR" -e ANSIBLE_CONFIG=/baseline/ansible/ansible.cfg \
    -e ANSIBLE_ROLES_PATH=/baseline/ansible/roles -e ANSIBLE_FORCE_COLOR=0 \
    -w /baseline/ansible ogov/ansible-runner:test "$@"
}

echo "== 1. Cifrado de los vaults (ansible-vault)"
run sh -c 'for f in /work/host_vars/*/vault.yml; do ansible-vault encrypt --vault-password-file /work/vault-pass "$f" >/dev/null; head -1 "$f"; done'

echo "== 2. Lint (yamllint + ansible-lint, perfil production)"
run sh -c 'yamllint -s . && ansible-lint --profile production --offline playbooks/deploy.yml'

echo "== 3. Sintaxis"
run ansible-playbook -i /work/hosts.yml --vault-password-file /work/vault-pass --syntax-check playbooks/deploy.yml

echo "== 4. Despliegue de dos instancias"
run ansible-playbook -i /work/hosts.yml --vault-password-file /work/vault-pass playbooks/deploy.yml > "$WORK/run1.log" \
  || { tail -40 "$WORK/run1.log"; echo "FALLO: el despliegue terminó con error"; exit 1; }
grep -E "TASK \[|changed:|ok: .*=>|msg:|PLAY RECAP" "$WORK/run1.log" | grep -A1 "Comprobar versión" | grep -E "msg|ok:" || true
grep -A3 "PLAY RECAP" "$WORK/run1.log"

echo "== 5. Idempotencia: segunda ejecución sin cambios"
run ansible-playbook -i /work/hosts.yml --vault-password-file /work/vault-pass playbooks/deploy.yml > "$WORK/run2.log"
grep -A3 "PLAY RECAP" "$WORK/run2.log"
if grep -E "prueba-[ab] .*changed=[1-9]" "$WORK/run2.log"; then echo "FALLO: la segunda ejecución hizo cambios"; exit 1; fi
echo "idempotente: changed=0 en ambas instancias"

echo "== 6. Aislamiento entre instancias"
for i in ogov-prueba-a ogov-prueba-b; do
  printf "%s: contenedores=%s volúmenes=%s permisos .env=%s\n" "$i" \
    "$(docker ps --filter label=com.docker.compose.project=$i -q | wc -l | tr -d ' ')" \
    "$(docker volume ls --filter label=com.docker.compose.project=$i -q | tr '\n' ' ')" \
    "$(stat -f %Lp "$DEPLOY_DIR/$i/.env" 2>/dev/null || stat -c %a "$DEPLOY_DIR/$i/.env")"
done
for p in 8091 8092; do
  curl -s "http://localhost:$p/api/3/action/status_show" | python3 -c "import sys,json;r=json.load(sys.stdin)['result'];print($p, r['site_title'], r['site_url'], r['ckan_version'])"
done

echo "== 7. Validación: un valor con '%' se rechaza antes de tocar el servidor"
if run ansible-playbook -i /work/hosts.yml --vault-password-file /work/vault-pass playbooks/deploy.yml \
     --limit prueba-a -e "ckan_site_title='100% prueba'" > "$WORK/run3.log" 2>&1; then
  echo "FALLO: se aceptó un valor con '%'"; exit 1
fi
grep -q "comilla simple, salto de línea o '%'" "$WORK/run3.log" && echo "rechazado por el rol (correcto)"
grep -q "Levantar la instancia" "$WORK/run3.log" && { echo "FALLO: llegó a desplegar"; exit 1; } || true
