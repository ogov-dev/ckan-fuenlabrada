#!/bin/bash
# Prueba de la sincronización programada con el Portal de Transparencia en el
# servidor remoto de pruebas (ejecutar después de run.sh, que lo deja en marcha).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
WORK=/tmp/ogov-remote-work                   # inventario y vault que deja run.sh
SERVER=ogov-remote-test
INST=/opt/ogov-ckan/ogov-remoto
URL=http://localhost:8093
TOKEN=$(cat /tmp/ogov-remote-token)
VERSION=${TO_VERSION:-1.3.0}
CONF=/baseline/connectors/transparencia/portales/fuenlabrada.yml

step() { printf '\n== %s\n' "$*"; }
ansible_run() {
  docker run --rm -v "$REPO:/baseline:ro" -v "$WORK:/work" \
    -e ANSIBLE_CONFIG=/baseline/ansible/ansible.cfg -e ANSIBLE_ROLES_PATH=/baseline/ansible/roles \
    -e ANSIBLE_FORCE_COLOR=0 -e ANSIBLE_DEPRECATION_WARNINGS=0 -w /baseline/ansible ogov/ansible-runner:test \
    ansible-playbook -i /work/hosts.yml --vault-password-file /work/vault-pass \
    -e "ckan_image=host.docker.internal:5050/ogov/ckan:$VERSION" "$@" playbooks/deploy.yml > /tmp/ogov-sync-deploy.log 2>&1 \
    || { tail -25 /tmp/ogov-sync-deploy.log; echo "FALLO: despliegue"; exit 1; }
  grep -A1 "PLAY RECAP" /tmp/ogov-sync-deploy.log | grep remoto
}
cron_job() { docker exec "$SERVER" crontab -l | grep -A1 "transparencia" | tail -1 | cut -d' ' -f6-; }
run_job() { docker exec "$SERVER" sh -c "$(cron_job)"; docker exec "$SERVER" sh -c "grep '^\[transparencia\]' $INST/logs/sync-transparencia.log | tail -1"; }
api() { curl -s -H "Authorization: $TOKEN" "$URL/api/3/action/$1"; }

step "1. Organización de destino (la del YAML del conector)"
curl -s -H "Authorization: $TOKEN" -H 'Content-Type: application/json' "$URL/api/3/action/organization_create" \
  -d '{"name":"ayuntamiento-de-fuenlabrada","title":"Ayuntamiento de Fuenlabrada"}' >/dev/null
api "organization_show?id=ayuntamiento-de-fuenlabrada" | python3 -c "import sys,json;print('organización:', json.load(sys.stdin)['result']['name'])"

step "2. Despliegue con el conector: tarea mensual instalada"
ansible_run -e "ckan_transparencia_config=$CONF"
docker exec "$SERVER" crontab -l | grep -A1 transparencia

step "3. Primera sincronización (el mismo comando que ejecuta cron)"
run_job
api "package_search?fq=tags:transparencia&rows=0" | python3 -c "import sys,json;print('datasets de transparencia:', json.load(sys.stdin)['result']['count'])"

step "4. Segunda sincronización: sin cambios en origen"
run_job

step "5. Tokens temporales revocados"
n=$(docker exec "$SERVER" docker exec ogov-remoto-ckan-1 ckan -c /srv/app/ckan.ini user token list ckan_admin 2>/dev/null | grep -c "sync-transparencia" || true)
echo "tokens sync-transparencia activos: $n"; [ "$n" = 0 ]

step "6. Una tabla deja de publicarse (se excluye): se retira"
python3 - "$REPO/connectors/transparencia/portales/fuenlabrada.yml" "$WORK/fuenlabrada-excluye.yml" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])); cfg["excluir"] = cfg.get("excluir", []) + ["contratos-menores"]
yaml.safe_dump(cfg, open(sys.argv[2], "w"), allow_unicode=True)
PY
ansible_run -e "ckan_transparencia_config=/work/fuenlabrada-excluye.yml"
run_job
code=$(curl -s -o /dev/null -w '%{http_code}' "$URL/api/3/action/package_show?id=transparencia-contratos-menores")
echo "transparencia-contratos-menores público tras la retirada: HTTP $code"; [ "$code" = 404 ] || [ "$code" = 403 ]

step "7. cron ejecuta la tarea (programación temporal cada minuto)"
ansible_run -e "ckan_transparencia_config=$CONF" -e '{"ckan_transparencia_cron": {"minute": "*", "hour": "*", "day": "*", "month": "*", "weekday": "*"}}'
before=$(docker exec "$SERVER" sh -c "grep -c 'sincronización de transparencia' $INST/logs/sync-transparencia.log")
for _ in $(seq 36); do
  now=$(docker exec "$SERVER" sh -c "grep -c 'sincronización de transparencia' $INST/logs/sync-transparencia.log")
  [ "$now" -gt "$before" ] && break; sleep 10
done
[ "$now" -gt "$before" ] && echo "cron lanzó la sincronización ($before -> $now ejecuciones registradas)" || { echo "FALLO: cron no la lanzó"; exit 1; }
docker exec "$SERVER" sh -c "while pgrep -f ogov-sync-transparencia >/dev/null; do sleep 5; done"
docker exec "$SERVER" sh -c "grep '^\[transparencia\]' $INST/logs/sync-transparencia.log | tail -1"
api "package_search?fq=tags:transparencia&rows=0" | python3 -c "import sys,json;print('datasets de transparencia (la tabla excluida vuelve):', json.load(sys.stdin)['result']['count'])"

step "8. Vuelta a la programación mensual"
ansible_run -e "ckan_transparencia_config=$CONF"
docker exec "$SERVER" crontab -l | grep -A1 transparencia | tail -1 | cut -d' ' -f1-5

step "9. Federación del catálogo resultante (SHACL oficial)"
docker run --rm -v "$REPO/tests/federation:/v" -w /v python:3.12-slim sh -c \
  "pip install -q -r requirements.txt 2>/dev/null >/dev/null; python validate_dcat_ap_es.py --url http://host.docker.internal:8093 2>/dev/null" \
  | grep -E "^catálogo|^Resultado|Violation"
