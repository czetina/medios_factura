# Sistema de Ingreso de Facturas de Proveedor contra Órdenes de Compra

Proyecto Django que permite:
1. Buscar una orden de compra por su número (`orden.orden`) usando la
   consulta que proporcionaste (adaptada para búsqueda genérica, ver
   "Supuestos" abajo).
2. Si la orden aparece en más de un presupuesto/cliente, el usuario elige
   cuál es.
3. Calcula automáticamente el saldo disponible: `totalorden` de la orden
   menos la suma de lo ya registrado en `ordenesrd` (considerando notas
   de crédito con signo negativo y descartando facturas anuladas).
4. Captura: número de factura del proveedor (alfanumérico, 50), fecha de
   factura, monto (y opcionalmente IVA / otro impuesto), un PDF o imagen
   adjunto, y observaciones.
5. Valida que `saldo_ya_facturado + monto_nuevo <= totalorden`. Si se
   excede, rechaza el registro con un mensaje claro.
6. Si todo es válido, hace el `INSERT` en `ordenesrd` con la estructura
   exacta que enviaste, y guarda el archivo adjunto en una tabla propia
   (`facturas_proveedor_adjuntos`) enlazada por `keyorden` (auto_increment).
7. **Listado de facturas recibidas** (`/facturas/recibidas/`): muestra
   todas las facturas ya registradas, filtradas por año (obligatorio) y
   mes (opcional), con el total del período. El filtro es por
   `fecrecep` (fecha en que se recibió/registró la factura en el
   sistema); si prefieres filtrar por `fecfactura` o por `mesfac`, es un
   cambio de un solo lookup en
   `facturas/services.py::listar_facturas_recibidas`.
8. **Anular factura** (botón "Anular" en el listado): pide un motivo
   obligatorio y marca la factura como anulada
   (`facanula='Si'`, `f_anula`, `obsanula`, `usranula`) — **no la borra
   físicamente**, a propósito, para conservar el rastro de auditoría.
   Una vez anulada, automáticamente deja de contar en el saldo de la
   orden y desaparece del listado (que ya excluye `facanula='Si'`).
9. **Formato de número**: todos los montos se muestran con separador de
   miles = coma y decimal = punto (`1,234.56`), vía un filtro de
   plantilla propio (`facturas/templatetags/facturas_extras.py::moneyfmt`)
   que NO depende del `LANGUAGE_CODE` de Django (el locale español
   normalmente formatea al revés).
10. **No se permite repetir el no. de factura por proveedor**: antes de
    guardar, se valida que no exista ya una factura ACTIVA (no anulada)
    con el mismo número para el mismo proveedor. *** SUPUESTO A
    CONFIRMAR ***: "proveedor" se identifica aquí con
    `ordenesrd.codfacturar` (el código del medio que se factura). Si el
    proveedor real es otro campo, dime cuál y ajusto
    `facturas/services.py::numfactura_ya_registrada`.
11. **Filtros adicionales en "Facturas recibidas"**: ahora también se
    puede filtrar por Cliente (código, `codcli`) y por Presupuesto
    (`codpresup`), ambos con coincidencia parcial. El filtro de cliente
    busca por CÓDIGO, no por nombre (el nombre vive en `climae`, en el
    esquema `pivot_comsys`, y `ordenesrd` no lo guarda directamente) —
    si prefieres buscar/mostrar por nombre del cliente, dime y agrego
    el JOIN correspondiente.
