"""
Modelos del sistema de facturación de proveedores.

Hay dos tipos de modelos aquí:

1. Modelos "espejo" de tablas YA EXISTENTES en la base legacy
   (managed = False -> Django NUNCA las crea, altera ni borra con
   `migrate`; solo las usa para leer/escribir filas).
   -> Ordenes, OrdenesRd

2. Un modelo NUEVO, propio de esta app, para guardar el PDF/imagen
   de la factura (esto no existía en el esquema original, así que
   Django SÍ la crea vía migración).
   -> FacturaAdjunto

SUPUESTOS A VALIDAR CON EL DBA / EQUIPO (marcados también en el README):
  - Tipos y longitudes exactos de columnas (aquí se usan longitudes
    razonables según los nombres, pero deben confirmarse contra el
    `DESCRIBE ordenesrd;` real).
  - `keyorden` es la PK de `ordenesrd` pero no se explicó cómo se genera
    en el sistema legacy (autoincremental, secuencia propia, o
    concatenación de campos). En este proyecto se genera con una
    función reemplazable: ver `facturas/services.py::generar_keyorden`.
"""

import mimetypes
import time

from django.core.files.storage import FileSystemStorage
from django.db import models


class AlmacenamientoSobrescribible(FileSystemStorage):
    """Por defecto, si Django encuentra que ya existe un archivo con el
    nombre que le piden guardar, NO lo sobrescribe: le agrega un sufijo
    aleatorio al nuevo (ej. "2020115720_BSw0cZk.pdf") para no perder el
    viejo. Para PresupuestoAdjunto/OrdenCompraAdjunto NO queremos eso:
    si FoxPro vuelve a subir la misma orden o el mismo presupuesto, el
    archivo debe reemplazar al anterior en el mismo lugar.

    Se intenta borrar el archivo existente ANTES de guardar el nuevo
    (con reintentos: en Windows, borrar un archivo justo después de
    crearlo a veces falla porque el SO todavía lo tiene bloqueado un
    instante -- antivirus, etc.). Si aun así no se puede borrar, se
    sigue adelante igual: es mejor dejar un archivo viejo huérfano que
    duplicar el nuevo con un nombre distinto (que es el bug real que
    se vio: la "orden.pdf" nunca se actualizaba, quedaba viviendo junto
    a un "orden_XXXXX.pdf" nuevo que nadie referenciaba)."""

    def get_available_name(self, name, max_length=None):
        if self.exists(name):
            for intento in range(3):
                try:
                    self.delete(name)
                    break
                except OSError:
                    if intento == 2:
                        break
                    time.sleep(0.1)
        return name


ALMACENAMIENTO_SOBRESCRIBIBLE = AlmacenamientoSobrescribible()


class Ordenes(models.Model):
    """Espejo (solo lectura) de la tabla `ordenes`."""

    codpai = models.CharField(max_length=10)
    codagencia = models.CharField(max_length=20)
    codtipmed = models.CharField(max_length=10)
    codtsubmed = models.CharField(max_length=10)
    aniopresup = models.CharField(max_length=4)
    mespresup = models.CharField(max_length=10)
    codpresup = models.CharField(max_length=20)
    no_rev = models.CharField(max_length=10)
    codordno = models.CharField(max_length=20)
    # Django exige una PK, pero la real es compuesta (codpai+codagencia+
    # ...+orden) y la tabla NO tiene columna `id` (a diferencia de lo que
    # decía este modelo antes: declaraba un `id = AutoField(primary_key=
    # True)` que Django intentaba incluir en el SELECT -- y como esa
    # columna no existe de verdad en `ordenes`, CUALQUIER consulta por
    # ORM sobre este modelo tronaba con "Unknown column 'ordenes.id'".
    # No se había notado porque hasta ahora este modelo solo se leía por
    # SQL crudo (ver buscar_ordenes) -- nunca por el ORM.
    # Se marca `orden` como primary_key=True únicamente para que el ORM
    # funcione (no es único de verdad: una misma orden puede repetirse
    # por presupuesto/cliente distintos); NO se usa get_or_create ni se
    # hacen INSERT/UPDATE sobre este modelo, así que la falta de
    # unicidad real no causa ningún problema de integridad.
    orden = models.CharField(max_length=20, primary_key=True)
    codcli = models.CharField(max_length=20, null=True)
    codmar = models.CharField(max_length=20, null=True)
    codprd = models.CharField(max_length=20, null=True)
    codcam = models.CharField(max_length=20, null=True)
    fecorden = models.DateField(null=True)
    anula = models.CharField(max_length=5, null=True)
    fecanula = models.DateField(null=True)
    codfacturar = models.CharField(max_length=20, null=True)
    mesfac = models.CharField(max_length=10, null=True)
    codmon = models.CharField(max_length=10, null=True)
    tiporden = models.CharField(max_length=10, null=True)
    concepto = models.CharField(max_length=5, null=True)
    fecpublica = models.DateField(null=True)
    ordimpresa = models.CharField(max_length=5, null=True)
    ctobruto = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    valdescuento = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    ctoneto = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    valtotal = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    valiva = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    valtp = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    totalorden = models.DecimalField(max_digits=18, decimal_places=2, null=True)

    class Meta:
        managed = False
        db_table = 'ordenes'

    def __str__(self):
        return f"Orden {self.orden} ({self.codpresup})"


