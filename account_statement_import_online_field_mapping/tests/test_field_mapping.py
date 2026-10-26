# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
from datetime import date, datetime

from odoo.exceptions import ValidationError

from odoo.addons.base.tests.common import BaseCommon

RAW_DATA = {
    "id": "txn_1",
    "created": 1678886400,
    "source": {
        "id": "ch_1",
        "payment_intent": None,
        "billing_details": {
            "email": "jane@example.com",
            "address": {"country": "IT"},
        },
    },
    "invoice": {"number": "INV-1"},
    "items": [{"name": "First"}, {"name": "Second"}],
    "refunded": False,
}


class TestFieldMapping(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Bank",
                "type": "bank",
                "code": "MAPB",
                "bank_statements_source": "online",
            }
        )
        cls.provider = cls.env["online.bank.statement.provider"].create(
            {"service": "dummy", "journal_id": cls.journal.id, "tz": "UTC"}
        )

    @classmethod
    def _field(cls, name):
        return cls.env["ir.model.fields"]._get("account.bank.statement.line", name)

    def _map(self, *mappings):
        self.provider.mapping_ids = [
            (0, 0, {"expression": expression, "field_id": self._field(name).id})
            for expression, name in mappings
        ]

    def _import(self, raw_data=None, unique_import_id="txn_1"):
        raw_data = RAW_DATA if raw_data is None else raw_data
        self.provider._create_or_update_statement(
            (
                [
                    {
                        "date": datetime(2023, 3, 15, 10),
                        "amount": 10.0,
                        "payment_ref": "payment",
                        "unique_import_id": unique_import_id,
                        "raw_data": json.dumps(raw_data)
                        if isinstance(raw_data, dict)
                        else raw_data,
                    }
                ],
                {},
            ),
            datetime(2023, 3, 15),
            datetime(2023, 3, 16),
        )
        return self.env["account.bank.statement.line"].search(
            [("journal_id", "=", self.journal.id)], order="id desc", limit=1
        )

    def test_mapping(self):
        self._map(
            ("{source.billing_details.email}", "partner_email"),
            ("{source.billing_details.address.country}", "partner_country_id"),
            ("Invoice {invoice.number}", "ref"),
            ("{source.payment_intent} | {source.id}", "partner_external_ref"),
            ("{created}", "card_expiry_date"),
            ("{items.1.name}", "narration"),
        )
        line = self._import()
        self.assertEqual(line.partner_email, "jane@example.com")
        self.assertEqual(line.partner_country_id, self.env.ref("base.it"))
        self.assertEqual(line.ref, "Invoice INV-1")
        self.assertEqual(line.partner_external_ref, "ch_1")
        self.assertEqual(line.card_expiry_date, date(2023, 3, 15))
        self.assertIn("Second", line.narration)

    def test_mapping_missing_values(self):
        """Missing or invalid values leave the field empty"""
        self._map(
            ("Invoice {missing.number}", "ref"),
            ("{source.billing_details.email}", "card_expiry_date"),
            ("{source.billing_details.address.country}", "partner_email"),
        )
        line = self._import()
        self.assertFalse(line.ref)
        self.assertFalse(line.card_expiry_date)
        self.assertEqual(line.partner_email, "IT")

    def test_mapping_first_value_wins(self):
        self._map(
            ("{source.missing}", "partner_email"),
            ("{source.billing_details.email}", "partner_email"),
            ("other@example.com", "partner_email"),
        )
        self.assertEqual(self._import().partner_email, "jane@example.com")

    def test_raw_data_not_json(self):
        """Raw data that is not JSON is ignored"""
        self._map(("{id}", "partner_external_ref"))
        line = self._import(raw_data="{'id': 'txn_1'}")
        self.assertFalse(line.partner_external_ref)

    def test_protected_field(self):
        with self.assertRaises(ValidationError):
            self._map(("{created}", "amount"))

    def test_reapply_mapping(self):
        line = self._import()
        self.assertFalse(line.partner_email)
        self._map(
            ("{source.billing_details.email}", "partner_email"),
            ("Paid {invoice.number}", "payment_ref"),
        )
        line.action_reapply_field_mapping()
        self.assertEqual(line.partner_email, "jane@example.com")
        self.assertEqual(line.payment_ref, "Paid INV-1")

    def test_reapply_mapping_reconciled_line(self):
        """The label of a reconciled line is not rewritten"""
        line = self._import()
        self._map(
            ("{source.billing_details.email}", "partner_email"),
            ("Paid {invoice.number}", "payment_ref"),
        )
        line.is_reconciled = True
        line.action_reapply_field_mapping()
        self.assertEqual(line.partner_email, "jane@example.com")
        self.assertEqual(line.payment_ref, "payment")
