"""Returns, inventory movement history, expenses, and audit records."""

from extensions import db
from models.base import ID_TYPE
from sqlalchemy.dialects.postgresql import INET, JSONB


class ReturnRecord(db.Model):
    __tablename__ = "returns"

    id = db.Column(ID_TYPE, primary_key=True)
    return_number = db.Column(db.String(60), nullable=False, unique=True)
    return_type = db.Column(db.String(16), nullable=False)
    sale_id = db.Column(ID_TYPE, db.ForeignKey("sales.id", ondelete="RESTRICT"))
    purchase_id = db.Column(ID_TYPE, db.ForeignKey("purchases.id", ondelete="RESTRICT"))
    status = db.Column(db.String(16), nullable=False, default="completed", server_default="completed")
    reason = db.Column(db.Text, nullable=False)
    return_date = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    total_amount_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    created_by = db.Column(ID_TYPE, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    items = db.relationship("ReturnItem", back_populates="return_record")

    __table_args__ = (
        db.Index("idx_returns_date", "return_date"),
        db.CheckConstraint("return_type IN ('sale', 'purchase')", name="ck_returns_type"),
        db.CheckConstraint("status IN ('pending', 'completed', 'rejected')", name="ck_returns_status"),
        db.CheckConstraint("total_amount_lkr >= 0", name="ck_returns_amount_nonnegative"),
        db.CheckConstraint(
            "(return_type = 'sale' AND sale_id IS NOT NULL AND purchase_id IS NULL) "
            "OR (return_type = 'purchase' AND purchase_id IS NOT NULL AND sale_id IS NULL)",
            name="ck_returns_source",
        ),
    )


class ReturnItem(db.Model):
    __tablename__ = "return_items"

    id = db.Column(ID_TYPE, primary_key=True)
    return_id = db.Column(ID_TYPE, db.ForeignKey("returns.id", ondelete="RESTRICT"), nullable=False)
    sale_item_id = db.Column(ID_TYPE, db.ForeignKey("sale_items.id", ondelete="RESTRICT"))
    purchase_item_id = db.Column(ID_TYPE, db.ForeignKey("purchase_items.id", ondelete="RESTRICT"))
    quantity = db.Column(db.Integer, nullable=False)
    resalable = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    amount_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    return_record = db.relationship("ReturnRecord", back_populates="items")

    __table_args__ = (
        db.CheckConstraint("quantity > 0", name="ck_return_items_quantity_positive"),
        db.CheckConstraint("amount_lkr >= 0", name="ck_return_items_amount_nonnegative"),
        db.CheckConstraint(
            "(sale_item_id IS NOT NULL AND purchase_item_id IS NULL) "
            "OR (purchase_item_id IS NOT NULL AND sale_item_id IS NULL)",
            name="ck_return_items_source",
        ),
    )


class StockMovement(db.Model):
    __tablename__ = "stock_movements"

    id = db.Column(ID_TYPE, primary_key=True)
    product_id = db.Column(ID_TYPE, db.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False)
    movement_type = db.Column(db.String(24), nullable=False)
    quantity_delta = db.Column(db.Integer, nullable=False)
    previous_stock = db.Column(db.Integer, nullable=False)
    new_stock = db.Column(db.Integer, nullable=False)
    sale_item_id = db.Column(ID_TYPE, db.ForeignKey("sale_items.id", ondelete="RESTRICT"))
    purchase_item_id = db.Column(ID_TYPE, db.ForeignKey("purchase_items.id", ondelete="RESTRICT"))
    return_item_id = db.Column(ID_TYPE, db.ForeignKey("return_items.id", ondelete="RESTRICT"))
    reason = db.Column(db.Text)
    recorded_by = db.Column(ID_TYPE, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    sale_item = db.relationship("SaleItem")
    purchase_item = db.relationship("PurchaseItem")
    return_item = db.relationship("ReturnItem")

    __table_args__ = (
        db.Index("idx_stock_movements_product_date", "product_id", "occurred_at"),
        db.CheckConstraint(
            "movement_type IN ('sale', 'purchase_receipt', 'sales_return', 'purchase_return', "
            "'adjustment', 'damage', 'missing')",
            name="ck_stock_movements_type",
        ),
        db.CheckConstraint("quantity_delta <> 0", name="ck_stock_movements_delta"),
        db.CheckConstraint("previous_stock >= 0 AND new_stock >= 0", name="ck_stock_movements_stock"),
        db.CheckConstraint("new_stock = previous_stock + quantity_delta", name="ck_stock_movements_balance"),
        db.CheckConstraint(
            "(CASE WHEN sale_item_id IS NOT NULL THEN 1 ELSE 0 END) "
            "+ (CASE WHEN purchase_item_id IS NOT NULL THEN 1 ELSE 0 END) "
            "+ (CASE WHEN return_item_id IS NOT NULL THEN 1 ELSE 0 END) <= 1",
            name="ck_stock_movements_single_reference",
        ),
    )


class Expense(db.Model):
    __tablename__ = "expenses"

    id = db.Column(ID_TYPE, primary_key=True)
    category = db.Column(db.String(80), nullable=False)
    description = db.Column(db.Text, nullable=False)
    amount_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    expense_date = db.Column(db.Date, nullable=False, server_default=db.func.current_date())
    payment_method = db.Column(db.String(24), nullable=False)
    recorded_by = db.Column(ID_TYPE, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    data_origin = db.Column(db.String(20), nullable=False, default="business", server_default="business")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    __table_args__ = (
        db.Index("idx_expenses_date_category", "expense_date", "category"),
        db.CheckConstraint(
            "category IN ('shop_rent', 'electricity', 'water', 'transport', 'salaries', "
            "'repairs_maintenance', 'internet_telephone', 'other')",
            name="ck_expenses_category",
        ),
        db.CheckConstraint("amount_lkr > 0", name="ck_expenses_amount_positive"),
        db.CheckConstraint(
            "payment_method IN ('cash', 'card', 'bank_transfer', 'other')",
            name="ck_expenses_payment_method",
        ),
        db.CheckConstraint("data_origin IN ('business', 'synthetic_demo')", name="ck_expenses_origin"),
    )


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (
        db.Index("idx_audit_logs_entity", "entity_type", "entity_id", "occurred_at"),
    )

    id = db.Column(ID_TYPE, primary_key=True)
    user_id = db.Column(ID_TYPE, db.ForeignKey("users.id", ondelete="SET NULL"))
    action = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(db.String(80))
    before_data = db.Column(db.JSON().with_variant(JSONB, "postgresql"))
    after_data = db.Column(db.JSON().with_variant(JSONB, "postgresql"))
    ip_address = db.Column(db.String(45).with_variant(INET, "postgresql"))
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False, server_default=db.func.now())
