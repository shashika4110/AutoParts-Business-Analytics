"""Sales invoices, line items, and customer payments."""

from extensions import db
from models.base import ID_TYPE, TimestampMixin


class Sale(db.Model, TimestampMixin):
    __tablename__ = "sales"

    id = db.Column(ID_TYPE, primary_key=True)
    invoice_number = db.Column(db.String(60), nullable=False, unique=True)
    idempotency_key = db.Column(db.String(80), unique=True)
    request_fingerprint = db.Column(db.String(64))
    customer_id = db.Column(ID_TYPE, db.ForeignKey("customers.id", ondelete="RESTRICT"), nullable=False)
    created_by = db.Column(ID_TYPE, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    sale_date = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    sale_type = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="completed", server_default="completed")
    subtotal_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    discount_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    total_amount_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    notes = db.Column(db.Text)
    data_origin = db.Column(db.String(20), nullable=False, default="business", server_default="business")

    items = db.relationship("SaleItem", back_populates="sale")
    payments = db.relationship("Payment", back_populates="sale")
    customer = db.relationship("Customer")

    __table_args__ = (
        db.Index("idx_sales_date_status", "sale_date", "status"),
        db.Index("idx_sales_customer_date", "customer_id", "sale_date"),
        db.CheckConstraint("sale_type IN ('retail', 'wholesale')", name="ck_sales_type"),
        db.CheckConstraint(
            "status IN ('draft', 'completed', 'cancelled', 'partially_returned', 'returned')",
            name="ck_sales_status",
        ),
        db.CheckConstraint("subtotal_lkr >= 0 AND discount_lkr >= 0 AND total_amount_lkr >= 0",
                           name="ck_sales_amounts_nonnegative"),
        db.CheckConstraint("discount_lkr <= subtotal_lkr", name="ck_sales_discount_lte_subtotal"),
        db.CheckConstraint("total_amount_lkr = subtotal_lkr - discount_lkr", name="ck_sales_total"),
        db.CheckConstraint("data_origin IN ('business', 'synthetic_demo')", name="ck_sales_origin"),
    )


class SaleItem(db.Model):
    __tablename__ = "sale_items"

    id = db.Column(ID_TYPE, primary_key=True)
    sale_id = db.Column(ID_TYPE, db.ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False)
    product_id = db.Column(ID_TYPE, db.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    unit_price_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    unit_cost_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    discount_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    line_total_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    gross_profit_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    sale = db.relationship("Sale", back_populates="items")
    product = db.relationship("Product")

    __table_args__ = (
        db.Index("idx_sale_items_product", "product_id"),
        db.CheckConstraint("quantity > 0", name="ck_sale_items_quantity_positive"),
        db.CheckConstraint("unit_price_lkr >= 0 AND unit_cost_lkr >= 0", name="ck_sale_items_prices_nonnegative"),
        db.CheckConstraint("discount_lkr >= 0 AND discount_lkr <= quantity * unit_price_lkr",
                           name="ck_sale_items_discount"),
        db.CheckConstraint("line_total_lkr = quantity * unit_price_lkr - discount_lkr",
                           name="ck_sale_items_total"),
    )


class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(ID_TYPE, primary_key=True)
    sale_id = db.Column(ID_TYPE, db.ForeignKey("sales.id", ondelete="RESTRICT"), nullable=False)
    amount_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    payment_method = db.Column(db.String(24), nullable=False)
    payment_date = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    reference_number = db.Column(db.String(120))
    received_by = db.Column(ID_TYPE, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="recorded", server_default="recorded")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())

    sale = db.relationship("Sale", back_populates="payments")

    __table_args__ = (
        db.Index("idx_payments_sale_date", "sale_id", "payment_date"),
        db.CheckConstraint("amount_lkr > 0", name="ck_payments_amount_positive"),
        db.CheckConstraint(
            "payment_method IN ('cash', 'card', 'bank_transfer', 'other')",
            name="ck_payments_method",
        ),
        db.CheckConstraint("status IN ('recorded', 'voided')", name="ck_payments_status"),
    )
