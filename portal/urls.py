from django.urls import path
from . import views

app_name = 'portal'

urlpatterns = [
    path('login/', views.login_proveedor, name='login'),
    path('logout/', views.logout_proveedor, name='logout'),
    path('', views.buscar_orden, name='buscar_orden'),
    path('mis-ordenes/', views.mis_ordenes, name='mis_ordenes'),
    path('mis-ordenes/<str:numero_orden>/subir/', views.seleccionar_orden, name='seleccionar_orden'),
    path('ingresar/', views.ingresar_factura, name='ingresar_factura'),
    path('confirmacion/', views.confirmacion, name='confirmacion'),
    path('mis-facturas/', views.mis_facturas, name='mis_facturas'),
    path('mis-facturas/<int:keyorden>/anular/', views.anular_factura, name='anular_factura'),
    path('mis-facturas/<int:keyorden>/subir-adjunto/', views.subir_adjunto, name='subir_adjunto'),
]
