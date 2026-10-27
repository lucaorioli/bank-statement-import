# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.addons.base.tests.common import BaseCommon

from ..models.online_bank_statement_provider_paypal import _paypal_payment_keys

# PayPal gives the transactions of the last three years only
DAY = datetime.combine(datetime.now().date(), datetime.min.time()) - timedelta(days=1)

MODULE = (
    "odoo.addons.account_statement_import_online_paypal.models"
    ".online_bank_statement_provider_paypal.OnlineBankStatementProviderPayPal"
)


def _transaction(transaction_id, order_id=None):
    info = {
        "transaction_id": transaction_id,
        "transaction_event_code": "T0006",
        "transaction_updated_date": DAY + timedelta(hours=10),
        "transaction_amount": {"currency_code": "USD", "value": "10.00"},
        "fee_amount": {"currency_code": "USD", "value": "-0.50"},
        "available_balance": {"currency_code": "USD", "value": "100.00"},
        "transaction_subject": "Order",
    }
    if order_id:
        info["paypal_reference_id"] = order_id
        info["paypal_reference_id_type"] = "ODR"
    return {"transaction_info": info, "payer_info": {}, "cart_info": {}}


class TestPayPalOdooPayments(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "PayPal",
                "type": "bank",
                "code": "PAYP",
                "bank_statements_source": "online",
                "currency_id": cls.env.ref("base.USD").id,
            }
        )
        cls.partner = cls.env["res.partner"].create({"name": "Customer"})
        cls.provider = cls.env["online.bank.statement.provider"].create(
            {
                "service": "paypal",
                "username": "client_id",
                "password": "secret",
                "journal_id": cls.journal.id,
                "paypal_skip_website_payments": True,
            }
        )
        cls.transactions = [
            _transaction("CAPTURE1"),
            _transaction("CAPTURE2", order_id="ORDER2"),
            _transaction("CAPTURE3"),
        ]

    def _obtain_lines(self, odoo_payments=None, line_values=None):
        with (
            patch(f"{MODULE}._paypal_get_token", return_value="TOKEN"),
            patch(f"{MODULE}._paypal_get_transactions", return_value=self.transactions),
            patch(
                f"{MODULE}._paypal_get_transaction",
                return_value=self.transactions[0],
            ),
            patch(
                f"{MODULE}._paypal_get_odoo_payments", return_value=odoo_payments or {}
            ) as get_odoo_payments,
            patch(
                f"{MODULE}._paypal_odoo_payment_line_values",
                side_effect=lambda payment: line_values[payment],
            ),
        ):
            lines, __ = self.provider._obtain_statement_data(
                DAY, DAY + timedelta(days=1)
            )
        return {line["unique_import_id"]: line for line in lines}, get_odoo_payments

    def test_payment_keys(self):
        self.assertEqual(_paypal_payment_keys(self.transactions[0]), {"CAPTURE1"})
        self.assertEqual(
            _paypal_payment_keys(self.transactions[1]), {"CAPTURE2", "ORDER2"}
        )
        # A refund refers to the payment it refunds: not a key of the refund
        refund = _transaction("REFUND1")
        refund["transaction_info"].update(
            paypal_reference_id="CAPTURE1", paypal_reference_id_type="TXN"
        )
        self.assertEqual(_paypal_payment_keys(refund), {"REFUND1"})

    def test_payments_recorded_by_odoo(self):
        lines, __ = self._obtain_lines(
            {"CAPTURE1": "posted on the journal", "ORDER2": "to reconcile"},
            {
                "posted on the journal": None,
                "to reconcile": {"partner_id": self.partner.id},
            },
        )
        timestamp = int((DAY + timedelta(hours=10)).timestamp())
        # Already posted on the journal: only its fee is imported
        self.assertNotIn(f"CAPTURE1-{timestamp}", lines)
        self.assertIn(f"CAPTURE1-{timestamp}-FEE", lines)
        # Recorded elsewhere (found by its order): imported with the partner
        self.assertEqual(lines[f"CAPTURE2-{timestamp}"]["partner_id"], self.partner.id)
        self.assertNotIn("partner_id", lines[f"CAPTURE2-{timestamp}-FEE"])
        # Not recorded by Odoo: imported as usual
        self.assertNotIn("partner_id", lines[f"CAPTURE3-{timestamp}"])

    def test_option_disabled(self):
        self.provider.paypal_skip_website_payments = False
        lines, get_odoo_payments = self._obtain_lines()
        get_odoo_payments.assert_not_called()
        self.assertEqual(len(lines), 6)

    def test_odoo_payments_search(self):
        """With the PayPal payment provider, find the done transactions"""
        if "payment.transaction" not in self.env:
            self.skipTest("The Payment module is not installed")
        paypal = self.env["payment.provider"].search([("code", "=", "paypal")], limit=1)
        if not paypal:
            self.skipTest("The PayPal payment provider is not installed")
        method = (
            self.env["payment.method"]
            .with_context(active_test=False)
            .search([("code", "=", "paypal")], limit=1)
        )
        values = {
            "provider_id": paypal.id,
            "payment_method_id": method.id,
            "amount": 10.0,
            "currency_id": self.env.ref("base.USD").id,
            "partner_id": self.partner.id,
        }
        done, __ = self.env["payment.transaction"].create(
            [
                dict(values, reference="T1", provider_reference="ORDER2", state="done"),
                dict(values, reference="T2", provider_reference="CAPTURE3"),
            ]
        )
        odoo_payments = self.provider._paypal_get_odoo_payments(self.transactions)
        self.assertEqual(odoo_payments, {"ORDER2": done})
        # Without a payment posted on the journal, the line gets the partner
        self.assertEqual(
            self.provider._paypal_odoo_payment_line_values(done),
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
        self.assertIsNone(self.provider._paypal_odoo_payment_line_values(done))
