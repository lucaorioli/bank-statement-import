# Copyright 2026 Luca Orioli <lucaorioli@gmail.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Online Bank Statements: Field Mapping",
    "version": "18.0.1.0.0",
    "author": "Luca Orioli, Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/bank-statement-import",
    "license": "AGPL-3",
    "category": "Accounting",
    "summary": "Fill fields of the statement lines from the raw data of the provider",
    "depends": ["account_statement_import_online"],
    "data": [
        "security/ir.model.access.csv",
        "security/online_bank_statement_provider_mapping.xml",
        "views/online_bank_statement_provider.xml",
        "views/account_bank_statement_line.xml",
    ],
    "installable": True,
}
