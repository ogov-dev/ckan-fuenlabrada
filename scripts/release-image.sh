#!/bin/bash
# Construye, prueba y (opcionalmente) publica la imagen de la baseline.
#
#   scripts/release-image.sh 1.1.0                         # construye y prueba (local)
#   scripts/release-image.sh 1.1.0 --push REGISTRO/REPO    # además publica
#       p. ej. --push ghcr.io/ogov-dev/ogov-ckan
#
# Reglas: solo desde un árbol git limpio; la versión publicada lleva el commit
# exacto en sus etiquetas OCI; nunca se sobrescribe una versión ya publicada.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=${1:?Uso: $0 VERSION [--push REGISTRO/REPO]}
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] || { echo "Versión no válida (semver): $VERSION" >&2; exit 1; }
PUSH_REPO=""
[ "${2:-}" = "--push" ] && PUSH_REPO=${3:?Falta REGISTRO/REPO tras --push}

if [ -n "$(git status --porcelain)" ]; then
  echo "El árbol de trabajo tiene cambios sin commit: no se construye una versión." >&2
  git status --short >&2; exit 1
fi
REF=$(git rev-parse HEAD)
DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
LOCAL="ogov/ckan:$VERSION"

echo "== Construyendo $LOCAL (commit ${REF:0:12}, linux/amd64)"
docker build --platform linux/amd64 --target baseline \
  --build-arg OGOV_VERSION="$VERSION" --build-arg VCS_REF="$REF" --build-arg BUILD_DATE="$DATE" \
  -f docker/ckan/Dockerfile -t "$LOCAL" .

echo "== Comprobaciones de la imagen"
docker run --rm --platform linux/amd64 --entrypoint sh "$LOCAL" -c '
  set -e
  test "$(head -1 /srv/app/OGOV_VERSION)" = "'"$VERSION"'"
  pip check >/dev/null && echo "dependencias Python coherentes"
  python3 /srv/app/migration/migrate.py --help >/dev/null && echo "migración disponible"
  python3 /srv/app/connectors/transparencia/sync.py --help >/dev/null && echo "conector disponible"
  ! grep -qE "^SECRET_KEY *= *[^ ]" "$CKAN_INI" && echo "sin SECRET_KEY en ckan.ini"
  ! find /srv/app /docker-entrypoint.d -name ".env*" | grep -q . && echo "sin ficheros .env"'
docker run --rm --platform linux/amd64 -e CKAN_SECRET_KEY=corta "$LOCAL" >/dev/null 2>&1 \
  && { echo "FALLO: arrancó con secretos débiles" >&2; exit 1; } || echo "rechaza secretos débiles"
if docker history --no-trunc "$LOCAL" --format '{{.CreatedBy}}' | grep -qiE "password=|secret_key=|api_token="; then
  echo "FALLO: posible secreto en el historial de capas" >&2; exit 1
fi
echo "sin secretos en el historial de capas"
docker image inspect "$LOCAL" --format '{{range $k, $v := .Config.Labels}}{{if or (eq $k "org.opencontainers.image.version") (eq $k "org.opencontainers.image.revision")}}{{$k}}={{$v}} {{end}}{{end}}'
echo

if [ -n "$PUSH_REPO" ]; then
  REMOTE="$PUSH_REPO:$VERSION"
  if docker manifest inspect "$REMOTE" >/dev/null 2>&1; then
    echo "La versión $REMOTE ya existe en el registro: no se sobrescribe." >&2; exit 1
  fi
  docker tag "$LOCAL" "$REMOTE"
  docker push "$REMOTE" | tail -1
  DIGEST=$(docker image inspect "$REMOTE" --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep "^$PUSH_REPO@" | head -1)
  echo "== Publicada: $REMOTE"
  echo "   Digest (usar en ckan_image para despliegues reproducibles): $DIGEST"
fi
