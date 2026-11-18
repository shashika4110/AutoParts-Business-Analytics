from pathlib import Path
import re

import pytest

from app import create_app
from extensions import db
import models
from models.identity import Role, User


@pytest.fixture
def app():
    application = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite://",
            "JWT_SECRET_KEY": "test-key-not-for-production-32-bytes-minimum",
        }
    )
    with application.app_context():
        db.create_all()
        owner_role = Role(name="owner")
        staff_role = Role(name="staff")
        owner = User(
            role=owner_role,
            username="owner",
            email="owner@example.com",
            full_name="Test Owner",
        )
        owner.set_password("owner-password-123")
        staff = User(
            role=staff_role,
            username="staff",
            email="staff@example.com",
            full_name="Test Staff",
        )
        staff.set_password("staff-password-123")
        db.session.add_all([owner_role, staff_role, owner, staff])
        db.session.commit()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def _token(client, email, password):
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json["access_token"]


def test_login_uses_generic_failure_and_returns_public_user(client):
    response = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json == {"error": "Invalid email or password"}

    token = _token(client, "OWNER@example.com", "owner-password-123")
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json["user"]["role"] == "owner"
    assert "password_hash" not in response.json["user"]


def test_business_analytics_requires_owner_role(client):
    assert client.get("/api/overview").status_code == 401

    staff_token = _token(client, "staff@example.com", "staff-password-123")
    response = client.get("/api/overview", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 403

    owner_token = _token(client, "owner@example.com", "owner-password-123")
    response = client.get("/api/overview", headers={"Authorization": f"Bearer {owner_token}"})
    assert response.status_code == 200


def test_only_owner_can_create_staff_accounts(client):
    staff_token = _token(client, "staff@example.com", "staff-password-123")
    response = client.post(
        "/api/auth/users",
        json={
            "full_name": "New Staff",
            "username": "newstaff",
            "email": "newstaff@example.com",
            "password": "new-staff-password-123",
        },
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 403

    owner_token = _token(client, "owner@example.com", "owner-password-123")
    response = client.post(
        "/api/auth/users",
        json={
            "full_name": "New Staff",
            "username": "newstaff",
            "email": "newstaff@example.com",
            "password": "new-staff-password-123",
        },
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 201
    assert response.json["user"]["role"] == "staff"
    assert "password_hash" not in response.json["user"]


def test_public_registration_creates_staff_account(client):
    response = client.post(
        "/api/auth/register",
        json={
            "full_name": "Public Staff",
            "username": "publicstaff",
            "email": "publicstaff@example.com",
            "password": "public-staff-password-123",
        },
    )
    assert response.status_code == 201
    assert response.json["user"]["role"] == "staff"

    duplicate = client.post(
        "/api/auth/register",
        json={
            "full_name": "Duplicate Staff",
            "username": "PUBLICSTAFF",
            "email": "PUBLICSTAFF@example.com",
            "password": "duplicate-password-123",
        },
    )
    assert duplicate.status_code == 409

    login = client.post(
        "/api/auth/login",
        json={"email": "publicstaff@example.com", "password": "public-staff-password-123"},
    )
    assert login.status_code == 200
    assert login.json["user"]["role"] == "staff"


def test_password_validation_and_inactive_accounts(client, app):
    owner_token = _token(client, "owner@example.com", "owner-password-123")
    response = client.post(
        "/api/auth/users",
        json={
            "full_name": "Short Password",
            "username": "short",
            "email": "short@example.com",
            "password": "short",
        },
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 400

    with app.app_context():
        staff = User.query.filter_by(email="staff@example.com").one()
        staff.is_active = False
        db.session.commit()
    response = client.post(
        "/api/auth/login",
        json={"email": "staff@example.com", "password": "staff-password-123"},
    )
    assert response.status_code == 401


def test_reset_owner_access_cli_updates_local_owner_credentials(app, client):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["reset-owner-access"],
        input="new-owner@example.com\n\n\nnew-owner-password-123\nnew-owner-password-123\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "Owner credentials updated." in result.output

    old_login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "owner-password-123"},
    )
    assert old_login.status_code == 401
    new_login = client.post(
        "/api/auth/login",
        json={"email": "new-owner@example.com", "password": "new-owner-password-123"},
    )
    assert new_login.status_code == 200


def test_owner_can_import_synthetic_customer_and_supplier_csv(client):
    staff_token = _token(client, "staff@example.com", "staff-password-123")
    staff_headers = {"Authorization": f"Bearer {staff_token}"}
    assert client.post("/api/operations/customers/import-csv", json={}, headers=staff_headers).status_code == 403
    assert client.post("/api/operations/suppliers/import-csv", json={}, headers=staff_headers).status_code == 403

    owner_token = _token(client, "owner@example.com", "owner-password-123")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    customers = client.post("/api/operations/customers/import-csv", json={}, headers=owner_headers)
    suppliers = client.post("/api/operations/suppliers/import-csv", json={}, headers=owner_headers)
    assert customers.status_code == 200
    assert customers.json["imported"] == 300
    assert suppliers.status_code == 200
    assert suppliers.json["imported"] == 20

    customers_again = client.post("/api/operations/customers/import-csv", json={}, headers=owner_headers)
    suppliers_again = client.post("/api/operations/suppliers/import-csv", json={}, headers=owner_headers)
    assert customers_again.json["imported"] == 0
    assert customers_again.json["skipped"] == 300
    assert suppliers_again.json["imported"] == 0
    assert suppliers_again.json["skipped"] == 20


def test_owner_can_import_synthetic_product_csv(client):
    staff_token = _token(client, "staff@example.com", "staff-password-123")
    denied = client.post(
        "/api/operations/products/import-csv",
        json={},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert denied.status_code == 403

    owner_token = _token(client, "owner@example.com", "owner-password-123")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    first = client.post("/api/operations/products/import-csv", json={}, headers=owner_headers)
    assert first.status_code == 200
    assert first.json["imported"] == 150
    assert first.json["skipped"] == 0

    second = client.post("/api/operations/products/import-csv", json={}, headers=owner_headers)
    assert second.status_code == 200
    assert second.json["imported"] == 0
    assert second.json["skipped"] == 150


def test_owner_can_import_synthetic_sales_and_purchases(client):
    owner_token = _token(client, "owner@example.com", "owner-password-123")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    assert client.post("/api/operations/products/import-csv", json={}, headers=owner_headers).status_code == 200
    assert client.post("/api/operations/customers/import-csv", json={}, headers=owner_headers).status_code == 200
    assert client.post("/api/operations/suppliers/import-csv", json={}, headers=owner_headers).status_code == 200

    sales = client.post("/api/operations/sales/import-csv", json={}, headers=owner_headers)
    purchases = client.post("/api/operations/purchases/import-csv", json={}, headers=owner_headers)
    assert sales.status_code == 200
    assert sales.json["imported_lines"] == 5000
    assert purchases.status_code == 200
    assert purchases.json["imported"] == 500

    sales_again = client.post("/api/operations/sales/import-csv", json={}, headers=owner_headers)
    purchases_again = client.post("/api/operations/purchases/import-csv", json={}, headers=owner_headers)
    assert sales_again.json["imported_lines"] == 0
    assert purchases_again.json["imported"] == 0


def test_postgresql_baseline_covers_all_orm_tables():
    schema_path = Path(__file__).resolve().parents[2] / "database" / "postgresql_schema.sql"
    schema = schema_path.read_text(encoding="utf-8")
    ddl_tables = set(re.findall(r"CREATE TABLE\s+([a-z_]+)", schema, flags=re.IGNORECASE))
    assert ddl_tables == set(db.metadata.tables)


def test_sale_is_atomic_idempotent_and_supports_partial_payment(client):
    owner_token = _token(client, "owner@example.com", "owner-password-123")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    product_response = client.post(
        "/api/operations/products",
        json={
            "product_code": "BRK-001",
            "name": "Brake Pad Set",
            "category": "Brakes",
            "brand": "Toyota",
            "unit_cost_lkr": 50,
            "retail_price_lkr": 100,
            "wholesale_price_lkr": 90,
            "initial_stock": 5,
            "minimum_stock_level": 1,
            "maximum_stock_level": 8,
        },
        headers=owner_headers,
    )
    assert product_response.status_code == 201
    product_id = product_response.json["item"]["id"]

    staff_token = _token(client, "staff@example.com", "staff-password-123")
    staff_headers = {"Authorization": f"Bearer {staff_token}"}
    customer_response = client.post(
        "/api/operations/customers",
        json={"name": "Walk-in Customer", "customer_type": "retail"},
        headers=staff_headers,
    )
    assert customer_response.status_code == 201
    customer_id = customer_response.json["item"]["id"]

    sale_payload = {
        "idempotency_key": "test-sale-request-001",
        "customer_id": customer_id,
        "items": [{"product_id": product_id, "quantity": 2}],
        "amount_paid_lkr": 50,
        "payment_method": "cash",
    }
    sale_response = client.post("/api/sales", json=sale_payload, headers=staff_headers)
    assert sale_response.status_code == 201
    sale = sale_response.json["sale"]
    assert sale["total_amount_lkr"] == 200
    assert sale["amount_paid_lkr"] == 50
    assert sale["balance_due_lkr"] == 150
    assert sale["payment_status"] == "partially_paid"

    repeated = client.post("/api/sales", json=sale_payload, headers=staff_headers)
    assert repeated.status_code == 201
    assert repeated.json["sale"]["invoice_number"] == sale["invoice_number"]

    product_after_sale = client.get(
        f"/api/products/lookup?search=BRK-001",
        headers=staff_headers,
    )
    assert product_after_sale.json["items"][0]["current_stock"] == 3

    payment_response = client.post(
        f"/api/invoices/{sale['invoice_number']}/payments",
        json={"amount_lkr": 150, "payment_method": "bank_transfer"},
        headers=staff_headers,
    )
    assert payment_response.status_code == 201
    assert payment_response.json["sale"]["payment_status"] == "paid"

    insufficient = client.post(
        "/api/sales",
        json={
            "idempotency_key": "test-sale-request-002",
            "customer_id": customer_id,
            "items": [{"product_id": product_id, "quantity": 4}],
        },
        headers=staff_headers,
    )
    assert insufficient.status_code == 409
    assert "Insufficient stock" in insufficient.json["error"]

    pdf_response = client.get(
        f"/api/invoices/{sale['invoice_number']}/pdf",
        headers=staff_headers,
    )
    assert pdf_response.status_code == 200
    assert pdf_response.mimetype == "application/pdf"
    assert pdf_response.data.startswith(b"%PDF")


def test_management_crud_enforces_roles_and_preserves_inactive_records(client):
    owner_token = _token(client, "owner@example.com", "owner-password-123")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    staff_token = _token(client, "staff@example.com", "staff-password-123")
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    product_payload = {
        "product_code": "OIL-001",
        "name": "Engine Oil Filter",
        "category": "Filters",
        "brand": "Toyota",
        "unit_cost_lkr": 1000,
        "retail_price_lkr": 1500,
        "wholesale_price_lkr": 1300,
        "initial_stock": 12,
        "minimum_stock_level": 2,
        "maximum_stock_level": 20,
    }
    assert client.post(
        "/api/operations/products",
        json=product_payload,
        headers=staff_headers,
    ).status_code == 403
    product_response = client.post(
        "/api/operations/products",
        json=product_payload,
        headers=owner_headers,
    )
    assert product_response.status_code == 201
    product_id = product_response.json["item"]["id"]
    assert product_response.json["item"]["current_stock"] == 12

    product_update = client.patch(
        f"/api/operations/products/{product_id}",
        json={"name": "Engine Oil Filter - Updated", "category": "Filters"},
        headers=owner_headers,
    )
    assert product_update.status_code == 200
    assert product_update.json["item"]["name"] == "Engine Oil Filter - Updated"
    assert client.delete(
        f"/api/operations/products/{product_id}",
        headers=owner_headers,
    ).status_code == 204
    listed_products = client.get("/api/operations/products", headers=owner_headers)
    assert listed_products.json["items"][0]["is_active"] is False

    customer_response = client.post(
        "/api/operations/customers",
        json={"name": "Colombo Motors", "customer_type": "wholesale"},
        headers=staff_headers,
    )
    assert customer_response.status_code == 201
    customer_id = customer_response.json["item"]["id"]
    customer_update = client.patch(
        f"/api/operations/customers/{customer_id}",
        json={"city": "Colombo", "customer_type": "retail"},
        headers=staff_headers,
    )
    assert customer_update.status_code == 200
    assert customer_update.json["item"]["city"] == "Colombo"
    assert customer_update.json["item"]["customer_type"] == "retail"

    supplier_payload = {
        "name": "Island Parts Supply",
        "city": "Colombo",
        "supplier_type": "Distributor",
        "payment_terms": "30 days",
    }
    assert client.post(
        "/api/operations/suppliers",
        json=supplier_payload,
        headers=staff_headers,
    ).status_code == 403
    supplier_response = client.post(
        "/api/operations/suppliers",
        json=supplier_payload,
        headers=owner_headers,
    )
    assert supplier_response.status_code == 201
    supplier_id = supplier_response.json["item"]["id"]
    assert client.patch(
        f"/api/operations/suppliers/{supplier_id}",
        json={"payment_terms": "45 days"},
        headers=owner_headers,
    ).json["item"]["payment_terms"] == "45 days"
    assert client.delete(
        f"/api/operations/suppliers/{supplier_id}",
        headers=owner_headers,
    ).status_code == 204
    listed_suppliers = client.get("/api/operations/suppliers", headers=owner_headers)
    assert listed_suppliers.json["items"][0]["is_active"] is False
