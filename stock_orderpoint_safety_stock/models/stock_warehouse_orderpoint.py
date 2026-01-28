# Copyright 2026 Camptocamp SA (https://www.camptocamp.com).
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import math

from odoo import api, fields, models
from odoo.tools.constants import PREFETCH_MAX


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
    csl = fields.Float(
        string="Cycle Service Level Target",
        related="cycle_service_level_id.csl",
    )
    z_score = fields.Float(
        related="cycle_service_level_id.z_score",
    )
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
        compute="_compute_daily_demand",
        digits="Product Unit of Measure",
        help="The average daily outgoing quantity on this warehouse.",
    )
    demand_std_dev = fields.Float(
        string="Standard Deviation of Daily Demand",
        compute="_compute_daily_demand",
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
    safety_stock_update_date = fields.Datetime(
        string="Last modification date of Min and Max quantities from Safety Stock",
        readonly=True,
    )

    @api.depends(
        "safety_stock_method",
        "demand_history_days",
        "warehouse_id",
        "product_id",
        "product_id.stock_move_ids",
    )
    def _compute_daily_demand(self):
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
            aggregated_vals_by_product = products._get_daily_demand_aggregated_vals(
                warehouse=warehouse,
                days=days,
            )
            for orderpoint in orderpoints:
                vals = aggregated_vals_by_product.get(orderpoint.product_id, {})
                orderpoint.update(
                    {k: v for k, v in vals.items() if not k.startswith("_")}
                )

    @api.depends("demand_std_dev", "lead_days")
    def _compute_demand_lt_std_dev(self):
        for rec in self:
            rec.demand_lt_std_dev = rec.demand_std_dev * math.sqrt(rec.lead_days)

    @api.depends("safety_stock_method", "demand_lt_std_dev", "z_score", "growth_factor")
    def _compute_safety_stock(self):
        self.safety_stock = 0.0
        for rec in self.filtered(lambda rec: rec.safety_stock_method == "csl"):
            rec.safety_stock = rec.product_id.uom_id.round(
                rec.demand_lt_std_dev * rec.z_score * (1.0 + rec.growth_factor)
            )

    def _apply_safety_stock(self):
        """Apply the safety stock to the orderpoint min and max quantities"""
        to_apply = self.filtered(lambda rec: rec.safety_stock_method != "manual")
        to_apply.safety_stock_update_date = fields.Datetime.now()
        for rec in to_apply:
            rec.product_min_qty = rec.safety_stock
            rec.product_max_qty = rec.product_min_qty + (
                rec.demand_avg_qty * rec.lead_days
            )

    def action_apply_safety_stock(self):
        """Apply the safety stock to the orderpoint min and max quantities"""
        self._apply_safety_stock()
        return True

    @api.onchange(
        "product_id",
        "location_id",
        "safety_stock_method",
        "cycle_service_level_id",
        "growth_factor",
    )
    def _onchange_safety_stock_method_apply(self):
        """Onchange: safety stock method or cycle service level

        Immediately apply the safety stock to the orderpoint min and max quantities.
        """
        self._apply_safety_stock()

    @api.model
    def _cron_recompute_safety_stock(self, batch_size: int | None = PREFETCH_MAX):
        """Scheduled action: Recompute the safety stock for all orderpoints"""
        domain = [
            ("safety_stock_method", "!=", "manual"),
            ("safety_stock_update_date", "<", "today"),
        ]
        records = self.search(domain, order="id", limit=batch_size)
        records._apply_safety_stock()
        remaining = self.search_count(domain)
        self.env["ir.cron"]._commit_progress(
            processed=len(records),
            remaining=remaining,
        )
