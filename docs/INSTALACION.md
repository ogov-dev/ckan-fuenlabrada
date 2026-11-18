# Primera instalación de un portal

Guía para instalar una instancia nueva de la baseline (un ayuntamiento) con
Ansible. Todo se hace desde el **nodo de control** (el equipo de quien despliega);
en el servidor no se instala nada a mano.

> Probado de extremo a extremo con `ansible/test/remote/run.sh`: servidor con SSH
> y Docker propios, imagen descargada de un registro, despliegue, carga de datos,
> actualización y aceptación.

## 1. Requisitos

### Servidor (lo prepara la infraestructura del cliente; anexo técnico, apdo. 2)

| Requisito | Valor |
|---|---|
| Sistema | Linux x86_64 (las imágenes son `linux/amd64`) |
| Recursos orientativos | 4 vCPU, 8 GB de RAM, 50 GB de disco (según volumen de datos) |
| Software | Docker Engine ≥ 24 con el plugin **Docker Compose v2**, `python3` |
| Acceso | SSH con clave para un usuario con `sudo` |
| Red | Salida hacia el registro de imágenes; entrada al puerto HTTP de la instancia desde el proxy/balanceador |
| HTTPS | Lo termina el proxy o balanceador de la infraestructura, que reenvía a `http://servidor:<ckan_http_port>` |
| Registro | Si es privado: `docker login <registro>` hecho en el servidor |

El rol **comprueba** que Docker y Compose existen; no los instala.

### Nodo de control

```bash
pip install -r ansible/requirements.txt              # ansible-core 2.22.0, ansible-lint, yamllint
ansible-galaxy collection install -r ansible/requirements.yml   # community.docker 5.4.0
```

## 2. Imagen a desplegar

Se despliega siempre una **versión publicada**, nunca código sin versionar:

```bash
git checkout v1.1.0
scripts/release-image.sh 1.1.0 --push ghcr.io/ogov-dev/ogov-ckan
# -> imprime el digest: ghcr.io/ogov-dev/ogov-ckan@sha256:…
```

El script se niega a construir con cambios sin commit, prueba la imagen
(dependencias, herramientas, ausencia de secretos, rechazo de secretos débiles) y
no sobrescribe una versión ya publicada. Usar el **digest** en el inventario
garantiza que todas las instancias ejecutan exactamente la misma imagen.

## 3. Inventario del ayuntamiento

En el repositorio de despliegues (ver README, "Organización de repositorios"),
una carpeta por ayuntamiento:

```
inventories/produccion/
  hosts.yml
  host_vars/fuenlabrada/main.yml
  host_vars/fuenlabrada/vault.yml      # cifrado
```

`hosts.yml`:

```yaml
all:
  children:
    ckan_instances:
      hosts:
        fuenlabrada:
          ansible_host: servidor.ejemplo.internal
          ansible_user: despliegue
          ansible_become: true
```

`host_vars/fuenlabrada/main.yml` (ver `ansible/roles/ckan/meta/argument_specs.yml`
para todas las variables):

```yaml
ckan_baseline_dir: /ruta/al/checkout/de/ogov-ckan   # misma versión que la imagen
ckan_image: ghcr.io/ogov-dev/ogov-ckan@sha256:…
ckan_instance_name: fuenlabrada
ckan_http_port: 8080
ckan_site_url: https://datosabiertos.ayto-fuenlabrada.es
ckan_site_title: Fuenlabrada Datos Abiertos
ckan_site_id: fuenlabrada
ckan_sysadmin_email: datosabiertos@ayto-fuenlabrada.es
ckan_dir3_code: L01…                                  # DIR3 oficial
ckan_spatial_uri: http://datos.gob.es/recurso/sector-publico/territorio/Provincia/Madrid
```

Secretos (generar una vez por instancia y **no cambiarlos** después):

```bash
cp ansible/inventories/vault.ejemplo.yml inventories/produccion/host_vars/fuenlabrada/vault.yml
python3 -c 'import secrets;print(secrets.token_urlsafe(48))'   # uno por cada valor
ansible-vault encrypt inventories/produccion/host_vars/fuenlabrada/vault.yml
```

Restricciones que el rol valida antes de tocar el servidor: secretos con longitud
mínima, y ningún valor con comilla simple, salto de línea ni `%` (CKAN no arranca
con `%` en sus variables).

## 4. Despliegue

```bash
cd ansible
ansible -i <inventario> fuenlabrada -m ping --ask-vault-pass           # conectividad
ansible-playbook playbooks/deploy.yml -i <inventario> --limit fuenlabrada --ask-vault-pass
```

