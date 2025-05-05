from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    hka_user = fields.Char(
        related='company_id.hka_user',
        readonly=False
    )
    hka_password = fields.Char(
        related='company_id.hka_password',
        readonly=False,
        password=True
    )
    hka_test_mode = fields.Boolean(
        related='company_id.hka_test_mode',
        readonly=False
    )