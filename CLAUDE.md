# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Django prototype for entering supplier invoices (`facturas de proveedor`) against
existing purchase orders (`ordenes` / `ordenesrd`) that live in a **legacy MySQL
database** the project does not own. Two Django apps share one settings/DB config:

- **`facturas/`** — internal staff-facing system (no login required, prototype-only).
- **`portal/`** — separate proveedor-facing portal (invitation-based signup, always
  requires a logged-in session), which reuses `facturas.services` for all the actual
  business logic rather than duplicating it.

Everything in Spanish (models, comments, templates, URLs) matches the legacy domain
language on purpose — keep new code consistent with that rather than translating.

## Commands

```bash
# Setup (venv already exists at .venv/ — activate it, don't recreate)
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

# Run
python manage.py runserver
python manage.py migrate          # only creates this project's OWN new tables (see below)
python manage.py createsuperuser  # required for /admin/ to create portal invitations

# Utility: dump the real column structure of accounting tables (pivot schema)
python manage.py inspeccionar_pivot
```

There is no test suite (`tests.py` does not exist in either app, despite some
README claims of "pruebas automatizadas" — treat those claims as aspirational, not
present). There is no linter/formatter config in the repo. Before assuming a
command like `pytest` or `manage.py test` works, check that it's actually wired up.

## Configuration — `config.py`, not `settings.py`

All environment-specific values (DB credentials, schema names, business defaults)
live in **`config.py`** at the repo root (next to `manage.py`), imported by
`proyecto_facturas/settings.py`. Edit `config.py`, never hardcode values in
`settings.py`. It currently holds plaintext DB credentials on purpose (documented
prototype shortcut — not yet moved to env vars/secrets).

Three MySQL schema names are configured independently and are assumed to be **on
the same MySQL server** (raw cross-schema SQL joins depend on this):
- `ESQUEMA_PIVOT_MEDIOS` — the Django `default` database (`ordenes`, `ordenesrd`, `tipmed`, `tsubmed`, `medios`, `circmae`).
- `ESQUEMA_PIVOT_COMSYS` — referenced as `esquema.tabla` inside raw SQL (`climae`, `marmae`, `prdmae`, `clicamae`, `ageperso`, `monmae`, `impmae`, `paimae`, `agemae`). Not modeled as Django models.
- `ESQUEMA_PIVOT_CONTABILIDAD` — the real accounting schema (`liq_quedan`, `liq_liquidaciones`, `transacciones`, `mnt_retenciones`, ...). **Read-only, and currently not even read** — this system deliberately does not write to it to avoid interfering with the real accounting process (polizas, retenciones). `inspeccionar_pivot` is the only thing that touches it, for exploration.

If schema names differ per environment, only `config.py` needs to change — both the
Django DB connection and the raw SQL's `.format(esquema_comsys=...)` pick it up
automatically.

## Data model: legacy tables vs. this project's own tables

This is the single most important thing to understand before touching `models.py`
in either app.

- **`managed = False` models** (`facturas.models.Ordenes`, `facturas.models.OrdenesRd`)
  mirror tables that already exist in the legacy DB. Django never creates, alters,
  or drops them via `migrate`. `OrdenesRd` *is* written to via the ORM (real
  `INSERT`/`UPDATE`), but its schema is defined by the legacy DB, not by Django
  migrations — column lengths/types here are best-effort guesses documented as
  assumptions in the model's docstring, not verified against a real `DESCRIBE`.
  `keyorden` is confirmed `AUTO_INCREMENT` in MySQL, so it's modeled as `AutoField`
  and never set manually.
- **Ordinary Django-managed models** are net-new tables this project owns and
  migrates normally: `FacturaAdjunto`, `FacturaCodificacion`, `Liquidacion`,
  `LiquidacionDetalle` (in `facturas`), and `ProveedorInvitacion`, `ProveedorPerfil`
  (in `portal`). These never touch the real accounting flow — e.g.
  "accepting"/"liquidating" an invoice here only writes to these tables, and
  explicitly does *not* generate a poliza or touch `liq_quedan`/`liq_liquidaciones`.

