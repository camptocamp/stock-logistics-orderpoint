# Copyright 2026 Camptocamp SA (https://www.camptocamp.com).
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import math
from datetime import timedelta
from statistics import stdev

from freezegun import freeze_time

from odoo import fields

from .common import TestStockOrderpointSafetyStockCommon


class TestStockOrderpointSafetyStock(TestStockOrderpointSafetyStockCommon):
    def test_history_series(self):
        """Test that the history series is aggregated correctly"""
        today = fields.Date.today()
        self._create_moves_from_serie(
            self.product,
            [
                (today - timedelta(days=6), 10),
                (today - timedelta(days=6), 5),
                (today - timedelta(days=4), 12),
                (today, 10),  # Will be ignored, as today's not finished
            ],
        )
        # Check the history series
        series = self.orderpoint._get_product_demand_history_series(
            self.orderpoint.warehouse_id,
            self.orderpoint.product_id,
            self.orderpoint.demand_history_days,
        )
        self.assertEqual(
            series,
            {
                self.product: {
                    today - timedelta(days=6): 10 + 5,
                    today - timedelta(days=4): 12,
                }
            },
        )

    def test_math_simple(self):
        """Test the math on a small and understandable example

        We use a window of 7 days, with only these moves:
        - 6d ago: 10 + 5 out
        - 4d ago: 12 out

        Lead time is 1 day.
        """
        today = fields.Date.today()
        self._create_moves_from_serie(
            self.product,
            [
                (today - timedelta(days=6), 10),
                (today - timedelta(days=6), 5),
                (today - timedelta(days=4), 12),
                (today, 10),  # Will be ignored, as today's not finished
            ],
        )
        self.env.company.safety_stock_history_days = 7
        self.orderpoint.rule_ids.delay = 1
        self.orderpoint.invalidate_recordset(["lead_days"])
        self.orderpoint.action_apply_safety_stock()
        # Precompute results
        avg_qty = round((10 + 5 + 12) / 7, 2)
        std_dev = round(stdev([15, 12, 0, 0, 0, 0, 0]), 2)
        z_score = 1.6449
        growth_factor = 1.0
        safety_stock = round(std_dev * z_score * growth_factor, 2)
        self.assertRecordValues(
            self.orderpoint,
            [
                {
                    "demand_avg_qty": avg_qty,
                    "demand_std_dev": std_dev,
                    "demand_lt_std_dev": std_dev * math.sqrt(1),
                    "safety_stock": safety_stock,
                    "product_min_qty": safety_stock,
                    "product_max_qty": safety_stock + (avg_qty * 1),
                }
            ],
        )

    @freeze_time("2026-01-14")
    def test_fixture_serie_01(self):
        serie = self._load_serie_from_csv(
            "stock_orderpoint_safety_stock/tests/data/serie_01.csv"
        )
        self._create_moves_from_serie(self.product, serie)
        self.env.company.safety_stock_history_days = 69
        self.orderpoint.rule_ids.delay = 7
        self.orderpoint.invalidate_recordset(["lead_days"])
        self.orderpoint.action_apply_safety_stock()
        self.assertRecordValues(
            self.orderpoint,
            [
                {
                    "demand_avg_qty": 5850.03,
                    "demand_std_dev": 20444.87,
                    "demand_lt_std_dev": 54092.04,
                    "safety_stock": 88976,
                    "product_min_qty": 88976,
                    "product_max_qty": 129926.21,
                }
            ],
        )