El rol crea `/opt/ogov-ckan/<instancia>`, escribe el `.env` (0600), levanta la
instancia, espera a que CKAN responda y comprueba URL, título y extensiones.
Una segunda ejecución no cambia nada (`changed=0`).

## 5. Verificación

```bash
# token de sysadmin para las pruebas
ssh servidor 'docker exec <instancia>-ckan-1 ckan -c /srv/app/ckan.ini user token add ckan_admin aceptacion'

# aceptación (19 comprobaciones; crea y borra sus propios datos)
docker run --rm -v "$PWD/tests/acceptance:/acc" -w /acc --entrypoint python3 ogov/ckan:test \
    acceptance.py --url https://<portal> --token <token> --report-dir /acc/reports

# federación: catálogo contra las formas SHACL oficiales de DCAT-AP-ES
docker run --rm -v "$PWD/tests/federation:/v" -w /v python:3.12-slim \
    sh -c 'pip install -q -r requirements.txt && python validate_dcat_ap_es.py --url https://<portal>'
```

Resultado esperado: 19/19 y **0 violaciones** (los avisos son propiedades
recomendadas que dependen de la calidad de los metadatos).

## 6. Puesta en marcha

1. Entrar como `ckan_admin` (contraseña del vault) y configurar en
   `/ckan-admin/config`: logotipo, colores, imagen de portada, textos, enlaces del
   pie y **correo de contacto** (también se usa como punto de contacto en DCAT).
2. Crear organizaciones, grupos y usuarios editores.
3. Si se sustituye un portal existente: migración estándar (README, "Migración").
4. Conector de transparencia, si aplica (apartado 7).
5. Federación con datos.gob.es (apartado 8).

## 7. Sincronización con el Portal de Transparencia

Si el ayuntamiento tiene portal de transparencia de oGov, sus tablas se publican
como datasets y se mantienen al día **automáticamente**.

1. Crear en CKAN la organización indicada en el YAML del conector (y, si se
   quieren usar, los grupos de su `clasificacion`; si un grupo no existe, el
   dataset se publica sin él y queda un aviso en el informe).
2. En el inventario, apuntar a su configuración (en el repositorio de
   despliegues, p. ej. `connectors/fuenlabrada.yml`):
   ```yaml
   ckan_transparencia_config: "{{ inventory_dir }}/connectors/fuenlabrada.yml"
   # Opcional: por defecto, el día 1 de cada mes a las 03:30
   ckan_transparencia_cron: {minute: "30", hour: "3", day: "1", month: "*", weekday: "*"}
   ```
3. Desplegar. El rol copia la configuración a la instancia y programa la tarea
   en el `crontab` de root. Para lanzarla en el momento:
   ```bash
   sudo sh -c 'cd /opt/ogov-ckan/<instancia> && docker compose exec -T ckan /srv/app/ogov-sync-transparencia.sh'
   ```

Cada ejecución:

- usa un **token temporal** que se revoca al terminar (no hay credenciales guardadas);
- solo resube las tablas que han cambiado (y solo esas pasan por XLoader);
- **retira** (borrado recuperable desde la papelera de CKAN) los datasets de tablas
  que ya no existen, se han vaciado o se han excluido, **salvo** que la ejecución no
  sea fiable: errores, origen sin tablas o retirada de más del 20 % (configurable
  con `max_retirada` en el YAML). En esos casos no retira nada y termina con error;
- deja registro en `/opt/ogov-ckan/<instancia>/logs/sync-transparencia.log` y un
  informe JSON en el volumen (`/var/lib/ckan/ogov-tools/connectors/transparencia/
  reports/<portal>-ultimo.json`).

Los datasets de transparencia **no se editan a mano en CKAN**: la siguiente
sincronización los sobrescribe. Las correcciones se hacen en el Portal de
Transparencia. La vigilancia de que la tarea no falle (revisar el log o el
informe, alertas) corresponde a la monitorización de la infraestructura.

## 8. Federación con datos.gob.es

- **Alta (una vez, manual y administrativa):** el ayuntamiento se registra en
  datos.gob.es como organismo publicador y da de alta la URL del catálogo
  `https://<portal>/catalog.rdf`, eligiendo la **periodicidad** de federación.
- **Después es automático:** el federador de datos.gob.es recoge el catálogo con
  esa periodicidad (desde su panel también puede lanzarse a mano). Todo lo que
  cambie en CKAN —datasets editados por el ayuntamiento y tablas sincronizadas
  desde transparencia— llega en la siguiente recogida.
- Para que lo sincronizado el día 1 llegue cuanto antes, la periodicidad de
  datos.gob.es debe ser igual o más frecuente que la de la sincronización (p. ej.
  semanal o diaria).
- Antes del alta, validar el catálogo con las formas SHACL oficiales (apartado 5):
  0 violaciones.