12. **Revisar y aceptar factura** (link "Revisar" en el listado): abre
    una pantalla con TODOS los datos capturados de la factura (orden,
    presupuesto, cliente, medio, montos, adjunto) para confirmarlos
    antes de aceptar. Al aceptar, solo marca un estado interno
    (Pendiente / Aceptada) — **no genera ningún movimiento, póliza ni
    CXP real** en el sistema contable; ese paso sigue siendo manual en
    el sistema contable actual, tal como pediste ("el mismo proceso sin
    llegar a la contabilidad"). El estado se guarda en una tabla propia
    (`facturas_proveedor_codificacion`, modelo `FacturaCodificacion`),
    sin tocar `ordenesrd`. Filtro disponible: Todas / Pendientes de
    aceptar / Ya aceptadas. Sin login/roles todavía (prototipo).
13. **Esquema de la BD contable (`pivot`)**: agregado como tercera
    variable en `config.py` (`ESQUEMA_PIVOT_CONTABILIDAD`), junto a
    `pivot_medios` y `pivot_comsys`. Por ahora el sistema NO lee ni
    escribe nada ahí (para no interferir con el proceso contable real
    de pólizas/retenciones descrito en `Sistema_de_liquidación.docx`).
    Incluí un comando de utilería,
    `python manage.py inspeccionar_pivot`, que imprime la estructura
    real (columnas/tipos) de las tablas contables detectadas en ese
    documento (`liq_quedan`, `liq_liquidaciones`, `transacciones`,
    `mnt_retenciones`, etc.) — córrelo localmente y comparte la salida
    para diseñar cualquier integración futura con datos reales en vez
    de adivinar la estructura.
14. **Liquidar clientes** (`/facturas/liquidar/`): agrupa las facturas
    ya **Aceptadas** (y no anuladas, y que no estén ya en otra
    liquidación) por el criterio que elijas — Presupuesto, Cliente,
    Marca o Tipo de medio — muestra subtotal por grupo, y permite
    **guardar** cada grupo como una `Liquidacion` propia de este
    sistema (`/facturas/liquidaciones/` para verlas después). Al
    guardar, el grupo se vuelve a calcular en el servidor en ese
    momento (no confía en lo que mandó el navegador), así que si dos
    personas intentan liquidar el mismo grupo casi al mismo tiempo, la
    segunda simplemente no encuentra nada pendiente. **Esto NO genera
    póliza, no calcula retenciones IVA/ISR, ni toca `liq_quedan` /
    `liq_liquidaciones`** — es un registro de agrupación propio,
    completamente separado del sistema contable real, tal como
    definimos ("el mismo proceso sin llegar a la contabilidad").

## Portal de Proveedores (`portal/`)

App nueva y separada (`/portal/`) para que el proveedor mismo suba su
factura, sin pasar por el sistema interno. Reutiliza toda la lógica ya
probada de `facturas.services` (búsqueda de orden, saldo, duplicados,
`registrar_factura`) — el portal es solo una capa de seguridad y una
UI simplificada encima.

- **Alta por invitación**: alguien de tu equipo crea una
  `ProveedorInvitacion` (email + código de proveedor `codfacturar`)
  desde `/admin/` — ahí mismo aparece el link listo para copiar y
  enviar. El proveedor entra a `/portal/registro/<token>/`, define su
  contraseña, y su cuenta queda ligada a **ese único código de
  proveedor** para siempre (no lo puede cambiar ni escribir él mismo).
- **Seguridad clave**: `portal/services.py::buscar_orden_para_proveedor`
  filtra el resultado de la búsqueda para quedarse SOLO con las
  órdenes cuyo `codfacturar` sea igual al del proveedor autenticado.
  Un proveedor no puede ver ni facturar una orden de otro proveedor,
  aunque adivine o comparta el número de orden — probado con pruebas
  automatizadas.
- **Solo pide el monto total**: el formulario del proveedor
  (`FacturaProveedorPortalForm`) no tiene campos de IVA ni de otros
  impuestos. `facturas/services.py::calcular_impuestos_proporcionales`
  los calcula solo, proporcional a como ya vienen definidos en la
  orden (`orden.valiva / orden.totalorden`, `orden.valtp /
  orden.totalorden`) — probado con pruebas automatizadas.
- **"Mis facturas"**: el proveedor puede ver el historial de lo que él
  mismo ha enviado (`/portal/mis-facturas/`).
- Login/registro completamente separados del sistema interno
  (`/facturas/...` sigue sin login, tal como decidiste para el
  prototipo; `/portal/...` SIEMPRE requiere sesión de proveedor).

### Cómo invitar a un proveedor (mientras no haya envío de correo automático)

1. Entra a `/admin/portal/proveedorinvitacion/add/`.
2. Llena email, código de proveedor (`codfacturar`) y nombre.
3. Guarda y vuelve a la lista — la columna "Link de registro" trae el
   link listo para copiar y mandarle al proveedor (por el momento,
   manualmente; no hay envío de correo automático en este prototipo).

### Código QR en Visual FoxPro 9

El link que debe codificar el QR es:

```
{PORTAL_BASE_URL}/portal/?orden=<numero_de_orden>
```

(`PORTAL_BASE_URL` se configura en `config.py`). El proveedor entra
ya autenticado con su propia sesión, así que el número de orden se
precarga automáticamente en el formulario de búsqueda — no hace falta
mandar el código de proveedor en el link (siempre se toma de la cuenta
con la que inició sesión, por seguridad). Te compartí un snippet de
ejemplo en VFP9 para generar y mostrar el QR usando un servicio público
de generación de imágenes; para producción probablemente convenga que
este mismo sistema exponga su propio endpoint `/portal/qr/` que
devuelva la imagen directamente (evita depender de un servicio externo
y de tener internet en la estación que imprime) — avísame si lo
quieres y lo agrego.

### Pendiente / próximos pasos posibles

- Envío de correo real de la invitación (hoy es manual, copiar/pegar
  el link).
- Si una orden aparece en más de un presupuesto para el mismo
  proveedor, hoy el portal toma la primera coincidencia — se puede
  agregar una pantalla de selección igual a la del sistema interno si
  hace falta.
- Endpoint propio para generar el QR como imagen (en vez de depender
  de un servicio externo desde VFP).

## Estructura del proyecto

```
proyecto_facturas/
├── manage.py
├── config.py                # 👈 credenciales DB + nombres de esquema + codpai/codagencia
├── requirements.txt
├── proyecto_facturas/       # settings, urls, wsgi
├── facturas/                # sistema interno (staff)
│   ├── models.py            # Ordenes, OrdenesRd (managed=False) + tablas propias nuevas
│   ├── services.py          # SQL de búsqueda, saldo, INSERT, liquidaciones
│   ├── forms.py / views.py / urls.py / admin.py
│   ├── management/commands/inspeccionar_pivot.py
│   └── templates/facturas/
└── portal/                  # portal de proveedores (login por invitación)
    ├── models.py            # ProveedorInvitacion, ProveedorPerfil
    ├── services.py          # búsqueda restringida al proveedor, activar invitación
    ├── forms.py / views.py / urls.py / admin.py
    └── templates/portal/
```

## ⚙️ Configuración (`config.py`)

Todo lo que cambia entre ambientes vive en **`config.py`** (junto a
`manage.py`, en la raíz del proyecto). Es el único archivo que necesitas
tocar para el prototipo:

```python
# config.py
DB_HOST = 'localhost'
DB_PORT = '3306'
DB_USER = 'admin'
DB_PASSWORD = '@dmin2!'

ESQUEMA_PIVOT_MEDIOS = 'pivot_medios'   # BD default (ordenes, ordenesrd, tipmed, ...)
ESQUEMA_PIVOT_COMSYS = 'pivot_comsys'   # BD referenciada en el SQL (climae, marmae, ...)
ESQUEMA_PIVOT_CONTABILIDAD = 'pivot'    # BD contable (liq_quedan, transacciones, ...) -- solo lectura por ahora

CODPAI_DEFAULT = 'GT'
CODAGENCIA_DEFAULT = 'PIVOT'
```

- Si en otro ambiente las bases se llaman distinto (p. ej.
  `pivot_medios_qa` / `pivot_comsys_qa`), solo cambias esas dos líneas
  y tanto la conexión de Django como el `JOIN` cruzado del SQL de
  búsqueda se actualizan automáticamente (`facturas/services.py` arma
  el SQL con `.format(esquema_comsys=...)` antes de ejecutarlo).
- `CODPAI_DEFAULT` / `CODAGENCIA_DEFAULT` son los valores que aparecen
  precargados en el formulario de búsqueda (el usuario los puede
  cambiar en pantalla si algún día se maneja más de un país/agencia).

⚠️ **Prototipo**: las credenciales quedan en texto plano en este
archivo a propósito, para simplificar. Antes de producción, muévelas a
variables de entorno o a un gestor de secretos y agrega `config.py` a
`.gitignore`.

## Instalación

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Edita `config.py` con los datos de tu conexión (host, usuario,
contraseña, puerto y nombres de esquema) según la sección de arriba.

Luego:

```bash
python manage.py migrate           # crea las tablas propias nuevas (facturas + portal)
python manage.py createsuperuser   # NECESARIO para entrar a /admin/ y crear invitaciones de proveedor
python manage.py runserver
```

- Sistema interno (staff): `http://127.0.0.1:8000/facturas/`
- Portal de proveedores: `http://127.0.0.1:8000/portal/`
- Admin (crear invitaciones): `http://127.0.0.1:8000/admin/`

Abre: `http://127.0.0.1:8000/facturas/`

## ⚠️ Supuestos que debes confirmar / ajustar

Como no tenía el `DESCRIBE` real de las tablas ni algunas reglas de
negocio, tomé decisiones razonables que **debes validar**:

1. **Generación de `keyorden`** (PK de `ordenesrd`): no se especificó
   cómo la genera el sistema legacy. Implementé
   `facturas/services.py::generar_keyorden()` con el formato
   `orden-mesfac-<8 caracteres aleatorios>` (máx. 50 caracteres). Si
   existe una convención real (autoincremental, secuencia en otra
   tabla, etc.), dime cuál es y ajusto esa función únicamente.

2. **Filtros de fecha en la búsqueda**: tu consulta original traía
   `orden.mesfac >= '2026-05' AND ... = '2026-05'` fijo, porque era un
   reporte de un mes puntual. Para la pantalla de "buscar orden por
   número" quité esos filtros de mes/año (se busca la orden sin
   importar cuándo se factura), pero mantuve: `anula='No'`,
   `ordimpresa='Si'`, `concepto IN ('1','11','12')`. Si en realidad la
   búsqueda SÍ debe limitarse a un mes/año vigente, lo agrego de vuelta
   como filtro opcional en el formulario.

