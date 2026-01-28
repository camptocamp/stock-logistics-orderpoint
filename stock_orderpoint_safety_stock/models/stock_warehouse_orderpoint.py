# Copyright 2026 Camptocamp SA (https://www.camptocamp.com).
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import datetime
import math
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models
from odoo.fields import Domain

from odoo.addons.product.models.product_product import ProductProduct
from odoo.addons.stock.models.stock_warehouse import StockWarehouse


class StockWarehouseOrderpoint(models.Model):
    _inherit = "stock.warehouse.orderpoint"

    safety_stock_method = fields.Selection(
        selection=[
            ("manual", "Manual"),
            ("csl", "Cycle Service Level"),
        ],
        default="manual",
        help=(
            "The method to use to set the safety stock.\n"
            "* Manual: The safety stock is set manually.\n"
            "* Cycle Service Level: The safety stock is computed based on the target "
            "cycle service level.\n"
        ),
    )
    cycle_service_level_id = fields.Many2one(
        "stock.cycle.service.level",
        string="Cycle Service Level",
        ondelete="restrict",
    )
    csl = fields.Float(related="cycle_service_level_id.csl")
    z_score = fields.Float(related="cycle_service_level_id.z_score")
    growth_factor = fields.Float(
        digits=(2, 2),
        default=0.0,
        help=(
            "A multiplier to apply to the resulting safety stock value.\n"
            "A positive value will increase the safety stock, a negative value will "
            "decrease it.\n"
        ),
    )
    demand_history_days = fields.Integer(
        string="Demand History Days",
        related="company_id.safety_stock_history_days",
        store=True,  # Used in SQL query
    )
    # demand_history_exclude_weekends = fields.Boolean(
    #     string="Exclude Weekends from Demand History",
    #     related="company_id.safety_stock_history_exclude_weekends",
    #     store=True,  # Used in SQL query
    # )
    demand_avg_qty = fields.Float(
        string="Average Daily Demand",
        # TODO: help
    )
    demand_std_dev = fields.Float(
        string="Standard Deviation of Daily Demand",
        # TODO: help
    )
    demand_lead_time_std_dev = fields.Float(
        string="Standard Deviation of Daily Demand over Lead Time",
        # TODO: help
        compute="_compute_demand_lead_time_std_dev",
    )
    demand_analysis_date = fields.Datetime(
        # string="Demand Analysis Date",
        # TODO: help
    )

    def _get_product_moves_history_domains(
        self, warehouse: StockWarehouse, products: ProductProduct, days: int
    ) -> tuple[Domain, Domain]:
        """Returns the incoming and outgoing moves domains"""
        moves_domain = Domain(
            [
                ("product_id", "in", products.ids),
                ("date", ">=", f"today -{days}d +1d"),
                ("date", "<", "today -1d"),
                ("state", "=", "done"),
                ("product_qty", ">", 0),
            ]
        )
        moves_out_domain = Domain(
            [
                ("location_id", "child_of", warehouse.view_location_id.id),
                ("location_dest_id.usage", "=", "customer"),
            ]
        )
        moves_in_domain = Domain(
            [
                ("location_id.usage", "=", "customer"),
                ("location_dest_id", "child_of", warehouse.view_location_id.id),
            ]
        )
        return (moves_domain + moves_in_domain), (moves_domain + moves_out_domain)

    @api.model
    def _get_product_moves_history_series(
        self,
        warehouse: StockWarehouse,
        products: ProductProduct,
        days: int,
    ) -> dict[ProductProduct, dict[datetime.date, float]]:
        """Get the history demand series for the products."""
        moves_in_domain, moves_out_domain = self._get_product_moves_history_domains(
            warehouse, products, days
        )
        moves_in_groups = self.env["stock.move"]._read_group(
            moves_in_domain,
            groupby=["product_id", "date:day"],
            aggregates=["product_qty:sum"],
        )
        moves_out_groups = self.env["stock.move"]._read_group(
            moves_out_domain,
            groupby=["product_id", "date:day"],
            aggregates=["product_qty:sum"],
        )
        # Initialize a zero-filled series
        fill_from = datetime.date.today() - timedelta(days=days)
        zero_filled_serie = dict.fromkeys(
            (fill_from + timedelta(days=i) for i in range(days)), 0.0
        )
        # Group by product and consolidate in/out moves
        # Quantities are now signed: positive for outgoing, negative for incoming
        consumptions_by_product = defaultdict(lambda: zero_filled_serie.copy())
        for product, date, consumption in moves_in_groups:
            consumptions_by_product[product][date.date()] -= consumption
        for product, date, consumption in moves_out_groups:
            consumptions_by_product[product][date.date()] += consumption
        return consumptions_by_product

    def _get_product_consumption_serie_stats(
        self, filled_serie: dict[datetime.date, float]
    ) -> dict[str, float]:
        """Get the stats of the product consumption series"""
        # Lazy load numpy to keep memory-efficient when not needed
        import numpy as np

        values = np.array(list(filled_serie.values()), dtype=float)
        return {
            "_raw": values,
            "demand_avg_qty": values.mean(values),
            "demand_std_dev": values.std(values),
        }

    def _recompute_daily_demand(self):
        """Recompute the Average Daily Demand and its Standard Deviation."""
        to_recompute = self.filtered(lambda rec: rec.safety_stock_method != "manual")
        if not to_recompute:
            return

        # Clear existing values
        to_recompute.demand_avg_qty = 0.0
        to_recompute.demand_std_dev = 0.0
        to_recompute.demand_analysis_date = fields.Datetime.now()

        # Group by warehouse and demand history days to batch read groups of stock moves
        grouped = to_recompute.grouped(
            lambda rec: (rec.warehouse_id, rec.demand_history_days)
        )
        for (warehouse, days), orderpoints in grouped.items():
            products = orderpoints.product_id
            product_consumptions = self._get_product_moves_history_series(
                warehouse, products, days
            )
            # Compute the average and standard deviation of the series
            product_consumption_stats = {
                product: self._get_product_consumption_serie_stats(
                    product_consumptions[product]
                )
                for product in product_consumptions
            }
            # Set the stats in the orderpoints
            for orderpoint in orderpoints:
                if orderpoint.product_id in product_consumption_stats:
                    stats = product_consumption_stats[orderpoint.product_id]
                    vals = {k: v for k, v in stats.items() if not k.startswith("_")}
                    orderpoint.write(vals)

    def action_recompute_history_demand(self):
        self._recompute_daily_demand()
        return True

    @api.depends("demand_std_dev", "lead_days")
    def _compute_demand_lead_time_std_dev(self):
        for record in self:
            record.demand_lead_time_std_dev = record.demand_std_dev * math.sqrt(
                record.lead_days
            )
