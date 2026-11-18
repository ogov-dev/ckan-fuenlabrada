# "Servidor remoto" de pruebas: Docker propio (dind) + SSH, como un servidor
# preparado por la infraestructura del cliente. Solo para pruebas.
FROM docker:29-dind
RUN apk add --no-cache openssh-server sudo python3 bash \
 && ssh-keygen -A \
 && adduser -D -s /bin/bash despliegue && passwd -u despliegue \
 && addgroup despliegue docker \
 && echo "despliegue ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/despliegue \
 && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
 && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
COPY start.sh /usr/local/bin/start-server.sh
ENTRYPOINT ["/usr/local/bin/start-server.sh"]
