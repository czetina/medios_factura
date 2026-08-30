"""
Lógica de negocio propia del Portal de Proveedores.

Reutiliza TODA la lógica ya probada de `facturas.services` (búsqueda de
orden, saldo, validación de duplicados, INSERT en ordenesrd). Lo único
específico de este módulo es:

  1. Restringir la búsqueda de orden al código de proveedor del usuario
     autenticado (seguridad clave del portal).
  2. Activar una invitación (crear el User + ProveedorPerfil) -- función
     que se dejó implementada pero actualmente NO se usa desde ninguna
     vista: el alta de proveedores se hace directamente en /admin/ con
     código+contraseña (ver portal.admin.ProveedorPerfilAdmin).
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.db import transaction
from django.utils import timezone

from facturas import services as facturas_services

from .models import ProveedorInvitacion, ProveedorPerfil

User = get_user_model()


def buscar_orden_para_proveedor(orden_ingresada: str, codpai: str, codagencia: str,
                                 codfacturar_proveedor: str) -> list[dict]:
    """Busca la orden (mismo SQL que usa el sistema interno) y luego
    filtra el resultado para quedarse SOLO con las filas cuyo
    `codfacturar` sea igual al del proveedor autenticado.

    Esto es lo que impide que un proveedor vea o facture una orden que
    no le corresponde, aunque adivine o comparta el número de orden de
    otro proveedor.
    """
    resultados = facturas_services.buscar_ordenes(orden_ingresada, codpai, codagencia)
    propio = (codfacturar_proveedor or '').strip().upper()
    return [
        r for r in resultados
        if (r.get('codfacturar') or '').strip().upper() == propio
    ]


class InvitacionInvalidaError(Exception):
    """El token no existe, ya fue usado, o no corresponde a ninguna invitación."""


@transaction.atomic
def activar_invitacion(token, password: str) -> ProveedorPerfil:
    """Crea el usuario + perfil de proveedor a partir de una invitación
    válida y la marca como usada. Lanza InvitacionInvalidaError si el
    token no existe o ya fue usado."""
    try:
        invitacion = ProveedorInvitacion.objects.select_for_update().get(token=token, usada=False)
    except ProveedorInvitacion.DoesNotExist:
        raise InvitacionInvalidaError('Este enlace de invitación ya no es válido (ya fue usado o no existe).')

    if User.objects.filter(username=invitacion.email).exists():
        raise InvitacionInvalidaError('Ya existe una cuenta con ese correo electrónico.')

    user = User.objects.create(
        username=invitacion.email,
        email=invitacion.email,
        password=make_password(password),
    )
    perfil = ProveedorPerfil.objects.create(
        user=user,
        codfacturar=invitacion.codfacturar,
        nombre_proveedor=invitacion.nombre_proveedor,
    )

    invitacion.usada = True
    invitacion.fecha_uso = timezone.now()
    invitacion.save(update_fields=['usada', 'fecha_uso'])

    return perfil
