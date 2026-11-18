"""Product catalogue, customers, suppliers, and stock balances."""

from extensions import db
from models.base import ID_TYPE, TimestampMixin


class Category(db.Model, TimestampMixin):
    __tablename__ = "categories"

    id = db.Column(ID_TYPE, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    products = db.relationship("Product", back_populates="category")


class Product(db.Model, TimestampMixin):
    __tablename__ = "products"

    id = db.Column(ID_TYPE, primary_key=True)
    product_code = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(200), nullable=False)
    category_id = db.Column(ID_TYPE, db.ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False)
    brand = db.Column(db.String(100), nullable=False)
    compatible_vehicle_make = db.Column(db.String(100))
    vehicle_model = db.Column(db.String(100))
    part_number = db.Column(db.String(100), unique=True)
    barcode = db.Column(db.String(100), unique=True)
    description = db.Column(db.Text)
    unit_cost_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    retail_price_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    wholesale_price_lkr = db.Column(db.Numeric(14, 2), nullable=False)
    reorder_level = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    data_origin = db.Column(db.String(20), nullable=False, default="business", server_default="business")

    category = db.relationship("Category", back_populates="products")
    inventory = db.relationship("Inventory", back_populates="product", uselist=False)

    __table_args__ = (
        db.CheckConstraint("unit_cost_lkr >= 0", name="ck_products_cost_nonnegative"),
        db.CheckConstraint("retail_price_lkr >= 0", name="ck_products_retail_nonnegative"),
        db.CheckConstraint("wholesale_price_lkr >= 0", name="ck_products_wholesale_nonnegative"),
        db.CheckConstraint("reorder_level >= 0", name="ck_products_reorder_nonnegative"),
        db.CheckConstraint("data_origin IN ('business', 'synthetic_demo')", name="ck_products_origin"),
    )


class Customer(db.Model, TimestampMixin):
    __tablename__ = "customers"

    id = db.Column(ID_TYPE, primary_key=True)
    customer_code = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(200), nullable=False)
    customer_type = db.Column(db.String(20), nullable=False)
    contact_number = db.Column(db.String(40))
    email = db.Column(db.String(254))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    district = db.Column(db.String(100))
    province = db.Column(db.String(100))
    opening_balance_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    registered_at = db.Column(db.Date, nullable=False, server_default=db.func.current_date())
    data_origin = db.Column(db.String(20), nullable=False, default="business", server_default="business")

    __table_args__ = (
        db.CheckConstraint("customer_type IN ('retail', 'wholesale')", name="ck_customers_type"),
        db.CheckConstraint("opening_balance_lkr >= 0", name="ck_customers_opening_balance"),
        db.CheckConstraint("data_origin IN ('business', 'synthetic_demo')", name="ck_customers_origin"),
    )


class Supplier(db.Model, TimestampMixin):
    __tablename__ = "suppliers"

    id = db.Column(ID_TYPE, primary_key=True)
    supplier_code = db.Column(db.String(40), nullable=False, unique=True)
    name = db.Column(db.String(200), nullable=False)
    company_name = db.Column(db.String(200))
    contact_person = db.Column(db.String(160))
    contact_number = db.Column(db.String(40))
    email = db.Column(db.String(254))
    address = db.Column(db.Text)
    city = db.Column(db.String(100))
    district = db.Column(db.String(100))
    province = db.Column(db.String(100))
    supplier_type = db.Column(db.String(80))
    payment_terms = db.Column(db.String(100))
    opening_balance_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    registered_at = db.Column(db.Date, nullable=False, server_default=db.func.current_date())
    data_origin = db.Column(db.String(20), nullable=False, default="business", server_default="business")
    is_active = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())

    __table_args__ = (
        db.CheckConstraint("opening_balance_lkr >= 0", name="ck_suppliers_opening_balance"),
        db.CheckConstraint("data_origin IN ('business', 'synthetic_demo')", name="ck_suppliers_origin"),
    )


class Inventory(db.Model):
    __tablename__ = "inventory"

    id = db.Column(ID_TYPE, primary_key=True)
    product_id = db.Column(ID_TYPE, db.ForeignKey("products.id", ondelete="RESTRICT"), nullable=False, unique=True)
    current_stock = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    minimum_stock_level = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    maximum_stock_level = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    weighted_average_cost_lkr = db.Column(db.Numeric(14, 2), nullable=False, default=0, server_default="0")
    updated_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        server_default=db.func.now(),
        onupdate=db.func.now(),
    )

    product = db.relationship("Product", back_populates="inventory")

    __table_args__ = (
        db.CheckConstraint("current_stock >= 0", name="ck_inventory_stock_nonnegative"),
        db.CheckConstraint("minimum_stock_level >= 0", name="ck_inventory_min_nonnegative"),
        db.CheckConstraint(
            "maximum_stock_level >= minimum_stock_level",
            name="ck_inventory_max_gte_min",
        ),
        db.CheckConstraint("weighted_average_cost_lkr >= 0", name="ck_inventory_cost_nonnegative"),
    )
