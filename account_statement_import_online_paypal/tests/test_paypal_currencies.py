# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tools import mute_logger

from odoo.addons.base.tests.common import BaseCommon

MODULE = (
    "odoo.addons.account_statement_import_online_paypal.models"
    ".online_bank_statement_provider_paypal.OnlineBankStatementProviderPayPal"
)
# PayPal gives the transactions of the last three years only
DAY = datetime.combine(datetime.now().date(), datetime.min.time()) - timedelta(days=1)


class TestPayPalCurrencies(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eur = cls.env.ref("base.EUR")
        cls.usd = cls.env.ref("base.USD")
        cls.usd.active = True
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "PayPal EUR",
                "type": "bank",
                "code": "PPEUR",
                "bank_statements_source": "online",
                "currency_id": cls.eur.id,
            }
        )
        cls.provider = cls.env["online.bank.statement.provider"].create(
            {
                "service": "paypal",
                "username": "client_id",
                "password": "secret",
                "journal_id": cls.journal.id,
            }
        )

    def _warn(self, before, after):
        with patch(f"{MODULE}._paypal_get_balances", side_effect=[before, after]):
            self.provider._paypal_warn_other_currencies(
                "TOKEN", "EUR", DAY, DAY + timedelta(days=1)
            )

    def _messages(self):
        return self.provider.message_ids.filtered(
            lambda message: "were not imported" in (message.body or "")
        )

    def test_get_balances(self):
        response = {
            "balances": [
                {
                    "currency": "EUR",
                    "primary": True,
                    "available_balance": {"currency_code": "EUR", "value": "10.50"},
                },
                {
                    "currency": "USD",
                    "available_balance": {"currency_code": "USD", "value": "3.00"},
                },
                {"currency": "GBP"},
            ]
        }
        with patch(f"{MODULE}._paypal_retrieve", return_value=response) as retrieve:
            balances = self.provider._paypal_get_balances("TOKEN", DAY)
        self.assertEqual(balances, {"EUR": Decimal("10.50"), "USD": Decimal("3.00")})
        self.assertIn("/v1/reporting/balances?as_of_time=", retrieve.call_args[0][0])

    @mute_logger(
        "odoo.addons.account_statement_import_online_paypal.models."
        "online_bank_statement_provider_paypal"
    )
    def test_other_currency_changed(self):
        self._warn(
            {"EUR": Decimal(10), "USD": Decimal(0)},
            {"EUR": Decimal(20), "USD": Decimal(5)},
        )
        self.assertEqual(len(self._messages()), 1)
        self.assertIn("USD", self._messages().body)
        # Once, not at each pull
        self._warn({"USD": Decimal(5)}, {"USD": Decimal(8)})
        self.assertEqual(len(self._messages()), 1)

    def test_other_currency_unchanged(self):
        self._warn({"EUR": Decimal(10), "USD": Decimal(5)}, {"USD": Decimal(5)})
        self.assertFalse(self._messages())

    def test_other_currency_imported(self):
        """No warning when another provider imports that currency"""
        journal = self.env["account.journal"].create(
            {
                "name": "PayPal USD",
                "type": "bank",
                "code": "PPUSD",
                "bank_statements_source": "online",
                "currency_id": self.usd.id,
            }
        )
        self.env["online.bank.statement.provider"].create(
            {
                "service": "paypal",
                "username": "client_id",
                "password": "secret",
                "journal_id": journal.id,
            }
        )
        self._warn({"USD": Decimal(0)}, {"USD": Decimal(5)})
        self.assertFalse(self._messages())

    @mute_logger(
        "odoo.addons.account_statement_import_online_paypal.models."
        "online_bank_statement_provider_paypal"
    )
    def test_balances_not_available(self):
        """An error on the balances does not stop the pull"""
        with patch(
            f"{MODULE}._paypal_get_balances", side_effect=UserError("No balances")
        ):
            self.provider._paypal_warn_other_currencies(
                "TOKEN", "EUR", DAY, DAY + timedelta(days=1)
            )
        self.assertFalse(self._messages())

    def test_pull_warns(self):
        with (
            patch(f"{MODULE}._paypal_get_token", return_value="TOKEN"),
            patch(f"{MODULE}._paypal_get_transactions", return_value=[]),
            patch(f"{MODULE}._paypal_get_balance", return_value=Decimal(0)),
            patch(f"{MODULE}._paypal_warn_other_currencies") as warn,
        ):
            self.provider._obtain_statement_data(DAY, DAY + timedelta(days=1))
        warn.assert_called_once_with("TOKEN", "EUR", DAY, DAY + timedelta(days=1))
