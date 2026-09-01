"""
Configuración de Django para el proyecto 'proyecto_facturas'.

Sistema para el ingreso de facturas de proveedor asociadas a órdenes de
compra (tabla legacy `ordenes` / `ordenesrd`) en la base de datos MySQL
`pivot_medios`.

NOTA IMPORTANTE SOBRE MULTI-BASE DE DATOS:
La consulta original hace JOIN entre `pivot_medios` (tablas ordenes,
ordenesrd, tipmed, tsubmed, medios, circmae) y `pivot_comsys` (climae,
marmae, prdmae, ageperso, monmae, impmae, paimae, agemae, clicamae)
usando la sintaxis `esquema.tabla`. Esto SOLO funciona si ambos esquemas
viven en el MISMO servidor MySQL y el usuario configurado abajo tiene
permisos sobre ambos. Por eso se ejecuta como SQL crudo (raw SQL) contra
una única conexión ('default'), en vez de modelar cada tabla como una
base de datos Django separada con DATABASE_ROUTERS.

Si en tu ambiente `pivot_comsys` está en un servidor distinto, avísame
para dividir la consulta en dos consultas + un JOIN en Python.
"""

from pathlib import Path
import os
import sys

BASE_DIR = Path(__file__).resolve().parent.parent

# config.py vive junto a manage.py, en la raíz del proyecto.
sys.path.insert(0, str(BASE_DIR))
import config  # noqa: E402  -> ver ese archivo para editar credenciales/esquemas

# ---------------------------------------------------------------------------
# Seguridad
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'CAMBIA-ESTA-LLAVE-EN-PRODUCCION')
DEBUG = os.environ.get('DJANGO_DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', '*').split(',')

# ---------------------------------------------------------------------------
# Apps instaladas
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'facturas',  # app propia de este sistema
    'portal',    # portal de proveedores (login por invitación)
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'proyecto_facturas.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'proyecto_facturas.wsgi.application'

# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
# Los valores reales se editan en config.py (raíz del proyecto), NO aquí.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': config.ESQUEMA_PIVOT_MEDIOS,
        'USER': config.DB_USER,
        'PASSWORD': config.DB_PASSWORD,
        'HOST': config.DB_HOST,
        'PORT': config.DB_PORT,
        'OPTIONS': {
            'charset': 'utf8mb4',
            # 'sql_mode' STRICT deshabilitado por compatibilidad con datos legacy
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es-gt'
TIME_ZONE = 'America/Guatemala'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

# Archivos subidos por el usuario (PDF / imágenes de facturas)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Tamaño máximo de subida (10 MB) - ajustar si se requieren PDFs más grandes
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# Parámetros de negocio propios del sistema.
# Todos se editan en config.py (raíz del proyecto), NO aquí.
# ---------------------------------------------------------------------------
FACTURAS_CODPAI_DEFAULT = config.CODPAI_DEFAULT
FACTURAS_CODAGENCIA_DEFAULT = config.CODAGENCIA_DEFAULT
FACTURAS_ESQUEMA_PIVOT_COMSYS = config.ESQUEMA_PIVOT_COMSYS
FACTURAS_ESQUEMA_PIVOT_CONTABILIDAD = config.ESQUEMA_PIVOT_CONTABILIDAD
FACTURAS_PORTAL_BASE_URL = config.PORTAL_BASE_URL
FACTURAS_SUBIRPDF_API_KEY = config.SUBIRPDF_API_KEY

LOGIN_URL = '/admin/login/'
LOGIN_REDIRECT_URL = '/facturas/'

# Para que los mensajes de error se vean con la clase Bootstrap 'danger'
from django.contrib.messages import constants as messages_constants  # noqa: E402
MESSAGE_TAGS = {
    messages_constants.ERROR: 'danger',
}
