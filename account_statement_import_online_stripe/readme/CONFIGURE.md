1. Ensure your user has the necessary permissions. Assign the user to the group "Technical / Show Full Accounting Features" to gain access to the required settings.
2. Go to "Invoicing" -> "Configuration" -> "Online Bank Statement Providers".
3. Configure the Provider

For more detailed information about the Stripe API and the balance transactions used by this module, please refer to the official Stripe documentation: https://docs.stripe.com/api/balance_transactions

To use the customer, the invoice or the subscription of the payments in
the labels (for instance `{customer.email}`, `{invoice.number}` or
`{subscription.id}`), check *Fetch Billing Data*. The module then asks
Stripe for these objects, once per pull for each of them, and keeps
them in the raw data of the lines. The source of the transactions must
be expanded, as it is by default (`data.source`). Both the API versions
before and after 2025-03-31.basil are supported, so the version of the
Stripe account does not need to be changed.
Stripe takes its fees from the balance, and sends invoices for them
later. To record each fee as a payment of those invoices, check *Fees as
Payments to Stripe* and choose the partner of Stripe: the fee lines get
this partner and the transaction type `fee`. Then create a
reconciliation model (*Invoicing \> Configuration \> Reconciliation
Models*) for the Stripe journal, matching the lines of that partner with
a negative amount, with the payable account of the partner as
counterpart. The invoices of Stripe can then be reconciled with these
payments.
If Odoo itself takes payments through Stripe (the Stripe payment
provider, for instance on the website), those payments are already
recorded by Odoo. To avoid importing them twice, check *Payments
Already Recorded by Odoo* (visible when the Payment module is
installed). For each transaction paid through Odoo:

- if its payment is posted on the account of this journal, the line of
  the payment is not imported (its fee is);
- otherwise the line is imported with the partner of the payment, ready
  to be reconciled with it.
