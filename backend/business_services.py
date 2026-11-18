"""Transactional business services used by the sales and billing API."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from extensions import db
from models.catalog import Customer, Inventory, Product
from models.identity import User
from models.operations import AuditLog, StockMovement
from models.sales import Payment, Sale, SaleItem

CENT = Decimal("0.01")
PAYMENT_METHODS = {"cash", "card", "bank_transfer", "other"}


class BusinessError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _existing_idempotent_sale(key: str, fingerprint: str, user: User) -> Sale | None:
    existing = Sale.query.filter_by(idempotency_key=key).first()
    if existing is None:
        return None
    if existing.created_by == user.id and existing.request_fingerprint == fingerprint:
        return existing
    raise BusinessError("idempotency_key has already been used for a different request", 409)


def parse_money(value, field: str, *, allow_zero: bool = True) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        raise BusinessError(f"{field} must be a valid LKR amount")
    if not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0):
        qualifier = "greater than zero" if not allow_zero else "zero or greater"
        raise BusinessError(f"{field} must be {qualifier}")
    return amount


def parse_quantity(value, field: str = "quantity") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise BusinessError(f"{field} must be a positive whole number")
    return value


def payment_totals(sale: Sale) -> tuple[Decimal, Decimal, str]:
    paid = sum(
        (payment.amount_lkr for payment in sale.payments if payment.status == "recorded"),
        start=Decimal("0.00"),
    ).quantize(CENT)
    balance = (sale.total_amount_lkr - paid).quantize(CENT)
    if balance <= 0:
        status = "paid"
    elif paid:
        status = "partially_paid"
    else:
        status = "unpaid"
    return paid, max(balance, Decimal("0.00")), status


def serialize_sale(sale: Sale, user: User) -> dict:
    paid, balance, payment_status = payment_totals(sale)
    result = {
        "id": sale.id,
        "invoice_number": sale.invoice_number,
        "sale_date": sale.sale_date.isoformat(),
        "status": sale.status,
        "sale_type": sale.sale_type,
        "customer": {
            "id": sale.customer.id,
            "customer_code": sale.customer.customer_code,
            "name": sale.customer.name,
            "contact_number": sale.customer.contact_number,
            "email": sale.customer.email,
        },
        "items": [
            {
                "product_id": item.product_id,
                "product_code": item.product.product_code,
                "product_name": item.product.name,
                "quantity": item.quantity,
                "unit_price_lkr": float(item.unit_price_lkr),
                "discount_lkr": float(item.discount_lkr),
                "line_total_lkr": float(item.line_total_lkr),
            }
            for item in sale.items
        ],
        "subtotal_lkr": float(sale.subtotal_lkr),
        "discount_lkr": float(sale.discount_lkr),
        "total_amount_lkr": float(sale.total_amount_lkr),
        "amount_paid_lkr": float(paid),
        "balance_due_lkr": float(balance),
        "payment_status": payment_status,
        "payments": [
            {
                "amount_lkr": float(payment.amount_lkr),
                "payment_method": payment.payment_method,
                "payment_date": payment.payment_date.isoformat(),
            }
            for payment in sale.payments
            if payment.status == "recorded"
        ],
    }
    if user.role.name == "owner":
        result["gross_profit_lkr"] = float(
            sum((item.gross_profit_lkr for item in sale.items), start=Decimal("0.00"))
        )
    return result


def create_sale(payload: dict, user: User) -> Sale:
    idempotency_key = payload.get("idempotency_key")
    if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 80:
        raise BusinessError("idempotency_key must contain 8 to 80 characters")

    customer_id = payload.get("customer_id")
    if isinstance(customer_id, bool) or not isinstance(customer_id, int):
        raise BusinessError("customer_id is required")
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        raise BusinessError("Customer not found", 404)

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items or len(raw_items) > 100:
        raise BusinessError("A sale must contain between 1 and 100 product lines")

    line_inputs: dict[int, tuple[int, Decimal]] = {}
    for row in raw_items:
        if not isinstance(row, dict):
            raise BusinessError("Each sale item must be an object")
        product_id = row.get("product_id")
        if isinstance(product_id, bool) or not isinstance(product_id, int):
            raise BusinessError("Each sale item requires a valid product_id")
        if product_id in line_inputs:
            raise BusinessError("A product may appear only once in the sale")
        quantity = parse_quantity(row.get("quantity"))
        discount = parse_money(row.get("discount_lkr", 0), "discount_lkr")
        if discount and user.role.name != "owner":
            raise BusinessError("Only an owner may apply a discount", 403)
        line_inputs[product_id] = (quantity, discount)

    normalized_request = {
        "customer_id": customer_id,
        "items": [
            {"product_id": product_id, "quantity": quantity, "discount_lkr": str(discount)}
            for product_id, (quantity, discount) in sorted(line_inputs.items())
        ],
        "amount_paid_lkr": str(parse_money(payload.get("amount_paid_lkr", 0), "amount_paid_lkr")),
        "payment_method": payload.get("payment_method", "credit"),
        "notes": str(payload.get("notes", "")).strip()[:1000] or None,
    }
    fingerprint = hashlib.sha256(
        json.dumps(normalized_request, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    existing_sale = _existing_idempotent_sale(idempotency_key, fingerprint, user)
    if existing_sale:
        return existing_sale

    product_ids = sorted(line_inputs)
    products = {
        product.id: product
        for product in db.session.scalars(
            select(Product).where(Product.id.in_(product_ids)).order_by(Product.id)
        ).all()
    }
    if len(products) != len(product_ids):
        raise BusinessError("One or more products were not found", 404)
    if any(not product.is_active for product in products.values()):
        raise BusinessError("Inactive products cannot be sold")

    stocks = {
        inventory.product_id: inventory
        for inventory in db.session.scalars(
            select(Inventory)
            .where(Inventory.product_id.in_(product_ids))
            .order_by(Inventory.product_id)
            .with_for_update()
        ).all()
    }
    existing_sale = _existing_idempotent_sale(idempotency_key, fingerprint, user)
    if existing_sale:
        return existing_sale

    for product_id, (quantity, _discount) in line_inputs.items():
        available = stocks[product_id].current_stock if product_id in stocks else 0
        if available < quantity:
            product = products[product_id]
            raise BusinessError(
                f"Insufficient stock for {product.name}: {available} available, {quantity} requested",
                409,
            )

    line_values = []
    subtotal = Decimal("0.00")
    discount_total = Decimal("0.00")
    for product_id, (quantity, discount) in line_inputs.items():
        product = products[product_id]
        inventory = stocks[product_id]
        unit_price = (
            product.wholesale_price_lkr
            if customer.customer_type == "wholesale"
            else product.retail_price_lkr
        )
        unit_cost = inventory.weighted_average_cost_lkr or product.unit_cost_lkr
        gross = (unit_price * quantity).quantize(CENT)
        if discount > gross:
            raise BusinessError(f"Discount exceeds line amount for {product.name}")
        line_total = gross - discount
        subtotal += gross
        discount_total += discount
        line_values.append((product, inventory, quantity, unit_price, unit_cost, discount, line_total))

    subtotal = subtotal.quantize(CENT)
    discount_total = discount_total.quantize(CENT)
    total = subtotal - discount_total
    amount_paid = Decimal(normalized_request["amount_paid_lkr"])
    if amount_paid > total:
        raise BusinessError("Amount paid cannot exceed the invoice total")
    payment_method = payload.get("payment_method", "credit")
    if amount_paid and payment_method not in PAYMENT_METHODS:
        raise BusinessError("A valid payment method is required for payments")
    if not amount_paid and payment_method not in PAYMENT_METHODS | {"credit"}:
        raise BusinessError("Unsupported payment method")

    sale = Sale(
        invoice_number=f"AP-{uuid4().hex[:12].upper()}",
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        customer=customer,
        created_by=user.id,
        sale_type=customer.customer_type,
        subtotal_lkr=subtotal,
        discount_lkr=discount_total,
        total_amount_lkr=total,
        notes=normalized_request["notes"],
        data_origin="business",
    )
    db.session.add(sale)
    for product, inventory, quantity, unit_price, unit_cost, discount, line_total in line_values:
        item = SaleItem(
            sale=sale,
            product=product,
            quantity=quantity,
            unit_price_lkr=unit_price,
            unit_cost_lkr=unit_cost,
            discount_lkr=discount,
            line_total_lkr=line_total,
            gross_profit_lkr=line_total - unit_cost * quantity,
        )
        db.session.add(item)
        previous_stock = inventory.current_stock
        inventory.current_stock -= quantity
        db.session.add(
            StockMovement(
                product_id=product.id,
                movement_type="sale",
                quantity_delta=-quantity,
                previous_stock=previous_stock,
                new_stock=inventory.current_stock,
                sale_item=item,
                recorded_by=user.id,
                reason=f"Invoice {sale.invoice_number}",
            )
        )

    if amount_paid:
        db.session.add(
            Payment(
                sale=sale,
                amount_lkr=amount_paid,
                payment_method=payment_method,
                received_by=user.id,
            )
        )
    db.session.add(
        AuditLog(
            user_id=user.id,
            action="sale_created",
            entity_type="sale",
            entity_id=sale.invoice_number,
            after_data={
                "total_amount_lkr": str(total),
                "item_count": len(line_values),
                "data_origin": "business",
            },
        )
    )

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing_sale = Sale.query.filter_by(idempotency_key=idempotency_key).first()
        if existing_sale and existing_sale.created_by == user.id and existing_sale.request_fingerprint == fingerprint:
            return existing_sale
        raise BusinessError("The sale could not be saved because a record already exists", 409)
    except SQLAlchemyError:
        db.session.rollback()
        raise
    return sale


def record_payment(sale: Sale, user: User, payload: dict) -> Payment:
    if sale.status != "completed":
        raise BusinessError("Payments can only be recorded on completed invoices", 409)
    amount = parse_money(payload.get("amount_lkr"), "amount_lkr", allow_zero=False)
    payment_method = payload.get("payment_method")
    if payment_method not in PAYMENT_METHODS:
        raise BusinessError("A valid payment method is required")
    _paid, balance, _status = payment_totals(sale)
    if amount > balance:
        raise BusinessError("Payment exceeds the invoice balance")

    payment = Payment(
        sale=sale,
        amount_lkr=amount,
        payment_method=payment_method,
        reference_number=str(payload.get("reference_number", "")).strip()[:120] or None,
        received_by=user.id,
    )
    db.session.add(payment)
    db.session.add(
        AuditLog(
            user_id=user.id,
            action="payment_recorded",
            entity_type="sale",
            entity_id=sale.invoice_number,
            after_data={"amount_lkr": str(amount), "payment_method": payment_method},
        )
    )
    try:
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        raise
    return payment
