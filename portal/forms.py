from django import forms
from django.contrib.auth.forms import AuthenticationForm


class LoginProveedorForm(AuthenticationForm):
    """Login por código de acceso (en vez de correo electrónico). El
    código y la contraseña se crean directamente en /admin/ (ver
    ProveedorPerfilAdmin) -- no hay invitación ni registro propio."""
    username = forms.CharField(
        label='Código de proveedor',
        widget=forms.TextInput(attrs={'class': 'form-control', 'autofocus': True}),
    )
    password = forms.CharField(
        label='Contraseña',
        widget=forms.PasswordInput(attrs={'class': 'form-control'}),
    )


class BuscarOrdenPortalForm(forms.Form):
    """Igual que la búsqueda interna, pero SIN país/agencia (fijos) y
    sin selector de proveedor (el proveedor SIEMPRE es el del usuario
    autenticado -- eso se aplica en la vista, no aquí)."""
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


class FacturaProveedorPortalForm(forms.Form):
    """Versión simplificada para el proveedor: SOLO monto total. El IVA
    y el otro impuesto se calculan automáticamente (proporcional a
    orden.valiva / orden.valtp) -- el proveedor no los captura."""

    numfactura = forms.CharField(
        label='No. de tu factura',
        max_length=50,
        help_text='Alfanumérico, máximo 50 caracteres.',
        widget=forms.TextInput(attrs={'class': 'form-control'}),
    )
    fecfactura = forms.DateField(
        label='Fecha de la factura',
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
    )
    monto = forms.DecimalField(
        label='Monto TOTAL de la factura',
        max_digits=18, decimal_places=2, min_value=0,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
        help_text='El IVA y otros impuestos se calculan automáticamente según la orden de compra.',
    )
    archivo = forms.FileField(
        label='Adjuntar PDF o imagen de tu factura',
        required=False,
        widget=forms.ClearableFileInput(attrs={'class': 'form-control', 'accept': '.pdf,.jpg,.jpeg,.png'}),
    )
    observaciones = forms.CharField(
        label='Observaciones (opcional)',
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
    )

    def clean_archivo(self):
        archivo = self.cleaned_data.get('archivo')
        if archivo:
            if archivo.content_type not in ARCHIVOS_PERMITIDOS:
                raise forms.ValidationError('Solo se permiten archivos PDF, JPG o PNG.')
            if archivo.size > TAMANO_MAX_MB * 1024 * 1024:
                raise forms.ValidationError(f'El archivo supera el máximo de {TAMANO_MAX_MB} MB.')
        return archivo
