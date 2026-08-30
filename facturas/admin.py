from django.contrib import admin
from .models import FacturaAdjunto, PresupuestoAdjunto, OrdenCompraAdjunto


@admin.register(FacturaAdjunto)
class FacturaAdjuntoAdmin(admin.ModelAdmin):
    list_display = ('numfactura', 'orden', 'keyorden', 'usuario', 'fecha_carga')
    search_fields = ('numfactura', 'orden', 'keyorden')
    list_filter = ('fecha_carga',)
    readonly_fields = ('fecha_carga', 'tamano_bytes', 'content_type')


@admin.register(PresupuestoAdjunto)
class PresupuestoAdjuntoAdmin(admin.ModelAdmin):
    """Alta manual del PDF de presupuesto (hoy vive en Drive/OneDrive;
    mientras no se conecte esa integración, se sube aquí a mano) para
    que quede disponible al generar el PDF de una liquidación."""
    list_display = ('codpresup', 'usuario', 'fecha_carga')
    search_fields = ('codpresup',)
    list_filter = ('fecha_carga',)
    readonly_fields = ('fecha_carga', 'tamano_bytes', 'content_type')


@admin.register(OrdenCompraAdjunto)
class OrdenCompraAdjuntoAdmin(admin.ModelAdmin):
    """Alta manual del PDF de la orden de compra (mismo caso que
    PresupuestoAdjunto: hoy vive en Drive/OneDrive, se sube aquí a
    mano mientras no se conecte esa integración)."""
    list_display = ('orden', 'usuario', 'fecha_carga')
    search_fields = ('orden',)
    list_filter = ('fecha_carga',)
    readonly_fields = ('fecha_carga', 'tamano_bytes', 'content_type')

# Nota: 'Ordenes' y 'OrdenesRd' (managed=False) no se registran aquí
# porque son tablas legacy que ya tienen su propio sistema de
# administración; esta app solo las lee/inserta, no las gestiona.
