# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime
from unittest.mock import patch

from odoo.tools import mute_logger

from odoo.addons.base.tests.common import BaseCommon

MODEL = (
    "odoo.addons.account_statement_import_online_stripe.models"
    ".online_bank_statement_provider_stripe"
)
MODULE = f"{MODEL}.OnlineBankStatementProviderStripe"


def _transaction(tx_id, amount, currency, source):
    return {
        "id": tx_id,
        "amount": amount,
        "currency": currency,
        "created": 1678886400,
        "object": "balance_transaction",
        "fee": 0,
        "type": "charge",
        "source": source,
    }


TRANSACTIONS = [
    # A payment of 100.00 USD, settled as 92.00 EUR
    _transaction(
        "txn_usd",
        9200,
        "eur",
        {"id": "ch_1", "object": "charge", "amount": 10000, "currency": "usd"},
    ),
    # A payment of 1000 JPY captured only for 800 JPY, settled as 5.00 EUR
    _transaction(
        "txn_jpy",
        500,
        "eur",
        {
            "id": "ch_2",
            "object": "charge",
            "amount": 1000,
            "amount_captured": 800,
            "currency": "jpy",
        },
    ),
    # A refund of 20.00 USD, settled as -18.40 EUR
    _transaction(
        "txn_refund",
        -1840,
        "eur",
        {"id": "re_1", "object": "refund", "amount": 2000, "currency": "usd"},
    ),
    # A payment in EUR
    _transaction(
        "txn_eur",
        1000,
        "eur",
        {"id": "ch_3", "object": "charge", "amount": 1000, "currency": "eur"},
    ),
    # A transaction of the balance in GBP
    _transaction(
        "txn_gbp",
        700,
        "gbp",
        {"id": "ch_4", "object": "charge", "amount": 700, "currency": "gbp"},
    ),
]


class TestStripeCurrencies(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.ref("base.EUR").active = True
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Stripe EUR",
                "type": "bank",
                "code": "STRE",
                "bank_statements_source": "online",
                "currency_id": cls.env.ref("base.EUR").id,
            }
        )
        cls.provider = cls.env["online.bank.statement.provider"].create(
            {
                "service": "stripe",
                "password": "test_api_key",
                "journal_id": cls.journal.id,
                "stripe_foreign_currency": True,
            }
        )

    def _obtain_lines(self):
        with (
            patch(f"{MODULE}._stripe_api_get_all", return_value=TRANSACTIONS),
            mute_logger(MODEL),
        ):
            lines, __ = self.provider._obtain_statement_data(
                datetime(2023, 3, 15), datetime(2023, 3, 16)
            )
        return {line["unique_import_id"]: line for line in lines}

    def test_foreign_currency(self):
        lines = self._obtain_lines()
        self.assertEqual(lines["txn_usd"]["amount"], 92.0)
        self.assertEqual(
            lines["txn_usd"]["foreign_currency_id"], self.env.ref("base.USD").id
        )
        self.assertEqual(lines["txn_usd"]["amount_currency"], 100.0)
        self.assertEqual(
            lines["txn_jpy"]["foreign_currency_id"], self.env.ref("base.JPY").id
        )
        self.assertEqual(lines["txn_jpy"]["amount_currency"], 800.0)
        self.assertEqual(lines["txn_refund"]["amount_currency"], -20.0)
        self.assertNotIn("foreign_currency_id", lines["txn_eur"])

    def test_foreign_currency_disabled(self):
        self.provider.stripe_foreign_currency = False
        lines = self._obtain_lines()
        self.assertNotIn("foreign_currency_id", lines["txn_usd"])

    def test_other_balance_currency(self):
        lines = self._obtain_lines()
        self.assertNotIn("txn_gbp", lines)
        message = self.provider.message_ids[:1]
        self.assertIn("GBP", message.body)
        # The warning is not repeated at the next pull
        self._obtain_lines()
        self.assertEqual(self.provider.message_ids[:1], message)

    def test_other_balance_currency_with_provider(self):
        """No warning when a provider imports the balance in that currency"""
        self.env.ref("base.GBP").active = True
        journal_gbp = self.env["account.journal"].create(
            {
                "name": "Stripe GBP",
                "type": "bank",
                "code": "STRG",
                "bank_statements_source": "online",
                "currency_id": self.env.ref("base.GBP").id,
            }
        )
        self.env["online.bank.statement.provider"].create(
            {
                "service": "stripe",
                "password": "test_api_key",
                "journal_id": journal_gbp.id,
            }
        )
        messages = self.provider.message_ids
        self._obtain_lines()
        self.assertEqual(self.provider.message_ids, messages)

    def test_foreign_currency_statement_line(self):
        with patch(f"{MODULE}._stripe_api_get_all", return_value=TRANSACTIONS[:1]):
            self.provider._pull(datetime(2023, 3, 15), datetime(2023, 3, 16))
        line = self.env["account.bank.statement.line"].search(
            [("journal_id", "=", self.journal.id)]
        )
        self.assertEqual(line.foreign_currency_id, self.env.ref("base.USD"))
        self.assertEqual(line.amount_currency, 100.0)
