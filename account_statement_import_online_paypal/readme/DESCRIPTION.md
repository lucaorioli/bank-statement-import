This module provides online bank statements from
[PayPal.com](https://paypal.com/).

The lines of the transactions also get the details of the payer and of
the payment: email, phone, address and country of the payer, its PayPal
account id, the payment instrument and the subscription, when the
transaction belongs to one.

Requests that fail for a temporary reason (rate limit, server or network
error) are retried a few times, waiting longer at each attempt. Other
errors are reported with the message sent by PayPal and its debug id.
