from odoo import fields, models

class ProductTemplate(models.Model):
    _inherit = "product.template"

    l10n_pe_edi_product_code_id = fields.Many2one(
        comodel_name="l10n_pe_edi.catalog.25",
        string="Product code SUNAT",
    )
    l10n_pe_edi_detraction_type_id = fields.Many2one(
        comodel_name="l10n_pe_edi.catalog.54", string="Detraction Type", copy=False
    )
