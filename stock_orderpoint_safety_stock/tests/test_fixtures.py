# Copyright 2026 Camptocamp SA (https://www.camptocamp.com).
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from freezegun import freeze_time

from .common import TestStockOrderpointSafetyStockCommon


class TestStockOrderpointSafetyStockFixtures(TestStockOrderpointSafetyStockCommon):
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
