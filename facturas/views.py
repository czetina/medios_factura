import datetime
import hmac
import os
import re
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .forms import (
    BuscarOrdenForm, FacturaProveedorForm, FiltroFacturasRecibidasForm,
    MotivoAnulacionForm, FiltroLiquidacionForm, ReemplazarAdjuntoForm,
)
from .models import OrdenesRd, FacturaAdjunto, Liquidacion, OrdenCompraAdjunto, PresupuestoAdjunto
from . import services

SESSION_RESULTADOS = 'facturas_resultados_busqueda'
SESSION_ORDEN_SEL = 'facturas_orden_seleccionada'


# ---------------------------------------------------------------------------
# Helpers de (de)serialización para poder guardar los resultados en sesión
# (Decimal / date no son JSON-serializables por defecto).
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
# Paso 1: buscar la orden de compra
# ---------------------------------------------------------------------------

def buscar_orden(request):
    if request.method == 'POST':
        form = BuscarOrdenForm(request.POST)
        if form.is_valid():
            orden_ingresada = form.cleaned_data['orden'].strip()
            # País y agencia YA NO se piden en pantalla: se toman fijos
            # de config.py.
            codpai = settings.FACTURAS_CODPAI_DEFAULT
            codagencia = settings.FACTURAS_CODAGENCIA_DEFAULT

            resultados = services.buscar_ordenes(orden_ingresada, codpai, codagencia)

            if not resultados:
                messages.error(
                    request,
                    f'No se encontró ninguna orden de compra "{orden_ingresada}" '
                    f'vigente para facturar (país={codpai}, agencia={codagencia}).'
                )
                return render(request, 'facturas/buscar_orden.html', {
                    'form': form,
                    'codpai_actual': codpai,
                    'codagencia_actual': codagencia,
                })

            if len(resultados) == 1:
                request.session[SESSION_ORDEN_SEL] = _dict_a_sesion(resultados[0])
                return redirect('facturas:ingresar_factura')

            # Varias coincidencias -> el usuario debe elegir presupuesto/cliente
            request.session[SESSION_RESULTADOS] = [_dict_a_sesion(r) for r in resultados]
            return redirect('facturas:seleccionar_orden')
    else:
        form = BuscarOrdenForm()

    return render(request, 'facturas/buscar_orden.html', {
        'form': form,
        'codpai_actual': settings.FACTURAS_CODPAI_DEFAULT,
        'codagencia_actual': settings.FACTURAS_CODAGENCIA_DEFAULT,
    })


# ---------------------------------------------------------------------------
# Paso 2 (opcional): elegir presupuesto/cliente cuando hay varias coincidencias
# ---------------------------------------------------------------------------

def seleccionar_orden(request):
    resultados_sesion = request.session.get(SESSION_RESULTADOS)
    if not resultados_sesion:
        messages.warning(request, 'Primero busca una orden de compra.')
        return redirect('facturas:buscar_orden')

    resultados = [_dict_de_sesion(r) for r in resultados_sesion]

    if request.method == 'POST':
        try:
            idx = int(request.POST.get('idx'))
            seleccionada = resultados[idx]
        except (TypeError, ValueError, IndexError):
            messages.error(request, 'Selección inválida.')
            return redirect('facturas:seleccionar_orden')

        request.session[SESSION_ORDEN_SEL] = resultados_sesion[idx]
        del request.session[SESSION_RESULTADOS]
        return redirect('facturas:ingresar_factura')

    return render(request, 'facturas/seleccionar_orden.html', {
        'resultados': list(enumerate(resultados)),
    })


# ---------------------------------------------------------------------------
# Paso 3: capturar datos de la factura + validar saldo + guardar
# ---------------------------------------------------------------------------

