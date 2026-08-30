from django.urls import path
from . import views

app_name = 'facturas'

urlpatterns = [
    path('', views.buscar_orden, name='buscar_orden'),
    path('seleccionar/', views.seleccionar_orden, name='seleccionar_orden'),
    path('ingresar/', views.ingresar_factura, name='ingresar_factura'),
    path('confirmacion/', views.confirmacion, name='confirmacion'),
    path('recibidas/', views.listado_facturas_recibidas, name='listado_facturas_recibidas'),
    path('anular/<int:keyorden>/', views.anular_factura, name='anular_factura'),
    path('revisar/<int:keyorden>/', views.revisar_factura, name='revisar_factura'),
    path('liquidar/', views.liquidar_clientes, name='liquidar_clientes'),
    path('liquidar/guardar/', views.guardar_liquidacion, name='guardar_liquidacion'),
    path('liquidaciones/', views.listado_liquidaciones, name='listado_liquidaciones'),
    path('liquidaciones/<int:numero>/', views.detalle_liquidacion, name='detalle_liquidacion'),
    path('liquidaciones/<int:numero>/pdf/', views.descargar_pdf_liquidacion, name='descargar_pdf_liquidacion'),
    path('liquidaciones/<int:numero>/anular/', views.anular_liquidacion, name='anular_liquidacion'),
]