3. **Campo "monto de la factura"**: lo mapeé a `ordenesrd.totalfac`
   (y también a `valtotal` por consistencia). Si tu negocio maneja
   `valtotal`, `valiva` y `valtp` como conceptos distintos que deben
   sumar `totalfac`, dime la fórmula exacta y ajusto el formulario
   (agregué campos opcionales de IVA y otro impuesto, pero no se
   auto-calculan ni se validan entre sí).

4. **Validación de saldo**: uso `orden.totalorden` como el "total de la
   orden de compra" contra el cual no se puede exceder. Si el campo
   correcto es otro (`orden.valtotal`, `ctoneto`, etc.), es un cambio
   de una línea en `services.py::calcular_saldo_orden`.

5. **`tipofac`**: agregué un selector Factura/Nota de Crédito (`FC`/`NC`)
   porque la consulta original resta las notas de crédito del total
   facturado. Ajusta los códigos si en tu catálogo son distintos.

6. **Usuario que registra** (`creusr`/`codusr`): actualmente toma
   `request.user.username` si hay sesión de Django iniciada, o
   `"anonimo"` si no. Si el sistema debe forzar login, activa
   `@login_required` en las vistas (dejé el import listo para
   agregarlo) y configura `django.contrib.auth`.

7. **Multi-base de datos**: la consulta original mezcla `pivot_medios`
   (implícita) y `pivot_comsys` (explícita) vía `esquema.tabla`. Esto
   solo funciona si ambas bases están en el **mismo servidor MySQL** y
   el usuario configurado tiene permisos sobre ambas — así lo dejé
   armado (una sola conexión `default`, SQL crudo). Si están en
   servidores distintos, avísame para separarlo en dos consultas + join
   en Python.

8. **Tipos/longitudes de columnas**: usé longitudes razonables
   (`numfactura` VARCHAR(50) tal como pediste, etc.) pero no tenía el
   `DESCRIBE` real de `ordenesrd`/`ordenes`. Como los modelos son
   `managed = False`, Django **no** valida ni migra estas tablas — solo
   debes asegurarte de que los `max_length` no sean menores a lo real
   (si son mayores no pasa nada).

## Seguridad / cosas pendientes antes de producción

- Cambiar `SECRET_KEY` y poner `DEBUG=False`.
- Restringir `ALLOWED_HOSTS`.
- Agregar autenticación (`@login_required`) a las vistas si el sistema
  debe ser solo para usuarios internos.
- Servir `MEDIA_ROOT` desde almacenamiento real (S3, etc.) en vez del
  filesystem local si se despliega en un servidor efímero.
