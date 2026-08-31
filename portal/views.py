import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from facturas import services as facturas_services
from facturas.forms import MotivoAnulacionForm
from facturas.models import FacturaAdjunto, LiquidacionDetalle, OrdenesRd

from . import services
from .forms import (
    BuscarOrdenPortalForm, FacturaProveedorPortalForm, LoginProveedorForm, SubirAdjuntoPortalForm,
)

LOGIN_URL = 'portal:login'
SESSION_ORDEN_SEL = 'portal_orden_seleccionada'


# ---------------------------------------------------------------------------
# Serialización de sesión (igual que en facturas/views.py: Decimal/date
# no son JSON-serializables por defecto)
# ---------------------------------------------------------------------------

def _serializar(valor):
    if isinstance(valor, Decimal):
        return {'__decimal__': str(valor)}
    if isinstance(valor, (datetime.date, datetime.datetime)):
        return {'__date__': valor.isoformat()}
    return valor


def _deserializar(valor):
    if isinstance(valor, dict):
        if '__decimal__' in valor:
            return Decimal(valor['__decimal__'])
        if '__date__' in valor:
            return datetime.date.fromisoformat(valor['__date__'])
    return valor


def _dict_a_sesion(d: dict) -> dict:
    return {k: _serializar(v) for k, v in d.items()}


def _dict_de_sesion(d: dict) -> dict:
    return {k: _deserializar(v) for k, v in d.items()}


# ---------------------------------------------------------------------------
# Login / Logout
#
# El código de acceso y la contraseña se crean directamente en /admin/
# (ver portal.admin.ProveedorPerfilAdmin) -- ya no hay invitación por
# correo ni registro propio del proveedor. El modelo ProveedorInvitacion
# y services.activar_invitacion se dejaron sin usar por si se quiere
# reactivar ese flujo más adelante (p. ej. para producción).
# ---------------------------------------------------------------------------

def login_proveedor(request):
    if request.user.is_authenticated:
        return redirect('portal:buscar_orden')

    if request.method == 'POST':
        form = LoginProveedorForm(request, data=request.POST)
        if form.is_valid():
            auth_login(request, form.get_user())
            return redirect('portal:buscar_orden')
    else:
        form = LoginProveedorForm(request)

    return render(request, 'portal/login.html', {'form': form})


def logout_proveedor(request):
    auth_logout(request)
    return redirect('portal:login')


def _sin_perfil(request):
    """Un usuario autenticado sin ProveedorPerfil (p. ej. un superusuario
    de /admin/ que entra a /portal/ con la misma sesión) NO debe quedar
    "atrapado": si solo lo mandáramos a portal:login, login_proveedor lo
    regresaría de inmediato a buscar_orden (porque ya está autenticado),
    formando un loop infinito de redirecciones. Por eso aquí se cierra
    la sesión antes de mandarlo al login."""
    auth_logout(request)
    messages.error(request, 'Ese usuario no tiene un perfil de proveedor asociado. Contacta a soporte.')
    return redirect('portal:login')


# ---------------------------------------------------------------------------
# Buscar orden (restringida al proveedor autenticado)
# ---------------------------------------------------------------------------

@login_required(login_url=LOGIN_URL)
def buscar_orden(request):
    perfil = getattr(request.user, 'proveedorperfil', None)
    if perfil is None:
        return _sin_perfil(request)

    # Prefill vía QR: ?orden=2020115636 (puesto por el link generado en VFP)
    orden_inicial = request.GET.get('orden', '')
    form = BuscarOrdenPortalForm(initial={'orden': orden_inicial} if orden_inicial else None)

    if request.method == 'POST':
        form = BuscarOrdenPortalForm(request.POST)
        if form.is_valid():
            orden_ingresada = form.cleaned_data['orden'].strip()

            resultados = services.buscar_orden_para_proveedor(
                orden_ingresada,
                settings.FACTURAS_CODPAI_DEFAULT,
                settings.FACTURAS_CODAGENCIA_DEFAULT,
                perfil.codfacturar,
            )

            if not resultados:
                messages.error(
                    request,
                    f'No se encontró la orden "{orden_ingresada}" asociada a tu código de proveedor. '
                    f'Verifica el número, o contacta a quien te la envió.'
                )
                return render(request, 'portal/buscar_orden.html', {'form': form, 'perfil': perfil})

            # Si hay más de una coincidencia (mismo proveedor, distinto
            # presupuesto), tomamos la primera -- caso raro para un
            # proveedor externo; si hace falta elegir, se puede agregar
            # una pantalla de selección igual a la interna.
            request.session[SESSION_ORDEN_SEL] = _dict_a_sesion(resultados[0])
            return redirect('portal:ingresar_factura')

    return render(request, 'portal/buscar_orden.html', {'form': form, 'perfil': perfil})


