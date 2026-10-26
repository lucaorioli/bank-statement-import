# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.modules import neutralize
from odoo.tests import common, tagged


@tagged("post_install", "-at_install")
class TestNeutralize(common.TransactionCase):
    def test_neutralize_disables_providers(self):
        journal = self.env["account.journal"].create(
            {
                "name": "Bank",
                "type": "bank",
                "code": "BANK",
                "bank_statements_source": "online",
            }
        )
        provider = self.env["online.bank.statement.provider"].create(
            {"service": "dummy", "journal_id": journal.id}
        )
        self.assertTrue(provider.active)
        queries = neutralize.get_neutralization_queries(
            ["account_statement_import_online"]
        )
        for query in queries:
            self.env.cr.execute(query)
        provider.invalidate_recordset(["active"])
        self.assertFalse(provider.active)
