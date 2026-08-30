"""
Filtro de formato de moneda: separador de MILES = coma (,), separador
DECIMAL = punto (.) -> ej. 1,234.56

Se hace con un filtro propio (en vez de {{ valor|floatformat:2 }} +
USE_THOUSAND_SEPARATOR de Django) porque el locale 'es-gt' de Django
normalmente formatea al revés (coma decimal, punto de miles). Así
garantizamos el formato exacto que pidió el negocio, sin importar el
LANGUAGE_CODE configurado.
"""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter(name='moneyfmt')
def moneyfmt(valor):
    """1234.5 -> '1,234.50'  |  None -> '0.00'  |  -50 -> '-50.00'"""
    if valor in (None, ''):
        valor = 0
    try:
        valor = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        return valor
    return f'{valor:,.2f}'
