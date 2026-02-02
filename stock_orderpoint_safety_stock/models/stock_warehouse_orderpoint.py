# Copyright 2026 Camptocamp SA (https://www.camptocamp.com).
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import datetime
import math
from collections import defaultdict
from datetime import timedelta
from statistics import fmean, stdev
from typing import Any

from odoo import api, fields, models
from odoo.fields import Domain
from odoo.tools.constants import PREFETCH_MAX

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
    )
    demand_avg_qty = fields.Float(
        string="Average Daily Demand",
        compute="_compute_demand_history_stats",
        digits="Product Unit of Measure",
        help="The average daily outgoing quantity on this warehouse.",
    )
    demand_std_dev = fields.Float(
        string="Standard Deviation of Daily Demand",
        compute="_compute_demand_history_stats",
        digits="Product Unit of Measure",
        help="The standard deviation of the daily outgoing quantity on this warehouse.",
    )
    demand_lt_std_dev = fields.Float(
        string="Standard Deviation of Daily Demand over Lead Time",
        compute="_compute_demand_lt_std_dev",
        digits="Product Unit of Measure",
        help=(
            "The standard deviation of the daily outgoing quantity on this warehouse "
            "over the lead time."
        ),
    )
    safety_stock = fields.Float(
        compute="_compute_safety_stock",
        digits="Product Unit of Measure",
        help=(
            "The safety stock is the amount of stock to keep on this warehouse to "
            "cover the demand during the lead time.\n"
            "It is computed as the product of the standard deviation of the daily "
            "outgoing quantity over the lead time, the z-score (statistical factor "
            "derived from the cycle service level) and the growth factor.\n\n"
            "safety_stock = demand_lt_std_dev * z_score * (1.0 + growth_factor)"
        ),
    )

    def _get_product_demand_history_domains(
        self, warehouse: StockWarehouse, products: ProductProduct, days: int
    ) -> tuple[Domain, Domain]:
        """Returns the incoming and outgoing moves domains"""
        # We take into account all "confirmed" move states, cause they represent the
        # actual demand even if it's not fully done.
        moves_states = ["assigned", "confirmed", "partially_available", "done"]
        moves_domain = Domain(
            [
                ("product_id", "in", products.ids),
                ("date", ">=", f"today -{days}d"),
                ("date", "<", "today"),
                ("state", "in", moves_states),
                ("product_qty", ">", 0),
            ]
        )
        # Domains for outgoing and incoming warehouse moves, except inventory
        # adjustments that do not represent any demand.
        wh_location = warehouse.lot_stock_id
        moves_out_domain = Domain(
            [
                ("location_id", "child_of", wh_location.id),
                ("location_dest_id.usage", "!=", "inventory"),
                "!",
                ("location_dest_id", "child_of", wh_location.id),
            ]
        )
        moves_in_domain = Domain(
            [
                ("location_dest_id", "child_of", wh_location.id),
                ("location_id.usage", "!=", "inventory"),
                "!",
                ("location_id", "child_of", wh_location.id),
            ]
        )
        return (moves_domain + moves_in_domain), (moves_domain + moves_out_domain)

    @api.model
    def _get_product_demand_history_series(
        self,
        warehouse: StockWarehouse,
        products: ProductProduct,
        days: int,
    ) -> dict[ProductProduct, dict[datetime.date, float]]:
        """Get the history demand series for the products."""
        moves_in_domain, moves_out_domain = self._get_product_demand_history_domains(
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
        # Group by product and consolidate in/out moves
        # Quantities are now signed: positive for outgoing, negative for incoming
        consumptions_by_product = defaultdict(lambda: defaultdict(float))
        for product, date, consumption in moves_in_groups:
            consumptions_by_product[product][date.date()] -= consumption
        for product, date, consumption in moves_out_groups:
            consumptions_by_product[product][date.date()] += consumption
        return consumptions_by_product

    def _get_product_demand_history_serie_stats(
        self,
        serie: dict[datetime.date, float],
        fill_from: datetime.date,
        fill_to: datetime.date,
    ) -> dict[str, Any]:
        """Get the stats of the product demand history series

        :param serie: The series of demand values.
        :param fill_from: The date from which to zero-fill the series.
        :param fill_to: The date to which to zero-fill the series.
        :return: A dictionary with the stats of the series.
        """
        # Initialize a zero-filled series
        filled_serie: list[float] = [
            serie.get(fill_from + timedelta(days=i), 0.0)
            for i in range((fill_to - fill_from).days)
        ]
        return {
            "_filled": filled_serie,
            "demand_avg_qty": fmean(filled_serie),
            "demand_std_dev": stdev(filled_serie),
        }

    @api.depends(
        "safety_stock_method",
        "demand_history_days",
        "warehouse_id",
        "product_id",
        "product_id.stock_move_ids",
    )
    def _compute_demand_history_stats(self):
        """Compute the Average Daily Demand and its Standard Deviation."""
        # Clear existing values
        self.demand_avg_qty = 0.0
        self.demand_std_dev = 0.0

        # Process only orderpoints not using an automatic safety stock method
        self = self.filtered(lambda rec: rec.safety_stock_method != "manual")
        if not self:
            return

        # Group by warehouse and demand history days to batch read groups of stock moves
        grouped = self.grouped(lambda rec: (rec.warehouse_id, rec.demand_history_days))
        for (warehouse, days), orderpoints in grouped.items():
            products = orderpoints.product_id
            product_consumptions = self._get_product_demand_history_series(
                warehouse, products, days
            )
            # Compute the average and standard deviation of the series
            today = datetime.date.today()
            fill_from = today - timedelta(days=days)
            product_consumption_stats = {
                product: self._get_product_demand_history_serie_stats(
                    serie=product_consumptions[product],
                    fill_from=fill_from,
                    fill_to=today,
                )
                for product in product_consumptions
            }
            # Set the stats in the orderpoints
            for orderpoint in orderpoints:
                if orderpoint.product_id in product_consumption_stats:
                    stats = product_consumption_stats[orderpoint.product_id]
                    vals = {k: v for k, v in stats.items() if not k.startswith("_")}
                    orderpoint.update(vals)

    @api.depends("demand_std_dev", "lead_days")
    def _compute_demand_lt_std_dev(self):
        for record in self:
            record.demand_lt_std_dev = record.demand_std_dev * math.sqrt(
                record.lead_days
            )

    @api.depends("safety_stock_method", "demand_lt_std_dev", "z_score", "growth_factor")
    def _compute_safety_stock(self):
        self.safety_stock = 0.0
        for rec in self.filtered(lambda rec: rec.safety_stock_method == "csl"):
            rec.safety_stock = rec.product_id.uom_id.round(
                rec.demand_lt_std_dev * rec.z_score * (1.0 + rec.growth_factor)
            )

    def _apply_safety_stock(self):
        """Apply the safety stock to the orderpoint min and max quantities"""
        for rec in self.filtered(lambda rec: rec.safety_stock_method != "manual"):
            rec.product_min_qty = rec.safety_stock
            rec.product_max_qty = rec.product_min_qty + (
                rec.demand_avg_qty * rec.lead_days
            )

    def action_apply_safety_stock(self):
        """Apply the safety stock to the orderpoint min and max quantities"""
        self._apply_safety_stock()
        return True

    @api.onchange("safety_stock_method", "cycle_service_level_id")
    def _onchange_safety_stock_method_apply(self):
        """Onchange: safety stock method or cycle service level

        Immediately apply the safety stock to the orderpoint min and max quantities.
        """
        self._apply_safety_stock()

    @api.model
    def _cron_recompute_safety_stock(self, batch_size=PREFETCH_MAX):
        """Scheduled action: Recompute the safety stock for all orderpoints"""
        domain = [
            ("safety_stock_method", "!=", "manual"),
            ("write_date", "<", "today"),
        ]
        orderpoints = self.search(domain, order="id", limit=batch_size)
        orderpoints._apply_safety_stock()
        self.env["ir.cron"]._commit_progress(
            len(orderpoints),
            remaining=self.search_count(domain),
        )