class OrdenesRd(models.Model):
    """Espejo de la tabla `ordenesrd`. Aquí se registran las facturas de
    proveedor ya ingresadas contra una orden de compra. Se hace INSERT
    real sobre esta tabla (managed=False -> Django no la migra, pero sí
    permite `.objects.using('default').create(...)`)."""

    # keyorden es INT AUTO_INCREMENT en la BD real (confirmado por el
    # usuario). Al ser AutoField, Django NUNCA envía este valor en el
    # INSERT: MySQL lo genera solo y Django lo recupera automáticamente
    # después de guardar (nueva_factura.keyorden queda con el valor real).
    keyorden = models.AutoField(primary_key=True)
    codpai = models.CharField(max_length=10)
    codagencia = models.CharField(max_length=20)
    codtipmed = models.CharField(max_length=10)
    codtsubmed = models.CharField(max_length=10)
    aniopresup = models.CharField(max_length=4)
    mespresup = models.CharField(max_length=10)
    codpresup = models.CharField(max_length=20)
    no_rev = models.CharField(max_length=10)
    codordno = models.CharField(max_length=20)
    orden = models.CharField(max_length=20)
    codcli = models.CharField(max_length=20, null=True)
    codmar = models.CharField(max_length=20, null=True)
    codprd = models.CharField(max_length=20, null=True)
    codcam = models.CharField(max_length=20, null=True)
    fecorden = models.DateField(null=True)
    codfacturar = models.CharField(max_length=20, null=True)
    mesfac = models.CharField(max_length=10, null=True)
    valtotal = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    valiva = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    valivaret = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    valtp = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    valdescfin = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    ctonetodescfin = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    totalfac = models.DecimalField(max_digits=18, decimal_places=2, null=True)
    tiporden = models.CharField(max_length=10, null=True)
    concepto = models.CharField(max_length=5, null=True)
    fecpublica = models.DateField(null=True)
    ordimpresa = models.CharField(max_length=5, null=True)
    fecfactura = models.DateField(null=True)
    numfactura = models.CharField(max_length=50, null=True)  # <-- factura del proveedor (alfanumérico 50)
    fecrecep = models.DateField(null=True)
    tipofac = models.CharField(max_length=5, null=True)  # 'FC' factura, 'NC' nota de crédito, etc.
    corre = models.CharField(max_length=20, null=True)
    numeroncf = models.CharField(max_length=50, null=True)
    origendoc = models.CharField(max_length=20, null=True)
    keytra_quedan = models.CharField(max_length=50, null=True)
    codid = models.CharField(max_length=20, null=True)
    obsfactura = models.CharField(max_length=255, null=True)
    facanula = models.CharField(max_length=5, default='No')
    f_anula = models.DateField(null=True)
    tipord = models.CharField(max_length=10, null=True)
    obsanula = models.CharField(max_length=255, null=True)
    # usranula/codusr: CONFIRMADO por DESCRIBE ordenesrd real = varchar(16)
    # (no era un supuesto -- ver facturas.services.LARGO_MAX_CODUSR /
    # _a_codusr(), que trunca cualquier valor antes de guardarlo aquí).
    usranula = models.CharField(max_length=16, null=True)
    creusr = models.CharField(max_length=92, null=True)
    fecusr = models.DateTimeField(null=True)
    codusr = models.CharField(max_length=16, null=True)
    stausr = models.CharField(max_length=10, null=True)

    class Meta:
        managed = False
        db_table = 'ordenesrd'

    def __str__(self):
        return f"Factura {self.numfactura} - Orden {self.orden}"


