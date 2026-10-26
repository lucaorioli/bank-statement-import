This module provides online bank statements from stripe.com


Requests that fail for a temporary reason (rate limit, conflict, server
or network error) are retried a few times, waiting longer at each
attempt, as recommended by Stripe. Other errors are reported with the
message sent by Stripe and the id of the request.
The lines of the payments also get the details of the payer and of the
payment: email, phone and address of the billing details, the customer,
the payment method, the masked card number and its expiry date, the
subscription (when the billing data are fetched) and a link to the
payment or the payout on the Stripe dashboard.