Because `ordenes`/`ordenesrd` are raw legacy tables, the purchase-order search
(`facturas/services.py::buscar_ordenes`) is **raw SQL** (`SQL_BUSCAR_ORDEN_TEMPLATE`),
not the ORM — it's a cross-schema query that the ORM can't express directly. The
schema name is interpolated via `.format()` (always from `config.py`, never from
user input — safe), while user-supplied search values go through parameterized
`%(...)s` placeholders (safe from injection). Everything downstream of the search
(saldo calculation, insert, list/filter) uses the ORM against `OrdenesRd`.

## Business logic flow (`facturas/services.py`)

This module is the core of the system; `views.py` in both apps is a thin layer
over it. Key functions, in the order a request typically flows through them:

1. `buscar_ordenes` — raw SQL search for a purchase order by number (+codpai/codagencia).
2. `calcular_saldo_orden` — sums existing (non-voided) `ordenesrd` rows for that
   order to compute remaining balance against `orden.totalorden`. Credit notes
   (`tipofac == 'NC'`) subtract; voided rows (`facanula == 'Si'`) are excluded.
3. `calcular_impuestos_proporcionales` — (portal only) prorates IVA/TP from the
   order's own IVA/TP ratio, since the supplier portal only captures a total amount.
4. `numfactura_ya_registrada` — no duplicate invoice numbers per supplier
   (identified by `codfacturar` — see the "assumption to confirm" note in the
   docstring; this is a business-rule guess, not confirmed).
5. `registrar_factura` — the actual `INSERT` into `ordenesrd` (`@transaction.atomic`,
   re-validates the duplicate-number check inside the transaction to close a race
   condition) plus saving the `FacturaAdjunto` file. NOT NULL legacy columns without
   a real value use documented defaults/sentinels (e.g. `FECHA_CENTINELA = 1900-01-01`
   for dateless NOT NULL columns).
6. `anular_factura` — soft-void only (`facanula='Si'`, `select_for_update`); never a
   hard delete, to preserve the audit trail. Voided invoices automatically drop out
   of balance calculations and listings.
7. `listar_facturas_recibidas` / `facturas_aceptadas_por_liquidar` /
   `guardar_liquidacion` — listing, filtering, and the "liquidar clientes" grouping
   flow. `guardar_liquidacion` recomputes the group server-side at save time rather
   than trusting what the browser sent, so a concurrent liquidation of the same
   group just finds nothing pending instead of double-booking.

All monetary values coming from the DB must be funneled through `_a_decimal()`
before arithmetic — several legacy columns are MySQL `double`, so the driver can
hand back Python `float` instead of `Decimal`, and `Decimal + float` raises.

## Portal security model (`portal/`)

The entire security guarantee of the supplier portal is one filter:
`portal/services.py::buscar_orden_para_proveedor` takes the same search results as
the internal system and keeps only rows where `codfacturar` matches the
authenticated supplier's own `ProveedorPerfil.codfacturar`. A supplier can never
choose or type their own `codfacturar` — it always comes from their account, set
once at invitation-activation time (`activar_invitacion`) and never editable
afterward. When adding portal views, always filter through this function (or the
same pattern) rather than calling `facturas_services.buscar_ordenes` directly.

Invitations are created manually via `/admin/` (no automated email send yet); the
link is `{PORTAL_BASE_URL}/portal/registro/<token>/`. `/facturas/...` has no login
by design (prototype); `/portal/...` always requires a supplier session.

## Custom template formatting

`facturas/templatetags/facturas_extras.py::moneyfmt` formats money as
`1,234.56` (comma thousands, dot decimal) independent of `LANGUAGE_CODE`
(`es-gt` would otherwise format the reverse way). Use this filter for any new
monetary display rather than Django's built-in `floatformat`/`intcomma`.

## Known prototype gaps (don't "fix" silently — flag to the user first)

- Plaintext DB credentials in `config.py`, default `SECRET_KEY`, `DEBUG=True`,
  `ALLOWED_HOSTS='*'` — documented as pre-production TODOs, not oversights.
- No `@login_required` on `facturas/` views (portal views do require auth).
- Several business rules are explicitly marked as unconfirmed assumptions in
  README.md and in code docstrings (e.g. what "proveedor" means for duplicate
  detection, exact `ordenesrd` column widths/types, whether `orden.totalorden` is
  really the right balance-cap field). Check the relevant docstring/README section
  before changing behavior that touches these.