def ingresar_factura(request):
    orden_sesion = request.session.get(SESSION_ORDEN_SEL)
    if not orden_sesion:
        messages.warning(request, 'Primero busca y selecciona una orden de compra.')
        return redirect('facturas:buscar_orden')

    orden_dict = _dict_de_sesion(orden_sesion)
    saldo = services.calcular_saldo_orden(orden_dict)

    if request.method == 'POST':
        form = FacturaProveedorForm(request.POST, request.FILES)
        if form.is_valid():
            monto = form.cleaned_data['monto']
            numfactura = form.cleaned_data['numfactura']

            # --- Regla de negocio: no exceder el total de la orden ---
            if monto > saldo.saldo_disponible:
                messages.error(
                    request,
                    f'El monto ingresado (Q{monto:,.2f}) excede el saldo disponible de '
                    f'la orden (Q{saldo.saldo_disponible:,.2f}). Total de la orden: '
                    f'Q{saldo.total_orden:,.2f}, ya facturado: Q{saldo.total_facturado:,.2f}.'
                )
                return render(request, 'facturas/ingresar_factura.html', {
                    'form': form, 'orden': orden_dict, 'saldo': saldo,
                })

            # --- Regla de negocio: no repetir no. de factura por proveedor ---
            if services.numfactura_ya_registrada(
                orden_dict['codpai'], orden_dict['codagencia'],
                orden_dict.get('codfacturar'), numfactura,
            ):
                proveedor = orden_dict.get('rsfacturar') or orden_dict.get('codfacturar') or ''
                messages.error(
                    request,
                    f'Ya existe una factura activa con el número "{numfactura}" '
                    f'para el proveedor {proveedor}. Verifica el número o revisa '
                    f'si ya fue registrada anteriormente.'
                )
                return render(request, 'facturas/ingresar_factura.html', {
                    'form': form, 'orden': orden_dict, 'saldo': saldo,
                })

            usuario = request.user.username if request.user.is_authenticated else 'anonimo'

            nueva_factura = services.registrar_factura(
                orden_dict=orden_dict,
                numfactura=numfactura,
                fecfactura=form.cleaned_data['fecfactura'],
                monto=monto,
                valiva=form.cleaned_data.get('valiva'),
                valtp=form.cleaned_data.get('valtp'),
                tipofac=form.cleaned_data['tipofac'],
                obsfactura=form.cleaned_data.get('obsfactura', ''),
                archivo=form.cleaned_data.get('archivo'),
                usuario=usuario,
            )

            del request.session[SESSION_ORDEN_SEL]
            messages.success(
                request,
                f'Factura {nueva_factura.numfactura} registrada correctamente '
                f'contra la orden {nueva_factura.orden} (keyorden={nueva_factura.keyorden}).'
            )
            return redirect(reverse('facturas:confirmacion') + f'?keyorden={nueva_factura.keyorden}')
    else:
        form = FacturaProveedorForm()

    return render(request, 'facturas/ingresar_factura.html', {
        'form': form, 'orden': orden_dict, 'saldo': saldo,
    })


# ---------------------------------------------------------------------------
# Paso 4: confirmación
# ---------------------------------------------------------------------------

def confirmacion(request):
    keyorden = request.GET.get('keyorden')
    factura = None
    if keyorden:
        factura = OrdenesRd.objects.using('default').filter(keyorden=keyorden).first()
    return render(request, 'facturas/confirmacion.html', {'factura': factura})


# ---------------------------------------------------------------------------
# Paso 5: listado de facturas recibidas por mes/año
# ---------------------------------------------------------------------------

def listado_facturas_recibidas(request):
    form = FiltroFacturasRecibidasForm(request.GET or None)

    filas = []
    total_general = Decimal('0.00')
    filtrado = False

    if form.is_valid():
        anio = int(form.cleaned_data['anio'])
        mes = form.cleaned_data.get('mes')
        mes = int(mes) if mes else None

        filas, total_general = services.listar_facturas_recibidas(
            anio=anio,
            mes=mes,
            codpai=settings.FACTURAS_CODPAI_DEFAULT,
            codagencia=settings.FACTURAS_CODAGENCIA_DEFAULT,
            codcli=form.cleaned_data.get('codcli'),
            codpresup=form.cleaned_data.get('codpresup'),
            estado_codificacion=form.cleaned_data.get('estado_codificacion'),
        )
        filtrado = True

    return render(request, 'facturas/listado_facturas.html', {
        'form': form,
        'filas': filas,
        'total_general': total_general,
        'filtrado': filtrado,
    })


# ---------------------------------------------------------------------------
# Paso 6: anular una factura ya registrada
# ---------------------------------------------------------------------------

