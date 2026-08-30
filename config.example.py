"""
CONFIGURACIÓN DEL PROTOTIPO — EJEMPLO
=======================================
Copia este archivo a `config.py` (que NO se versiona, ver .gitignore) y
ajusta los valores reales de tu ambiente.

⚠️ NOTA: esto es válido para un PROTOTIPO (credenciales en texto plano).
Antes de pasar a producción, mueve DB_USER / DB_PASSWORD a variables de
entorno o a un gestor de secretos.
"""

# ---------------------------------------------------------------------
# 1) Conexión a MySQL
# ---------------------------------------------------------------------
DB_HOST = 'localhost'
DB_PORT = '3306'
DB_USER = 'CAMBIA_ESTE_USUARIO'
DB_PASSWORD = 'CAMBIA_ESTA_CONTRASENA'

# ---------------------------------------------------------------------
# 2) Nombres de los esquemas / bases de datos MySQL
# ---------------------------------------------------------------------
# Base donde viven: ordenes, ordenesrd, tipmed, tsubmed, medios, circmae
# -> Esta es la base "default" a la que Django se conecta.
ESQUEMA_PIVOT_MEDIOS = 'pivot_medios'

# Base donde viven: climae, marmae, prdmae, clicamae, ageperso, monmae,
# impmae, paimae, agemae -> se referencia como "esquema.tabla" dentro
# del SQL de búsqueda de la orden (JOIN cruzado entre bases, mismo
# servidor MySQL).
ESQUEMA_PIVOT_COMSYS = 'pivot_comsys'

# Base de la CONTABILIDAD (liq_quedan, liq_liquidaciones, transacciones,
# mnt_retenciones, etc.) -- mismo servidor MySQL. Este sistema NO
# escribe todavía en estas tablas (por ahora solo lectura/consulta, si
# se necesita), para no duplicar ni interferir con el proceso contable
# real de pólizas y retenciones.
ESQUEMA_PIVOT_CONTABILIDAD = 'pivot'

# ---------------------------------------------------------------------
# 3) Parámetros de negocio por defecto (precargados en el formulario de
#    búsqueda; el usuario los puede sobreescribir en pantalla)
# ---------------------------------------------------------------------
CODPAI_DEFAULT = 'GT'
CODAGENCIA_DEFAULT = 'PIVOT'

# ---------------------------------------------------------------------
# 4) Portal de Proveedores
# ---------------------------------------------------------------------
# URL base pública donde corre este sistema (para armar los links de
# invitación y, más adelante, el link que codifica el QR). En
# desarrollo local déjalo así; cuando se publique, cámbialo al dominio
# real (ej. 'https://facturas.tuempresa.com').
PORTAL_BASE_URL = 'http://localhost:8000'
