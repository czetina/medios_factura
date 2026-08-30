import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from facturas import services as facturas_services
from facturas.models import OrdenesRd

from . import services
from .forms import BuscarOrdenPortalForm, FacturaProveedorPortalForm, LoginProveedorForm

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

    facturas = OrdenesRd.objects.using('default').filter(
        codpai=settings.FACTURAS_CODPAI_DEFAULT,
        codagencia=settings.FACTURAS_CODAGENCIA_DEFAULT,
        codfacturar=perfil.codfacturar,
    ).exclude(facanula='Si').order_by('-fecrecep', '-keyorden')[:200]

    return render(request, 'portal/mis_facturas.html', {
        'facturas': facturas,
        'perfil': perfil,
    })
