This module adds service level–driven replenishment to Odoo’s reordering rules.

Instead of setting safety stock manually, planners can define a target service level
and have Odoo compute the buffer (minimum and maximum stock) and resulting reorder point
based on observed variability in demand (and optionally lead time).

The goal is to make replenishment policies consistent, measurable, and scalable across
many products/locations, while still allowing manual overrides where needed.