def anular_factura(request, keyorden):
    factura = get_object_or_404(OrdenesRd.objects.using('default'), keyorden=keyorden)

    if factura.facanula == 'Si':
        messages.info(request, f'La factura {factura.numfactura} ya estaba anulada.')
        return redirect('facturas:listado_facturas_recibidas')

    # Igual que en "Revisar factura": si ya está en una liquidación
    # activa, anularla dejaría el total de esa liquidación
    # desactualizado (la factura desaparecería del saldo pero la
    # liquidación seguiría "cobrando" su monto) -- hay que anular esa
    # liquidación primero (lo que la libera).
    liquidacion_activa = services.liquidacion_activa_de(keyorden)

    if request.method == 'POST' and liquidacion_activa:
        messages.error(
            request,
            f'La factura {factura.numfactura} ya forma parte de la Liquidación #{liquidacion_activa.numero} '
            f'y no se puede anular. Anula esa liquidación primero si necesitas hacerlo.'
        )
        return redirect('facturas:anular_factura', keyorden=keyorden)

    if request.method == 'POST':
        form = MotivoAnulacionForm(request.POST)
        if form.is_valid():
            usuario = request.user.username if request.user.is_authenticated else 'anonimo'
            try:
                services.anular_factura(
                    keyorden=keyorden,
                    motivo=form.cleaned_data['motivo'],
                    usuario=usuario,
                )
            except services.FacturaYaAnuladaError as exc:
                messages.warning(request, str(exc))
            else:
                messages.success(
                    request,
                    f'Factura {factura.numfactura} (orden {factura.orden}) anulada correctamente.'
                )
            return redirect('facturas:listado_facturas_recibidas')
    else:
        form = MotivoAnulacionForm()

    return render(request, 'facturas/anular_factura.html', {
        'form': form,
        'factura': factura,
        'liquidacion_activa': liquidacion_activa,
    })


# ---------------------------------------------------------------------------
# Paso 7: revisar y aceptar la factura (antes de que "se envíe a CXP" en
# el sistema contable real -- aquí solo se marca el estado, sin generar
# ningún movimiento contable de verdad).
# ---------------------------------------------------------------------------

def revisar_factura(request, keyorden):
    """Pantalla de revisión: muestra todos los datos capturados de la
    factura (y su adjunto, si tiene) para que quien revise confirme que
    están correctos antes de "Aceptar". Aceptar/Quitar aceptación es
    idempotente y no dispara ningún proceso contable -- es solo un
    estado de control dentro de este sistema.

    Si la factura ya forma parte de una liquidación ACTIVA (no
    anulada), queda "congelada" aquí: no se puede aceptar/quitar
    aceptación ni reemplazar su adjunto, porque eso dejaría el PDF ya
    armado de esa liquidación desincronizado de lo que el sistema
    muestra. Para volver a tocarla hay que anular esa liquidación
    primero (lo que la libera)."""
    factura = get_object_or_404(OrdenesRd.objects.using('default'), keyorden=keyorden)
    aceptada = services.esta_codificada(keyorden)
    adjunto = FacturaAdjunto.objects.filter(keyorden=keyorden).order_by('-fecha_carga').first()
    form_adjunto = ReemplazarAdjuntoForm()
    liquidacion_activa = services.liquidacion_activa_de(keyorden)

    if request.method == 'POST' and liquidacion_activa:
        messages.error(
            request,
            f'Esta factura ya forma parte de la Liquidación #{liquidacion_activa.numero} '
            f'y no se puede modificar. Anula esa liquidación primero si necesitas cambiarla.'
        )
        return redirect('facturas:revisar_factura', keyorden=keyorden)

    if request.method == 'POST':
        accion = request.POST.get('accion')
        usuario = request.user.username if request.user.is_authenticated else 'anonimo'

        if accion == 'aceptar' and not aceptada:
            services.marcar_codificada(keyorden, factura.orden, factura.numfactura, usuario)
            messages.success(request, f'Factura {factura.numfactura} aceptada.')
            return redirect('facturas:listado_facturas_recibidas')
        elif accion == 'quitar' and aceptada:
            services.quitar_codificacion(keyorden)
            messages.info(request, f'Se quitó la aceptación de la factura {factura.numfactura}.')
            return redirect('facturas:listado_facturas_recibidas')
        elif accion == 'reemplazar_adjunto':
            # El usuario subió el archivo equivocado: se agrega uno
            # nuevo (no se toca monto/fecha/número -- eso se corrige
            # anulando la factura y registrando una nueva).
            form_adjunto = ReemplazarAdjuntoForm(request.POST, request.FILES)
            if form_adjunto.is_valid():
                services.reemplazar_adjunto_factura(
                    keyorden=keyorden,
                    archivo=form_adjunto.cleaned_data['archivo'],
                    usuario=usuario,
                )
                messages.success(request, 'Adjunto reemplazado correctamente.')
                return redirect('facturas:revisar_factura', keyorden=keyorden)
            # si el form no es válido, sigue abajo y lo vuelve a mostrar con errores

    return render(request, 'facturas/revisar_factura.html', {
        'factura': factura,
        'aceptada': aceptada,
        'adjunto': adjunto,
        'form_adjunto': form_adjunto,
        'liquidacion_activa': liquidacion_activa,
    })