# ---------------------------------------------------------------------------
# Mis órdenes (método alterno a "buscar orden por número"): lista TODAS
# las órdenes del proveedor, con filtro de año y de número de orden, para
# que pueda ver de una vez cuáles le falta facturar y elegir una desde
# ahí -- sin tener que saber/teclear el número exacto.
# ---------------------------------------------------------------------------

@login_required(login_url=LOGIN_URL)
def mis_ordenes(request):
    perfil = getattr(request.user, 'proveedorperfil', None)
    if perfil is None:
        return _sin_perfil(request)

    anio_str = request.GET.get('anio', '').strip()
    orden_filtro = request.GET.get('orden', '').strip()
    # Checkbox "ver todas" (incluye ya facturadas); por defecto, solo se
    # muestran las que todavía tienen saldo pendiente de facturar.
    ver_todas = request.GET.get('todas') == '1'

    anio = None
    if anio_str:
        try:
            anio = int(anio_str)
        except ValueError:
            messages.error(request, f'"{anio_str}" no es un año válido.')
            anio_str = ''

    ordenes = facturas_services.listar_ordenes_por_codfacturar(
        perfil.codfacturar,
        settings.FACTURAS_CODPAI_DEFAULT,
        settings.FACTURAS_CODAGENCIA_DEFAULT,
        anio=anio,
        orden_filtro=orden_filtro or None,
        solo_con_saldo=not ver_todas,
    )

    return render(request, 'portal/mis_ordenes.html', {
        'perfil': perfil,
        'ordenes': ordenes,
        'anio': anio_str,
        'orden_filtro': orden_filtro,
        'ver_todas': ver_todas,
        'anio_actual': datetime.date.today().year,
    })


@login_required(login_url=LOGIN_URL)
def seleccionar_orden(request, numero_orden):
    """Punto de entrada desde 'Mis órdenes': el proveedor elige una
    orden de la lista (en vez de teclearla) y aquí se vuelve a buscar
    con la misma función segura de siempre (buscar_orden_para_proveedor)
    para traer el detalle completo antes de pasar a ingresar_factura."""
    perfil = getattr(request.user, 'proveedorperfil', None)
    if perfil is None:
        return _sin_perfil(request)

    resultados = services.buscar_orden_para_proveedor(
        numero_orden,
        settings.FACTURAS_CODPAI_DEFAULT,
        settings.FACTURAS_CODAGENCIA_DEFAULT,
        perfil.codfacturar,
    )
    if not resultados:
        messages.error(request, f'No se encontró la orden "{numero_orden}" asociada a tu código de proveedor.')
        return redirect('portal:mis_ordenes')

    request.session[SESSION_ORDEN_SEL] = _dict_a_sesion(resultados[0])
    return redirect('portal:ingresar_factura')


# ---------------------------------------------------------------------------
# Ingresar factura (monto total únicamente; impuestos automáticos)
# ---------------------------------------------------------------------------

