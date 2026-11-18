"""Supplier purchases and supplier payments."""

from extensions import db
from models.base import ID_TYPE, TimestampMixin


class Purchase(db.Model, TimestampMixin):
    __tablename__ = "purchases"
    __table_args__ = (
        db.Index("idx_purchases_supplier_date", "supplier_id", "purchase_date"),
        db.UniqueConstraint("supplier_id", "supplier_invoice_number", name="uq_supplier_invoice"),
        db.CheckConstraint(
            "status IN ('draft', 'ordered', 'partially_received', 'received', 'cancelled')",
            name="ck_purchases_status",
        ),
        db.CheckConstraint(
            "subtotal_lkr >= 0 AND discount_lkr >= 0 AND total_cost_lkr >= 0",
            name="ck_purchases_amounts_nonnegative",
        ),
        db.CheckConstraint("discount_lkr <= subtotal_lkr", name="ck_purchases_discount_lte_subtotal"),
        db.CheckConstraint("total_cost_lkr = subtotal_lkr - discount_lkr", name="ck_purchases_total"),
        db.CheckConstraint("data_origin IN ('business', 'synthetic_demo')", name="ck_purchases_origin"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    purchase_number = db.Column(db.String(60), nullable=False, unique=True)
    supplier_id = db.Column(ID_TYPE, db.ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False)
    supplier_invoice_number = db.Column(db.String(100))
    purchase_date = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    status = db.Column(db.String(24), nullable=False, default="draft", server_default="draft")
    subtotal_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    discount_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    total_cost_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    notes = db.Column(db.Text)
    data_origin = db.Column(db.String(20), nullable=False, default="business", server_default="business")
    created_by = db.Column(ID_TYPE, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    items = db.relationship("PurchaseItem", back_populates="purchase")


class PurchaseItem(db.Model):
    __tablename__ = "purchase_items"

    id = db.Column(ID_TYPE, primary_key=True)
    purchase_id = db.Column(ID_TYPE, db.ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=False)
    product_id = db.Column(ID_TYPE, db.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    quantity_ordered = db.Column(db.Integer, nullable=False)
    quantity_received = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    unit_cost_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    discount_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    line_total_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    purchase = db.relationship("Purchase", back_populates="items")
    product = db.relationship("Product")

    __table_args__ = (
        db.Index("idx_purchase_items_product", "product_id"),
        db.CheckConstraint("quantity_ordered > 0", name="ck_purchase_items_ordered_positive"),
        db.CheckConstraint("quantity_received >= 0 AND quantity_received <= quantity_ordered",
                           name="ck_purchase_items_received"),
        db.CheckConstraint("unit_cost_lkr >= 0", name="ck_purchase_items_cost_nonnegative"),
        db.CheckConstraint("discount_lkr >= 0 AND discount_lkr <= quantity_ordered * unit_cost_lkr",
                           name="ck_purchase_items_discount"),
        db.CheckConstraint("line_total_lkr = quantity_ordered * unit_cost_lkr - discount_lkr",
                           name="ck_purchase_items_total"),
    )


class SupplierPayment(db.Model):
    __tablename__ = "supplier_payments"

    id = db.Column(ID_TYPE, primary_key=True)
    supplier_id = db.Column(ID_TYPE, db.ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False)
    purchase_id = db.Column(ID_TYPE, db.ForeignKey("purchases.id", ondelete="RESTRICT"))
    amount_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    payment_method = db.Column(db.String(24), nullable=False)
    payment_date = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    reference_number = db.Column(db.String(120))
    recorded_by = db.Column(ID_TYPE, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    __table_args__ = (
        db.CheckConstraint("amount_lkr > 0", name="ck_supplier_payments_amount_positive"),
        db.CheckConstraint(
            "payment_method IN ('cash', 'card', 'bank_transfer', 'other')",
            name="ck_supplier_payments_method",
        ),
    )
