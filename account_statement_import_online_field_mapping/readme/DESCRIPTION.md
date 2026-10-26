This module lets you choose, for each online bank statement provider,
which fields of the statement lines are filled from the raw data of the
transactions, and how.

It works with the providers that keep the transaction as JSON in the raw
data of the lines (for instance Stripe, PayPal or Ponto), and with any
field of the statement lines: the standard ones, the common transaction
details of *account_statement_import_base* or fields added by other
modules. The fields that define the transaction and its journal entry
(amount, date, journal, currencies...) cannot be mapped.
