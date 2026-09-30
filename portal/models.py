"""
Modelos del Portal de Proveedores.

Usamos el modelo de usuario estándar de Django (auth.User) + un perfil
propio (ProveedorPerfil) que amarra ese usuario a UN proveedor. Esto es
lo que garantiza la seguridad clave del portal: un proveedor autenticado
SOLO puede ver/facturar órdenes cuyo código de proveedor sea igual al de
su propio perfil -- nunca elige ni escribe su propio código, siempre
viene de su cuenta.

CONFIRMADO por el usuario: el código de proveedor NO es `codfacturar`
solo -- un mismo `codfacturar` puede repetirse bajo distinto
`codtipmed`/`codtsubmed` y en realidad tratarse de otro proveedor (ver
facturas.services.codigo_proveedor, y el JOIN a `medios` en
SQL_BUSCAR_ORDEN_TEMPLATE, que ya usa las tres columnas juntas). Por eso
`ProveedorPerfil` guarda las tres columnas, no solo `codfacturar`.

El alta de proveedores se hace directamente en /admin/ (ver
portal.admin.ProveedorPerfilAdmin): alguien de tu equipo crea ahí mismo
un código de acceso y una contraseña para el proveedor -- no hay
invitación por correo ni registro propio del proveedor. El modelo
ProveedorInvitacion y el flujo de activación por token (ver
services.activar_invitacion) se dejaron en el código sin usar, por si
se quiere reactivar ese flujo más adelante (p. ej. para producción).
"""
import uuid

from django.conf import settings
from django.db import models


class ProveedorInvitacion(models.Model):
    """Invitación pendiente (o ya usada) para que un proveedor se
    registre en el portal, ligada a un código de proveedor específico.

    Actualmente NO está conectada a ninguna vista ni al admin (el alta
    se hace directamente en ProveedorPerfilAdmin, con código+contraseña
    en vez de correo). Se dejó el modelo y la tabla por si se retoma
    este flujo más adelante."""

    email = models.EmailField()
    codtipmed = models.CharField(max_length=10, blank=True, default='')
    codtsubmed = models.CharField(max_length=10, blank=True, default='')
    codfacturar = models.CharField(
        max_length=20,
        help_text='Código del proveedor (ordenesrd.codfacturar) al que va a quedar ligada esta cuenta.'
    )
    nombre_proveedor = models.CharField(max_length=255, blank=True)
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    creada = models.DateTimeField(auto_now_add=True)
    creada_por = models.CharField(max_length=50, blank=True)
    usada = models.BooleanField(default=False)
    fecha_uso = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'portal_proveedor_invitaciones'
        verbose_name = 'Invitación de proveedor'
        verbose_name_plural = 'Invitaciones de proveedores'

    def __str__(self):
        estado = 'usada' if self.usada else 'pendiente'
        return f'{self.email} ({self.codfacturar}) - {estado}'


class ProveedorPerfil(models.Model):
    """Liga un usuario de Django (auth.User) a un proveedor. Esta es la
    pieza de seguridad: todas las vistas del portal filtran SIEMPRE por
    el código de proveedor de request.user.proveedorperfil (ver
    `codigo_proveedor` abajo), nunca por un valor que el proveedor pueda
    escribir o manipular."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='proveedorperfil'
    )
    codtipmed = models.CharField(max_length=10, blank=True, default='')
    codtsubmed = models.CharField(max_length=10, blank=True, default='')
    codfacturar = models.CharField(max_length=20, db_index=True)
    nombre_proveedor = models.CharField(max_length=255, blank=True)
    activo = models.BooleanField(default=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'portal_proveedor_perfiles'
        verbose_name = 'Perfil de proveedor'
        verbose_name_plural = 'Perfiles de proveedores'

    @property
    def codigo_proveedor(self) -> str:
        """Identidad real del proveedor (codtipmed+codtsubmed+codfacturar
        concatenados) -- ver facturas.services.codigo_proveedor. Import
        local para no acoplar la carga de este módulo a la app facturas."""
        from facturas.services import codigo_proveedor
        return codigo_proveedor(self.codtipmed, self.codtsubmed, self.codfacturar)

    def __str__(self):
        return f'{self.user.email} ({self.codigo_proveedor})'
