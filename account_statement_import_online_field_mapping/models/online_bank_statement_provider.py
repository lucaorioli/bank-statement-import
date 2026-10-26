# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json

from odoo import fields, models


class OnlineBankStatementProvider(models.Model):
    _inherit = "online.bank.statement.provider"

    mapping_ids = fields.One2many(
        comodel_name="online.bank.statement.provider.mapping",
        inverse_name="provider_id",
        string="Field Mapping",
        copy=True,
    )

    def _get_statement_filtered_lines(
        self,
        unfiltered_lines,
        statement_values,
        statement_date_since,
        statement_date_until,
    ):
        lines = super()._get_statement_filtered_lines(
            unfiltered_lines,
            statement_values,
            statement_date_since,
            statement_date_until,
        )
        if self.mapping_ids:
            for line_values in lines:
                line_values.update(self._get_mapped_values(line_values.get("raw_data")))
        return lines

    def _get_mapped_values(self, raw_data, skip_fields=()):
        """Values of the mapped fields for the raw data of a transaction"""
        self.ensure_one()
        data = _load_raw_data(raw_data)
        if data is None:
            return {}
        values = {}
        for mapping in self.mapping_ids:
            name = mapping.field_id.name
            if name in values or name in skip_fields:
                continue
            value = mapping._get_value(data)
            if value is not None:
                values[name] = value
        return values

    def _refresh_raw_data(self, lines):
        """Hook to read again the raw data of the lines from the provider.

        A provider can override it for the data that change over time; by
        default the raw data stored on the lines are used as they are.
        """
        return lines


def _load_raw_data(raw_data):
    if isinstance(raw_data, dict | list):
        return raw_data
    if not isinstance(raw_data, str):
        return None
    try:
        return json.loads(raw_data)
    except ValueError:
        return None
