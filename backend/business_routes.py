"""Product, customer, supplier, sales, invoice, and payment API routes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import csv
from decimal import Decimal
from io import BytesIO
import os
from urllib.parse import quote
from uuid import uuid4
from xml.sax.saxutils import escape

from flask import Blueprint, Response, g, jsonify, request
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from auth import roles_required
from business_services import (
    BusinessError,
    create_sale,
    parse_money,
    parse_quantity,
    payment_totals,
    record_payment,
    serialize_sale,
)
from extensions import db
from models.catalog import Category, Customer, Inventory, Product, Supplier
from models.identity import User
from models.operations import AuditLog
from models.purchasing import Purchase, PurchaseItem
from models.sales import Payment, Sale, SaleItem

business_bp = Blueprint("business", __name__, url_prefix="/api")
PAYMENT_METHODS = {"cash", "card", "bank_transfer", "other"}
CUSTOMER_TYPES = {"retail", "wholesale"}


def _body() -> dict:
    if not request.is_json:
        return {}
    payload = request.get_json()
    return payload if isinstance(payload, dict) else {}


def _required_text(payload: dict, key: str, max_length: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise BusinessError(f"{key} is required")
    value = value.strip()
    if len(value) > max_length:
        raise BusinessError(f"{key} cannot exceed {max_length} characters")
    return value


def _optional_text(payload: dict, key: str, max_length: int) -> str | None:
    value = payload.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BusinessError(f"{key} must be text")
    value = value.strip()
    if len(value) > max_length:
        raise BusinessError(f"{key} cannot exceed {max_length} characters")
    return value or None


def _nonnegative_int(value, field: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BusinessError(f"{field} must be a non-negative whole number")
    return value


def _audit(user: User, action: str, entity_type: str, entity_id: str, after: dict) -> None:
    db.session.add(
        AuditLog(
            user_id=user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            after_data=after,
            ip_address=request.remote_addr,
        )
    )


def _commit_or_conflict() -> None:
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise BusinessError("A record with the supplied code or identifier already exists", 409) from exc


def _product_json(product: Product) -> dict:
    return {
        "id": product.id,
        "product_id": product.product_code,
        "product_code": product.product_code,
        "product_name": product.name,
        "name": product.name,
        "category": product.category.name,
        "category_id": product.category_id,
        "brand": product.brand,
        "compatible_vehicle_make": product.compatible_vehicle_make,
        "vehicle_model": product.vehicle_model,
        "part_number": product.part_number,
        "barcode": product.barcode,
        "unit_cost_lkr": float(product.unit_cost_lkr),
        "retail_price_lkr": float(product.retail_price_lkr),
        "wholesale_price_lkr": float(product.wholesale_price_lkr),
        "reorder_level": product.reorder_level,
        "current_stock": product.inventory.current_stock if product.inventory else 0,
        "minimum_stock_level": product.inventory.minimum_stock_level if product.inventory else 0,
        "maximum_stock_level": product.inventory.maximum_stock_level if product.inventory else 0,
        "is_active": product.is_active,
    }


def _customer_json(customer: Customer) -> dict:
    return {
        "id": customer.id,
        "customer_id": customer.customer_code,
        "customer_code": customer.customer_code,
        "customer_name": customer.name,
        "name": customer.name,
        "customer_type": customer.customer_type,
        "contact_number": customer.contact_number,
        "email": customer.email,
        "address": customer.address,
        "city": customer.city,
        "district": customer.district,
        "province": customer.province,
        "registered_at": customer.registered_at.isoformat(),
    }


def _supplier_json(supplier: Supplier) -> dict:
    return {
        "id": supplier.id,
        "supplier_id": supplier.supplier_code,
        "supplier_code": supplier.supplier_code,
        "supplier_name": supplier.name,
        "name": supplier.name,
        "company_name": supplier.company_name,
        "contact_person": supplier.contact_person,
        "contact_number": supplier.contact_number,
        "email": supplier.email,
        "address": supplier.address,
        "city": supplier.city,
        "district": supplier.district,
        "province": supplier.province,
        "supplier_type": supplier.supplier_type,
        "payment_terms": supplier.payment_terms,
        "opening_balance_lkr": float(supplier.opening_balance_lkr),
        "is_active": supplier.is_active,
        "registered_at": supplier.registered_at.isoformat(),
    }


def _search_query(query, columns, search: str):
    term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if not term:
        return query
    pattern = f"%{term}%"
    return query.filter(or_(*(column.ilike(pattern, escape="\\") for column in columns)))


@business_bp.errorhandler(BusinessError)
def handle_business_error(error: BusinessError):
    return jsonify({"error": str(error)}), error.status_code


@business_bp.get("/products/lookup")
@roles_required("owner", "staff")
def product_lookup():
    query = Product.query.options(joinedload(Product.category), joinedload(Product.inventory)).filter(
        Product.is_active.is_(True)
    )
    search = request.args.get("search", "")
    query = _search_query(
        query,
        [Product.product_code, Product.name, Product.part_number, Product.barcode],
        search,
    )
    items = query.order_by(Product.name).limit(50).all()
    return jsonify(
        {
            "items": [
                {
                    "id": item.id,
                    "product_code": item.product_code,
                    "name": item.name,
                    "brand": item.brand,
                    "retail_price_lkr": float(item.retail_price_lkr),
                    "wholesale_price_lkr": float(item.wholesale_price_lkr),
                    "current_stock": item.inventory.current_stock if item.inventory else 0,
                }
                for item in items
            ]
        }
    )


def _product_values(payload: dict, *, partial: bool = False) -> dict:
    values = {}
    text_fields = {
        "product_code": 40,
        "name": 200,
        "brand": 100,
        "compatible_vehicle_make": 100,
        "vehicle_model": 100,
        "part_number": 100,
        "barcode": 100,
        "description": 4000,
    }
    required = {"product_code", "name", "brand"}
    for key, max_length in text_fields.items():
        if key in payload or (not partial and key in required):
            if key in required:
                values[key] = _required_text(payload, key, max_length)
            else:
                values[key] = _optional_text(payload, key, max_length)
    for key in ("unit_cost_lkr", "retail_price_lkr", "wholesale_price_lkr"):
        if key in payload or not partial:
            values[key] = parse_money(payload.get(key), key, allow_zero=False)
    if "reorder_level" in payload or not partial:
        values["reorder_level"] = _nonnegative_int(payload.get("reorder_level"), "reorder_level")
    if "category" in payload or not partial:
        values["category_name"] = _required_text(payload, "category", 100)
    if "is_active" in payload:
        if not isinstance(payload["is_active"], bool):
            raise BusinessError("is_active must be a boolean")
        values["is_active"] = payload["is_active"]
    return values


def _category(name: str) -> Category:
    category = Category.query.filter(func.lower(Category.name) == name.lower()).first()
    if category is None:
        category = Category(name=name)
        db.session.add(category)
        db.session.flush()
    return category


@business_bp.get("/operations/products")
@roles_required("owner")
def list_managed_products():
    query = Product.query.options(joinedload(Product.category), joinedload(Product.inventory))
    query = _search_query(query, [Product.product_code, Product.name, Product.part_number], request.args.get("search", ""))
    items = query.order_by(Product.name).limit(500).all()
    return jsonify({"items": [_product_json(item) for item in items]})


@business_bp.post("/operations/products")
@roles_required("owner")
def create_product():
    payload = _body()
    values = _product_values(payload)
    stock = _nonnegative_int(payload.get("initial_stock"), "initial_stock")
    minimum = _nonnegative_int(payload.get("minimum_stock_level"), "minimum_stock_level")
    maximum = _nonnegative_int(payload.get("maximum_stock_level"), "maximum_stock_level", default=stock)
    if maximum < minimum:
        raise BusinessError("maximum_stock_level must be at least minimum_stock_level")

    product = Product(
        product_code=values["product_code"],
        name=values["name"],
        category=_category(values.pop("category_name")),
        brand=values["brand"],
        compatible_vehicle_make=values.get("compatible_vehicle_make"),
        vehicle_model=values.get("vehicle_model"),
        part_number=values.get("part_number"),
        barcode=values.get("barcode"),
        description=values.get("description"),
        unit_cost_lkr=values["unit_cost_lkr"],
        retail_price_lkr=values["retail_price_lkr"],
        wholesale_price_lkr=values["wholesale_price_lkr"],
        reorder_level=values["reorder_level"],
    )
    db.session.add(product)
    db.session.flush()
    inventory = Inventory(
        product=product,
        current_stock=stock,
        minimum_stock_level=minimum,
        maximum_stock_level=maximum,
        weighted_average_cost_lkr=values["unit_cost_lkr"],
    )
    db.session.add(inventory)
    _audit(g.current_user, "product_created", "product", product.product_code, {"name": product.name})
    _commit_or_conflict()
    return jsonify({"item": _product_json(product)}), 201


@business_bp.post("/operations/products/import-csv")
@roles_required("owner")
def import_products_from_csv():
    """One-time, non-destructive import of the synthetic product and inventory CSVs."""
    products_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset", "products.csv"))
    inventory_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset", "inventory.csv"))
    if not os.path.isfile(products_path) or not os.path.isfile(inventory_path):
        raise BusinessError("Product or inventory CSV file is missing", 404)

    inventory_by_product = {}
    with open(inventory_path, "r", encoding="utf-8-sig", newline="") as inventory_file:
        for row in csv.DictReader(inventory_file):
            inventory_by_product[row["product_id"].strip()] = {
                "current_stock": int(row["current_stock"]),
                "minimum_stock_level": int(row["minimum_stock_level"]),
                "maximum_stock_level": int(row["maximum_stock_level"]),
            }

    imported = 0
    skipped = 0
    with open(products_path, "r", encoding="utf-8-sig", newline="") as products_file:
        for row in csv.DictReader(products_file):
            product_code = (row.get("product_id") or "").strip()
            if not product_code:
                skipped += 1
                continue
            existing = Product.query.filter_by(product_code=product_code).first()
            if existing:
                skipped += 1
                continue

            values = {
                "product_code": product_code,
                "name": (row.get("product_name") or "").strip(),
                "category_name": (row.get("category") or "").strip(),
                "brand": (row.get("brand") or "").strip(),
                "compatible_vehicle_make": (row.get("compatible_vehicle_make") or "").strip() or None,
                "part_number": (row.get("part_number") or "").strip() or None,
            }
            if not values["name"] or not values["category_name"] or not values["brand"]:
                skipped += 1
                continue

            try:
                unit_cost = parse_money(row.get("unit_cost_lkr"), "unit_cost_lkr", allow_zero=False)
                retail_price = parse_money(row.get("retail_price_lkr"), "retail_price_lkr", allow_zero=False)
                wholesale_price = parse_money(row.get("wholesale_price_lkr"), "wholesale_price_lkr", allow_zero=False)
                reorder_level = int(row.get("reorder_level") or 0)
            except (TypeError, ValueError):
                skipped += 1
                continue

            stock = inventory_by_product.get(product_code, {
                "current_stock": 0,
                "minimum_stock_level": reorder_level,
                "maximum_stock_level": max(reorder_level * 10, reorder_level),
            })
            if stock["maximum_stock_level"] < stock["minimum_stock_level"]:
                stock["maximum_stock_level"] = stock["minimum_stock_level"]

            product = Product(
                product_code=product_code,
                name=values["name"],
                category=_category(values["category_name"]),
                brand=values["brand"],
                compatible_vehicle_make=values["compatible_vehicle_make"],
                part_number=values["part_number"],
                unit_cost_lkr=unit_cost,
                retail_price_lkr=retail_price,
                wholesale_price_lkr=wholesale_price,
                reorder_level=reorder_level,
                is_active=(row.get("is_active", "True").strip().lower() in {"true", "1", "yes"}),
                data_origin="synthetic_demo",
            )
            db.session.add(product)
            db.session.flush()
            db.session.add(Inventory(
                product=product,
                current_stock=stock["current_stock"],
                minimum_stock_level=stock["minimum_stock_level"],
                maximum_stock_level=stock["maximum_stock_level"],
                weighted_average_cost_lkr=unit_cost,
            ))
            imported += 1

    _audit(g.current_user, "products_imported_csv", "product", "dataset/products.csv", {
        "imported": imported,
        "skipped": skipped,
    })
    _commit_or_conflict()
    return jsonify({"imported": imported, "skipped": skipped})


@business_bp.patch("/operations/products/<int:product_id>")
@roles_required("owner")
def update_product(product_id: int):
    product = db.session.get(
        Product,
        product_id,
        options=[joinedload(Product.category), joinedload(Product.inventory)],
    )
    if product is None:
        return jsonify({"error": "Product not found"}), 404
    values = _product_values(_body(), partial=True)
    before = {"name": product.name, "product_code": product.product_code, "is_active": product.is_active}
    for key in (
        "product_code", "name", "brand", "compatible_vehicle_make", "vehicle_model",
        "part_number", "barcode", "description", "unit_cost_lkr", "retail_price_lkr",
        "wholesale_price_lkr", "reorder_level", "is_active",
    ):
        if key in values:
            setattr(product, key, values[key])
    if "category_name" in values:
        product.category = _category(values["category_name"])
    _audit(g.current_user, "product_updated", "product", product.product_code, {
        "before": before,
        "name": product.name,
        "is_active": product.is_active,
    })
    _commit_or_conflict()
    return jsonify({"item": _product_json(product)})


@business_bp.delete("/operations/products/<int:product_id>")
@roles_required("owner")
def deactivate_product(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        return jsonify({"error": "Product not found"}), 404
    product.is_active = False
    _audit(g.current_user, "product_deactivated", "product", product.product_code, {"is_active": False})
    _commit_or_conflict()
    return "", 204


@business_bp.get("/customers/lookup")
@roles_required("owner", "staff")
def customer_lookup():
    query = Customer.query
    query = _search_query(query, [Customer.customer_code, Customer.name, Customer.contact_number], request.args.get("search", ""))
    items = query.order_by(Customer.name).limit(50).all()
    return jsonify({"items": [_customer_json(item) for item in items]})


@business_bp.get("/operations/customers")
@roles_required("owner", "staff")
def list_managed_customers():
    query = Customer.query
    query = _search_query(query, [Customer.customer_code, Customer.name, Customer.contact_number], request.args.get("search", ""))
    items = query.order_by(Customer.name).limit(500).all()
    return jsonify({"items": [_customer_json(item) for item in items]})


@business_bp.post("/operations/customers")
@roles_required("owner", "staff")
def create_customer():
    payload = _body()
    name = _required_text(payload, "name", 200)
    customer_type = payload.get("customer_type", "retail")
    if customer_type not in CUSTOMER_TYPES:
        raise BusinessError("customer_type must be retail or wholesale")
    code = _optional_text(payload, "customer_code", 40) or f"CUS-{uuid4().hex[:8].upper()}"
    opening_balance = parse_money(payload.get("opening_balance_lkr", 0), "opening_balance_lkr")
    if opening_balance and g.current_user.role.name != "owner":
        raise BusinessError("Only an owner may set an opening balance", 403)
    customer = Customer(
        customer_code=code,
        name=name,
        customer_type=customer_type,
        contact_number=_optional_text(payload, "contact_number", 40),
        email=_optional_text(payload, "email", 254),
        address=_optional_text(payload, "address", 2000),
        city=_optional_text(payload, "city", 100),
        district=_optional_text(payload, "district", 100),
        province=_optional_text(payload, "province", 100),
        opening_balance_lkr=opening_balance,
        data_origin="business",
    )
    db.session.add(customer)
    db.session.flush()
    _audit(g.current_user, "customer_created", "customer", customer.customer_code, {"name": name})
    _commit_or_conflict()
    return jsonify({"item": _customer_json(customer)}), 201


@business_bp.post("/operations/customers/import-csv")
@roles_required("owner")
def import_customers_from_csv():
    """Import the synthetic customer master without replacing existing live records."""
    customers_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset", "customers.csv"))
    if not os.path.isfile(customers_path):
        raise BusinessError("dataset/customers.csv is missing", 404)

    imported = 0
    skipped = 0
    with open(customers_path, "r", encoding="utf-8-sig", newline="") as customers_file:
        for row in csv.DictReader(customers_file):
            customer_code = (row.get("customer_id") or "").strip()
            name = (row.get("customer_name") or "").strip()
            if not customer_code or not name:
                skipped += 1
                continue
            if Customer.query.filter_by(customer_code=customer_code).first():
                skipped += 1
                continue

            customer_type = (row.get("customer_type") or "retail").strip().lower()
            if customer_type not in CUSTOMER_TYPES:
                skipped += 1
                continue
            try:
                registered_at = date.fromisoformat(row["registration_date"])
            except (KeyError, TypeError, ValueError):
                registered_at = date.today()

            db.session.add(Customer(
                customer_code=customer_code,
                name=name,
                customer_type=customer_type,
                contact_number=(row.get("contact_number_synthetic") or "").strip() or None,
                city=(row.get("city") or "").strip() or None,
                district=(row.get("district") or "").strip() or None,
                province=(row.get("province") or "").strip() or None,
                registered_at=registered_at,
                data_origin="synthetic_demo",
            ))
            imported += 1

    _audit(g.current_user, "customers_imported_csv", "customer", "dataset/customers.csv", {
        "imported": imported,
        "skipped": skipped,
    })
    _commit_or_conflict()
    return jsonify({"imported": imported, "skipped": skipped})


@business_bp.patch("/operations/customers/<int:customer_id>")
@roles_required("owner", "staff")
def update_customer(customer_id: int):
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        return jsonify({"error": "Customer not found"}), 404
    payload = _body()
    text_fields = {
        "name": 200, "contact_number": 40, "email": 254, "address": 2000,
        "city": 100, "district": 100, "province": 100,
    }
    for key, max_length in text_fields.items():
        if key in payload:
            value = _required_text(payload, key, max_length) if key == "name" else _optional_text(payload, key, max_length)
            setattr(customer, key, value)
    if "customer_type" in payload:
        if payload["customer_type"] not in CUSTOMER_TYPES:
            raise BusinessError("customer_type must be retail or wholesale")
        customer.customer_type = payload["customer_type"]
    if "opening_balance_lkr" in payload:
        if g.current_user.role.name != "owner":
            raise BusinessError("Only an owner may update opening balances", 403)
        customer.opening_balance_lkr = parse_money(payload["opening_balance_lkr"], "opening_balance_lkr")
    _audit(g.current_user, "customer_updated", "customer", customer.customer_code, {"name": customer.name})
    _commit_or_conflict()
    return jsonify({"item": _customer_json(customer)})


@business_bp.get("/operations/suppliers")
@roles_required("owner")
def list_managed_suppliers():
    query = Supplier.query
    query = _search_query(query, [Supplier.supplier_code, Supplier.name, Supplier.contact_number], request.args.get("search", ""))
    items = query.order_by(Supplier.name).limit(500).all()
    return jsonify({"items": [_supplier_json(item) for item in items]})


@business_bp.post("/operations/suppliers")
@roles_required("owner")
def create_supplier():
    payload = _body()
    name = _required_text(payload, "name", 200)
    code = _optional_text(payload, "supplier_code", 40) or f"SUP-{uuid4().hex[:8].upper()}"
    supplier = Supplier(
        supplier_code=code,
        name=name,
        company_name=_optional_text(payload, "company_name", 200),
        contact_person=_optional_text(payload, "contact_person", 160),
        contact_number=_optional_text(payload, "contact_number", 40),
        email=_optional_text(payload, "email", 254),
        address=_optional_text(payload, "address", 2000),
        city=_optional_text(payload, "city", 100),
        district=_optional_text(payload, "district", 100),
        province=_optional_text(payload, "province", 100),
        supplier_type=_optional_text(payload, "supplier_type", 80),
        payment_terms=_optional_text(payload, "payment_terms", 100),
        opening_balance_lkr=parse_money(payload.get("opening_balance_lkr", 0), "opening_balance_lkr"),
        data_origin="business",
    )
    db.session.add(supplier)
    db.session.flush()
    _audit(g.current_user, "supplier_created", "supplier", supplier.supplier_code, {"name": name})
    _commit_or_conflict()
    return jsonify({"item": _supplier_json(supplier)}), 201


@business_bp.post("/operations/suppliers/import-csv")
@roles_required("owner")
def import_suppliers_from_csv():
    """Import the synthetic supplier master without replacing existing live records."""
    suppliers_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset", "suppliers.csv"))
    if not os.path.isfile(suppliers_path):
        raise BusinessError("dataset/suppliers.csv is missing", 404)

    imported = 0
    skipped = 0
    with open(suppliers_path, "r", encoding="utf-8-sig", newline="") as suppliers_file:
        for row in csv.DictReader(suppliers_file):
            supplier_code = (row.get("supplier_id") or "").strip()
            name = (row.get("supplier_name") or "").strip()
            if not supplier_code or not name:
                skipped += 1
                continue
            if Supplier.query.filter_by(supplier_code=supplier_code).first():
                skipped += 1
                continue

            try:
                registered_at = date.fromisoformat(row["registration_date"])
            except (KeyError, TypeError, ValueError):
                registered_at = date.today()

            db.session.add(Supplier(
                supplier_code=supplier_code,
                name=name,
                contact_person=(row.get("contact_person") or "").strip() or None,
                city=(row.get("city") or "").strip() or None,
                district=(row.get("district") or "").strip() or None,
                province=(row.get("province") or "").strip() or None,
                supplier_type=(row.get("supplier_type") or "").strip() or None,
                payment_terms=(row.get("payment_terms") or "").strip() or None,
                registered_at=registered_at,
                data_origin="synthetic_demo",
            ))
            imported += 1

    _audit(g.current_user, "suppliers_imported_csv", "supplier", "dataset/suppliers.csv", {
        "imported": imported,
        "skipped": skipped,
    })
    _commit_or_conflict()
    return jsonify({"imported": imported, "skipped": skipped})


@business_bp.patch("/operations/suppliers/<int:supplier_id>")
@roles_required("owner")
def update_supplier(supplier_id: int):
    supplier = db.session.get(Supplier, supplier_id)
    if supplier is None:
        return jsonify({"error": "Supplier not found"}), 404
    payload = _body()
    fields = {
        "name": 200, "company_name": 200, "contact_person": 160, "contact_number": 40,
        "email": 254, "address": 2000, "city": 100, "district": 100, "province": 100,
        "supplier_type": 80, "payment_terms": 100,
    }
    for key, max_length in fields.items():
        if key in payload:
            value = _required_text(payload, key, max_length) if key == "name" else _optional_text(payload, key, max_length)
            setattr(supplier, key, value)
    if "opening_balance_lkr" in payload:
        supplier.opening_balance_lkr = parse_money(payload["opening_balance_lkr"], "opening_balance_lkr")
    _audit(g.current_user, "supplier_updated", "supplier", supplier.supplier_code, {"name": supplier.name})
    _commit_or_conflict()
    return jsonify({"item": _supplier_json(supplier)})


@business_bp.delete("/operations/suppliers/<int:supplier_id>")
@roles_required("owner")
def deactivate_supplier(supplier_id: int):
    supplier = db.session.get(Supplier, supplier_id)
    if supplier is None:
        return jsonify({"error": "Supplier not found"}), 404
    supplier.is_active = False
    _audit(g.current_user, "supplier_deactivated", "supplier", supplier.supplier_code, {"active": False})
    _commit_or_conflict()
    return "", 204


@business_bp.post("/operations/sales/import-csv")
@roles_required("owner")
def import_sales_from_csv():
    """Import historical synthetic invoices without changing current inventory."""
    sales_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset", "sales.csv"))
    if not os.path.isfile(sales_path):
        raise BusinessError("dataset/sales.csv is missing", 404)

    invoices = {}
    with open(sales_path, "r", encoding="utf-8-sig", newline="") as sales_file:
        for row in csv.DictReader(sales_file):
            invoice_number = (row.get("invoice_number") or "").strip()
            if not invoice_number:
                continue
            invoices.setdefault(invoice_number, []).append(row)

    imported = 0
    skipped = 0
    for invoice_number, rows in invoices.items():
        if Sale.query.filter_by(invoice_number=invoice_number).first():
            skipped += len(rows)
            continue

        customer_code = (rows[0].get("customer_id") or "").strip()
        customer = Customer.query.filter_by(customer_code=customer_code).first()
        if customer is None:
            skipped += len(rows)
            continue
        try:
            sale_date = datetime.combine(
                date.fromisoformat(rows[0]["sale_date"]),
                time.min,
                tzinfo=timezone.utc,
            )
        except (KeyError, TypeError, ValueError):
            skipped += len(rows)
            continue

        subtotal = Decimal("0.00")
        discount = Decimal("0.00")
        total = Decimal("0.00")
        parsed_items = []
        invalid = False
        for row in rows:
            product = Product.query.filter_by(product_code=(row.get("product_id") or "").strip()).first()
            if product is None:
                invalid = True
                break
            try:
                quantity = int(row["quantity"])
                unit_price = parse_money(row["unit_price"], "unit_price")
                unit_cost = parse_money(row["unit_cost"], "unit_cost")
                line_total = parse_money(row["total_amount"], "total_amount")
                gross_profit = parse_money(row.get("profit", 0), "profit")
            except (KeyError, TypeError, ValueError, BusinessError):
                invalid = True
                break
            if quantity <= 0:
                invalid = True
                break
            full_line = (unit_price * quantity).quantize(Decimal("0.01"))
            line_discount = (full_line - line_total).quantize(Decimal("0.01"))
            if line_discount < 0:
                invalid = True
                break
            subtotal += full_line
            discount += line_discount
            total += line_total
            parsed_items.append((product, quantity, unit_price, unit_cost, line_discount, line_total, gross_profit))

        if invalid:
            skipped += len(rows)
            continue

        subtotal = subtotal.quantize(Decimal("0.01"))
        discount = discount.quantize(Decimal("0.01"))
        total = total.quantize(Decimal("0.01"))
        sale = Sale(
            invoice_number=invoice_number,
            idempotency_key=f"CSV:{invoice_number}"[:80],
            customer_id=customer.id,
            created_by=g.current_user.id,
            sale_date=sale_date,
            sale_type=customer.customer_type,
            status="completed",
            subtotal_lkr=subtotal,
            discount_lkr=discount,
            total_amount_lkr=total,
            data_origin="synthetic_demo",
        )
        db.session.add(sale)
        db.session.flush()
        for product, quantity, unit_price, unit_cost, line_discount, line_total, gross_profit in parsed_items:
            db.session.add(SaleItem(
                sale_id=sale.id,
                product_id=product.id,
                quantity=quantity,
                unit_price_lkr=unit_price,
                unit_cost_lkr=unit_cost,
                discount_lkr=line_discount,
                line_total_lkr=line_total,
                gross_profit_lkr=gross_profit,
            ))
        if total > 0:
            db.session.add(Payment(
                sale_id=sale.id,
                amount_lkr=total,
                payment_method="cash",
                payment_date=sale_date,
                received_by=g.current_user.id,
                status="recorded",
                reference_number=f"CSV-{invoice_number}",
            ))
        imported += len(rows)

    _audit(g.current_user, "sales_imported_csv", "sale", "dataset/sales.csv", {
        "imported_lines": imported,
        "skipped_lines": skipped,
    })
    _commit_or_conflict()
    return jsonify({"imported_lines": imported, "skipped_lines": skipped})


@business_bp.post("/operations/purchases/import-csv")
@roles_required("owner")
def import_purchases_from_csv():
    """Import historical synthetic purchase records without changing current stock."""
    purchases_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dataset", "purchases.csv"))
    if not os.path.isfile(purchases_path):
        raise BusinessError("dataset/purchases.csv is missing", 404)

    imported = 0
    skipped = 0
    with open(purchases_path, "r", encoding="utf-8-sig", newline="") as purchases_file:
        for row in csv.DictReader(purchases_file):
            purchase_code = (row.get("purchase_id") or "").strip()
            supplier_code = (row.get("supplier_id") or "").strip()
            product_code = (row.get("product_id") or "").strip()
            if not purchase_code:
                skipped += 1
                continue
            if Purchase.query.filter_by(purchase_number=purchase_code).first():
                skipped += 1
                continue
            supplier = Supplier.query.filter_by(supplier_code=supplier_code).first()
            product = Product.query.filter_by(product_code=product_code).first()
            if supplier is None or product is None:
                skipped += 1
                continue
            try:
                purchase_date = datetime.combine(
                    date.fromisoformat(row["purchase_date"]),
                    time.min,
                    tzinfo=timezone.utc,
                )
                quantity = int(row["quantity_purchased"])
                csv_unit_cost = parse_money(row["unit_cost"], "unit_cost")
                csv_total_cost = parse_money(row["total_purchase_cost"], "total_purchase_cost")
            except (KeyError, TypeError, ValueError, BusinessError):
                skipped += 1
                continue
            if quantity <= 0:
                skipped += 1
                continue

            unit_cost = (csv_total_cost / quantity).quantize(Decimal("0.01"))
            subtotal = (unit_cost * quantity).quantize(Decimal("0.01"))
            total_cost = subtotal
            purchase = Purchase(
                purchase_number=purchase_code,
                supplier_id=supplier.id,
                supplier_invoice_number=purchase_code,
                purchase_date=purchase_date,
                status="received",
                subtotal_lkr=subtotal,
                discount_lkr=Decimal("0.00"),
                total_cost_lkr=total_cost,
                data_origin="synthetic_demo",
                created_by=g.current_user.id,
            )
            db.session.add(purchase)
            db.session.flush()
            imported += 1

    _audit(g.current_user, "purchases_imported_csv", "purchase", "dataset/purchases.csv", {
        "imported": imported,
        "skipped": skipped,
    })
    _commit_or_conflict()
    return jsonify({"imported": imported, "skipped": skipped})


@business_bp.post("/sales")
@roles_required("owner", "staff")
def post_sale():
    payload = _body()
    header_key = request.headers.get("Idempotency-Key")
    if header_key:
        if payload.get("idempotency_key") and payload["idempotency_key"] != header_key:
            raise BusinessError("Conflicting idempotency keys were supplied")
        payload["idempotency_key"] = header_key
    sale = create_sale(payload, g.current_user)
    response = jsonify({"sale": serialize_sale(sale, g.current_user)})
    response.status_code = 201
    response.headers["Location"] = f"/api/invoices/{quote(sale.invoice_number)}"
    return response


def _can_view_sale(sale: Sale) -> bool:
    return g.current_user.role.name == "owner" or sale.created_by == g.current_user.id


def _sale_query():
    return Sale.query.options(
        joinedload(Sale.customer),
        selectinload(Sale.items).joinedload(SaleItem.product),
        selectinload(Sale.payments),
    )


def _date_filter(query, name: str, *, end: bool = False):
    value = request.args.get(name)
    if not value:
        return query
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise BusinessError(f"{name} must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise BusinessError(f"{name} must use YYYY-MM-DD format")
    boundary = datetime.combine(parsed + (timedelta(days=1) if end else timedelta()), time.min, timezone.utc)
    return query.filter(Sale.sale_date < boundary if end else Sale.sale_date >= boundary)


@business_bp.get("/sales")
@roles_required("owner", "staff")
def list_sales():
    query = _sale_query()
    if g.current_user.role.name != "owner":
        query = query.filter(Sale.created_by == g.current_user.id)
    invoice = request.args.get("invoice", "").strip()
    if invoice:
        query = query.filter(Sale.invoice_number.ilike(f"%{invoice.replace('%', '\\%').replace('_', '\\_')}%", escape="\\"))
    customer = request.args.get("customer", "").strip()
    if customer:
        query = query.join(Customer).filter(Customer.name.ilike(f"%{customer.replace('%', '\\%').replace('_', '\\_')}%", escape="\\"))
    payment_method = request.args.get("payment_method")
    if payment_method:
        if payment_method not in PAYMENT_METHODS:
            raise BusinessError("Unsupported payment method")
        query = query.join(Payment).filter(Payment.payment_method == payment_method, Payment.status == "recorded")
    query = _date_filter(query, "start_date")
    query = _date_filter(query, "end_date", end=True)

    try:
        page = max(int(request.args.get("page", "1")), 1)
        per_page = min(max(int(request.args.get("per_page", "20")), 1), 100)
    except ValueError as exc:
        raise BusinessError("page and per_page must be whole numbers") from exc
    total = query.distinct().count()
    items = query.distinct().order_by(Sale.sale_date.desc(), Sale.id.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return jsonify(
        {
            "items": [serialize_sale(item, g.current_user) for item in items],
            "page": page,
            "per_page": per_page,
            "total": total,
        }
    )


def _get_sale(invoice_number: str) -> Sale | None:
    sale = _sale_query().filter_by(invoice_number=invoice_number).first()
    if sale and not _can_view_sale(sale):
        return None
    return sale


@business_bp.get("/invoices/<invoice_number>")
@roles_required("owner", "staff")
def invoice_detail(invoice_number: str):
    sale = _get_sale(invoice_number)
    if sale is None:
        return jsonify({"error": "Invoice not found"}), 404
    return jsonify({"sale": serialize_sale(sale, g.current_user)})


@business_bp.post("/invoices/<invoice_number>/payments")
@roles_required("owner", "staff")
def add_sale_payment(invoice_number: str):
    sale = Sale.query.filter_by(invoice_number=invoice_number).with_for_update().first()
    if sale is None or not _can_view_sale(sale):
        return jsonify({"error": "Invoice not found"}), 404
    payment = record_payment(sale, g.current_user, _body())
    return jsonify(
        {
            "payment": {
                "id": payment.id,
                "amount_lkr": float(payment.amount_lkr),
                "payment_method": payment.payment_method,
                "payment_date": payment.payment_date.isoformat(),
            },
            "sale": serialize_sale(sale, g.current_user),
        }
    ), 201


def _money_text(value) -> str:
    return f"LKR {Decimal(str(value)):,.2f}"


@business_bp.get("/invoices/<invoice_number>/pdf")
@roles_required("owner", "staff")
def invoice_pdf(invoice_number: str):
    sale = _get_sale(invoice_number)
    if sale is None:
        return jsonify({"error": "Invoice not found"}), 404
    data = serialize_sale(sale, g.current_user)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    styles = getSampleStyleSheet()
    business_name = escape(os.getenv("BUSINESS_NAME", "AutoParts Business"))
    contact = escape(os.getenv("BUSINESS_CONTACT", ""))
    story = [
        Paragraph(business_name, styles["Title"]),
        Paragraph(contact or "Sri Lanka", styles["Normal"]),
        Spacer(1, 8 * mm),
        Paragraph(f"<b>Invoice:</b> {escape(data['invoice_number'])}", styles["Heading2"]),
        Paragraph(f"<b>Date:</b> {escape(data['sale_date'][:19].replace('T', ' '))}", styles["Normal"]),
        Paragraph(f"<b>Customer:</b> {escape(data['customer']['name'])}", styles["Normal"]),
        Paragraph(f"<b>Type:</b> {escape(data['sale_type'].title())}", styles["Normal"]),
        Spacer(1, 6 * mm),
    ]
    rows = [["Code", "Product", "Qty", "Unit price", "Discount", "Amount"]]
    for item in data["items"]:
        rows.append(
            [
                escape(item["product_code"]),
                Paragraph(escape(item["product_name"]), styles["BodyText"]),
                str(item["quantity"]),
                _money_text(item["unit_price_lkr"]),
                _money_text(item["discount_lkr"]),
                _money_text(item["line_total_lkr"]),
            ]
        )
    table = Table(rows, colWidths=[22 * mm, 55 * mm, 12 * mm, 28 * mm, 26 * mm, 30 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eff6ff")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend(
        [
            table,
            Spacer(1, 5 * mm),
            Paragraph(f"Subtotal: {_money_text(data['subtotal_lkr'])}", styles["Normal"]),
            Paragraph(f"Discount: {_money_text(data['discount_lkr'])}", styles["Normal"]),
            Paragraph(f"<b>Total: {_money_text(data['total_amount_lkr'])}</b>", styles["Heading2"]),
            Paragraph(f"Amount paid: {_money_text(data['amount_paid_lkr'])}", styles["Normal"]),
            Paragraph(f"Balance due: {_money_text(data['balance_due_lkr'])}", styles["Normal"]),
            Paragraph(f"Payment status: {escape(data['payment_status'].replace('_', ' ').title())}", styles["Normal"]),
            Spacer(1, 8 * mm),
            Paragraph("Thank you for your business.", styles["Normal"]),
        ]
    )
    document.build(story)
    return Response(
        buffer.getvalue(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{sale.invoice_number}.pdf"'},
    )
