# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import date, datetime
from unittest.mock import patch

from odoo.addons.base.tests.common import BaseCommon

MODULE = (
    "odoo.addons.account_statement_import_online_stripe.models"
    ".online_bank_statement_provider_stripe.OnlineBankStatementProviderStripe"
)


def _transaction(source, **values):
    return {
        "id": "txn_1",
        "amount": 1000,
        "currency": "usd",
        "created": 1678886400,
        "object": "balance_transaction",
        "fee": 30,
        "type": "charge",
        "source": source,
        **values,
    }


class TestStripeLineDetails(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        journal = cls.env["account.journal"].create(
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
                "journal_id": journal.id,
            }
        )

    def _obtain_lines(self, transactions):
        with patch(f"{MODULE}._stripe_api_get_all", return_value=transactions):
            lines, __ = self.provider._obtain_statement_data(
                datetime(2023, 3, 15), datetime(2023, 3, 16)
            )
        return lines

    def test_charge_details(self):
        charge = {
            "id": "ch_1",
            "object": "charge",
            "livemode": True,
            "customer": "cus_1",
            "payment_intent": "pi_1",
            "billing_details": {
                "email": "jane@example.com",
                "phone": "+39 0541 000000",
                "address": {
                    "line1": "Via Roma 1",
                    "line2": "Scala B",
                    "postal_code": "47921",
                    "city": "Rimini",
                    "state": "RN",
                    "country": "it",
                },
            },
            "payment_method_details": {
                "type": "card",
                "card": {"last4": "4242", "exp_month": 2, "exp_year": 2028},
            },
        }
        payment_line, fee_line = self._obtain_lines(
            [_transaction(charge, subscription={"id": "sub_1"})]
        )
        self.assertEqual(payment_line["partner_email"], "jane@example.com")
        self.assertEqual(payment_line["partner_phone"], "+39 0541 000000")
        self.assertEqual(payment_line["partner_street"], "Via Roma 1, Scala B")
        self.assertEqual(payment_line["partner_zip"], "47921")
        self.assertEqual(payment_line["partner_city"], "Rimini")
        self.assertEqual(payment_line["partner_state_name"], "RN")
        self.assertEqual(payment_line["partner_country_id"], self.env.ref("base.it").id)
        self.assertEqual(payment_line["partner_external_ref"], "cus_1")
        self.assertEqual(payment_line["payment_method_type"], "card")
        self.assertEqual(payment_line["card_masked_number"], "**** 4242")
        self.assertEqual(payment_line["card_expiry_date"], date(2028, 2, 29))
        self.assertEqual(payment_line["subscription_ref"], "sub_1")
        self.assertEqual(
            payment_line["provider_url"], "https://dashboard.stripe.com/payments/pi_1"
        )
        self.assertNotIn("partner_email", fee_line)

    def test_payout_details(self):
        payout = {"id": "po_1", "object": "payout", "livemode": False}
        line = self._obtain_lines([_transaction(payout, fee=0)])[0]
        self.assertEqual(
            line["provider_url"], "https://dashboard.stripe.com/test/payouts/po_1"
        )
        self.assertNotIn("partner_email", line)

    def test_source_not_expanded(self):
        line = self._obtain_lines([_transaction("ch_1", fee=0)])[0]
        self.assertNotIn("provider_url", line)

    def test_details_on_statement_line(self):
        charge = {
            "id": "ch_1",
            "object": "charge",
            "billing_details": {"email": "jane@example.com"},
        }
        with patch(
            f"{MODULE}._stripe_api_get_all",
            return_value=[_transaction(charge, fee=0)],
        ):
            self.provider._pull(datetime(2023, 3, 15), datetime(2023, 3, 16))
        line = self.env["account.bank.statement.line"].search(
            [("journal_id", "=", self.provider.journal_id.id)]
        )
        self.assertEqual(line.partner_email, "jane@example.com")
        self.assertEqual(line.transaction_details["source"]["id"], "ch_1")
