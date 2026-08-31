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
from facturas.models import OrdenesRd

from .models import ProveedorInvitacion, ProveedorPerfil

User = get_user_model()


class FacturaNoPerteneceAlProveedorError(Exception):
    """El keyorden no existe, o su codfacturar no coincide con el del
    proveedor autenticado -- para que un proveedor no pueda anular la
    factura de otro adivinando/probando un keyorden."""


class FacturaYaLiquidadaError(Exception):
    """La factura ya forma parte de una liquidación activa -- igual que
    en el sistema interno, no se puede anular hasta que esa liquidación
    se anule (lo que la libera)."""


def anular_factura_del_proveedor(keyorden: int, codfacturar_proveedor: str,
                                  motivo: str, usuario: str) -> OrdenesRd:
    """Anula una factura del propio proveedor (mismo candado de
    seguridad que buscar_orden_para_proveedor: SOLO si su `codfacturar`
    coincide con el del usuario autenticado) y, con eso, libera de
    inmediato el saldo de la orden de compra (calcular_saldo_orden ya
    excluye las facturas anuladas).

    Lanza FacturaNoPerteneceAlProveedorError si el keyorden no existe o
    no es de este proveedor. Lanza FacturaYaLiquidadaError si ya forma
    parte de una liquidación activa. Lanza
    facturas_services.FacturaYaAnuladaError si ya estaba anulada."""
    propio = (codfacturar_proveedor or '').strip().upper()
    try:
        factura = OrdenesRd.objects.using('default').get(keyorden=keyorden)
    except OrdenesRd.DoesNotExist:
        raise FacturaNoPerteneceAlProveedorError('Esa factura no existe.')

    if (factura.codfacturar or '').strip().upper() != propio:
        raise FacturaNoPerteneceAlProveedorError('Esa factura no te pertenece.')

    if facturas_services.liquidacion_activa_de(keyorden):
        raise FacturaYaLiquidadaError(
            'Esta factura ya fue incluida en una liquidación y no se puede anular en este momento. '
            'Contacta a quien administra el sistema.'
        )

    return facturas_services.anular_factura(keyorden=keyorden, motivo=motivo, usuario=usuario)


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