class FacturaAdjunto(models.Model):
    """Tabla NUEVA (propia de este sistema) para el archivo PDF/imagen de
    cada factura de proveedor ingresada. Django SÍ gestiona esta tabla."""

    keyorden = models.CharField(
        max_length=50, db_index=True,
        help_text="FK lógica hacia ordenesrd.keyorden"
    )
    orden = models.CharField(max_length=20, db_index=True)
    numfactura = models.CharField(max_length=50)
    archivo = models.FileField(upload_to='facturas_proveedor/%Y/%m/')
    content_type = models.CharField(max_length=100, blank=True)
    tamano_bytes = models.PositiveIntegerField(default=0)
    fecha_carga = models.DateTimeField(auto_now_add=True)
    usuario = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = 'facturas_proveedor_adjuntos'
        verbose_name = 'Adjunto de factura de proveedor'
        verbose_name_plural = 'Adjuntos de facturas de proveedor'

    def __str__(self):
        return f"{self.numfactura} - Orden {self.orden}"


class PresupuestoAdjunto(models.Model):
    """Tabla NUEVA -- el PDF del presupuesto (tal como lo emite el
    sistema de presupuestos, hoy vive en Drive/OneDrive y se sube acá a
    mano), para poder incluirlo en el PDF final de una liquidación.
    Se identifica por `codpresup` (ej. "SAG-26-04-00012") -- un
    presupuesto, un PDF; volver a subir para el mismo código reemplaza
    el archivo anterior (ver services.armar_indice_liquidacion)."""

    codpresup = models.CharField(
        max_length=20, unique=True, db_index=True,
        help_text='Código del presupuesto (ordenesrd.codpresup / ordenes.codpresup).'
    )
    # Carpeta plana a propósito (sin subcarpetas de año/mes): es la
    # MISMA carpeta ("presupuestos/", ver
    # services.CARPETA_PRESUPUESTOS) que lee
    # sincronizar_adjuntos_desde_carpetas() -- así, sin importar si el
    # PDF llegó por la API de FoxPro o se dejó ahí a mano, siempre
    # queda en un único lugar.
    archivo = models.FileField(upload_to='presupuestos/', storage=ALMACENAMIENTO_SOBRESCRIBIBLE)
    content_type = models.CharField(max_length=100, blank=True)
    tamano_bytes = models.PositiveIntegerField(default=0)
    fecha_carga = models.DateTimeField(auto_now_add=True)
    usuario = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = 'facturas_proveedor_presupuestos_adjuntos'
        verbose_name = 'Adjunto de presupuesto'
        verbose_name_plural = 'Adjuntos de presupuestos'

    def __str__(self):
        return f"Presupuesto {self.codpresup}"

    def save(self, *args, **kwargs):
        # A diferencia de FacturaAdjunto (que siempre se crea desde
        # registrar_factura(), ya con content_type/tamano_bytes
        # calculados), este modelo se sube directo desde /admin/, así
        # que se autocompletan aquí si el archivo cambió.
        if self.archivo and not self.content_type:
            self.content_type = (
                getattr(self.archivo.file, 'content_type', '')
                or mimetypes.guess_type(self.archivo.name)[0]
                or ''
            )
            self.tamano_bytes = self.archivo.size or 0
        super().save(*args, **kwargs)


class OrdenCompraAdjunto(models.Model):
    """Tabla NUEVA -- el PDF de la orden de compra (tal como lo emite
    el sistema legacy, hoy vive en Drive/OneDrive y se sube acá a
    mano), para poder incluirlo en el PDF final de una liquidación.
    Se identifica por `orden` (ordenes.orden) -- una orden, un PDF."""

    orden = models.CharField(
        max_length=20, unique=True, db_index=True,
        help_text='Número de orden de compra (ordenes.orden).'
    )
    # Carpeta plana a propósito, y con el mismo nombre ("OrdenesPdf/",
    # ver services.CARPETA_ORDENES_COMPRA) que lee
    # sincronizar_adjuntos_desde_carpetas() -- mismo criterio que
    # PresupuestoAdjunto.archivo, ver ese comentario.
    archivo = models.FileField(upload_to='OrdenesPdf/', storage=ALMACENAMIENTO_SOBRESCRIBIBLE)
    content_type = models.CharField(max_length=100, blank=True)
    tamano_bytes = models.PositiveIntegerField(default=0)
    fecha_carga = models.DateTimeField(auto_now_add=True)
    usuario = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        db_table = 'facturas_proveedor_ordenes_compra_adjuntos'
        verbose_name = 'Adjunto de orden de compra'
        verbose_name_plural = 'Adjuntos de órdenes de compra'

    def __str__(self):
        return f"Orden de compra {self.orden}"

    def save(self, *args, **kwargs):
        if self.archivo and not self.content_type:
            self.content_type = (
                getattr(self.archivo.file, 'content_type', '')
                or mimetypes.guess_type(self.archivo.name)[0]
                or ''
            )
            self.tamano_bytes = self.archivo.size or 0
        super().save(*args, **kwargs)


