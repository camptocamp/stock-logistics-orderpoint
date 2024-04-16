# Copyright 2023 ACSONE SA/NV
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    is_missing_default_orderpoint_for_mto = fields.Boolean(
        compute="_compute_is_missing_default_orderpoint_for_mto",
    )

    @api.depends("is_mto", "orderpoint_ids", "type")
    def _compute_is_missing_default_orderpoint_for_mto(self):
        default_company = self.env["res.company"]._get_main_company()
        for product in self:
            company = product.company_id or default_company
            len_wh = len(
                self.env["stock.warehouse"].search([("company_id", "=", company.id)])
            )
            len_wh_orderpoint = len(
                product.orderpoint_ids.partition("warehouse_id").keys()
            )
            product.is_missing_default_orderpoint_for_mto = (
                product.is_mto
                and product.type == "product"
                and len_wh != len_wh_orderpoint
            )

    def _create_default_orderpoint_for_mto(self):
        default_company = self.env["res.company"]._get_main_company()
        for company, products in self.partition("company_id").items():
            company = company or default_company
            warehouses = self.env["stock.warehouse"].search(
                [("company_id", "=", company.id)]
            )
            for product in products:
                if not product.is_missing_default_orderpoint_for_mto:
                    continue
                for warehouse in warehouses:
                    product._get_mto_orderpoint(warehouse)

    def _get_mto_orderpoint(self, warehouse):
        self.ensure_one()
        orderpoint = (
            self.env["stock.warehouse.orderpoint"]
            .with_context(active_test=False)
            .search(
                [
                    ("product_id", "=", self.id),
                    (
                        "location_id",
                        "=",
                        warehouse._get_locations_for_mto_orderpoints().id,
                    ),
                ],
                limit=1,
            )
        )
        if orderpoint and not orderpoint.active:
            orderpoint.write(
                {"active": True, "product_min_qty": 0.0, "product_max_qty": 0.0}
            )
        elif not orderpoint:
            vals = self._prepare_missing_orderpoint_vals(warehouse)
            self.env["stock.warehouse.orderpoint"].create(vals)
        return orderpoint

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
        self.filtered(
            "is_missing_default_orderpoint_for_mto"
        )._create_default_orderpoint_for_mto()

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        products.sudo()._ensure_default_orderpoint_for_mto()
        return products

    def write(self, vals):
        # Archive orderpoints when MTO route is removed
        if "route_ids" not in vals:
            res = super().write(vals)
            self.sudo()._ensure_default_orderpoint_for_mto()
            return res
        mto_products = self._filter_mto_products()
        res = super().write(vals)
        not_mto_products = self._filter_mto_products(mto=False)
        # products to update are the intersection of both recordsets
        products_to_update = mto_products & not_mto_products
        if products_to_update:
            products_to_update._archive_orderpoints_on_mto_removal()
        return res

    def _filter_mto_products(self, mto=True):
        if mto:
            func = lambda p: p.is_mto  # noqa
        else:
            func = lambda p: not p.is_mto  # noqa
        return self.filtered(func)

    def _get_orderpoints_to_archive_domain(self):
        domain = []
        warehouses = self.env["stock.warehouse"].search(
            [("archive_orderpoints_mto_removal", "=", True)]
        )
        if warehouses:
            locations = warehouses._get_locations_for_mto_orderpoints()
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