# ---------------------------------------------------------------------------
# Paso 8: liquidar clientes -- agrupar facturas ACEPTADAS y guardarlas
# como una Liquidacion propia (sin tocar liq_quedan/liq_liquidaciones
# reales del sistema contable).
# ---------------------------------------------------------------------------

def liquidar_clientes(request):
    form = FiltroLiquidacionForm(request.GET or None)
    grupos = []
    filtrado = False

    if form.is_valid():
        anio = int(form.cleaned_data['anio'])
        mes = form.cleaned_data.get('mes')
        mes = int(mes) if mes else None
        criterio = form.cleaned_data['criterio']

        grupos = services.facturas_aceptadas_por_liquidar(
            anio=anio,
            mes=mes,
            codpai=settings.FACTURAS_CODPAI_DEFAULT,
            codagencia=settings.FACTURAS_CODAGENCIA_DEFAULT,
            criterio=criterio,
            codcli=form.cleaned_data.get('codcli'),
            codpresup=form.cleaned_data.get('codpresup'),
        )
        filtrado = True

    return render(request, 'facturas/liquidar_clientes.html', {
        'form': form,
        'grupos': grupos,
        'filtrado': filtrado,
    })


def guardar_liquidacion(request):
    """Solo POST -- guarda como Liquidacion el grupo indicado, volviendo
    a calcular sus facturas en el servidor (no confía en lo enviado por
    el navegador más que el criterio/valor/filtros usados)."""
    if request.method != 'POST':
        return redirect('facturas:liquidar_clientes')

    criterio = request.POST.get('criterio', '')
    valor = request.POST.get('valor', '')
    anio = request.POST.get('anio', '')
    mes = request.POST.get('mes') or None
    codcli = request.POST.get('codcli') or None
    codpresup = request.POST.get('codpresup') or None
    usuario = request.user.username if request.user.is_authenticated else 'anonimo'

    try:
        liquidacion = services.guardar_liquidacion(
            criterio=criterio,
            valor=valor,
            anio=int(anio),
            mes=int(mes) if mes else None,
            codpai=settings.FACTURAS_CODPAI_DEFAULT,
            codagencia=settings.FACTURAS_CODAGENCIA_DEFAULT,
            usuario=usuario,
            codcli=codcli,
            codpresup=codpresup,
        )
    except (services.NadaQueLiquidarError, ValueError) as exc:
        messages.error(request, str(exc))
    else:
        cantidad = liquidacion.detalles.count()
        messages.success(
            request,
            f'Liquidación #{liquidacion.numero} guardada: {cantidad} factura(s), '
            f'total Q{liquidacion.total:,.2f}.'
        )

    # Volver a la pantalla de liquidar, con los mismos filtros que traía.
    query = f'?anio={anio}&criterio={criterio}'
    if mes:
        query += f'&mes={mes}'
    if codcli:
        query += f'&codcli={codcli}'
    if codpresup:
        query += f'&codpresup={codpresup}'
    return redirect(reverse('facturas:liquidar_clientes') + query)


def listado_liquidaciones(request):
    liquidaciones = Liquidacion.objects.filter(
        codpai=settings.FACTURAS_CODPAI_DEFAULT,
        codagencia=settings.FACTURAS_CODAGENCIA_DEFAULT,
    ).order_by('-fecha_liquidacion')
    return render(request, 'facturas/listado_liquidaciones.html', {
        'liquidaciones': liquidaciones,
    })


