"""Flask application for CSV analytics and authenticated business access."""

from __future__ import annotations

import os
import secrets
from datetime import date, timedelta
from pathlib import Path

import click
from dotenv import load_dotenv
from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from werkzeug.exceptions import BadRequest, HTTPException

from extensions import db, jwt, migrate
from data_service import (
    customer_view,
    export_csv,
    inventory_view,
    overview,
    product_catalog,
    reports,
    sales_analytics,
    supplier_view,
)

load_dotenv(Path(__file__).with_name(".env"))

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def _database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        instance_dir = Path(__file__).resolve().parent / "instance"
        instance_dir.mkdir(parents=True, exist_ok=True)
        database_path = (instance_dir / "autoparts-dev.sqlite").resolve()
        return f"sqlite:///{database_path.as_posix()}"
    if value.startswith("postgres://"):
        return value.replace("postgres://", "postgresql+psycopg://", 1)
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def _dates() -> tuple[str | None, str | None]:
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    parsed_dates = {}

    for name, value in (("start_date", start_date), ("end_date", end_date)):
        if value is None:
            continue
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise BadRequest(f"{name} must use YYYY-MM-DD format") from exc
        if parsed.isoformat() != value:
            raise BadRequest(f"{name} must use YYYY-MM-DD format")
        parsed_dates[name] = parsed

    if (
        "start_date" in parsed_dates
        and "end_date" in parsed_dates
        and parsed_dates["start_date"] > parsed_dates["end_date"]
    ):
        raise BadRequest("start_date must be on or before end_date")

    return start_date, end_date


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(
        __name__,
        static_folder=str(FRONTEND_DIST) if FRONTEND_DIST.exists() else None,
        static_url_path="",
    )
    jwt_secret = os.getenv("JWT_SECRET_KEY")
    if jwt_secret == "GENERATE_A_LONG_RANDOM_SECRET":
        jwt_secret = None
    if not jwt_secret:
        if os.getenv("APP_ENV", "development").lower() == "production":
            raise RuntimeError("JWT_SECRET_KEY must be configured in production")
        jwt_secret = secrets.token_urlsafe(48)
        app.logger.warning("JWT_SECRET_KEY is unset; generated a temporary key for this process")

    app.config.from_mapping(
        SQLALCHEMY_DATABASE_URI=_database_url(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
        JWT_SECRET_KEY=jwt_secret,
        JWT_ACCESS_TOKEN_EXPIRES=timedelta(minutes=30),
        JWT_TOKEN_LOCATION=["headers"],
    )
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)

    from auth import EMAIL_PATTERN, auth_bp, roles_required
    from business_routes import business_bp
    import models  # noqa: F401 - registers every model with SQLAlchemy metadata

    app.register_blueprint(auth_bp)
    app.register_blueprint(business_bp)

    origins = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if origin.strip()
    ]
    CORS(app, resources={r"/api/*": {"origins": origins}})

    @jwt.unauthorized_loader
    def missing_token(_reason: str):
        return jsonify({"error": "Authentication required"}), 401

    @jwt.invalid_token_loader
    def invalid_token(_reason: str):
        return jsonify({"error": "Invalid authentication token"}), 401

    @jwt.expired_token_loader
    def expired_token(_jwt_header, _jwt_payload):
        return jsonify({"error": "Authentication token has expired"}), 401

    @app.errorhandler(FileNotFoundError)
    def missing_data(exc: FileNotFoundError):
        app.logger.error("Required dataset is missing: %s", exc)
        return jsonify({"error": "A required dataset file is missing"}), 500

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        db.session.rollback()
        if isinstance(exc, HTTPException):
            return jsonify({"error": exc.description}), exc.code
        app.logger.exception("Unhandled API error")
        return jsonify({"error": "Unable to process request"}), 500

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "autoparts-api"})

    @app.get("/")
    def index():
        index_file = FRONTEND_DIST / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}
        return jsonify({"error": "Frontend build not found. Run npm run build in frontend/."}), 404

    @app.get("/<path:asset_path>")
    def frontend_assets(asset_path: str):
        if asset_path.startswith("api/"):
            return jsonify({"error": "Not found"}), 404
        target = FRONTEND_DIST / asset_path
        if target.exists() and target.is_file():
            return app.send_static_file(asset_path)
        index_file = FRONTEND_DIST / "index.html"
        if index_file.exists():
            return index_file.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html"}
        return jsonify({"error": "Not found"}), 404

    @app.get("/api/overview")
    @roles_required("owner")
    def api_overview():
        start, end = _dates()
        return jsonify(overview(start, end))

    @app.get("/api/sales/analytics")
    @roles_required("owner")
    def api_sales_analytics():
        start, end = _dates()
        return jsonify(sales_analytics(start, end))

    @app.get("/api/products")
    @roles_required("owner")
    def api_products():
        return jsonify(
            product_catalog(
                search=request.args.get("search"),
                category=request.args.get("category"),
                brand=request.args.get("brand"),
            )
        )

    @app.get("/api/inventory")
    @roles_required("owner")
    def api_inventory():
        return jsonify(inventory_view())

    @app.get("/api/customers")
    @roles_required("owner")
    def api_customers():
        start, end = _dates()
        return jsonify(
            customer_view(
                customer_type=request.args.get("customer_type"),
                search=request.args.get("search"),
                start_date=start,
                end_date=end,
            )
        )

    @app.get("/api/suppliers")
    @roles_required("owner")
    def api_suppliers():
        return jsonify(supplier_view())

    @app.get("/api/reports")
    @roles_required("owner")
    def api_reports():
        start, end = _dates()
        report_type = request.args.get("type", "revenue")
        if report_type not in {"revenue", "profit", "products"}:
            return jsonify({"error": "type must be revenue, profit, or products"}), 400
        return jsonify(reports(report_type, start, end))

    @app.get("/api/reports/export")
    @roles_required("owner")
    def api_export():
        start, end = _dates()
        report_type = request.args.get("type", "revenue")
        if report_type not in {"revenue", "profit", "products"}:
            return jsonify({"error": "type must be revenue, profit, or products"}), 400
        filename, csv_text = export_csv(report_type, start, end)
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @app.cli.command("create-owner")
    @click.option("--email", prompt=True)
    @click.option("--username", prompt=True)
    @click.option("--full-name", prompt="Full name")
    def create_owner(email: str, username: str, full_name: str):
        """Create the initial owner account; there is no public owner registration."""
        from models.identity import Role, User

        if User.query.join(Role).filter(Role.name == "owner").first():
            raise click.ClickException("An owner account already exists")

        role = Role.query.filter_by(name="owner").first()
        if role is None:
            raise click.ClickException("Run `flask db upgrade` before creating an owner")

        email = email.strip().lower()
        username = username.strip().lower()
        full_name = full_name.strip()
        if "@" not in email or not username or not full_name:
            raise click.ClickException("Enter a valid email, username, and full name")
        password = click.prompt("Password (minimum 12 characters)", hide_input=True, confirmation_prompt=True)
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters")
        if User.query.filter(
            (db.func.lower(User.email) == email) | (db.func.lower(User.username) == username.lower())
        ).first():
            raise click.ClickException("Email or username is already in use")

        user = User(role=role, username=username, email=email, full_name=full_name)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Owner account created for {email}")

    @app.cli.command("import-csv-products")
    def import_csv_products():
        """Import the project product and inventory CSVs into the operational database."""
        import csv

        from business_services import parse_money
        from models.catalog import Category, Inventory, Product

        products_path = Path(__file__).resolve().parents[1] / "dataset" / "products.csv"
        inventory_path = Path(__file__).resolve().parents[1] / "dataset" / "inventory.csv"
        if not products_path.is_file() or not inventory_path.is_file():
            raise click.ClickException("dataset/products.csv or dataset/inventory.csv is missing")

        db.create_all()

        inventory_by_product = {}
        with inventory_path.open("r", encoding="utf-8-sig", newline="") as inventory_file:
            for row in csv.DictReader(inventory_file):
                inventory_by_product[row["product_id"].strip()] = {
                    "current_stock": int(row["current_stock"]),
                    "minimum_stock_level": int(row["minimum_stock_level"]),
                    "maximum_stock_level": int(row["maximum_stock_level"]),
                }

        imported = 0
        skipped = 0
        with products_path.open("r", encoding="utf-8-sig", newline="") as products_file:
            for row in csv.DictReader(products_file):
                product_code = (row.get("product_id") or "").strip()
                if not product_code or Product.query.filter_by(product_code=product_code).first():
                    skipped += 1
                    continue

                name = (row.get("product_name") or "").strip()
                category_name = (row.get("category") or "").strip()
                brand = (row.get("brand") or "").strip()
                if not name or not category_name or not brand:
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

                category = Category.query.filter(db.func.lower(Category.name) == category_name.lower()).first()
                if category is None:
                    category = Category(name=category_name)
                    db.session.add(category)
                    db.session.flush()

                stock = inventory_by_product.get(product_code, {
                    "current_stock": 0,
                    "minimum_stock_level": reorder_level,
                    "maximum_stock_level": max(reorder_level * 10, reorder_level),
                })
                if stock["maximum_stock_level"] < stock["minimum_stock_level"]:
                    stock["maximum_stock_level"] = stock["minimum_stock_level"]

                product = Product(
                    product_code=product_code,
                    name=name,
                    category=category,
                    brand=brand,
                    compatible_vehicle_make=(row.get("compatible_vehicle_make") or "").strip() or None,
                    part_number=(row.get("part_number") or "").strip() or None,
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

        db.session.commit()
        click.echo(f"Imported {imported} products; skipped {skipped} existing or invalid rows.")

    @app.cli.command("import-csv-customers")
    def import_csv_customers():
        """Import the project customer CSV into the operational database."""
        import csv

        from models.catalog import Customer

        customers_path = Path(__file__).resolve().parents[1] / "dataset" / "customers.csv"
        if not customers_path.is_file():
            raise click.ClickException("dataset/customers.csv is missing")

        imported = 0
        skipped = 0
        with customers_path.open("r", encoding="utf-8-sig", newline="") as customers_file:
            for row in csv.DictReader(customers_file):
                customer_code = (row.get("customer_id") or "").strip()
                name = (row.get("customer_name") or "").strip()
                customer_type = (row.get("customer_type") or "retail").strip().lower()
                if not customer_code or not name or customer_type not in {"retail", "wholesale"}:
                    skipped += 1
                    continue
                if Customer.query.filter_by(customer_code=customer_code).first():
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
        db.session.commit()
        click.echo(f"Imported {imported} customers; skipped {skipped} existing or invalid rows.")

    @app.cli.command("import-csv-sales")
    def import_csv_sales():
        """Import synthetic sales and invoice history into the operational database."""
        from flask import g
        from models.identity import Role, User
        import business_routes

        owner = User.query.join(Role).filter(Role.name == "owner").first()
        if owner is None:
            raise click.ClickException("An owner account is required to import historical data")
        with app.test_request_context("/api/operations/sales/import-csv", method="POST", json={}):
            g.current_user = owner
            response = business_routes.import_sales_from_csv.__wrapped__()
            payload = response[0].get_json() if isinstance(response, tuple) else response.get_json()
            click.echo(f"Imported {payload.get('imported_lines', 0)} sales lines; skipped {payload.get('skipped_lines', 0)} lines.")

    @app.cli.command("import-csv-purchases")
    def import_csv_purchases():
        """Import synthetic purchase headers into the operational database."""
        from flask import g
        from models.identity import Role, User
        import business_routes

        owner = User.query.join(Role).filter(Role.name == "owner").first()
        if owner is None:
            raise click.ClickException("An owner account is required to import historical data")
        with app.test_request_context("/api/operations/purchases/import-csv", method="POST", json={}):
            g.current_user = owner
            response = business_routes.import_purchases_from_csv.__wrapped__()
            payload = response[0].get_json() if isinstance(response, tuple) else response.get_json()
            click.echo(f"Imported {payload.get('imported', 0)} purchases; skipped {payload.get('skipped', 0)} rows.")

    @app.cli.command("import-csv-suppliers")
    def import_csv_suppliers():
        """Import the project supplier CSV into the operational database."""
        import csv

        from models.catalog import Supplier

        suppliers_path = Path(__file__).resolve().parents[1] / "dataset" / "suppliers.csv"
        if not suppliers_path.is_file():
            raise click.ClickException("dataset/suppliers.csv is missing")

        imported = 0
        skipped = 0
        with suppliers_path.open("r", encoding="utf-8-sig", newline="") as suppliers_file:
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
        db.session.commit()
        click.echo(f"Imported {imported} suppliers; skipped {skipped} existing or invalid rows.")

    @app.cli.command("reset-owner-access")
    def reset_owner_access():
        """Set a new email and password for the single existing owner account."""
        from sqlalchemy.exc import IntegrityError

        from models.identity import Role, User

        owners = User.query.join(Role).filter(Role.name == "owner").order_by(User.id).all()
        if not owners:
            raise click.ClickException("No owner account exists; run create-owner after applying migrations")
        if len(owners) != 1:
            raise click.ClickException(
                "More than one owner account exists; refusing to choose an account automatically"
            )

        owner = owners[0]
        email = click.prompt("Owner email", default=owner.email).strip().lower()
        username = click.prompt("Owner username", default=owner.username).strip().lower()
        full_name = click.prompt("Owner full name", default=owner.full_name).strip()
        if not EMAIL_PATTERN.fullmatch(email):
            raise click.ClickException("Enter a valid email address")
        if len(email) > 254 or not username or len(username) > 80 or not full_name or len(full_name) > 160:
            raise click.ClickException("Username, name, or email is empty or exceeds its maximum length")
        conflicting_user = User.query.filter(
            User.id != owner.id,
            (db.func.lower(User.email) == email) | (db.func.lower(User.username) == username),
        ).first()
        if conflicting_user:
            raise click.ClickException("That email or username is already assigned to another account")

        password = click.prompt(
            "New password (minimum 12 characters)",
            hide_input=True,
            confirmation_prompt=True,
        )
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters")
        if not click.confirm("Update the existing owner credentials?", default=False):
            raise click.ClickException("Owner credentials were not changed")

        owner.email = email
        owner.username = username
        owner.full_name = full_name
        owner.set_password(password)
        try:
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            raise click.ClickException("Email or username is already in use") from exc
        click.echo("Owner credentials updated.")

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    host = os.getenv("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=os.getenv("FLASK_DEBUG", "0") == "1")
