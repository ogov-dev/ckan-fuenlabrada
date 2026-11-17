# Nodo de control para probar el rol contra el Docker local (solo pruebas).
FROM docker:29-cli
RUN apk add --no-cache python3 py3-pip bash git
COPY requirements.txt requirements.yml /tmp/
RUN pip install --no-cache-dir --break-system-packages -r /tmp/requirements.txt \
 && ansible-galaxy collection install -r /tmp/requirements.yml -p /usr/share/ansible/collections
