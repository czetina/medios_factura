from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password

from .models import ProveedorPerfil

User = get_user_model()


class ProveedorPerfilForm(forms.ModelForm):
    """Formulario de alta/edición para el modo de prueba del portal:
    en vez de invitación por correo, aquí mismo se define el código de
    acceso (username) y la contraseña del proveedor; al guardar se crea
    (o actualiza) el auth.User asociado de forma transparente."""

    codigo = forms.CharField(
        label='Código de acceso',
        max_length=150,
        help_text='Con este código (en vez de correo electrónico) el proveedor inicia sesión en el portal.',
        widget=forms.TextInput(attrs={'autofocus': True}),
    )
    clave = forms.CharField(
        label='Contraseña',
        required=False,
        widget=forms.PasswordInput(render_value=False),
        help_text='Al editar, déjalo en blanco para no cambiar la contraseña actual.',
    )

    class Meta:
        model = ProveedorPerfil
        fields = ['codfacturar', 'nombre_proveedor', 'activo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields['codigo'].initial = self.instance.user.username
        else:
            self.fields['clave'].required = True
            self.fields['clave'].help_text = 'Contraseña con la que el proveedor iniciará sesión.'

    def clean_codigo(self):
        codigo = self.cleaned_data['codigo'].strip()
        if not codigo:
            raise forms.ValidationError('El código no puede estar vacío.')
        qs = User.objects.filter(username=codigo)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.user_id)
        if qs.exists():
            raise forms.ValidationError('Ya existe un proveedor registrado con ese código.')
        return codigo

    def save(self, commit=True):
        perfil = super().save(commit=False)
        codigo = self.cleaned_data['codigo']
        clave = self.cleaned_data.get('clave')

        if perfil.pk and perfil.user_id:
            user = perfil.user
            user.username = codigo
            if clave:
                user.set_password(clave)
            user.save()
        else:
            user = User.objects.create(
                username=codigo,
                password=make_password(clave),
                is_staff=False,
                is_superuser=False,
            )
            perfil.user = user

        if commit:
            perfil.save()
        return perfil


@admin.register(ProveedorPerfil)
class ProveedorPerfilAdmin(admin.ModelAdmin):
    form = ProveedorPerfilForm
    fields = ('codigo', 'clave', 'codfacturar', 'nombre_proveedor', 'activo')
    list_display = ('codigo_acceso', 'codfacturar', 'nombre_proveedor', 'activo', 'fecha_registro')
    list_filter = ('activo',)
    search_fields = ('user__username', 'codfacturar', 'nombre_proveedor')

    def codigo_acceso(self, obj):
        return obj.user.username
    codigo_acceso.short_description = 'Código de acceso'
