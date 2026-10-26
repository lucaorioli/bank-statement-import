# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, timezone
from unittest.mock import patch

from odoo.addons.base.tests.common import BaseCommon

MODULE = (
    "odoo.addons.account_statement_import_online_stripe.models"
    ".online_bank_statement_provider_stripe.OnlineBankStatementProviderStripe"
)


def _ts(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def _transaction(tx_id, created, amount, fee=0, currency="usd"):
    return {
        "id": tx_id,
        "amount": amount,
        "fee": fee,
        "net": amount - fee,
        "currency": currency,
        "created": created,
        "object": "balance_transaction",
        "type": "charge",
        "source": {"id": f"ch_{tx_id}", "object": "charge"},
    }


# Transactions of the account, from the oldest: the current balance is the
# sum of their net amounts, 100.00 + 50.00 - 1.50 - 20.00 + 10.00 = 138.50
TRANSACTIONS = [
    _transaction("t0", _ts(2023, 3, 14, 12), 10000),
    _transaction("t1", _ts(2023, 3, 15, 9), 5000, fee=150),
    _transaction("t2", _ts(2023, 3, 15, 18), -2000),
    _transaction("t3", _ts(2023, 3, 16, 10), 1000),
    _transaction("eur", _ts(2023, 3, 15, 10), 99999, currency="eur"),
]
BALANCE = {
    "available": [{"amount": 10000, "currency": "usd"}],
    "pending": [{"amount": 3850, "currency": "usd"}, {"amount": 5, "currency": "eur"}],
}


def _api_get_all(provider, path, params=None):
    params = dict(params or [])
    since = params.get("created[gte]", 0)
    until = params.get("created[lt]", float("inf"))
    return [tx for tx in TRANSACTIONS if since <= tx["created"] < until]


class TestStripeBalances(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Stripe",
                "type": "bank",
                "code": "STRP",
                "bank_statements_source": "online",
                "currency_id": cls.env.ref("base.USD").id,
            }
        )
        cls.provider = cls.env["online.bank.statement.provider"].create(
            {
                "service": "stripe",
                "password": "test_api_key",
                "journal_id": cls.journal.id,
                "stripe_statement_balances": True,
                "tz": "UTC",
            }
        )

    def _patches(self):
        return (
            patch(f"{MODULE}._stripe_api_get_all", _api_get_all),
            patch(f"{MODULE}._stripe_api_get", return_value=BALANCE),
        )

    def test_balances_of_a_period(self):
        get_all, get = self._patches()
        with get_all, get:
            __, statement_values = self.provider._obtain_statement_data(
                datetime(2023, 3, 15), datetime(2023, 3, 16)
            )
        # Before the 15th only t0; at its end t0, t1 and t2
        self.assertEqual(
            statement_values, {"balance_start": 100.0, "balance_end_real": 128.5}
        )

    def test_balances_of_the_statements(self):
        get_all, get = self._patches()
        with get_all, get as balance:
            self.provider._pull(datetime(2023, 3, 15), datetime(2023, 3, 17))
        statements = self.env["account.bank.statement"].search(
            [("journal_id", "=", self.journal.id)], order="name"
        )
        self.assertEqual(statements.mapped("balance_start"), [100.0, 128.5])
        self.assertEqual(statements.mapped("balance_end_real"), [128.5, 138.5])
        self.assertTrue(all(statements.mapped("is_valid")))
        # The balance is read once for the whole pull
        self.assertEqual(balance.call_count, 1)

    def test_balances_disabled(self):
        self.provider.stripe_statement_balances = False
        get_all, get = self._patches()
        with get_all, get as balance:
            __, statement_values = self.provider._obtain_statement_data(
                datetime(2023, 3, 15), datetime(2023, 3, 16)
            )
        self.assertEqual(statement_values, {})
        balance.assert_not_called()
