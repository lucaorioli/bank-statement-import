# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models

from .online_bank_statement_provider_mapping import MOVE_FIELDS


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    def action_reapply_field_mapping(self):
        """Fill again the mapped fields from the raw data of the lines.

        On reconciled lines, the fields that would rewrite their journal entry
        are left as they are.
        """
        lines = self.filtered("raw_data")
        for provider in lines.journal_id.online_bank_statement_provider_id:
            provider_lines = lines.filtered(
                lambda line, provider=provider: line.journal_id == provider.journal_id
            )
            provider_lines = provider._refresh_raw_data(provider_lines)
            for line in provider_lines:
                values = provider._get_mapped_values(
                    line.raw_data,
                    skip_fields=MOVE_FIELDS if line.is_reconciled else (),
                )
                values = {
                    name: value
                    for name, value in values.items()
                    if line._fields[name].convert_to_write(value, line)
                    != line._fields[name].convert_to_write(line[name], line)
                }
                if values:
                    line.write(values)
        return True