def detalle_liquidacion(request, numero):
    liquidacion = get_object_or_404(Liquidacion, numero=numero)
    # Recoge lo que haya en media/OrdenesPdf/ y media/presupuestos/ (las
    # carpetas "parametrizadas" mientras no se conecta Drive/OneDrive)
    # antes de armar el índice, para que un PDF recién dejado ahí
    # aparezca sin tener que subirlo aparte por /admin/.
    services.sincronizar_adjuntos_desde_carpetas()
    indice = services.armar_indice_liquidacion(liquidacion)
    return render(request, 'facturas/detalle_liquidacion.html', {
        'liquidacion': liquidacion,
        'indice': indice,
    })


def anular_liquidacion(request, numero):
    """Anula la liquidación (no la borra) y con eso libera sus facturas
    para que vuelvan a estar disponibles en 'Liquidar clientes'."""
    liquidacion = get_object_or_404(Liquidacion, numero=numero)

    if liquidacion.anulada:
        messages.info(request, f'La liquidación #{numero} ya estaba anulada.')
        return redirect('facturas:detalle_liquidacion', numero=numero)

    if request.method == 'POST':
        form = MotivoAnulacionForm(request.POST)
        if form.is_valid():
            usuario = request.user.username if request.user.is_authenticated else 'anonimo'
            try:
                services.anular_liquidacion(
                    numero=numero,
                    motivo=form.cleaned_data['motivo'],
                    usuario=usuario,
                )
            except services.LiquidacionYaAnuladaError as exc:
                messages.warning(request, str(exc))
            else:
                messages.success(
                    request,
                    f'Liquidación #{numero} anulada. Sus {liquidacion.detalles.count()} factura(s) '
                    f'quedaron disponibles de nuevo para liquidar.'
                )
            return redirect('facturas:detalle_liquidacion', numero=numero)
    else:
        form = MotivoAnulacionForm()

    return render(request, 'facturas/anular_liquidacion.html', {
        'form': form,
        'liquidacion': liquidacion,
    })


def descargar_pdf_liquidacion(request, numero):
    """Genera (al vuelo, no se guarda) el PDF final de la liquidación:
    portada "Detalle" + factura/presupuesto/orden de compra de cada
    fila, en el mismo orden que arman a mano hoy. Si falta algún
    adjunto (presupuesto/orden de compra todavía no subidos), el PDF
    se genera igual mostrando un mensaje con lo que quedó fuera."""
    services.sincronizar_adjuntos_desde_carpetas()
    liquidacion = get_object_or_404(Liquidacion, numero=numero)
    # Las advertencias (adjuntos faltantes) no se muestran aquí con
    # `messages` porque esta vista devuelve el PDF directo, no una
    # página -- ya se ven de antemano en detalle_liquidacion.html
    # (columna de estado por fila), antes de descargar.
    pdf_bytes, _advertencias = services.generar_pdf_liquidacion(liquidacion)

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="Liquidacion_{liquidacion.numero}.pdf"'
    return response


# ---------------------------------------------------------------------------
# API para el sistema legacy (Visual FoxPro): recibe el PDF de una Orden de
# Compra o un Presupuesto apenas se genera -- reemplaza la necesidad de
# dejarlos manualmente en media/OrdenesPdf/ o media/presupuestos/ (ver
# services.sincronizar_adjuntos_desde_carpetas) y evita depender de
# Google Drive/OneDrive: el propio sistema que ya los genera los empuja
# aquí directo. Mismo contrato que el prototipo en PHP ya probado por el
# cliente (upload_pdf.php) para que SUBIRPDF.PRG solo tenga que cambiar
# la URL y la API key.
# ---------------------------------------------------------------------------

TIPOS_SUBIRPDF_VALIDOS = {'ORDENESPDF', 'PRESUPUESTO'}


def _respuesta_subirpdf(ok, mensaje, status=200, **datos):
    return JsonResponse({'ok': ok, 'mensaje': mensaje, **datos}, status=status)


