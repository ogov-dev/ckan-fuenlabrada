#!/bin/sh
set -e
mkdir -p /home/despliegue/.ssh
cp /keys/authorized_keys /home/despliegue/.ssh/authorized_keys
chown -R despliegue:despliegue /home/despliegue/.ssh && chmod 700 /home/despliegue/.ssh && chmod 600 /home/despliegue/.ssh/authorized_keys
/usr/sbin/sshd
crond -b -l 8          # cron del servidor (tareas programadas de las instancias)
exec dockerd-entrypoint.sh --insecure-registry "${REGISTRY:-host.docker.internal:5050}"
