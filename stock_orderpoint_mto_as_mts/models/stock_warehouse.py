# Copyright 2020 Camptocamp SA
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl)
from odoo import fields, models


class StockWarehouse(models.Model):

    _inherit = "stock.warehouse"

    archive_orderpoints_mto_removal = fields.Boolean(default=False)

    def _get_locations_for_mto_orderpoints(self):
        return self.mapped("lot_stock_id")
