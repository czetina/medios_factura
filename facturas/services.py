"""
Lógica de negocio del sistema de facturación de proveedores.

Contiene:
  - buscar_ordenes(): ejecuta (SQL crudo) la consulta de búsqueda de orden
    de compra que se proporcionó, parametrizada por número de orden.
  - calcular_saldo_orden(): suma lo ya facturado en `ordenesrd` para esa
    orden/presupuesto y calcula cuánto saldo queda disponible.
  - generar_keyorden(): genera el valor de la PK `ordenesrd.keyorden`
    para el nuevo registro a insertar.
  - registrar_factura(): hace el INSERT final en `ordenesrd` + guarda el
    adjunto (PDF/imagen).

SUPUESTO IMPORTANTE (ver README): la consulta original traía filtros
fijos de mes/año (`orden.mesfac >= '2026-05' ...`) porque era un
reporte de un mes específico. Para una pantalla de búsqueda genérica por
número de orden, esos filtros de mes/año se QUITARON (se busca la orden
sin importar su mes de facturación); se conservan las demás reglas de
negocio: anula='No', ordimpresa='Si', concepto IN (1,11,12).
Si en realidad SÍ debe restringirse por mes/año vigente, dímelo y lo
agrego de vuelta como filtro opcional.
"""
from __future__ import annotations

import datetime
import io
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone
from pypdf import PdfReader, PdfWriter
from PIL import Image

from .models import (
    Ordenes, OrdenesRd, FacturaAdjunto, FacturaCodificacion,
    Liquidacion, LiquidacionDetalle, PresupuestoAdjunto, OrdenCompraAdjunto,
)

# CONFIRMADO por DESCRIBE ordenesrd real: `codusr` y `usranula` son
# varchar(16) -- a diferencia de lo que dice el modelo Django
# (max_length=50, una longitud "razonable" sin confirmar, ver
# facturas/models.py). El identificador de usuario ("anonimo",
# "portal:<código de proveedor>", etc.) se trunca a este largo antes de
# guardarlo en esas dos columnas para no tronar con "Data too long for
# column" -- `creusr` sí tiene espacio de sobra (varchar(92)) y ahí se
# guarda completo, sin truncar.
LARGO_MAX_CODUSR = 16


def _a_codusr(usuario: str) -> str:
    return (usuario or '')[:LARGO_MAX_CODUSR]


# -----------------------------------------------------------------------
# 1. Búsqueda de la orden de compra
# -----------------------------------------------------------------------
# El nombre del esquema "pivot_comsys" es configurable (ver config.py /
# settings.FACTURAS_ESQUEMA_PIVOT_COMSYS), por eso el SQL se arma con
# .format() ANTES de mandarlo a cursor.execute(). El esquema NUNCA viene
# de un input del usuario (siempre de config.py), así que no hay riesgo
# de SQL injection por este lado; los valores capturados por el usuario
# (orden, codpai, codagencia) siguen yendo como parámetros %(...)s.

SQL_BUSCAR_ORDEN_TEMPLATE = """
SELECT DISTINCT
  tipmed.destipmed,
  IFNULL(mediofac.`rsmedio`, "") AS rsfacturar,
  IFNULL(climae.nomcli, "") AS nomcli,
  orden.orden,
  orden.codpresup,
  orden.codpai,
  orden.codagencia,
  orden.codtipmed,
  orden.codtsubmed,
  orden.aniopresup,
  orden.mespresup,
  orden.no_rev,
  orden.codordno,
  orden.codcli,
  orden.codmar,
  orden.codprd,
  orden.codcam,
  orden.obsagencia,
  orden.obsorden,
  orden.obsmaterial,
  orden.fecorden,
  orden.anula,
  orden.codfacturar,
  orden.mesfac,
  orden.codmon,
  orden.ctobruto,
  orden.valdescuento,
  orden.ctoneto,
  orden.valtotal,
  orden.valiva,
  orden.valtp,
  orden.totalorden,
  orden.tiporden,
  orden.concepto,
  orden.fecpublica,
  orden.ordimpresa,
  tipmed.nctipmed,
  tsubmed.nctsubmed,
  IFNULL(marmae.nommar, "") AS nommar,
  IFNULL(prdmae.nomprd, "") AS nomprd,
  IFNULL(climae.nccli, "") AS nccli,
  monmae.desmon,
  IFNULL(mediofac.ncmedio, "") AS ncfacturar,
  clicamae.nomcam,
  CASE
    WHEN orden.concepto = "1" THEN "Compra"
    WHEN orden.concepto = "11" THEN "Precompra"
    WHEN orden.concepto = "12" THEN "Prepago"
    WHEN orden.concepto = "2" THEN "Bonificación"
    WHEN orden.concepto = "3" THEN "Reposición"
    WHEN orden.concepto = "4" THEN "Reinvertible"
    WHEN orden.concepto = "5" THEN "Prepago-Consumo"
  END AS DesConcepto
FROM ordenes orden
  LEFT JOIN tipmed ON orden.codtipmed = tipmed.codtipmed
  LEFT JOIN tsubmed ON orden.codtipmed = tsubmed.codtipmed
                    AND orden.codtsubmed = tsubmed.codtsubmed
  LEFT JOIN {esquema_comsys}.climae AS climae
    ON orden.codpai = climae.codpai AND orden.codcli = climae.codcli
  LEFT JOIN {esquema_comsys}.marmae AS marmae
    ON orden.codpai = marmae.codpai AND orden.codcli = marmae.codcli
   AND orden.codmar = marmae.codmar
  LEFT JOIN {esquema_comsys}.prdmae AS prdmae
    ON orden.codpai = prdmae.codpai AND orden.codcli = prdmae.codcli
   AND orden.codmar = prdmae.codmar AND orden.codprd = prdmae.codprd
  LEFT JOIN {esquema_comsys}.clicamae AS clicamae
    ON orden.codpai = clicamae.codpai AND orden.codcli = clicamae.codcli
   AND orden.codcam = clicamae.codcam
  LEFT JOIN {esquema_comsys}.monmae AS monmae
    ON orden.codpai = monmae.codpai AND orden.codmon = monmae.codmon
  LEFT JOIN medios AS mediofac
    ON orden.codpai = mediofac.codpai AND orden.codtipmed = mediofac.codtipmed
   AND orden.codtsubmed = mediofac.codtsubmed AND orden.codfacturar = mediofac.codmedio
WHERE orden.codpai = %(codpai)s
  AND orden.codagencia = %(codagencia)s
  AND orden.anula = "No"
  AND orden.ordimpresa = 'Si'
  AND (orden.concepto = '1' OR orden.concepto = '11' OR orden.concepto = '12')
  AND orden.orden = %(orden)s
ORDER BY orden.codpresup, orden.codfacturar, orden.orden
"""


