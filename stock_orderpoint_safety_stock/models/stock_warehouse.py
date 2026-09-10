# Copyright 2026 Camptocamp SA (https://www.camptocamp.com).
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models


class StockWarehouse(models.Model):
    _inherit = "stock.warehouse"

    def _skip_serie_leading_0s(self) -> bool:
        # Delegate to the WH company settings
        return self.company_id._skip_serie_leading_0s()
