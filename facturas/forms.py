from django import forms
from django.utils import timezone

MESES = [
    (1, 'Enero'), (2, 'Febrero'), (3, 'Marzo'), (4, 'Abril'),
    (5, 'Mayo'), (6, 'Junio'), (7, 'Julio'), (8, 'Agosto'),
    (9, 'Septiembre'), (10, 'Octubre'), (11, 'Noviembre'), (12, 'Diciembre'),
]


class BuscarOrdenForm(forms.Form):
    """Solo pide el número de orden. País y agencia YA NO se piden en
    pantalla: se toman fijos de config.py (settings.FACTURAS_CODPAI_DEFAULT
    / FACTURAS_CODAGENCIA_DEFAULT) directamente en la vista."""

    orden = forms.CharField(
        label='Número de orden de compra',
        max_length=20,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Ej. 2020115636',
            'autofocus': True,
        }),
    )


ARCHIVOS_PERMITIDOS = ['application/pdf', 'image/jpeg', 'image/png', 'image/jpg']
TAMANO_MAX_MB = 10


class FacturaProveedorForm(forms.Form):
    """Datos que el usuario captura para registrar la factura del
    proveedor contra la orden de compra ya seleccionada."""

    numfactura = forms.CharField(
        label='No. de factura del proveedor',
        max_length=50,
        help_text='Alfanumérico, máximo 50 caracteres.',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    tipofac = forms.ChoiceField(
        label='Tipo de documento',
        choices=[('FC', 'Factura'), ('NC', 'Nota de crédito')],
        initial='FC',
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    fecfactura = forms.DateField(
        label='Fecha de la factura',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    )
    monto = forms.DecimalField(
        label='Monto de la factura (total)',
        max_digits=18, decimal_places=2, min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    )
    valiva = forms.DecimalField(
        label='IVA (opcional)',
        max_digits=18, decimal_places=2, min_value=0,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    )
    valtp = forms.DecimalField(
        label='Timbre de prensa / otro impuesto (opcional)',
        max_digits=18, decimal_places=2, min_value=0,
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
    )
    obsfactura = forms.CharField(
        label='Observaciones (opcional)',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
    )
    archivo = forms.FileField(
        label='Adjuntar PDF o imagen de la factura',
        required=False,
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}),
    )

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')
        if archivo:
            if archivo.content_type not in ARCHIVOS_PERMITIDOS:
                raise forms.ValidationError('Solo se permiten archivos PDF, JPG o PNG.')
            if archivo.size > TAMANO_MAX_MB * 1024 * 1024:
                raise forms.ValidationError(f'El archivo supera el máximo de {TAMANO_MAX_MB} MB.')
        return archivo


class ReemplazarAdjuntoForm(forms.Form):
    """Para cuando el usuario subió el archivo equivocado: reemplaza el
    adjunto de una factura ya registrada por uno nuevo (ver
    services.reemplazar_adjunto_factura). No toca ningún otro dato de
    la factura -- si lo que está mal es el monto/fecha/número, eso se
    corrige anulando la factura y registrando una nueva."""
    archivo = forms.FileField(
        label='Nuevo archivo (reemplaza el adjunto actual)',
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}),
    )

    def clean_archivo(self):
        archivo = self.cleaned_data['archivo']
        if archivo.content_type not in ARCHIVOS_PERMITIDOS:
            raise forms.ValidationError('Solo se permiten archivos PDF, JPG o PNG.')
        if archivo.size > TAMANO_MAX_MB * 1024 * 1024:
            raise forms.ValidationError(f'El archivo supera el máximo de {TAMANO_MAX_MB} MB.')
        return archivo


class MotivoAnulacionForm(forms.Form):
    """Se pide un motivo obligatorio para anular una factura ya
    registrada (queda guardado en ordenesrd.obsanula, junto con quién y
    cuándo la anuló)."""
    motivo = forms.CharField(
        label='Motivo de la anulación',
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        max_length=255,
    )


class FiltroFacturasRecibidasForm(forms.Form):
    """Filtro por mes/año para el listado de facturas ya registradas.
    Se filtra por `fecrecep` (fecha en que se recibió/registró la
    factura), no por `fecfactura` ni `mesfac`. Si prefieres filtrar por
    otra fecha, dime y cambio un solo lookup en la vista."""

    anio = forms.ChoiceField(label='Año', widget=forms.Select(attrs={'class': 'form-select'}))
    mes = forms.ChoiceField(
        label='Mes',
        choices=[('', 'Todos los meses')] + MESES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    codcli = forms.CharField(
        label='Cliente (código)',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej. 001'}),
    )
    codpresup = forms.CharField(
        label='Presupuesto',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej. SAG-2026-001'}),
    )
    estado_codificacion = forms.ChoiceField(
        label='Aceptación',
        choices=[('', 'Todas'), ('pendiente', 'Pendientes de aceptar'), ('codificada', 'Ya aceptadas')],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        anio_actual = timezone.localdate().year
        # Rango razonable para un prototipo: 5 años atrás hasta el año actual.
        anios = [(y, str(y)) for y in range(anio_actual, anio_actual - 6, -1)]
        self.fields['anio'].choices = anios
        if not self.is_bound:
            self.fields['anio'].initial = anio_actual
            self.fields['mes'].initial = timezone.localdate().month


class FiltroLiquidacionForm(forms.Form):
    """Filtro para la pantalla de 'Liquidar clientes': año/mes (mismo
    criterio de fecha que el listado, fecrecep), a qué se agrupa, y los
    mismos filtros opcionales de cliente/presupuesto."""

    anio = forms.ChoiceField(label='Año', widget=forms.Select(attrs={'class': 'form-select'}))
    mes = forms.ChoiceField(
        label='Mes',
        choices=[('', 'Todos los meses')] + MESES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    criterio = forms.ChoiceField(
        label='Agrupar por',
        choices=[
            ('presupuesto', 'Presupuesto'),
            ('cliente', 'Cliente'),
            ('marca', 'Marca'),
            ('tipomedio', 'Tipo de medio'),
        ],
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    codcli = forms.CharField(
        label='Cliente (código)',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej. 001'}),
    )
    codpresup = forms.CharField(
        label='Presupuesto',
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Ej. SAG-2026-001'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        anio_actual = timezone.localdate().year
        anios = [(y, str(y)) for y in range(anio_actual, anio_actual - 6, -1)]
        self.fields['anio'].choices = anios
        if not self.is_bound:
            self.fields['anio'].initial = anio_actual
            self.fields['mes'].initial = timezone.localdate().month