def _dictfetchall(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _a_decimal(valor) -> Decimal:
    """Convierte de forma segura a Decimal cualquier valor numérico que
    venga de la BD. Varias columnas monetarias de `ordenesrd`/`ordenes`
    son `double` en MySQL (no `decimal`), así que MySQLdb a veces las
    entrega como `float` de Python en vez de `Decimal`; sumar
    Decimal + float directamente truena (TypeError). Pasar todo por
    aquí antes de sumar evita ese problema sin importar de dónde venga
    el valor (ORM, SQL crudo, o el propio formulario)."""
    if valor is None:
        return Decimal('0.00')
    if isinstance(valor, Decimal):
        return valor
    # str(valor) evita el ruido de precisión binaria de los float
    # (p. ej. 19.99 -> 19.990000000000002 si se convierte directo).
    return Decimal(str(valor))


def buscar_ordenes(orden_ingresada: str, codpai: str, codagencia: str) -> list[dict]:
    """Ejecuta la búsqueda de la orden de compra y devuelve una lista de
    dicts (puede tener 0, 1 o varias filas si la orden se repite por
    presupuesto/cliente distintos)."""
    sql = SQL_BUSCAR_ORDEN_TEMPLATE.format(
        esquema_comsys=settings.FACTURAS_ESQUEMA_PIVOT_COMSYS
    )
    with connection.cursor() as cursor:
        cursor.execute(
            sql,
            {
                'orden': orden_ingresada,
                'codpai': codpai,
                'codagencia': codagencia,
            },
        )
        return _dictfetchall(cursor)


# -----------------------------------------------------------------------
# 2. Saldo ya facturado / disponible de la orden
# -----------------------------------------------------------------------

@dataclass
class SaldoOrden:
    total_orden: Decimal
    total_facturado: Decimal
    saldo_disponible: Decimal


def calcular_saldo_orden(orden_dict: dict) -> SaldoOrden:
    """Suma las facturas YA existentes en `ordenesrd` para la misma orden
    (mismo presupuesto/año/mes/revisión/etc.) y calcula el saldo
    disponible contra `orden.totalorden`.

    Regla de signo: si `tipofac == 'NC'` (nota de crédito) el monto se
    resta; las filas con `facanula = 'Si'` se ignoran (facturas
    anuladas no cuentan).
    """
    qs = OrdenesRd.objects.using('default').filter(
        codpai=orden_dict['codpai'],
        codagencia=orden_dict['codagencia'],
        codtipmed=orden_dict['codtipmed'],
        codtsubmed=orden_dict['codtsubmed'],
        aniopresup=orden_dict['aniopresup'],
        mespresup=orden_dict['mespresup'],
        codpresup=orden_dict['codpresup'],
        no_rev=orden_dict['no_rev'],
        codordno=orden_dict['codordno'],
        orden=orden_dict['orden'],
    ).exclude(facanula='Si')

    total_facturado = Decimal('0.00')
    for fila in qs:
        monto = _a_decimal(fila.totalfac)
        if fila.tipofac == 'NC':
            monto = -monto
        total_facturado += monto

    total_orden = _a_decimal(orden_dict.get('totalorden'))

    saldo = total_orden - total_facturado
    return SaldoOrden(
        total_orden=total_orden,
        total_facturado=total_facturado,
        saldo_disponible=saldo,
    )


# -----------------------------------------------------------------------
# 2a. Listado de TODAS las órdenes de un proveedor (método alterno a
#     "buscar por número" -- para que el proveedor pueda ver de una vez
#     cuáles órdenes le quedan por facturar en vez de tener que conocer
#     el número exacto).
# -----------------------------------------------------------------------

def listar_ordenes_por_codfacturar(codfacturar: str, codpai: str, codagencia: str,
                                    anio: int | None = None, orden_filtro: str | None = None,
                                    solo_con_saldo: bool = True) -> list[dict]:
    """Lista las órdenes de compra (tabla `ordenes`, vía ORM -- sin el
    JOIN a otro esquema que usa `buscar_ordenes`, así que no trae
    nombres de cliente/marca, solo códigos) que pertenecen a un
    `codfacturar` dado, opcionalmente filtradas por año de presupuesto
    (`aniopresup`) y/o por número de orden (coincidencia parcial).

    A cada orden se le agrega `.saldo` (SaldoOrden, ver
    calcular_saldo_orden). Si `solo_con_saldo` es True (default), se
    excluyen las órdenes ya facturadas por completo (saldo_disponible
    <= 0) -- así el proveedor ve de una vez cuáles le falta facturar.

    Mismas reglas de negocio que `buscar_ordenes`: anula='No',
    ordimpresa='Si', concepto IN ('1','11','12').

    Devuelve una lista de objetos `Ordenes` (con `.saldo` agregado),
    ordenada por número de orden. Para efectivamente registrar una
    factura sobre una de estas órdenes, hay que volver a buscarla con
    `buscar_ordenes` / `buscar_orden_para_proveedor` -- esta función es
    solo para explorar/listar, no reemplaza esa búsqueda."""
    qs = Ordenes.objects.using('default').filter(
        codpai=codpai,
        codagencia=codagencia,
        codfacturar=codfacturar,
        anula='No',
        ordimpresa='Si',
        concepto__in=['1', '11', '12'],
    )
    if anio:
        qs = qs.filter(aniopresup=str(anio))
    if orden_filtro:
        qs = qs.filter(orden__icontains=orden_filtro.strip())

    ordenes = list(qs.order_by('orden')[:200])

    resultado = []
    for o in ordenes:
        o.saldo = calcular_saldo_orden({
            'codpai': o.codpai, 'codagencia': o.codagencia,
            'codtipmed': o.codtipmed, 'codtsubmed': o.codtsubmed,
            'aniopresup': o.aniopresup, 'mespresup': o.mespresup,
            'codpresup': o.codpresup, 'no_rev': o.no_rev,
            'codordno': o.codordno, 'orden': o.orden,
            'totalorden': o.totalorden,
        })
        if solo_con_saldo and o.saldo.saldo_disponible <= 0:
            continue
        resultado.append(o)

    return resultado


# -----------------------------------------------------------------------
# 2b. Listado de facturas recibidas, filtrado por mes/año
# -----------------------------------------------------------------------

def listar_facturas_recibidas(anio: int, mes: int | None, codpai: str, codagencia: str,
                               codcli: str | None = None, codpresup: str | None = None,
                               estado_codificacion: str | None = None):
    """Devuelve las facturas de proveedor ya registradas en `ordenesrd`,
    filtradas por el año (obligatorio) y mes (opcional) de `fecrecep`
    (fecha en la que se recibió/registró la factura en el sistema), y
    opcionalmente por cliente y/o presupuesto (coincidencia parcial,
    no distingue mayúsculas/minúsculas), y/o por estado de codificación
    ('pendiente' | 'codificada'; None/otro valor = todas).

    A cada fila se le agregan los atributos `.codificado` (bool, según
    exista o no un registro en `FacturaCodificacion` para ese keyorden)
    y `.liquidada` (bool, True si ya forma parte de una Liquidacion
    activa -- no anulada -- ver liquidacion_activa_de()).

    NOTA: el filtro de "cliente" busca por `ordenesrd.codcli` (el
    CÓDIGO del cliente), no por su nombre -- el nombre vive en
    `climae` (otro esquema, pivot_comsys) y `ordenesrd` no lo guarda
    directamente. Si prefieres buscar/mostrar por NOMBRE del cliente,
    dime y agrego el JOIN correspondiente.

    Excluye las facturas anuladas (`facanula = 'Si'`). Las notas de
    crédito (`tipofac = 'NC'`) se incluyen en el listado tal cual, pero
    se restan en el total general.

    Devuelve una tupla: (lista ordenada, total_general: Decimal)
    """
    qs = OrdenesRd.objects.using('default').filter(
        codpai=codpai,
        codagencia=codagencia,
        fecrecep__year=anio,
    ).exclude(facanula='Si')

    if mes:
        qs = qs.filter(fecrecep__month=mes)
    if codcli:
        qs = qs.filter(codcli__icontains=codcli.strip())
    if codpresup:
        qs = qs.filter(codpresup__icontains=codpresup.strip())

    qs = qs.order_by('-fecrecep', '-keyorden')
    filas = list(qs)

    codificadas = set(
        FacturaCodificacion.objects.filter(
            keyorden__in=[f.keyorden for f in filas]
        ).values_list('keyorden', flat=True)
    )
    liquidadas = set(
        LiquidacionDetalle.objects.filter(
            keyorden__in=[f.keyorden for f in filas], liquidacion__anulada=False,
        ).values_list('keyorden', flat=True)
    )
    for f in filas:
        f.codificado = f.keyorden in codificadas
        f.liquidada = f.keyorden in liquidadas

    if estado_codificacion == 'pendiente':
        filas = [f for f in filas if not f.codificado]
    elif estado_codificacion == 'codificada':
        filas = [f for f in filas if f.codificado]

    total_general = Decimal('0.00')
    for fila in filas:
        monto = _a_decimal(fila.totalfac)
        if fila.tipofac == 'NC':
            monto = -monto
        total_general += monto

    return filas, total_general


# -----------------------------------------------------------------------
# 2d. Codificación (revisión de contabilidad) de una factura
# -----------------------------------------------------------------------

def esta_codificada(keyorden: int) -> bool:
    return FacturaCodificacion.objects.filter(keyorden=keyorden).exists()


def marcar_codificada(keyorden: int, orden: str, numfactura: str, usuario: str,
                       observaciones: str = '') -> FacturaCodificacion:
    """Idempotente: si ya estaba codificada, no duplica el registro."""
    obj, _creado = FacturaCodificacion.objects.get_or_create(
        keyorden=keyorden,
        defaults={
            'orden': orden,
            'numfactura': numfactura,
            'usuario': usuario,
            'observaciones': observaciones,
        },
    )
    return obj


def quitar_codificacion(keyorden: int) -> None:
    FacturaCodificacion.objects.filter(keyorden=keyorden).delete()


def liquidacion_activa_de(keyorden: int) -> Liquidacion | None:
    """Si esta factura ya forma parte de una Liquidacion que NO está
    anulada, la devuelve (si no, devuelve None). Se usa para bloquear
    "Revisar factura" (aceptar/quitar aceptación/reemplazar adjunto):
    una vez liquidada, la factura queda "congelada" -- para poder
    tocarla de nuevo hay que anular esa liquidación primero (ver
    anular_liquidacion), lo que la libera."""
    detalle = LiquidacionDetalle.objects.filter(
        keyorden=keyorden, liquidacion__anulada=False,
    ).select_related('liquidacion').first()
    return detalle.liquidacion if detalle else None


# -----------------------------------------------------------------------
# 2c. Validar que el no. de factura no se repita para el mismo proveedor
# -----------------------------------------------------------------------

def numfactura_ya_registrada(codpai: str, codagencia: str, codfacturar: str, numfactura: str) -> bool:
    """True si YA existe una factura activa (no anulada) con ese mismo
    número para el mismo proveedor.

    *** SUPUESTO A CONFIRMAR ***
    "Proveedor" se identifica aquí con `codfacturar` (el código del
    medio que se está facturando -- lo que en tu pantalla de
    "Contraseñas de Proveedores" aparece como "Código de proveedor").
    Si el proveedor real es otro campo/tabla distinta, dime cuál y
    ajusto este único filtro.
    """
    return OrdenesRd.objects.using('default').filter(
        codpai=codpai,
        codagencia=codagencia,
        codfacturar=codfacturar,
        numfactura=numfactura,
    ).exclude(facanula='Si').exists()


# -----------------------------------------------------------------------
# 3. keyorden (PK de ordenesrd)
# -----------------------------------------------------------------------
# CONFIRMADO por el usuario: `keyorden` es INT AUTO_INCREMENT en MySQL.
# Por eso ya NO se genera manualmente: se omite del INSERT y Django/MySQL
# lo asignan solos (Django lo recupera automáticamente vía lastrowid y
# lo deja disponible en `nueva_factura.keyorden` después del .create()).


# -----------------------------------------------------------------------
# 3b. Cálculo automático de impuestos proporcionales a la orden
#     (usado por el Portal de Proveedores: el proveedor solo captura el
#     monto TOTAL, y el sistema calcula IVA / TP proporcional a como ya
#     vienen definidos en la orden de compra).
# -----------------------------------------------------------------------

def calcular_impuestos_proporcionales(orden_dict: dict, monto_total: Decimal) -> tuple[Decimal, Decimal]:
    """A partir de los porcentajes que YA trae la orden de compra
    (orden.valiva / orden.totalorden, orden.valtp / orden.totalorden),
    calcula cuánto de `monto_total` corresponde a IVA y a TP.

        % IVA de la orden = orden.valiva / orden.totalorden
        % TP  de la orden = orden.valtp  / orden.totalorden
        IVA factura = monto_total * % IVA de la orden
        TP  factura = monto_total * % TP  de la orden

    Si la orden no tiene totalorden (o es 0), devuelve (0, 0) -- no hay
    base para prorratear.
    """
    total_orden = _a_decimal(orden_dict.get('totalorden'))
    if total_orden == 0:
        return Decimal('0.00'), Decimal('0.00')

    valiva_orden = _a_decimal(orden_dict.get('valiva'))
    valtp_orden = _a_decimal(orden_dict.get('valtp'))
    monto_total = _a_decimal(monto_total)

    pct_iva = valiva_orden / total_orden
    pct_tp = valtp_orden / total_orden

    iva = (monto_total * pct_iva).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    tp = (monto_total * pct_tp).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return iva, tp


# -----------------------------------------------------------------------
# 4. Registrar la factura (INSERT final + adjunto)
# -----------------------------------------------------------------------

class NumeroFacturaDuplicadoError(Exception):
    """Ya existe una factura activa con ese número para ese proveedor."""


@transaction.atomic
def registrar_factura(*, orden_dict: dict, numfactura: str, fecfactura, monto: Decimal,
                       valiva: Decimal | None, valtp: Decimal | None,
                       tipofac: str, obsfactura: str, archivo, usuario: str) -> OrdenesRd:
    """Inserta el nuevo registro en `ordenesrd` y guarda el adjunto.
    Asume que la validación de saldo YA se hizo antes de llamar esto.

    Los valores de respaldo de abajo están tomados EXACTO del
    `INFORMATION_SCHEMA.COLUMNS` real de `ordenesrd` (todas sus columnas
    son NOT NULL). Donde la columna real tiene un DEFAULT razonable
    (''  / 0 / 0.00 / 'Total' / 'No'), se replica ese mismo default.
    Las columnas de fecha NOT NULL cuyo default real es el "cero" de
    MySQL ('0000-00-00', inválido para Python/Django) usan en su lugar
    una fecha centinela (1900-01-01) cuando no hay un valor real que
    tenga sentido de negocio -> ver FECHA_CENTINELA más abajo.
    """
    # Última línea de defensa contra número de factura duplicado por
    # proveedor (la vista ya valida esto antes de llegar aquí, pero se
    # revalida dentro de la transacción para evitar condiciones de
    # carrera entre dos registros simultáneos).
    codfacturar = orden_dict.get('codfacturar')
    if numfactura_ya_registrada(orden_dict['codpai'], orden_dict['codagencia'], codfacturar, numfactura):
        raise NumeroFacturaDuplicadoError(
            f'Ya existe una factura activa con el número "{numfactura}" para este proveedor.'
        )

    hoy = timezone.localdate()
    valiva = valiva or Decimal('0.00')
    valtp = valtp or Decimal('0.00')

    # 1900-01-01: fecha "sin valor" para columnas date NOT NULL sin
    # significado de negocio en este flujo (p. ej. f_anula cuando la
    # factura no está anulada). Si tu negocio prefiere otra convención
    # (p. ej. la misma fecha de registro), dime y la cambio aquí.
    FECHA_CENTINELA = datetime.date(1900, 1, 1)

    def _txt(valor, default=''):
        return valor if valor not in (None, '') else default

    def _fecha(valor, respaldo=hoy):
        return valor if valor is not None else respaldo

    nueva = OrdenesRd.objects.using('default').create(
        codpai=orden_dict['codpai'],
        codagencia=orden_dict['codagencia'],
        codtipmed=orden_dict['codtipmed'],
        codtsubmed=orden_dict['codtsubmed'],
        aniopresup=orden_dict['aniopresup'],
        mespresup=orden_dict['mespresup'],
        codpresup=orden_dict['codpresup'],
        no_rev=orden_dict['no_rev'],
        codordno=orden_dict['codordno'],
        orden=orden_dict['orden'],
        codcli=_txt(orden_dict.get('codcli')),
        codmar=_txt(orden_dict.get('codmar')),
        codprd=_txt(orden_dict.get('codprd')),
        codcam=_txt(orden_dict.get('codcam')),
        fecorden=_fecha(orden_dict.get('fecorden')),
        codfacturar=_txt(orden_dict.get('codfacturar')),
        mesfac=_txt(orden_dict.get('mesfac')) or f'{hoy:%Y-%m}',
        valtotal=monto,
        valiva=valiva,
        valivaret=Decimal('0.00'),
        valtp=valtp,
        valdescfin=Decimal('0.00'),
        ctonetodescfin=Decimal('0.00'),
        totalfac=monto,  # monto de la factura del proveedor (total, según lo solicitado)
        tiporden=_txt(orden_dict.get('tiporden'), default='Total'),  # enum, default real = 'Total'
        concepto=_txt(orden_dict.get('concepto')),
        fecpublica=_fecha(orden_dict.get('fecpublica'), respaldo=fecfactura),
        ordimpresa=_txt(orden_dict.get('ordimpresa'), default='Si'),  # enum; la búsqueda ya filtra ordimpresa='Si'
        fecfactura=fecfactura,
        numfactura=numfactura,
        fecrecep=hoy,
        tipofac=tipofac or 'FC',
        obsfactura=_txt(obsfactura),
        facanula='No',
        f_anula=FECHA_CENTINELA,     # NOT NULL; no hay fecha de anulación real todavía
        tipord=_txt(''),
        obsanula=_txt(''),
        usranula=_txt(''),
        corre=1,                     # smallint NOT NULL, default real = 1
        numeroncf=_txt(''),
        origendoc=_txt(''),          # se deja igual al default real (largo de columna desconocido)
        keytra_quedan=_txt(''),
        codid=_txt(''),
        creusr=usuario,
        fecusr=timezone.now(),
        codusr=_a_codusr(usuario),
        stausr='Activo',
    )

    if archivo:
        FacturaAdjunto.objects.create(
            keyorden=nueva.keyorden,
            orden=orden_dict['orden'],
            numfactura=numfactura,
            archivo=archivo,
            content_type=getattr(archivo, 'content_type', ''),
            tamano_bytes=getattr(archivo, 'size', 0) or 0,
            usuario=usuario,
        )

    return nueva


# -----------------------------------------------------------------------
# 5. Anulación de una factura ya registrada
# -----------------------------------------------------------------------

class FacturaYaAnuladaError(Exception):
    """Se intentó anular una factura que ya estaba anulada."""


@transaction.atomic
def anular_factura(keyorden: int, motivo: str, usuario: str) -> OrdenesRd:
    """Marca la factura como anulada (`facanula='Si'`), NO la borra
    físicamente. Esto es intencional: preserva el rastro de auditoría
    (quién, cuándo y por qué se anuló) y automáticamente deja de contar
    contra el saldo de la orden, porque `calcular_saldo_orden()` y
    `listar_facturas_recibidas()` ya excluyen `facanula='Si'`.

    Lanza FacturaYaAnuladaError si la factura ya estaba anulada.
    Lanza OrdenesRd.DoesNotExist si el keyorden no existe.
    """
    factura = OrdenesRd.objects.using('default').select_for_update().get(keyorden=keyorden)

    if factura.facanula == 'Si':
        raise FacturaYaAnuladaError(
            f'La factura {factura.numfactura} (keyorden={keyorden}) ya estaba anulada.'
        )

    factura.facanula = 'Si'
    factura.f_anula = timezone.localdate()
    factura.obsanula = motivo
    factura.usranula = _a_codusr(usuario)
    factura.save(update_fields=['facanula', 'f_anula', 'obsanula', 'usranula'])

    return factura


def reemplazar_adjunto_factura(keyorden: int, archivo, usuario: str) -> FacturaAdjunto:
    """Para cuando se subió el archivo equivocado: agrega un
    FacturaAdjunto NUEVO para esa factura (no borra ni sobrescribe el
    anterior -- mismo criterio de nunca destruir historial que
    anular_factura). Las pantallas que muestran "el" adjunto de una
    factura (revisar_factura, armar_indice_liquidacion) siempre toman
    el más reciente por `fecha_carga`, así que en la práctica el nuevo
    archivo reemplaza al anterior sin perder el rastro de qué se subió
    antes."""
    orden = OrdenesRd.objects.using('default').get(keyorden=keyorden)
    return FacturaAdjunto.objects.create(
        keyorden=str(keyorden),
        orden=orden.orden,
        numfactura=orden.numfactura,
        archivo=archivo,
        content_type=getattr(archivo, 'content_type', ''),
        tamano_bytes=getattr(archivo, 'size', 0) or 0,
        usuario=usuario,
    )


# -----------------------------------------------------------------------
# 6. Liquidar clientes: agrupar facturas ACEPTADAS por un criterio y,
#    opcionalmente, guardarlas como una Liquidacion propia.
# -----------------------------------------------------------------------

# Mapa criterio de negocio -> columna real de ordenesrd por la que se agrupa.
CRITERIOS_AGRUPACION = {
    Liquidacion.CRITERIO_PRESUPUESTO: 'codpresup',
    Liquidacion.CRITERIO_CLIENTE: 'codcli',
    Liquidacion.CRITERIO_MARCA: 'codmar',
    Liquidacion.CRITERIO_TIPOMEDIO: 'codtipmed',
}


def facturas_aceptadas_por_liquidar(anio: int, mes: int | None, codpai: str, codagencia: str,
                                     criterio: str, codcli: str | None = None,
                                     codpresup: str | None = None) -> list[dict]:
    """Trae las facturas ACEPTADAS (con registro en FacturaCodificacion),
    activas (no anuladas) y que TODAVÍA no estén en ninguna Liquidacion
    guardada (y no anulada), y las agrupa por `criterio` (ver
    CRITERIOS_AGRUPACION).

    Una factura que estaba en una Liquidacion que luego se anuló (ver
    anular_liquidacion) vuelve a aparecer aquí como pendiente -- anular
    una liquidación es precisamente la forma de "liberar" sus facturas
    para poder incluirlas en una liquidación nueva.

    Devuelve una lista de dicts: [{'valor': <código del grupo>,
    'facturas': [OrdenesRd, ...], 'total': Decimal}, ...] ordenada por
    valor del grupo.
    """
    if criterio not in CRITERIOS_AGRUPACION:
        raise ValueError(f'Criterio de agrupación inválido: {criterio}')
    campo = CRITERIOS_AGRUPACION[criterio]

    keyordenes_aceptadas = set(FacturaCodificacion.objects.values_list('keyorden', flat=True))
    keyordenes_ya_liquidadas = set(
        LiquidacionDetalle.objects.exclude(liquidacion__anulada=True).values_list('keyorden', flat=True)
    )

    qs = OrdenesRd.objects.using('default').filter(
        codpai=codpai,
        codagencia=codagencia,
        fecrecep__year=anio,
    ).exclude(facanula='Si')

    if mes:
        qs = qs.filter(fecrecep__month=mes)
    if codcli:
        qs = qs.filter(codcli__icontains=codcli.strip())
    if codpresup:
        qs = qs.filter(codpresup__icontains=codpresup.strip())

    candidatas = [
        f for f in qs
        if f.keyorden in keyordenes_aceptadas and f.keyorden not in keyordenes_ya_liquidadas
    ]

    grupos: dict[str, list] = {}
    for f in candidatas:
        clave = getattr(f, campo, None) or '(sin valor)'
        grupos.setdefault(clave, []).append(f)

    resultado = []
    for clave in sorted(grupos.keys()):
        items = grupos[clave]
        total = Decimal('0.00')
        for it in items:
            monto = _a_decimal(it.totalfac)
            if it.tipofac == 'NC':
                monto = -monto
            total += monto
        resultado.append({'valor': clave, 'facturas': items, 'total': total})

    return resultado


class NadaQueLiquidarError(Exception):
    """No hay facturas pendientes para ese grupo (puede que ya se haya
    liquidado, o que los filtros hayan cambiado desde que se mostró la
    pantalla)."""


@transaction.atomic
def guardar_liquidacion(*, criterio: str, valor: str, anio: int, mes: int | None,
                         codpai: str, codagencia: str, usuario: str,
                         codcli: str | None = None, codpresup: str | None = None) -> Liquidacion:
    """Vuelve a calcular el grupo específico (mismo criterio+valor+
    filtros) EN ESTE MOMENTO -- no confía en una lista de facturas
    mandada desde el navegador -- y lo guarda como una Liquidacion +
    su detalle. Esto evita duplicar una factura en dos liquidaciones
    distintas si dos personas la liquidan casi al mismo tiempo."""
    grupos = facturas_aceptadas_por_liquidar(anio, mes, codpai, codagencia, criterio, codcli, codpresup)
    grupo = next((g for g in grupos if g['valor'] == valor), None)

    if not grupo or not grupo['facturas']:
        raise NadaQueLiquidarError(
            f'No hay facturas pendientes de liquidar para "{valor}". '
            f'Puede que ya se hayan incluido en otra liquidación.'
        )

    liquidacion = Liquidacion.objects.create(
        criterio=criterio,
        valor_agrupador=valor,
        codpai=codpai,
        codagencia=codagencia,
        usuario=usuario,
        total=grupo['total'],
    )
    for f in grupo['facturas']:
        monto = _a_decimal(f.totalfac)
        if f.tipofac == 'NC':
            monto = -monto
        LiquidacionDetalle.objects.create(
            liquidacion=liquidacion,
            keyorden=f.keyorden,
            orden=f.orden,
            numfactura=f.numfactura,
            monto=monto,
        )

    return liquidacion


class LiquidacionYaAnuladaError(Exception):
    """Se intentó anular una liquidación que ya estaba anulada."""


@transaction.atomic
def anular_liquidacion(numero: int, motivo: str, usuario: str) -> Liquidacion:
    """Anula una liquidación (mismo patrón que anular_factura): NO borra
    el registro ni su detalle -- los deja como están, para conservar el
    rastro de auditoría (quién, cuándo y por qué), y solo marca
    `anulada=True`.

    Esto automáticamente "libera" sus facturas: al estar anulada, sus
    LiquidacionDetalle dejan de contar como "ya liquidadas" en
    facturas_aceptadas_por_liquidar(), así que esas facturas vuelven a
    aparecer disponibles para incluirse en una liquidación nueva.

    Lanza LiquidacionYaAnuladaError si ya estaba anulada.
    Lanza Liquidacion.DoesNotExist si el número no existe."""
    liquidacion = Liquidacion.objects.select_for_update().get(numero=numero)

    if liquidacion.anulada:
        raise LiquidacionYaAnuladaError(f'La liquidación #{numero} ya estaba anulada.')

    liquidacion.anulada = True
    liquidacion.fecha_anula = timezone.now()
    liquidacion.usuario_anula = usuario
    liquidacion.motivo_anula = motivo
    liquidacion.save(update_fields=['anulada', 'fecha_anula', 'usuario_anula', 'motivo_anula'])

    return liquidacion


# -----------------------------------------------------------------------
# 7. Armar y fusionar el PDF final de una liquidación (Detalle + factura +
#    presupuesto + orden de compra de cada línea), automatizando el
#    proceso manual descrito por el cliente: separar por carpetas,
#    numerar cada medio según el orden del presupuesto, renombrar con
#    número+letra (a=factura, b=presupuesto, c=orden) y fusionar todo en
#    un solo PDF.
# -----------------------------------------------------------------------

# Carpetas "parametrizadas" (hoy carpetas locales dentro de MEDIA_ROOT;
# el día que se conecte Google Drive/OneDrive, esto es lo único que
# cambiaría) donde el equipo deja los PDFs de Orden de Compra y de
# Presupuesto tal como los emite cada sistema -- ver
# sincronizar_adjuntos_desde_carpetas().
CARPETA_ORDENES_COMPRA = 'OrdenesPdf'
CARPETA_PRESUPUESTOS = 'presupuestos'


def sincronizar_adjuntos_desde_carpetas() -> dict:
    """Lee los PDFs que haya en `MEDIA_ROOT/OrdenesPdf/` y
    `MEDIA_ROOT/presupuestos/` -- las carpetas donde hoy se dejan estos
    documentos mientras no se conecta Google Drive/OneDrive -- y
    crea/actualiza el `OrdenCompraAdjunto`/`PresupuestoAdjunto`
    correspondiente para cada uno.

    No copia el archivo: como ya vive dentro de MEDIA_ROOT, el
    `FileField` simplemente apunta a esa ruta relativa. Es idempotente
    (se puede llamar en cada carga de pantalla): si el archivo de un
    `orden`/`codpresup` no cambió de nombre, no hace nada.

    Convención de nombres (la misma que ya usa el equipo):
      - Orden de compra: `<numero_de_orden>.pdf` (ej. "2020115636.pdf").
      - Presupuesto: `<codpresup>-<revisión>.pdf` (ej.
        "SAG-26-04-00012-0.pdf" -> codpresup "SAG-26-04-00012"); si no
        trae el sufijo de revisión, se usa el nombre tal cual.

    Devuelve un resumen: {'ordenes_nuevas', 'ordenes_actualizadas',
    'presupuestos_nuevos', 'presupuestos_actualizados'}."""
    import os

    from django.conf import settings

    resumen = {
        'ordenes_nuevas': 0, 'ordenes_actualizadas': 0,
        'presupuestos_nuevos': 0, 'presupuestos_actualizados': 0,
    }

    carpeta_ordenes = os.path.join(settings.MEDIA_ROOT, CARPETA_ORDENES_COMPRA)
    if os.path.isdir(carpeta_ordenes):
        for fname in os.listdir(carpeta_ordenes):
            if not fname.lower().endswith('.pdf'):
                continue
            orden = os.path.splitext(fname)[0].strip()
            ruta_relativa = f'{CARPETA_ORDENES_COMPRA}/{fname}'
            obj, creado = OrdenCompraAdjunto.objects.get_or_create(
                orden=orden, defaults={'archivo': ruta_relativa, 'usuario': 'sync-carpeta'},
            )
            if creado:
                resumen['ordenes_nuevas'] += 1
            elif obj.archivo.name != ruta_relativa:
                obj.archivo.name = ruta_relativa
                obj.content_type = ''  # forzar recálculo en save()
                obj.save()
                resumen['ordenes_actualizadas'] += 1

    carpeta_presupuestos = os.path.join(settings.MEDIA_ROOT, CARPETA_PRESUPUESTOS)
    if os.path.isdir(carpeta_presupuestos):
        for fname in os.listdir(carpeta_presupuestos):
            if not fname.lower().endswith('.pdf'):
                continue
            codpresup = os.path.splitext(fname)[0].strip()
            base, _, sufijo = codpresup.rpartition('-')
            # Quita el sufijo de revisión ("-0", "-1", ...) SOLO si es
            # de un solo dígito -- el propio codpresup ya termina en un
            # segmento numérico de varios dígitos (ej. "...-00012"), así
            # que no se puede asumir "termina en número" sin más.
            if base and len(sufijo) == 1 and sufijo.isdigit():
                codpresup = base
            ruta_relativa = f'{CARPETA_PRESUPUESTOS}/{fname}'
            obj, creado = PresupuestoAdjunto.objects.get_or_create(
                codpresup=codpresup, defaults={'archivo': ruta_relativa, 'usuario': 'sync-carpeta'},
            )
            if creado:
                resumen['presupuestos_nuevos'] += 1
            elif obj.archivo.name != ruta_relativa:
                obj.archivo.name = ruta_relativa
                obj.content_type = ''
                obj.save()
                resumen['presupuestos_actualizados'] += 1

    return resumen

def armar_indice_liquidacion(liquidacion: Liquidacion) -> list[dict]:
    """Para cada `LiquidacionDetalle` de la liquidación, resuelve los
    datos necesarios para el índice/portada (tipo de medio, razón
    social, cliente, orden, presupuesto) y ubica los adjuntos de
    factura/presupuesto/orden de compra ya cargados en el sistema.

    Devuelve una lista de dicts ORDENADA por `codpresup` (igual que el
    proceso manual: "dejando como base el orden del presupuesto"), con
    un `numero` secuencial asignado en ese orden -- ese número + la
    letra fija (a=factura, b=presupuesto, c=orden de compra) es lo que
    en el proceso manual se usa para renombrar/ordenar los archivos
    antes de fusionarlos; aquí se usa para ordenar la fusión, sin
    necesidad de renombrar nada de verdad.

    Los nombres "bonitos" (razón social, cliente, tipo de medio) se
    obtienen reutilizando `buscar_ordenes` (mismo SQL con JOIN a
    pivot_comsys que usa la búsqueda normal de orden), cacheando por
    número de orden para no repetir la consulta cuando varias facturas
    de la liquidación pertenecen a la misma orden."""
    detalles = list(liquidacion.detalles.all())
    if not detalles:
        return []

    keyordenes = [d.keyorden for d in detalles]
    ordenesrd_por_key = {
        o.keyorden: o
        for o in OrdenesRd.objects.using('default').filter(keyorden__in=keyordenes)
    }
    # order_by('fecha_carga') a propósito: si una factura tiene más de
    # un adjunto (se reemplazó el archivo, ver
    # reemplazar_adjunto_factura), el dict se queda con el ÚLTIMO que
    # recorre -- es decir, el más reciente.
    facturas_adjuntas = {
        fa.keyorden: fa
        for fa in FacturaAdjunto.objects.filter(
            keyorden__in=[str(k) for k in keyordenes]
        ).order_by('fecha_carga')
    }

    # Primera pasada: resolver, para cada detalle, la fila real de
    # `ordenes` (vía buscar_ordenes) y de ahí el `codpresup` correcto.
    # OJO: `ordenesrd.codpresup` puede venir vacío según cómo se haya
    # registrado la factura -- la fuente confiable de `codpresup` es
    # `ordenes.codpresup` (lo que trae buscar_ordenes), no `ordenesrd`.
    cache_busqueda: dict[str, list[dict]] = {}
    resueltos = []
    for d in detalles:
        ordenesrd_row = ordenesrd_por_key.get(d.keyorden)
        codpresup_ordenesrd = ordenesrd_row.codpresup if ordenesrd_row else ''

        if d.orden not in cache_busqueda:
            cache_busqueda[d.orden] = buscar_ordenes(d.orden, liquidacion.codpai, liquidacion.codagencia)
        candidatas = cache_busqueda[d.orden]
        fila_orden = next(
            (f for f in candidatas if codpresup_ordenesrd and f.get('codpresup') == codpresup_ordenesrd),
            None,
        ) or (candidatas[0] if candidatas else {})

        codpresup = fila_orden.get('codpresup') or codpresup_ordenesrd or ''
        resueltos.append((d, ordenesrd_row, fila_orden, codpresup))

    # Segunda pasada: con los codpresup ya resueltos, traer de una vez
    # los adjuntos de presupuesto/orden de compra que ya estén subidos.
    presupuestos_adjuntos = {
        p.codpresup: p
        for p in PresupuestoAdjunto.objects.filter(codpresup__in={cp for *_, cp in resueltos if cp})
    }
    ordenes_compra_adjuntas = {
        o.orden: o for o in OrdenCompraAdjunto.objects.filter(orden__in={d.orden for d in detalles})
    }

    filas = []
    for d, ordenesrd_row, fila_orden, codpresup in resueltos:
        filas.append({
            'keyorden': d.keyorden,
            'orden': d.orden,
            'numfactura': d.numfactura,
            'monto': d.monto,
            'codpresup': codpresup,
            'tipo_medio': fila_orden.get('destipmed') or (ordenesrd_row.codtipmed if ordenesrd_row else ''),
            'razon_social': fila_orden.get('rsfacturar') or (ordenesrd_row.codfacturar if ordenesrd_row else ''),
            'cliente': fila_orden.get('nomcli') or (ordenesrd_row.codcli if ordenesrd_row else ''),
            'factura_adjunto': facturas_adjuntas.get(str(d.keyorden)),
            'presupuesto_adjunto': presupuestos_adjuntos.get(codpresup) if codpresup else None,
            'ordencompra_adjunto': ordenes_compra_adjuntas.get(d.orden),
        })

    filas.sort(key=lambda f: (f['codpresup'] or '', f['orden']))
    for numero, fila in enumerate(filas, start=1):
        fila['numero'] = numero

    return filas


def _generar_portada_pdf(liquidacion: Liquidacion, filas: list[dict]) -> io.BytesIO:
    """Genera (con reportlab, sin dependencias de sistema) la portada
    "Detalle" de la liquidación: el mismo índice que hoy arman a mano
    en Excel (Tipo de Medio, Razón Social, Cliente, #Orden,
    Presupuesto), como primera página del PDF final."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    styles = getSampleStyleSheet()

    elementos = [
        Paragraph(f"Liquidación #{liquidacion.numero}", styles['Title']),
        Paragraph(f"{liquidacion.get_criterio_display()}: {liquidacion.valor_agrupador}", styles['Normal']),
        Paragraph(f"Fecha: {liquidacion.fecha_liquidacion:%d/%m/%Y %H:%M}", styles['Normal']),
    ]

    if liquidacion.anulada:
        elementos.append(Spacer(1, 0.15 * inch))
        elementos.append(Paragraph(
            '<font color="red"><b>⚠ ESTA LIQUIDACIÓN FUE ANULADA</b></font>', styles['Heading2'],
        ))
        elementos.append(Paragraph(
            f"Anulada el {liquidacion.fecha_anula:%d/%m/%Y %H:%M} por "
            f"{liquidacion.usuario_anula or '—'}. Motivo: {liquidacion.motivo_anula}",
            styles['Normal'],
        ))

    elementos.append(Spacer(1, 0.3 * inch))

    data = [['#', 'Tipo de Medio', 'Razón Social', 'Cliente', 'Orden', 'Presupuesto', 'Monto']]
    for f in filas:
        data.append([
            str(f['numero']), f['tipo_medio'], f['razon_social'], f['cliente'],
            f['orden'], f['codpresup'], f"Q {f['monto']:,.2f}",
        ])
    data.append(['', '', '', '', '', 'TOTAL', f"Q {liquidacion.total:,.2f}"])

    tabla = Table(data, repeatRows=1)
    tabla.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#154360')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.grey),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (-1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elementos.append(tabla)

    doc.build(elementos)
    buffer.seek(0)
    return buffer


def _crear_marca_agua(width: float, height: float, texto: str = 'LIQUIDACIÓN ANULADA') -> io.BytesIO:
    """Página en blanco del tamaño exacto pedido, con `texto` en
    diagonal semitransparente -- para sobreponer (merge_page) en cada
    página del PDF final."""
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas as pdfcanvas

    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=(width, height))
    c.saveState()
    c.translate(width / 2, height / 2)
    c.rotate(45)
    c.setFillColor(Color(0.75, 0, 0, alpha=0.35))
    c.setFont('Helvetica-Bold', max(18, min(width, height) / 9))
    c.drawCentredString(0, 0, texto)
    c.restoreState()
    c.save()
    buffer.seek(0)
    return buffer


def _marcar_pdf_como_anulado(writer: PdfWriter) -> None:
    """Sobrepone la marca de agua "LIQUIDACIÓN ANULADA" en TODAS las
    páginas del PDF final (no solo en la portada), para que quede claro
    incluso si alguien solo ve una factura/orden suelta fuera de
    contexto. Cada página puede venir de un PDF distinto (con su propio
    tamaño de página), así que la marca se genera a la medida de cada
    una."""
    for page in writer.pages:
        ancho = float(page.mediabox.width)
        alto = float(page.mediabox.height)
        marca = PdfReader(_crear_marca_agua(ancho, alto)).pages[0]
        page.merge_page(marca)


def _agregar_adjunto_al_pdf(writer: PdfWriter, adjunto, descripcion: str, advertencias: list[str]) -> None:
    """Agrega las páginas de un adjunto (PDF o imagen) al `writer`. Si
    el adjunto no existe, o no se puede leer, o no es un PDF/imagen
    reconocible, no aborta la fusión completa -- solo lo anota en
    `advertencias` para que el usuario sepa qué quedó fuera antes de
    entregar el PDF al cliente."""
    if adjunto is None or not adjunto.archivo:
        advertencias.append(f'Falta el adjunto de {descripcion} -- no se incluyó en el PDF.')
        return

    try:
        adjunto.archivo.open('rb')
        datos = adjunto.archivo.read()
    finally:
        adjunto.archivo.close()

    content_type = (adjunto.content_type or '').lower()
    nombre = (adjunto.archivo.name or '').lower()

    try:
        if content_type == 'application/pdf' or nombre.endswith('.pdf'):
            reader = PdfReader(io.BytesIO(datos))
        elif content_type.startswith('image/') or nombre.endswith(('.png', '.jpg', '.jpeg')):
            # Las imágenes (facturas fotografiadas/escaneadas) se
            # convierten a un PDF de una página antes de fusionar --
            # pypdf solo fusiona páginas de PDF, no imágenes sueltas.
            imagen = Image.open(io.BytesIO(datos)).convert('RGB')
            pdf_bytes = io.BytesIO()
            imagen.save(pdf_bytes, format='PDF')
            pdf_bytes.seek(0)
            reader = PdfReader(pdf_bytes)
        else:
            advertencias.append(
                f'El adjunto de {descripcion} no es PDF ni imagen reconocible '
                f'({content_type or "tipo desconocido"}) -- no se incluyó.'
            )
            return

        for page in reader.pages:
            writer.add_page(page)
    except Exception as exc:
        advertencias.append(f'No se pudo leer el adjunto de {descripcion} ({exc}) -- no se incluyó.')


def generar_pdf_liquidacion(liquidacion: Liquidacion) -> tuple[bytes, list[str]]:
    """Arma el PDF final de la liquidación: portada "Detalle" (índice de
    medios) y, para cada fila en el orden de `armar_indice_liquidacion`,
    su factura + presupuesto + orden de compra -- automatizando
    exactamente el proceso manual ("Detalle, factura, presupuesto,
    orden") descrito por el cliente.

    Los documentos que falten (presupuesto/orden de compra todavía no
    subidos a PresupuestoAdjunto/OrdenCompraAdjunto) se omiten sin
    abortar la generación, pero quedan listados en las advertencias
    devueltas -- revísalas antes de entregar el PDF al cliente.

    Devuelve (bytes_del_pdf, advertencias)."""
    filas = armar_indice_liquidacion(liquidacion)
    advertencias: list[str] = []

    writer = PdfWriter()

    portada = _generar_portada_pdf(liquidacion, filas)
    for page in PdfReader(portada).pages:
        writer.add_page(page)

    for fila in filas:
        _agregar_adjunto_al_pdf(
            writer, fila['factura_adjunto'],
            f"factura (fila {fila['numero']}, orden {fila['orden']})", advertencias,
        )
        _agregar_adjunto_al_pdf(
            writer, fila['presupuesto_adjunto'],
            f"presupuesto {fila['codpresup']} (fila {fila['numero']})", advertencias,
        )
        _agregar_adjunto_al_pdf(
            writer, fila['ordencompra_adjunto'],
            f"orden de compra {fila['orden']} (fila {fila['numero']})", advertencias,
        )

    if liquidacion.anulada:
        _marcar_pdf_como_anulado(writer)

    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue(), advertencias
