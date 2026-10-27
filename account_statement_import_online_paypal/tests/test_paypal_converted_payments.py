# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from datetime import datetime, timedelta
from unittest.mock import patch

from odoo.addons.base.tests.common import BaseCommon

# PayPal gives the transactions of the last three years only
DAY = datetime.combine(datetime.now().date(), datetime.min.time()) - timedelta(days=1)
DATE = DAY + timedelta(hours=10)
TIMESTAMP = int(DATE.timestamp())

MODULE = (
    "odoo.addons.account_statement_import_online_paypal.models"
    ".online_bank_statement_provider_paypal.OnlineBankStatementProviderPayPal"
)


def _transaction(transaction_id, event_code, currency, amount, **info):
    values = {
        "transaction_id": transaction_id,
        "transaction_event_code": event_code,
        "transaction_updated_date": DATE,
        "transaction_amount": {"currency_code": currency, "value": amount},
        "available_balance": {"currency_code": currency, "value": "100.00"},
    }
    values.update(info)
    return {
        "transaction_info": values,
        "payer_info": {"address_status": "N", "payer_name": {}},
        "cart_info": {},
    }


def _conversion(transaction_id, currency, amount, payment_id):
    return _transaction(
        transaction_id,
        "T0200",
        currency,
        amount,
        paypal_reference_id=payment_id,
        paypal_reference_id_type="TXN",
        instrument_type="PayPal",
    )


def _payment(transaction_id, currency, amount, fee):
    payment = _transaction(
        transaction_id,
        "T0006",
        currency,
        amount,
        fee_amount={"currency_code": currency, "value": fee},
        transaction_subject="Donation",
        paypal_account_id="PAYER1",
        instrument_type="Credit Card",
    )
    payment["payer_info"] = {
        "account_id": "PAYER1",
        "email_address": "payer@example.com",
        "payer_name": {"alternate_full_name": "John Doe"},
    }
    return payment


class TestPayPalConvertedPayments(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.usd = cls.env.ref("base.USD")
        cls.eur = cls.env.ref("base.EUR")
        (cls.usd | cls.eur).active = True
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "PayPal",
                "type": "bank",
                "code": "PAYP",
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

    def _obtain_lines(self, journal_transactions, all_transactions):
        def get_transactions(token, currency, since, until):
            return journal_transactions if currency else all_transactions

        with (
            patch(f"{MODULE}._paypal_get_token", return_value="TOKEN"),
            patch(
                f"{MODULE}._paypal_get_transactions", side_effect=get_transactions
            ) as get_transactions_mock,
            patch(
                f"{MODULE}._paypal_get_transaction",
                return_value=journal_transactions[0],
            ),
        ):
            lines, __ = self.provider._obtain_statement_data(
                DAY, DAY + timedelta(days=1)
            )
        currencies = [call.args[1] for call in get_transactions_mock.call_args_list]
        return {line["unique_import_id"]: line for line in lines}, currencies

    def test_converted_payment(self):
        """The conversion of a payment in USD gets the payer and the USD amount"""
        payment = _payment("PAYMENT1", "USD", "25.00", "-0.60")
        journal_transactions = [_conversion("CONV1-EUR", "EUR", "21.13", "PAYMENT1")]
        all_transactions = [
            payment,
            _conversion("CONV1-USD", "USD", "-24.40", "PAYMENT1"),
            _conversion("CONV1-EUR", "EUR", "21.13", "PAYMENT1"),
        ]
        lines, currencies = self._obtain_lines(journal_transactions, all_transactions)
        self.assertEqual(currencies, ["EUR", None])
        line = lines[f"CONV1-EUR-{TIMESTAMP}"]
        self.assertEqual(line["amount"], "21.13")
        self.assertEqual(line["partner_name"], "John Doe")
        self.assertEqual(line["ref"], "Donation")
        self.assertTrue(line["payment_ref"].startswith("CONV1-EUR: Donation"))
        self.assertEqual(line["foreign_currency_id"], self.usd.id)
        # Net of the fee, taken in USD before the conversion
        self.assertEqual(line["amount_currency"], "24.40")
        info = journal_transactions[0]["transaction_info"]
        self.assertEqual(info["paypal_account_id"], "PAYER1")
        self.assertEqual(info["instrument_type"], "Credit Card")
        self.assertEqual(
            journal_transactions[0]["payer_info"]["email_address"],
            "payer@example.com",
        )
        self.assertIs(journal_transactions[0]["converted_transaction"], payment)
        # The conversion has no fee of its own
        self.assertEqual(len(lines), 1)

    def test_converted_refund(self):
        """A refund in USD converted from EUR gets a negative USD amount"""
        refund = _payment("REFUND1", "USD", "-10.00", "0.00")
        journal_transactions = [_conversion("CONV2-EUR", "EUR", "-8.66", "REFUND1")]
        lines, __ = self._obtain_lines(
            journal_transactions, [refund] + journal_transactions
        )
        line = lines[f"CONV2-EUR-{TIMESTAMP}"]
        self.assertEqual(line["foreign_currency_id"], self.usd.id)
        self.assertEqual(line["amount_currency"], "-10.00")

    def test_without_conversions(self):
        """Without conversions, the transactions of other currencies are not read"""
        journal_transactions = [_payment("PAYMENT2", "EUR", "10.00", "-0.50")]
        lines, currencies = self._obtain_lines(journal_transactions, [])
        self.assertEqual(currencies, ["EUR"])
        line = lines[f"PAYMENT2-{TIMESTAMP}"]
        self.assertNotIn("foreign_currency_id", line)
        self.assertEqual(line["partner_name"], "John Doe")

    def test_payment_not_found(self):
        """A conversion whose payment is not found is imported as before"""
        journal_transactions = [_conversion("CONV3-EUR", "EUR", "5.00", "MISSING")]
        lines, __ = self._obtain_lines(journal_transactions, journal_transactions)
        line = lines[f"CONV3-EUR-{TIMESTAMP}"]
        self.assertNotIn("foreign_currency_id", line)
        self.assertNotIn("partner_name", line)
        self.assertEqual(line["payment_ref"], "CONV3-EUR")

    def test_statement_line_in_foreign_currency(self):
        """The line of the conversion is created with its USD amount"""
        journal_transactions = [_conversion("CONV4-EUR", "EUR", "21.13", "PAYMENT4")]
        all_transactions = [
            _payment("PAYMENT4", "USD", "25.00", "-0.60")
        ] + journal_transactions
        lines, __ = self._obtain_lines(journal_transactions, all_transactions)
        values = dict(lines[f"CONV4-EUR-{TIMESTAMP}"])
        values.pop("raw_data")
        line = self.env["account.bank.statement.line"].create(
            dict(values, journal_id=self.journal.id)
        )
        self.assertEqual(line.foreign_currency_id, self.usd)
        self.assertAlmostEqual(line.amount_currency, 24.40)
        self.assertAlmostEqual(line.amount, 21.13)