@login_required(login_url=LOGIN_URL)
def ingresar_factura(request):
    perfil = getattr(request.user, 'proveedorperfil', None)
    if perfil is None:
        return _sin_perfil(request)

    orden_sesion = request.session.get(SESSION_ORDEN_SEL)
    if not orden_sesion:
        messages.warning(request, 'Primero busca tu orden de compra.')
        return redirect('portal:buscar_orden')

    orden_dict = _dict_de_sesion(orden_sesion)

    # Seguridad: revalidar que la orden en sesión sigue siendo del
    # proveedor autenticado (por si acaso).
    if (orden_dict.get('codfacturar') or '').strip().upper() != perfil.codfacturar.strip().upper():
        messages.error(request, 'Esta orden no corresponde a tu proveedor.')
        del request.session[SESSION_ORDEN_SEL]
        return redirect('portal:buscar_orden')

    saldo = facturas_services.calcular_saldo_orden(orden_dict)

    if request.method == 'POST':
        form = FacturaProveedorPortalForm(request.POST, request.FILES)
        if form.is_valid():
            monto = form.cleaned_data['monto']
            numfactura = form.cleaned_data['numfactura']

            if monto > saldo.saldo_disponible:
                messages.error(
                    request,
                    f'El monto ingresado (Q{monto:,.2f}) excede el saldo disponible de '
                    f'la orden (Q{saldo.saldo_disponible:,.2f}).'
                )
                return render(request, 'portal/ingresar_factura.html', {
                    'form': form, 'orden': orden_dict, 'saldo': saldo, 'perfil': perfil,
                })

            if facturas_services.numfactura_ya_registrada(
                orden_dict['codpai'], orden_dict['codagencia'],
                orden_dict.get('codfacturar'), numfactura,
            ):
                messages.error(
                    request,
                    f'Ya existe una factura con el número "{numfactura}" registrada para tu proveedor.'
                )
                return render(request, 'portal/ingresar_factura.html', {
                    'form': form, 'orden': orden_dict, 'saldo': saldo, 'perfil': perfil,
                })

            # --- Impuestos automáticos, proporcionales a la orden ---
            iva, tp = facturas_services.calcular_impuestos_proporcionales(orden_dict, monto)

            usuario = f'portal:{request.user.username}'

            nueva_factura = facturas_services.registrar_factura(
                orden_dict=orden_dict,
                numfactura=numfactura,
                fecfactura=form.cleaned_data['fecfactura'],
                monto=monto,
                valiva=iva,
                valtp=tp,
                tipofac='FC',
                obsfactura=form.cleaned_data.get('observaciones', ''),
                archivo=form.cleaned_data.get('archivo'),
                usuario=usuario,
            )

            del request.session[SESSION_ORDEN_SEL]
            messages.success(request, f'Tu factura {nueva_factura.numfactura} fue registrada correctamente.')
            return redirect(reverse('portal:confirmacion') + f'?keyorden={nueva_factura.keyorden}')
    else:
        form = FacturaProveedorPortalForm()

    return render(request, 'portal/ingresar_factura.html', {
        'form': form, 'orden': orden_dict, 'saldo': saldo, 'perfil': perfil,
    })


@login_required(login_url=LOGIN_URL)
def confirmacion(request):
    keyorden = request.GET.get('keyorden')
    factura = None
    if keyorden:
        factura = OrdenesRd.objects.using('default').filter(keyorden=keyorden).first()
    return render(request, 'portal/confirmacion.html', {'factura': factura})


# ---------------------------------------------------------------------------
# Mis facturas (historial del proveedor autenticado)
# ---------------------------------------------------------------------------

@login_required(login_url=LOGIN_URL)
def mis_facturas(request):
    perfil = getattr(request.user, 'proveedorperfil', None)
    if perfil is None:
        return _sin_perfil(request)

    facturas = list(OrdenesRd.objects.using('default').filter(
        codpai=settings.FACTURAS_CODPAI_DEFAULT,
        codagencia=settings.FACTURAS_CODAGENCIA_DEFAULT,
        codfacturar=perfil.codfacturar,
    ).order_by('-fecrecep', '-keyorden')[:200])

    keyordenes = [f.keyorden for f in facturas]

    # order_by('fecha_carga') a propósito: si una factura tiene más de
    # un adjunto (se reemplazó el archivo), el dict se queda con el más
    # reciente -- mismo criterio que armar_indice_liquidacion().
    adjuntos = {
        fa.keyorden: fa
        for fa in FacturaAdjunto.objects.filter(
            keyorden__in=[str(k) for k in keyordenes]
        ).order_by('fecha_carga')
    }
    liquidadas = set(
        LiquidacionDetalle.objects.filter(
            keyorden__in=keyordenes, liquidacion__anulada=False,
        ).values_list('keyorden', flat=True)
    )

    for f in facturas:
        f.adjunto = adjuntos.get(str(f.keyorden))
        f.liquidada = f.keyorden in liquidadas

    return render(request, 'portal/mis_facturas.html', {
        'facturas': facturas,
        'perfil': perfil,
    })


