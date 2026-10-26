# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import re
from datetime import date, datetime, timezone

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

PLACEHOLDER = re.compile(r"\{([^{}]+)\}")

# Fields that the mapping cannot fill: they define the transaction and its
# journal entry, or they are written by the import itself.
PROTECTED_FIELDS = {
    "amount",
    "amount_currency",
    "company_id",
    "currency_id",
    "date",
    "foreign_currency_id",
    "journal_id",
    "move_id",
    "raw_data",
    "statement_id",
    "transaction_details",
    "unique_import_id",
}
# Fields that rewrite the journal entry of the line when they change: they are
# not written again on reconciled lines.
MOVE_FIELDS = {"partner_id", "payment_ref"}
SUPPORTED_TYPES = [
    "boolean",
    "char",
    "date",
    "datetime",
    "float",
    "html",
    "integer",
    "many2one",
    "monetary",
    "selection",
    "text",
]


class OnlineBankStatementProviderMapping(models.Model):
    _name = "online.bank.statement.provider.mapping"
    _description = "Online Bank Statement Provider Field Mapping"
    _order = "provider_id, sequence, id"

    provider_id = fields.Many2one(
        comodel_name="online.bank.statement.provider",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="provider_id.company_id", store=True)
    sequence = fields.Integer(default=10)
    expression = fields.Char(
        required=True,
        help="Value to write, from the raw data of the transaction: the path of "
        "a value in braces, like {payer.email}, possibly within some text, like "
        "Invoice {invoice.number}. Alternatives separated by | are tried in order "
        "until one gives a value, like {source.payment_intent} | {source.id}.",
    )
    field_id = fields.Many2one(
        comodel_name="ir.model.fields",
        string="Field",
        required=True,
        ondelete="cascade",
        domain=lambda self: [
            ("model", "=", "account.bank.statement.line"),
            ("ttype", "in", SUPPORTED_TYPES),
            ("name", "not in", list(PROTECTED_FIELDS)),
            ("readonly", "=", False),
        ],
    )

    @api.constrains("field_id")
    def _check_field_id(self):
        for mapping in self:
            field = mapping.field_id
            if (
                field.model != "account.bank.statement.line"
                or field.ttype not in SUPPORTED_TYPES
                or field.name in PROTECTED_FIELDS
            ):
                raise ValidationError(
                    self.env._(
                        "The field %(field)s cannot be filled by a mapping.",
                        field=field.field_description,
                    )
                )

    def _get_value(self, data):
        """Value of the mapping for the raw data of a transaction, or None"""
        self.ensure_one()
        for alternative in self.expression.split("|"):
            value = _evaluate(alternative.strip(), data)
            if value not in (None, "", [], {}):
                return self._convert(value)
        return None

    def _convert(self, value):
        """Convert a value of the raw data for the field of the mapping"""
        self.ensure_one()
        field = self.env["account.bank.statement.line"]._fields[self.field_id.name]
        try:
            return _CONVERTERS[field.type](self.env, field, value)
        except (TypeError, ValueError, OverflowError):
            _logger.debug(
                "Value %r not valid for the field %s", value, self.field_id.name
            )
            return None


def _evaluate(expression, data):
    """Evaluate an expression on the data.

    An expression made of a single placeholder gives the value as it is (a
    number, a date...); otherwise the placeholders are replaced in the text, and
    an expression whose placeholders are all empty gives None.
    """
    match = PLACEHOLDER.fullmatch(expression)
    if match:
        return _resolve(data, match.group(1))
    found = False

    def replace(placeholder):
        nonlocal found
        value = _resolve(data, placeholder.group(1))
        if value in (None, ""):
            return ""
        found = True
        return str(value)

    text = PLACEHOLDER.sub(replace, expression)
    if PLACEHOLDER.search(expression) and not found:
        return None
    return text


def _resolve(data, path):
    value = data
    for key in path.strip().split("."):
        if isinstance(value, list) and key.isdigit():
            index = int(key)
            value = value[index] if index < len(value) else None
        elif isinstance(value, dict):
            value = value.get(key)
        else:
            return None
        if value is None:
            return None
    return value


def _to_datetime(value):
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)
    value = str(value)
    if value.isdigit():
        return datetime.fromtimestamp(int(value), timezone.utc).replace(tzinfo=None)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _to_date(value):
    if isinstance(value, str) and len(value) == 10:
        return date.fromisoformat(value)
    return _to_datetime(value).date()


def _to_boolean(value):
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no")
    return bool(value)


def _to_many2one(env, field, value):
    comodel = env[field.comodel_name]
    if isinstance(value, int) and not isinstance(value, bool):
        return comodel.browse(value).exists().id or None
    found = comodel.name_search(str(value), operator="=ilike", limit=1)
    return found[0][0] if found else None


def _to_selection(env, field, value):
    keys = [key for key, __ in field._description_selection(env)]
    return value if value in keys else None


_CONVERTERS = {
    "boolean": lambda env, field, value: _to_boolean(value),
    "char": lambda env, field, value: str(value),
    "date": lambda env, field, value: _to_date(value),
    "datetime": lambda env, field, value: _to_datetime(value),
    "float": lambda env, field, value: float(value),
    "html": lambda env, field, value: str(value),
    "integer": lambda env, field, value: int(value),
    "many2one": _to_many2one,
    "monetary": lambda env, field, value: float(value),
    "selection": _to_selection,
    "text": lambda env, field, value: str(value),
}
