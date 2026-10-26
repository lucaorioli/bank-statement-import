1.  Go to *Invoicing \> Configuration \> Online Bank Statement
    Providers* and open a provider.
2.  In *Field Mapping*, add a line for each field to fill: the
    *Expression* gives the value, the *Field* where to write it.

An expression is the path of a value of the raw data in braces, for
instance `{source.billing_details.email}`, possibly within some text,
for instance `Invoice {invoice.number}`. Elements of a list are reached
by their position: `{items.0.name}`. Alternatives separated by `|` are
tried in order, until one gives a value:
`{source.payment_intent} | {source.id}`.

The value is converted for the field: dates and datetimes from Unix
timestamps or ISO strings, numbers, booleans, and records of many2one
fields by name or code (for instance a country from `IT`). A value that
cannot be converted is left out.

When several lines fill the same field, the first one that gives a value
is used.
