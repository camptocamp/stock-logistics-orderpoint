# Copyright 2026 Camptocamp SA (https://www.camptocamp.com).
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from .common import TestStockOrderpointSafetyStockCommon


class TestCycleServiceLevel(TestStockOrderpointSafetyStockCommon):
    def test_known_z_scores(self):
        self.assertAlmostEqual(self.csl_90.z_score, 1.2816, places=4)
        self.assertAlmostEqual(self.csl_95.z_score, 1.6449, places=4)
        self.assertAlmostEqual(self.csl_97.z_score, 1.8808, places=4)
        self.assertAlmostEqual(self.csl_99.z_score, 2.3263, places=4)