@csrf_exempt
@require_POST
def api_subir_pdf(request):
    """POST multipart/form-data con:
      - header X-API-KEY: debe coincidir con settings.FACTURAS_SUBIRPDF_API_KEY.
      - campo 'tipo': 'ORDENESPDF' o 'PRESUPUESTO'.
      - campo 'nombre': nombre de archivo tal como lo genera FoxPro
        (convención ya usada por sincronizar_adjuntos_desde_carpetas():
        "<numero_de_orden>.pdf" para ORDENESPDF, "<codpresup>-<revisión>.pdf"
        para PRESUPUESTO).
      - campo 'file': el PDF.

    Responde JSON {"ok": bool, "mensaje": str, ...}, igual que el
    prototipo PHP. Exento de CSRF a propósito: es una API llamada por
    un sistema externo sin sesión de Django -- la autenticación real es
    la API key, no la cookie de sesión."""
    api_key = request.headers.get('X-API-KEY', '')
    if not hmac.compare_digest(settings.FACTURAS_SUBIRPDF_API_KEY, api_key):
        return _respuesta_subirpdf(False, 'API KEY incorrecta', status=401)

    archivo = request.FILES.get('file')
    if archivo is None:
        return _respuesta_subirpdf(False, 'No se recibió el PDF', status=400)

    tipo = (request.POST.get('tipo') or '').strip().upper()
    if tipo not in TIPOS_SUBIRPDF_VALIDOS:
        return _respuesta_subirpdf(False, 'Tipo de documento no válido', status=400)

    nombre = (request.POST.get('nombre') or '').strip()
    if not nombre:
        return _respuesta_subirpdf(False, 'Falta el nombre del archivo', status=400)

    nombre = os.path.basename(nombre)
    nombre = re.sub(r'[^A-Za-z0-9_.-]', '_', nombre)
    if not nombre.lower().endswith('.pdf'):
        return _respuesta_subirpdf(False, 'El archivo debe ser PDF', status=400)

    if archivo.size <= 0:
        return _respuesta_subirpdf(False, 'El archivo está vacío', status=400)

    # Verifica el contenido real, no solo la extensión (mismo criterio
    # que finfo() en el script PHP): un PDF real siempre arranca con
    # esta cabecera.
    cabecera = archivo.read(5)
    archivo.seek(0)
    if cabecera != b'%PDF-':
        return _respuesta_subirpdf(False, 'El archivo no es un PDF válido', status=400)

    codigo = os.path.splitext(nombre)[0]

    if tipo == 'ORDENESPDF':
        obj, _creado = OrdenCompraAdjunto.objects.get_or_create(orden=codigo)
    else:
        # Quita el sufijo de revisión ("-0", "-1", ...) SOLO si es de
        # un solo dígito -- mismo criterio que
        # sincronizar_adjuntos_desde_carpetas() (el propio codpresup ya
        # termina en un segmento numérico de varios dígitos).
        base, _, sufijo = codigo.rpartition('-')
        if base and len(sufijo) == 1 and sufijo.isdigit():
            codigo = base
        obj, _creado = PresupuestoAdjunto.objects.get_or_create(codpresup=codigo)

    if not _creado and obj.archivo:
        try:
            obj.archivo.delete(save=False)  # evita dejar huérfano el archivo anterior al reemplazarlo
        except OSError:
            # En Windows, borrar un archivo justo después de crearlo a
            # veces falla porque el SO todavía lo tiene bloqueado un
            # instante (antivirus, etc.). No debe impedir la subida del
            # archivo nuevo -- en el peor caso, queda un archivo viejo
            # huérfano en el storage, que no afecta el funcionamiento.
            pass

    try:
        obj.archivo.save(nombre, archivo, save=False)
        obj.usuario = 'foxpro:subirpdf'
        # content_type='' a propósito (aunque sea un reemplazo y ya
        # tuviera uno de antes): el save() del modelo solo recalcula
        # content_type/tamano_bytes cuando content_type viene vacío, así
        # que forzarlo así es lo que hace que tamano_bytes se actualice
        # también en un reemplazo, no solo la primera vez.
        obj.content_type = ''
        obj.save()
    except Exception as exc:
        return _respuesta_subirpdf(False, f'No se pudo guardar el PDF: {exc}', status=500)

    return _respuesta_subirpdf(True, 'PDF recibido correctamente', tipo=tipo, archivo=nombre)
