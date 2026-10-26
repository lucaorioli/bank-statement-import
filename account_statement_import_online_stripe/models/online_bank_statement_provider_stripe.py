# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging
import re
import time
import urllib.request
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

STRIPE_API_BASE = "https://api.stripe.com/v1"
# Retries of a request that failed for a temporary reason (rate limit,
# conflict, server error, network): the delay doubles at each attempt
# unless Stripe tells how long to wait.
STRIPE_MAX_RETRIES = 3
STRIPE_RETRY_DELAY = 1
STRIPE_MAX_RETRY_DELAY = 30


class OnlineBankStatementProviderStripe(models.Model):
    _inherit = "online.bank.statement.provider"

    stripe_label = fields.Char(
        default=(
            "stripe {source.metadata.invoice_number} {source.object} "
            "{source.id} {source.payment_method_details.type}"
        )
    )
    stripe_note = fields.Char()
    stripe_reference = fields.Char(default="{source.metadata.invoice_number}")
    stripe_fee_label = fields.Char(
        default=(
            "stripe fee {source.metadata.invoice_number} {source.object} "
            "{source.id} {source.payment_method_details.type}"
        )
    )
    stripe_fee_note = fields.Char()
    stripe_fee_reference = fields.Char()
    stripe_expand = fields.Char(default="data.source")
    stripe_fetch_billing = fields.Boolean(
        string="Fetch Billing Data",
        help="Also fetch the customer, the invoice and the subscription of each "
        "payment, so that they can be used in the labels, for instance "
        "{customer.email}, {invoice.number} or {subscription.id}, and they are "
        "kept in the raw data of the line. It needs more requests to Stripe, and "
        "the source of the transactions must be expanded (data.source).",
    )
    stripe_fee_as_payment = fields.Boolean(
        string="Fees as Payments to Stripe",
        help="Set the partner below on the fee lines, so that a reconciliation "
        "model can reconcile them with the payable account of the partner, as "
        "payments of the invoices Stripe sends for its fees.",
    )
    stripe_fee_partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Fees Partner",
        help="Partner set on the fee lines when they are recorded as payments "
        "to Stripe.",
    )

    @api.constrains("service", "stripe_fee_as_payment", "stripe_fee_partner_id")
    def _check_stripe_fee_partner(self):
        for provider in self:
            if (
                provider.service == "stripe"
                and provider.stripe_fee_as_payment
                and not provider.stripe_fee_partner_id
            ):
                raise ValidationError(
                    self.env._(
                        "Set the partner of the fees to record them as payments."
                    )
                )
    stripe_skip_website_payments = fields.Boolean(
        string="Payments Already Recorded by Odoo",
        help="Payments made through the Stripe payment provider of Odoo (for "
        "instance on the website) are already recorded by Odoo. When their "
        "payment is posted on the account of this journal, the line of the "
        "payment is not imported again (its fee is); otherwise the line gets the "
        "partner of the payment, ready to be reconciled. It needs the Payment "
        "module.",
    )
    stripe_payment_installed = fields.Boolean(
        compute="_compute_stripe_payment_installed"
    )

    def _compute_stripe_payment_installed(self):
        installed = "payment.transaction" in self.env
        for provider in self:
            provider.stripe_payment_installed = installed

    @api.model
    def _get_available_services(self):
        return super()._get_available_services() + [
            ("stripe", "Stripe"),
        ]

    def _obtain_statement_data(self, date_since, date_until):
        self.ensure_one()
        if self.service != "stripe":
            return super()._obtain_statement_data(
                date_since,
                date_until,
            )  # pragma: no cover
        currency = self.currency_id or self.company_id.currency_id
        if date_since.tzinfo:
            date_since = date_since.astimezone(pytz.utc).replace(tzinfo=None)
        if date_until.tzinfo:
            date_until = date_until.astimezone(pytz.utc).replace(tzinfo=None)
        date_since = int(date_since.timestamp())
        date_until = int(date_until.timestamp())
        params = [
            ("created[gte]", date_since),
            ("created[lt]", date_until),
        ]
        for s in self.stripe_expand.split(","):
            s = s.strip()
            if not s:
                continue
            params.append(("expand[]", s))
        fee_values = self._stripe_fee_line_values()
        lines = []
        cache = {}
        transactions = list(self._stripe_api_get_all("/balance_transactions", params))
        odoo_payments = {}
        if self.stripe_skip_website_payments:
            odoo_payments = self._stripe_get_odoo_payments(transactions)
        for tx in transactions:
            if tx["currency"].lower() != currency.name.lower():
                continue
            if self.stripe_fetch_billing:
                self._stripe_add_billing_objects(tx, cache)
            payment_values = {}
            odoo_payment = next(
                (
                    odoo_payments[key]
                    for key in _stripe_payment_keys(tx)
                    if key in odoo_payments
                ),
                None,
            )
            if odoo_payment:
                payment_values = self._stripe_odoo_payment_line_values(odoo_payment)
            if payment_values is not None:
                lines.append(
                    {
                        "ref": safe_format(self.stripe_reference, tx),
                        "payment_ref": safe_format(self.stripe_label, tx),
                        "narration": safe_format(self.stripe_note, tx),
                        "amount": float(tx["amount"]) / (10**currency.decimal_places),
                        "date": datetime.fromtimestamp(tx["created"]),
                        "unique_import_id": tx["id"],
                        "raw_data": json.dumps(tx),
                        **payment_values,
                    }
                )
            if tx.get("fee"):
                lines.append(
                    {
                        "ref": safe_format(self.stripe_fee_reference, tx),
                        "payment_ref": safe_format(self.stripe_fee_label, tx),
                        "narration": safe_format(self.stripe_fee_note, tx),
                        "amount": float(-tx["fee"]) / (10**currency.decimal_places),
                        "date": datetime.fromtimestamp(tx["created"]),
                        "unique_import_id": tx["id"] + "_fee",
                        "raw_data": json.dumps(tx),
                        **fee_values,
                    }
                )
        return lines, {}

    def _stripe_fee_line_values(self):
        """Values of the fee lines when they are recorded as payments to Stripe"""
        self.ensure_one()
        if not self.stripe_fee_as_payment:
            return {}
        partner = self.stripe_fee_partner_id
        return {
            "partner_id": partner.id,
            "partner_name": partner.name,
            "transaction_type": "fee",
        }

    @api.model
    def _stripe_add_billing_objects(self, tx, cache):
        """Add the customer, the invoice and the subscription of a payment.

        They are added to the transaction with the keys ``customer``, ``invoice``
        and ``subscription``. An object that Stripe does not find any more is
        left out.
        """
        source = tx.get("source")
        if not isinstance(source, dict) or source.get("object") != "charge":
            return
        customer_id = _stripe_id(source.get("customer"))
        if customer_id:
            customer = self._stripe_get_object(f"/customers/{customer_id}", cache)
            if customer:
                tx["customer"] = customer
        invoice = self._stripe_get_charge_invoice(source, cache)
        if not invoice:
            return
        tx["invoice"] = invoice
        # Since API version 2025-03-31.basil the subscription is in the parent
        subscription_id = _stripe_id(
            invoice.get("subscription")
            or ((invoice.get("parent") or {}).get("subscription_details") or {}).get(
                "subscription"
            )
        )
        if subscription_id:
            subscription = self._stripe_get_object(
                f"/subscriptions/{subscription_id}", cache
            )
            if subscription:
                tx["subscription"] = subscription

    @api.model
    def _stripe_get_charge_invoice(self, charge, cache):
        if "invoice" in charge:
            # Before API version 2025-03-31.basil the charge has its invoice
            invoice = charge["invoice"]
            if isinstance(invoice, dict):
                return invoice
            return invoice and self._stripe_get_object(f"/invoices/{invoice}", cache)
        payment_intent_id = _stripe_id(charge.get("payment_intent"))
        if not payment_intent_id:
            return None
        key = ("invoice_payments", payment_intent_id)
        if key not in cache:
            result = self._stripe_api_get(
                "/invoice_payments",
                [
                    ("payment[type]", "payment_intent"),
                    ("payment[payment_intent]", payment_intent_id),
                    ("expand[]", "data.invoice"),
                ],
            )
            data = result.get("data") or []
            cache[key] = data[0].get("invoice") if data else None
        return cache[key]

    @api.model
    def _stripe_get_object(self, path, cache):
        """Get an object from Stripe once per pull, None if it does not exist"""
        if path not in cache:
            try:
                cache[path] = self._stripe_api_get(path)
            except UserError as error:
                cause = error.__cause__
                if not (isinstance(cause, HTTPError) and cause.code == 404):
                    raise
                cache[path] = None
        return cache[path]
    def _stripe_get_odoo_payments(self, transactions):
        """Payments recorded by the Stripe payment provider of Odoo.

        :return: a dict mapping the Stripe ids of the payments (payment intent,
            charge or refund) to their ``payment.transaction``
        """
        self.ensure_one()
        if "payment.transaction" not in self.env:
            return {}
        keys = set()
        for tx in transactions:
            keys.update(_stripe_payment_keys(tx))
        if not keys:
            return {}
        payment_transactions = (
            self.env["payment.transaction"]
            .sudo()
            .search(
                [
                    ("provider_code", "=", "stripe"),
                    ("provider_reference", "in", list(keys)),
                    ("state", "=", "done"),
                ]
            )
        )
        return {ptx.provider_reference: ptx for ptx in payment_transactions}

    def _stripe_odoo_payment_line_values(self, payment_transaction):
        """Values of the line of a payment already recorded by Odoo.

        :return: None when the payment is already posted on the account of the
            journal, so that the line must not be imported again; otherwise the
            values to add to the line
        """
        self.ensure_one()
        payment = (
            payment_transaction.payment_id
            if "payment_id" in payment_transaction._fields
            else None
        )
        if (
            payment
            and payment.move_id.state == "posted"
            and payment.outstanding_account_id
            and payment.outstanding_account_id == self.journal_id.default_account_id
        ):
            return None
        return {"partner_id": payment_transaction.partner_id.id}

    @api.model
    def _stripe_api_get_all(self, path, params=None):
        if params is None:
            params = []
        starting_after = None
        while True:
            call_params = [*params]
            if starting_after:
                call_params.append(("starting_after", starting_after))
            j = self._stripe_api_get(path, params=call_params)
            yield from j["data"]
            if j["has_more"]:
                starting_after = j["data"][-1]["id"]
            else:
                break

    @api.model
    def _stripe_api_get(self, path, params=None, data=None):
        if params is None:
            params = {}
        attempt = 0
        while True:
            try:
                request = urllib.request.Request(
                    (self.api_base or STRIPE_API_BASE) + path + "?" + urlencode(params),
                    data=data,
                )
                request.add_header("Authorization", f"Bearer {self.password}")
                response = urllib.request.urlopen(request)
                content = response.read().decode("utf-8")
                return json.loads(content)
            except URLError as e:
                if attempt >= STRIPE_MAX_RETRIES or not self._stripe_should_retry(e):
                    if isinstance(e, HTTPError):
                        raise UserError(self._stripe_error_message(path, e)) from e
                    raise
                delay = self._stripe_retry_delay(e, attempt)
                _logger.info(
                    "Stripe API call %s failed (%s), retrying in %s seconds",
                    path,
                    e,
                    delay,
                )
                time.sleep(delay)
                attempt += 1

    @api.model
    def _stripe_should_retry(self, error):
        """Whether a failed request is worth retrying.

        See https://docs.stripe.com/error-low-level#idempotency
        """
        if not isinstance(error, HTTPError):
            # Network error: the request did not get an answer
            return True
        should_retry = error.headers and error.headers.get("Stripe-Should-Retry")
        if should_retry in ("true", "false"):
            return should_retry == "true"
        return error.code in (409, 429) or error.code >= 500

    @api.model
    def _stripe_retry_delay(self, error, attempt):
        retry_after = (
            isinstance(error, HTTPError)
            and error.headers
            and error.headers.get("Retry-After")
        )
        try:
            delay = float(retry_after)
        except (TypeError, ValueError):
            delay = STRIPE_RETRY_DELAY * 2**attempt
        return min(delay, STRIPE_MAX_RETRY_DELAY)

    @api.model
    def _stripe_error_message(self, path, error):
        """Readable message of a failed request, from the error sent by Stripe.

        See https://docs.stripe.com/api/errors
        """
        try:
            content = json.loads(error.read().decode("utf-8"))
        except (AttributeError, ValueError):
            content = {}
        details = content.get("error") if isinstance(content, dict) else None
        if not isinstance(details, dict):
            details = {}
        message = details.get("message") or f"{error.code} {error.reason}"
        extra = [
            f"{key}: {details[key]}" for key in ("type", "code") if details.get(key)
        ]
        request_id = error.headers and error.headers.get("Request-Id")
        if request_id:
            extra.append(f"request: {request_id}")
        if extra:
            message = f"{message} ({', '.join(extra)})"
        return self.env._(
            "Stripe API call failed: %(path)s: %(message)s",
            path=path,
            message=message,
        )


def _stripe_id(value):
    """Id of a Stripe object, expanded or not"""
    if isinstance(value, dict):
        return value.get("id")
    return value


def _stripe_payment_keys(tx):
    """Stripe ids under which Odoo may have recorded the payment of a transaction"""
    source = tx.get("source")
    if not isinstance(source, dict):
        return set()
    keys = {source.get("id"), source.get("payment_intent")}
    return {key for key in keys if isinstance(key, str)}


def safe_format(template, kwargs):
    template = template or ""

    def sub(m: re.Match):
        val = kwargs
        for k in m.group(1).split("."):
            try:
                val = val[k]
            except (KeyError, TypeError):
                val = None
        if val is None:
            return ""
        return str(val)

    return re.sub(r"\{([^}]+)\}", sub, template)
