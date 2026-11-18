#!/bin/sh
set -e
mkdir -p /home/despliegue/.ssh
cp /keys/authorized_keys /home/despliegue/.ssh/authorized_keys
chown -R despliegue:despliegue /home/despliegue/.ssh && chmod 700 /home/despliegue/.ssh && chmod 600 /home/despliegue/.ssh/authorized_keys
/usr/sbin/sshd
exec dockerd-entrypoint.sh --insecure-registry "${REGISTRY:-host.docker.internal:5050}"
