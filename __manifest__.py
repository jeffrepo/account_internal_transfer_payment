{
    "name": "Account Internal Transfer Payment",
    "version": "19.0.1.0.0",
    "summary": "Reintroduce internal transfer fields in payments form",
    "description": """
First functional draft for Odoo 19.
- Adds Internal Transfer checkbox on account.payment
- Adds destination journal on payment form
- Keeps UX similar to Odoo 15
- Tries to leverage native paired-payment logic when available
""",
    "category": "Accounting/Accounting",
    "author": "OpenAI",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "views/account_payment_views.xml",
    ],
    "installable": True,
    "application": False,
}
