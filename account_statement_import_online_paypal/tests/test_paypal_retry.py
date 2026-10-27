# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import io
import json
from email.message import Message
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from odoo.exceptions import UserError

from odoo.addons.base.tests.common import BaseCommon

MODULE = (
    "odoo.addons.account_statement_import_online_paypal.models"
    ".online_bank_statement_provider_paypal"
)
PROVIDER = f"{MODULE}.OnlineBankStatementProviderPayPal"
URL = "https://api.paypal.com/v1/reporting/transactions?page=1"


def _http_error(code, body=None, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    content = json.dumps(body or {}).encode("utf-8")
    return HTTPError(URL, code, "Error", headers, io.BytesIO(content))


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _response(data):
    return _Response(json.dumps(data).encode("utf-8"))


class TestPayPalRetry(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = cls.env["online.bank.statement.provider"]

    def _retrieve(self, responses):
        with (
            patch(f"{PROVIDER}._paypal_urlopen", side_effect=responses) as urlopen,
            patch(f"{MODULE}.time.sleep") as sleep,
        ):
            try:
                result = self.provider._paypal_retrieve(URL, "TOKEN")
            except UserError as error:
                result = error
        return (
            result,
            urlopen.call_count,
            [call.args[0] for call in sleep.call_args_list],
        )

    def test_retry_temporary_errors(self):
        result, calls, delays = self._retrieve(
            [
                _http_error(429),
                _http_error(503),
                URLError("Connection reset"),
                _response({"ok": True}),
            ]
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(calls, 4)
        # The delay doubles at each attempt
        self.assertEqual(delays, [1, 2, 4])

    def test_retry_after(self):
        result, __, delays = self._retrieve(
            [_http_error(429, retry_after=7), _response({"ok": True})]
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(delays, [7])

    def test_no_retry_on_client_errors(self):
        result, calls, delays = self._retrieve(
            [
                _http_error(
                    400,
                    {
                        "name": "INVALID_REQUEST",
                        "message": "Request is not well-formed",
                        "debug_id": "abc123",
                    },
                )
            ]
        )
        self.assertIsInstance(result, UserError)
        self.assertEqual(calls, 1)
        self.assertFalse(delays)
        # The debug id is in the message, to quote when asking PayPal for help
        self.assertIn("debug_id: abc123", str(result))

    def test_give_up(self):
        result, calls, delays = self._retrieve(
            [
                _http_error(500, {"name": "INTERNAL_SERVER_ERROR"}),
                _http_error(500, {"name": "INTERNAL_SERVER_ERROR"}),
                _http_error(500, {"name": "INTERNAL_SERVER_ERROR"}),
                _http_error(500, {"name": "INTERNAL_SERVER_ERROR"}),
            ]
        )
        self.assertIsInstance(result, UserError)
        self.assertIn("INTERNAL_SERVER_ERROR", str(result))
        self.assertEqual(calls, 4)
        self.assertEqual(len(delays), 3)