class FacturaCodificacion(models.Model):
    """Tabla NUEVA (propia de este sistema) que marca una factura ya
    registrada como "codificada" por contabilidad -- por ahora solo un
    estado de revisión (Sí/No), SIN cuenta contable ni póliza asociada
    (eso vive en el sistema contable actual, fuera del alcance de este
    prototipo). La existencia de un registro aquí = está codificada;
    borrarlo = se quita la codificación (por si contabilidad se
    equivoca)."""

    keyorden = models.IntegerField(
        unique=True, db_index=True,
        help_text="FK lógica hacia ordenesrd.keyorden (1 a 1: una factura, un estado de codificación)."
    )
    orden = models.CharField(max_length=20, db_index=True)
    numfactura = models.CharField(max_length=50)
    fecha_codificacion = models.DateTimeField(auto_now_add=True)
    usuario = models.CharField(max_length=50, blank=True, null=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        db_table = 'facturas_proveedor_codificacion'
        verbose_name = 'Codificación de factura de proveedor'
        verbose_name_plural = 'Codificaciones de facturas de proveedor'

    def __str__(self):
        return f"Codificada: {self.numfactura} - Orden {self.orden}"


class Liquidacion(models.Model):
    """Tabla NUEVA (propia de este sistema) -- una "liquidación de
    cliente" ligera: agrupa un conjunto de facturas ya ACEPTADAS por un
    criterio (presupuesto/cliente/marca/tipo de medio) y guarda el
    total. NO es la liquidación real del sistema contable (no genera
    póliza, no calcula retenciones IVA/ISR, no toca `liq_quedan` ni
    `liq_liquidaciones`) -- es solo un registro propio de este sistema
    para llevar control de qué se agrupó y cuándo."""

    CRITERIO_PRESUPUESTO = 'presupuesto'
    CRITERIO_CLIENTE = 'cliente'
    CRITERIO_MARCA = 'marca'
    CRITERIO_TIPOMEDIO = 'tipomedio'
    CRITERIO_CHOICES = [
        (CRITERIO_PRESUPUESTO, 'Presupuesto'),
        (CRITERIO_CLIENTE, 'Cliente'),
        (CRITERIO_MARCA, 'Marca'),
        (CRITERIO_TIPOMEDIO, 'Tipo de medio'),
    ]

    numero = models.AutoField(primary_key=True)
    criterio = models.CharField(max_length=20, choices=CRITERIO_CHOICES)
    valor_agrupador = models.CharField(
        max_length=50,
        help_text='El código agrupado (ej. el codpresup, codcli, codmar o codtipmed específico).'
    )
    codpai = models.CharField(max_length=10)
    codagencia = models.CharField(max_length=20)
    fecha_liquidacion = models.DateTimeField(auto_now_add=True)
    usuario = models.CharField(max_length=50, blank=True, null=True)
    total = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    observaciones = models.TextField(blank=True)

    # Anulación (mismo patrón que OrdenesRd.facanula): NO se borra el
    # registro -- se marca anulada, con motivo/usuario/fecha, para
    # conservar el rastro de auditoría. Una liquidación anulada libera
    # automáticamente sus facturas: facturas_aceptadas_por_liquidar()
    # deja de contarlas como "ya liquidadas", así que vuelven a
    # aparecer disponibles para armar una liquidación nueva.
    anulada = models.BooleanField(default=False)
    fecha_anula = models.DateTimeField(null=True, blank=True)
    usuario_anula = models.CharField(max_length=50, blank=True, null=True)
    motivo_anula = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'facturas_proveedor_liquidaciones'
        verbose_name = 'Liquidación de cliente'
        verbose_name_plural = 'Liquidaciones de clientes'

    def __str__(self):
        return f"Liquidación #{self.numero} ({self.get_criterio_display()}: {self.valor_agrupador})"


class LiquidacionDetalle(models.Model):
    """Cada factura incluida en una Liquidacion. Una vez que una factura
    (keyorden) aparece aquí, se considera "ya liquidada" y deja de
    aparecer como pendiente en pantallas futuras de liquidar clientes."""

    liquidacion = models.ForeignKey(Liquidacion, on_delete=models.CASCADE, related_name='detalles')
    keyorden = models.IntegerField(db_index=True)
    orden = models.CharField(max_length=20)
    numfactura = models.CharField(max_length=50)
    monto = models.DecimalField(max_digits=18, decimal_places=2)

    class Meta:
        db_table = 'facturas_proveedor_liquidaciones_detalle'
        verbose_name = 'Detalle de liquidación'
        verbose_name_plural = 'Detalles de liquidación'

    def __str__(self):
        return f"{self.numfactura} en liquidación #{self.liquidacion_id}"