@login_required(login_url=LOGIN_URL)
def anular_factura(request, keyorden):
    """El proveedor anula una factura que él mismo subió (por ejemplo,
    si se equivocó al ingresarla). Igual que en el sistema interno: no
    se borra, se marca anulada -- y con eso el saldo de la orden de
    compra queda disponible de nuevo de inmediato."""
    perfil = getattr(request.user, 'proveedorperfil', None)
    if perfil is None:
        return _sin_perfil(request)

    factura = get_object_or_404(OrdenesRd.objects.using('default'), keyorden=keyorden)

    if (factura.codfacturar or '').strip().upper() != perfil.codfacturar.strip().upper():
        messages.error(request, 'Esa factura no te pertenece.')
        return redirect('portal:mis_facturas')

    if factura.facanula == 'Si':
        messages.info(request, f'La factura {factura.numfactura} ya estaba anulada.')
        return redirect('portal:mis_facturas')

    liquidacion_activa = facturas_services.liquidacion_activa_de(keyorden)

    if request.method == 'POST' and liquidacion_activa:
        messages.error(
            request,
            'Esta factura ya fue incluida en una liquidación y no se puede anular en este momento. '
            'Contacta a quien administra el sistema.'
        )
        return redirect('portal:mis_facturas')

    if request.method == 'POST':
        form = MotivoAnulacionForm(request.POST)
        if form.is_valid():
            usuario = f'portal:{request.user.username}'
            try:
                services.anular_factura_del_proveedor(
                    keyorden=keyorden,
                    codfacturar_proveedor=perfil.codfacturar,
                    motivo=form.cleaned_data['motivo'],
                    usuario=usuario,
                )
            except (services.FacturaNoPerteneceAlProveedorError, services.FacturaYaLiquidadaError) as exc:
                messages.error(request, str(exc))
            except facturas_services.FacturaYaAnuladaError as exc:
                messages.warning(request, str(exc))
            else:
                messages.success(
                    request,
                    f'Tu factura {factura.numfactura} fue anulada. La orden {factura.orden} '
                    f'vuelve a tener saldo disponible.'
                )
            return redirect('portal:mis_facturas')
    else:
        form = MotivoAnulacionForm()

    return render(request, 'portal/anular_factura.html', {
        'form': form,
        'factura': factura,
        'liquidacion_activa': liquidacion_activa,
    })


@login_required(login_url=LOGIN_URL)
def subir_adjunto(request, keyorden):
    """Para una factura del proveedor que quedó sin documento (normal
    en las que se registraron antes de que el archivo fuera
    obligatorio): sube el archivo directo desde "Mis facturas", sin
    tocar ningún otro dato de la factura. Solo aplica si todavía no
    tiene adjunto -- si ya tiene uno, esto no lo reemplaza (para
    reemplazar un adjunto existente, ese caso lo maneja el equipo
    interno desde "Revisar factura")."""
    perfil = getattr(request.user, 'proveedorperfil', None)
    if perfil is None:
        return _sin_perfil(request)

    if request.method != 'POST':
        return redirect('portal:mis_facturas')

    factura = get_object_or_404(OrdenesRd.objects.using('default'), keyorden=keyorden)

    if (factura.codfacturar or '').strip().upper() != perfil.codfacturar.strip().upper():
        messages.error(request, 'Esa factura no te pertenece.')
        return redirect('portal:mis_facturas')

    if FacturaAdjunto.objects.filter(keyorden=str(keyorden)).exists():
        messages.info(request, f'La factura {factura.numfactura} ya tiene un documento adjunto.')
        return redirect('portal:mis_facturas')

    form = SubirAdjuntoPortalForm(request.POST, request.FILES)
    if form.is_valid():
        usuario = f'portal:{request.user.username}'
        facturas_services.reemplazar_adjunto_factura(
            keyorden=keyorden,
            archivo=form.cleaned_data['archivo'],
            usuario=usuario,
        )
        messages.success(request, f'Documento subido para la factura {factura.numfactura}.')
    else:
        errores = ' '.join(form.errors.get('archivo', ['No se pudo subir el archivo.']))
        messages.error(request, errores)

    return redirect('portal:mis_facturas')
