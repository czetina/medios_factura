"""
Comando de utilería: muestra la estructura real (columnas, tipos,
nulabilidad, default) de las tablas contables mencionadas en el módulo
de liquidaciones (Sistema_de_liquidación.docx), dentro del esquema
`pivot` (configurable en config.py -> ESQUEMA_PIVOT_CONTABILIDAD).

Uso:
    python manage.py inspeccionar_pivot
    python manage.py inspeccionar_pivot --tablas liq_quedan transacciones

Corre esto localmente (donde SÍ hay conexión a tu MySQL real) y
pégame la salida -- así diseño la integración con la estructura real
en vez de adivinar columnas.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

# Tablas detectadas por nombre en el código VFP del documento que
# compartiste (liq_liquidaciones, liq_quedan, liq_ctas_fact,
# v_tsh_clientes, transacciones, mnt_retenciones). Si contabilidad usa
# otras tablas relacionadas, agrégalas con --tablas.
TABLAS_POR_DEFECTO = [
    'liq_quedan',
    'liq_liquidaciones',
    'liq_ctas_fact',
    'v_tsh_clientes',
    'transacciones',
    'mnt_retenciones',
]


class Command(BaseCommand):
    help = (
        "Muestra columnas/tipos de las tablas contables (esquema "
        "FACTURAS_ESQUEMA_PIVOT_CONTABILIDAD, definido en config.py) "
        "para diseñar la integración con datos reales en vez de adivinar."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--tablas', nargs='*', default=None,
            help='Nombres de tabla a inspeccionar (default: las detectadas en el código VFP).',
        )

    def handle(self, *args, **options):
        esquema = settings.FACTURAS_ESQUEMA_PIVOT_CONTABILIDAD
        tablas = options['tablas'] or TABLAS_POR_DEFECTO

        with connection.cursor() as cursor:
            for tabla in tablas:
                cursor.execute(
                    """
                    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT,
                           CHARACTER_MAXIMUM_LENGTH, COLUMN_KEY, EXTRA
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                    ORDER BY ORDINAL_POSITION
                    """,
                    [esquema, tabla],
                )
                filas = cursor.fetchall()

                self.stdout.write('')
                self.stdout.write(self.style.MIGRATE_HEADING(f'=== {esquema}.{tabla} ==='))

                if not filas:
                    self.stdout.write(self.style.WARNING(
                        '  (no existe en este esquema, o el usuario de BD no tiene visibilidad)'
                    ))
                    continue

                for col, dtype, nullable, default, maxlen, key, extra in filas:
                    partes = [f'{col:30}', f'{dtype:12}', f'null={nullable:3}']
                    if maxlen:
                        partes.append(f'len={maxlen}')
                    if key:
                        partes.append(f'key={key}')
                    if extra:
                        partes.append(f'extra={extra}')
                    partes.append(f'default={default!r}')
                    self.stdout.write('  ' + ' '.join(partes))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            'Copia toda esta salida y pégamela para diseñar la integración con la estructura real.'
        ))
