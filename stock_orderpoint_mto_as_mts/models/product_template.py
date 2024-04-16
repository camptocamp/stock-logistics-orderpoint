# Copyright 2024
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.model_create_multi
    def create(self, vals_list):
        template = super().create(vals_list)
        template.product_variant_ids.sudo()._ensure_default_orderpoint_for_mto()
        return template

    def write(self, vals):
        original_mto_products = self.product_variant_ids._filter_mto_products()
        res = super().write(vals)
        self.product_variant_ids._update_mto_products(original_mto_products, vals)
        return res
