# Copyright 2023 ACSONE SA/NV
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _get_warehouse_missing_orderpoint_for_mto(self):
        self.ensure_one()
        res = False
        if not self.purchase_ok:
            return res
        wh_obj = self.env["stock.warehouse"]
        wh_domain = [("mto_as_mts", "=", True)]
        if self.company_id:
            wh_domain.append(("company_id", "=", self.company_id.id))
        wh = wh_obj.search(wh_domain)
        wh_orderpoint = self.orderpoint_ids.warehouse_id
        wh_wo_orderpoint = wh - wh_orderpoint
        if wh_wo_orderpoint:
            res = wh_wo_orderpoint
        return res

    def _create_default_orderpoint_for_mto(self, warehouses):
        self.ensure_one()
        orderpoints = self.env["stock.warehouse.orderpoint"]
        orderpoint_obj = self.env["stock.warehouse.orderpoint"]
        for warehouse in warehouses:
            orderpoint = orderpoint_obj.with_context(active_test=False).search(
                [
                    ("product_id", "=", self.id),
                    (
                        "location_id",
                        "=",
                        warehouse._get_locations_for_mto_orderpoints().id,
                    ),
                ],
                limit=1,
                order="id",
            )
            if orderpoint and not orderpoint.active:
                orderpoint.write(
                    {"active": True, "product_min_qty": 0.0, "product_max_qty": 0.0}
                )
            elif not orderpoint:
                vals = self._prepare_missing_orderpoint_vals(warehouse)
                orderpoint = orderpoint_obj.create(vals)
            orderpoints |= orderpoint
        return orderpoints

    def _prepare_missing_orderpoint_vals(self, warehouse):
        self.ensure_one()
        return {
            "warehouse_id": warehouse.id,
            "product_id": self.id,
            "company_id": warehouse.company_id.id,
            "product_min_qty": 0,
            "product_max_qty": 0,
            "location_id": warehouse._get_locations_for_mto_orderpoints().id,
            "product_uom": self.uom_id.id,
        }

    def _ensure_default_orderpoint_for_mto(self):
        """Ensure that a default orderpoint is created for the MTO products.

        that have no orderpoint yet.
        """
        for product in self._filter_mto_products():
            wh = product._get_warehouse_missing_orderpoint_for_mto()
            if wh:
                product._create_default_orderpoint_for_mto(wh)

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        products.sudo()._ensure_default_orderpoint_for_mto()
        return products

    def write(self, vals):
        original_mto_products = self._filter_mto_products()
        res = super().write(vals)
        self._update_mto_products(original_mto_products, vals)
        return res

    def _update_mto_products(self, original_mto_products, vals):
        if "company_id" in vals:
            # Change company, must archive orderpoints
            self.sudo()._archive_orderpoints_on_mto_removal()
            self.sudo()._ensure_default_orderpoint_for_mto()
            return
        elif "route_ids" not in vals:
            # No changes about is_mto
            self.sudo()._ensure_default_orderpoint_for_mto()
            return
        mto_products = self._filter_mto_products()
        # Find all MTO products that have been changed to Not MTO
        products_to_update = original_mto_products - mto_products
        if products_to_update:
            products_to_update._archive_orderpoints_on_mto_removal()
        return

    def _filter_mto_products(self):
        return self.filtered(lambda p: p.is_mto)

    def _get_orderpoints_to_archive_domain(self):
        domain = []
        warehouses = self.env["stock.warehouse"].search(
            [("mto_as_mts", "=", True), ("archive_orderpoints_mto_removal", "=", True)]
        )
        if warehouses:
            locations = self.env["stock.location"]
            for warehouse in warehouses:
                locations |= warehouse._get_locations_for_mto_orderpoints()
            domain.extend(
                [
                    ("product_id", "in", self.ids),
                    ("product_min_qty", "=", 0.0),
                    ("product_max_qty", "=", 0.0),
                    ("location_id", "in", locations.ids),
                ]
            )
        return domain

    def _archive_orderpoints_on_mto_removal(self):
        domain = self._get_orderpoints_to_archive_domain()
        if domain:
            ops = self.env["stock.warehouse.orderpoint"].search(domain)
            if ops:
                ops.write({"active": False})
