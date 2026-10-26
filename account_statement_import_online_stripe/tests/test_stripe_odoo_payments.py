# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime
from unittest.mock import patch

from odoo.addons.base.tests.common import BaseCommon

MODULE = (
    "odoo.addons.account_statement_import_online_stripe.models"
    ".online_bank_statement_provider_stripe.OnlineBankStatementProviderStripe"
)


def _transaction(tx_id, charge_id, payment_intent_id):
    return {
        "id": tx_id,
        "amount": 1000,
        "currency": "usd",
        "created": 1678886400,
        "object": "balance_transaction",
        "fee": 50,
        "type": "charge",
        "source": {
            "id": charge_id,
            "object": "charge",
            "payment_intent": payment_intent_id,
        },
    }


class TestStripeOdooPayments(BaseCommon):
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
        cls.partner = cls.env["res.partner"].create({"name": "Customer"})
        cls.provider = cls.env["online.bank.statement.provider"].create(
            {
                "service": "stripe",
                "password": "test_api_key",
                "journal_id": cls.journal.id,
                "stripe_skip_website_payments": True,
            }
        )
        cls.transactions = [
            _transaction("txn_1", "ch_1", "pi_1"),
            _transaction("txn_2", "ch_2", "pi_2"),
            _transaction("txn_3", "ch_3", "pi_3"),
        ]

    def _obtain_lines(self, odoo_payments, line_values):
        with (
            patch(f"{MODULE}._stripe_api_get_all", return_value=self.transactions),
            patch(f"{MODULE}._stripe_get_odoo_payments", return_value=odoo_payments),
            patch(
                f"{MODULE}._stripe_odoo_payment_line_values",
                side_effect=lambda payment: line_values[payment],
            ),
        ):
            lines, __ = self.provider._obtain_statement_data(
                datetime(2023, 3, 15), datetime(2023, 3, 16)
            )
        return {line["unique_import_id"]: line for line in lines}

    def test_payments_recorded_by_odoo(self):
        lines = self._obtain_lines(
            {"pi_1": "posted on the journal", "ch_2": "to reconcile"},
            {
                "posted on the journal": None,
                "to reconcile": {"partner_id": self.partner.id},
            },
        )
        # Already posted on the journal: only its fee is imported
        self.assertNotIn("txn_1", lines)
        self.assertIn("txn_1_fee", lines)
        # Recorded elsewhere: imported with the partner of the payment
        self.assertEqual(lines["txn_2"]["partner_id"], self.partner.id)
        self.assertNotIn("partner_id", lines["txn_2_fee"])
        # Not recorded by Odoo: imported as usual
        self.assertNotIn("partner_id", lines["txn_3"])

    def test_option_disabled(self):
        self.provider.stripe_skip_website_payments = False
        with (
            patch(f"{MODULE}._stripe_api_get_all", return_value=self.transactions),
            patch(f"{MODULE}._stripe_get_odoo_payments") as get_odoo_payments,
        ):
            lines, __ = self.provider._obtain_statement_data(
                datetime(2023, 3, 15), datetime(2023, 3, 16)
            )
        get_odoo_payments.assert_not_called()
        self.assertEqual(len(lines), 6)

    def test_odoo_payments_search(self):
        """With the Stripe payment provider, find the done transactions"""
        if "payment.transaction" not in self.env:
            self.skipTest("The Payment module is not installed")
        stripe = self.env["payment.provider"].search([("code", "=", "stripe")], limit=1)
        if not stripe:
            self.skipTest("The Stripe payment provider is not installed")
        method = (
            self.env["payment.method"]
            .with_context(active_test=False)
            .search([("code", "=", "card")], limit=1)
        )
        values = {
            "provider_id": stripe.id,
            "payment_method_id": method.id,
            "amount": 10.0,
            "currency_id": self.env.ref("base.USD").id,
            "partner_id": self.partner.id,
        }
        done, pending = self.env["payment.transaction"].create(
            [
                dict(values, reference="T1", provider_reference="pi_1", state="done"),
                dict(values, reference="T2", provider_reference="pi_2"),
            ]
        )
        odoo_payments = self.provider._stripe_get_odoo_payments(self.transactions)
        self.assertEqual(odoo_payments, {"pi_1": done})
        # Without a payment posted on the journal, the line gets the partner
        self.assertEqual(
            self.provider._stripe_odoo_payment_line_values(done),
            {"partner_id": self.partner.id},
        )
        if "payment_id" not in done._fields:
            return
        # Payment posted directly on the account of the journal: line skipped
        method_line = self.journal.inbound_payment_method_line_ids[:1]
        method_line.payment_account_id = self.journal.default_account_id
        payment = self.env["account.payment"].create(
            {
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": self.partner.id,
                "amount": 10.0,
                "journal_id": self.journal.id,
                "payment_method_line_id": method_line.id,
            }
        )
        payment.action_post()
        done.payment_id = payment
        self.assertIsNone(self.provider._stripe_odoo_payment_line_values(done))
