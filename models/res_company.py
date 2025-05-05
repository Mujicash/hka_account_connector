from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    hka_user = fields.Char(string="HKA Username")
    hka_password = fields.Char(string="HKA Password")
    hka_test_mode = fields.Boolean(
        string="HKA en modo prueba",
        help="Active this option to use the HKA test environment (OSE/PSE).",
    )