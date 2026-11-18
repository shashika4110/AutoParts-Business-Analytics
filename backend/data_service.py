"""Load CSV datasets and compute dashboard metrics from actual records."""

from __future__ import annotations

import json
from functools import lru_cache
from io import StringIO
from pathlib import Path

import pandas as pd


def records(df: pd.DataFrame) -> list:
    """Convert a DataFrame to JSON-safe dictionaries."""
    return json.loads(df.to_json(orient="records", date_format="iso"))

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "dataset"


def _csv(name: str) -> Path:
    path = DATASET_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Missing dataset file: {path}")
    return path


@lru_cache(maxsize=1)
def load_frames() -> dict[str, pd.DataFrame]:
    products = pd.read_csv(_csv("products.csv"))
    customers = pd.read_csv(_csv("customers.csv"))
    suppliers = pd.read_csv(_csv("suppliers.csv"))
    sales = pd.read_csv(_csv("sales.csv"), parse_dates=["sale_date"])
    inventory = pd.read_csv(_csv("inventory.csv"), parse_dates=["last_updated_date"])
    purchases = pd.read_csv(_csv("purchases.csv"), parse_dates=["purchase_date"])

    customers["registration_date"] = pd.to_datetime(customers["registration_date"])
    suppliers["registration_date"] = pd.to_datetime(suppliers["registration_date"])

    sales = sales.merge(
        products[
            [
                "product_id",
                "product_name",
                "category",
                "brand",
                "compatible_vehicle_make",
            ]
        ],
        on="product_id",
        how="left",
    )
    sales = sales.merge(
        customers[["customer_id", "customer_name", "customer_type", "city", "district", "province"]],
        on="customer_id",
        how="left",
    )
    return {
        "products": products,
        "customers": customers,
        "suppliers": suppliers,
        "sales": sales,
        "inventory": inventory,
        "purchases": purchases,
    }


def filter_sales(start_date: str | None, end_date: str | None) -> pd.DataFrame:
    sales = load_frames()["sales"].copy()
    if start_date:
        sales = sales[sales["sale_date"] >= pd.to_datetime(start_date)]
    if end_date:
        sales = sales[sales["sale_date"] < pd.to_datetime(end_date) + pd.Timedelta(days=1)]
    return sales


def _round_money(value: float) -> float:
    return round(float(value or 0), 2)


def overview(start_date: str | None, end_date: str | None) -> dict:
    sales = filter_sales(start_date, end_date)
    revenue = _round_money(sales["total_amount"].sum())
    profit = _round_money(sales["profit"].sum())
    margin = round((profit / revenue * 100) if revenue else 0, 2)

    monthly = (
        sales.assign(month=sales["sale_date"].dt.to_period("M").astype(str))
        .groupby("month", as_index=False)
        .agg(revenue=("total_amount", "sum"), profit=("profit", "sum"), orders=("invoice_number", "nunique"))
        .sort_values("month")
    )
    monthly["revenue"] = monthly["revenue"].round(2)
    monthly["profit"] = monthly["profit"].round(2)

    by_category = (
        sales.groupby("category", as_index=False)
        .agg(revenue=("total_amount", "sum"), quantity=("quantity", "sum"))
        .sort_values("revenue", ascending=False)
    )
    by_category["revenue"] = by_category["revenue"].round(2)

    recent = (
        sales.sort_values("sale_date", ascending=False)
        .head(12)[
            [
                "sale_id",
                "invoice_number",
                "sale_date",
                "product_name",
                "customer_name",
                "customer_type",
                "quantity",
                "total_amount",
                "profit",
            ]
        ]
        .copy()
    )
    recent["sale_date"] = recent["sale_date"].dt.strftime("%Y-%m-%d")
    recent["total_amount"] = recent["total_amount"].round(2)
    recent["profit"] = recent["profit"].round(2)

    channel = (
        sales.groupby("customer_type", as_index=False)
        .agg(revenue=("total_amount", "sum"), orders=("invoice_number", "nunique"))
    )
    wholesale = channel[channel["customer_type"] == "Wholesale"]
    wholesale_share = 0.0
    wholesale_orders = 0
    if not wholesale.empty and revenue:
        wholesale_share = round(float(wholesale["revenue"].iloc[0]) / revenue * 100, 1)
        wholesale_orders = int(wholesale["orders"].iloc[0])

    top_category = by_category.iloc[0] if not by_category.empty else None
    frames = load_frames()
    sold_ids = set(sales["product_id"].dropna().unique())
    never_sold_count = int((~frames["products"]["product_id"].isin(sold_ids)).sum())
    inventory = frames["inventory"]
    low_stock_count = int((inventory["current_stock"] <= inventory["minimum_stock_level"]).sum())

    insights = []
    if wholesale_share:
        insights.append(
            {
                "title": "Wholesale drives revenue concentration",
                "detail": (
                    f"Wholesale invoices are {wholesale_share}% of revenue from "
                    f"{wholesale_orders} orders, so a few large buyers dominate cash flow."
                ),
            }
        )
    if top_category is not None:
        insights.append(
            {
                "title": f"{top_category['category']} leads category mix",
                "detail": (
                    f"This category contributed {format(float(top_category['revenue']), ',.0f')} LKR "
                    "in the selected period. Keep those SKUs in stock first."
                ),
            }
        )
    if never_sold_count:
        insights.append(
            {
                "title": "Catalogue includes unsold SKUs",
                "detail": (
                    f"{never_sold_count} products had no sales in this date range. "
                    "Review pricing, fitment, or discontinue slow lines."
                ),
            }
        )
    insights.append(
        {
            "title": "Inventory risk is live",
            "detail": (
                f"{low_stock_count} SKUs are at or below minimum stock. "
                "Use the Inventory page for reorder quantities."
            ),
        }
    )

    return {
        "kpis": {
            "total_revenue": revenue,
            "total_profit": profit,
            "total_orders": int(sales["invoice_number"].nunique()),
            "products_sold": int(sales["quantity"].sum()),
            "profit_margin": margin,
        },
        "monthly_revenue": records(monthly),
        "sales_by_category": records(by_category),
        "recent_sales": records(recent),
        "insights": insights,
    }


def sales_analytics(start_date: str | None, end_date: str | None) -> dict:
    sales = filter_sales(start_date, end_date)
    daily = (
        sales.assign(day=sales["sale_date"].dt.strftime("%Y-%m-%d"))
        .groupby("day", as_index=False)
        .agg(revenue=("total_amount", "sum"), quantity=("quantity", "sum"))
        .sort_values("day")
    )
    daily["revenue"] = daily["revenue"].round(2)

    monthly = (
        sales.assign(month=sales["sale_date"].dt.to_period("M").astype(str))
        .groupby("month", as_index=False)
        .agg(revenue=("total_amount", "sum"), profit=("profit", "sum"))
        .sort_values("month")
    )
    monthly["revenue"] = monthly["revenue"].round(2)
    monthly["profit"] = monthly["profit"].round(2)

    by_brand = (
        sales.groupby("brand", as_index=False)
        .agg(revenue=("total_amount", "sum"), quantity=("quantity", "sum"))
        .sort_values("revenue", ascending=False)
    )
    by_brand["revenue"] = by_brand["revenue"].round(2)

    by_category = (
        sales.groupby("category", as_index=False)
        .agg(revenue=("total_amount", "sum"), quantity=("quantity", "sum"))
        .sort_values("revenue", ascending=False)
    )
    by_category["revenue"] = by_category["revenue"].round(2)

    channel = (
        sales.groupby("customer_type", as_index=False)
        .agg(
            revenue=("total_amount", "sum"),
            profit=("profit", "sum"),
            orders=("invoice_number", "nunique"),
            quantity=("quantity", "sum"),
        )
        .sort_values("revenue", ascending=False)
    )
    for col in ("revenue", "profit"):
        channel[col] = channel[col].round(2)

    product_stats = (
        sales.groupby(["product_id", "product_name", "brand", "category"], as_index=False)
        .agg(revenue=("total_amount", "sum"), quantity=("quantity", "sum"), profit=("profit", "sum"))
    )
    top_products = product_stats.sort_values("revenue", ascending=False).head(10)
    slow_movers = product_stats.sort_values(["quantity", "revenue"], ascending=[True, True]).head(10)
    for table in (top_products, slow_movers):
        table["revenue"] = table["revenue"].round(2)
        table["profit"] = table["profit"].round(2)

    sold_ids = set(sales["product_id"].dropna().unique())
    catalogue = load_frames()["products"]
    never_sold = catalogue[~catalogue["product_id"].isin(sold_ids)][
        ["product_id", "product_name", "brand", "category", "retail_price_lkr"]
    ].copy()

    return {
        "daily": records(daily),
        "monthly": records(monthly),
        "by_brand": records(by_brand),
        "by_category": records(by_category),
        "retail_vs_wholesale": records(channel),
        "top_products": records(top_products),
        "slow_movers": records(slow_movers),
        "never_sold": records(never_sold),
    }


def product_catalog(search: str | None, category: str | None, brand: str | None) -> dict:
    frames = load_frames()
    products = frames["products"].copy()
    sales = frames["sales"]
    stats = (
        sales.groupby("product_id", as_index=False)
        .agg(
            units_sold=("quantity", "sum"),
            revenue=("total_amount", "sum"),
            profit=("profit", "sum"),
        )
    )
    products = products.merge(stats, on="product_id", how="left")
    products[["units_sold", "revenue", "profit"]] = products[["units_sold", "revenue", "profit"]].fillna(0)
    products["profit_margin"] = products.apply(
        lambda row: round((row["profit"] / row["revenue"] * 100) if row["revenue"] else 0, 2),
        axis=1,
    )
    products["unit_margin_retail"] = (
        (products["retail_price_lkr"] - products["unit_cost_lkr"]) / products["retail_price_lkr"] * 100
    ).round(2)

    if search:
        q = search.lower()
        products = products[
            products["product_name"].str.lower().str.contains(q, na=False, regex=False)
            | products["product_id"].str.lower().str.contains(q, na=False, regex=False)
            | products["part_number"].str.lower().str.contains(q, na=False, regex=False)
        ]
    if category:
        products = products[products["category"] == category]
    if brand:
        products = products[products["brand"] == brand]

    products = products.sort_values("revenue", ascending=False)
    products["revenue"] = products["revenue"].round(2)
    products["profit"] = products["profit"].round(2)
    products["never_sold"] = products["units_sold"] == 0

    return {
        "items": records(products),
        "categories": sorted(frames["products"]["category"].dropna().unique().tolist()),
        "brands": sorted(frames["products"]["brand"].dropna().unique().tolist()),
        "never_sold_count": int(products["never_sold"].sum()),
    }


def inventory_view() -> dict:
    frames = load_frames()
    inventory = frames["inventory"].merge(frames["products"], on="product_id", how="left")
    inventory["inventory_value"] = (inventory["current_stock"] * inventory["unit_cost_lkr"]).round(2)
    inventory["is_low_stock"] = inventory["current_stock"] <= inventory["minimum_stock_level"]
    inventory["reorder_qty"] = inventory.apply(
        lambda row: max(int(row["maximum_stock_level"] - row["current_stock"]), 0)
        if row["is_low_stock"]
        else 0,
        axis=1,
    )
    inventory["last_updated_date"] = pd.to_datetime(inventory["last_updated_date"]).dt.strftime("%Y-%m-%d")

    low = inventory[inventory["is_low_stock"]].sort_values("current_stock")
    return {
        "summary": {
            "sku_count": int(len(inventory)),
            "low_stock_count": int(inventory["is_low_stock"].sum()),
            "inventory_value": _round_money(inventory["inventory_value"].sum()),
            "total_units": int(inventory["current_stock"].sum()),
        },
        "items": records(inventory.sort_values("inventory_value", ascending=False)),
        "low_stock": records(low),
        "reorder": records(low[["product_id", "product_name", "current_stock", "minimum_stock_level",
                        "maximum_stock_level", "reorder_qty", "unit_cost_lkr"]]),
    }


def customer_view(customer_type: str | None, search: str | None, start_date: str | None, end_date: str | None) -> dict:
    frames = load_frames()
    customers = frames["customers"].copy()
    sales = filter_sales(start_date, end_date)
    stats = (
        sales.groupby(["customer_id", "customer_type", "city", "province"], as_index=False)
        .agg(
            revenue=("total_amount", "sum"),
            profit=("profit", "sum"),
            orders=("invoice_number", "nunique"),
            quantity=("quantity", "sum"),
        )
    )
    customers = customers.merge(
        stats[["customer_id", "revenue", "profit", "orders", "quantity"]],
        on="customer_id",
        how="left",
    )
    customers[["revenue", "profit", "orders", "quantity"]] = customers[
        ["revenue", "profit", "orders", "quantity"]
    ].fillna(0)
    customers["registration_date"] = pd.to_datetime(customers["registration_date"]).dt.strftime("%Y-%m-%d")

    if customer_type:
        customers = customers[customers["customer_type"] == customer_type]
    if search:
        q = search.lower()
        customers = customers[
            customers["customer_name"].str.lower().str.contains(q, na=False, regex=False)
            | customers["customer_id"].str.lower().str.contains(q, na=False, regex=False)
            | customers["city"].str.lower().str.contains(q, na=False, regex=False)
        ]

    customers = customers.sort_values("revenue", ascending=False)
    customers["revenue"] = customers["revenue"].round(2)
    customers["profit"] = customers["profit"].round(2)

    segmentation = (
        frames["customers"].groupby("customer_type", as_index=False).size().rename(columns={"size": "customers"})
    )
    by_city = (
        stats.groupby("city", as_index=False)
        .agg(revenue=("revenue", "sum"), customers=("customer_id", "nunique"))
        .sort_values("revenue", ascending=False)
        .head(12)
    )
    by_city["revenue"] = by_city["revenue"].round(2)
    by_province = (
        stats.groupby("province", as_index=False)
        .agg(revenue=("revenue", "sum"), customers=("customer_id", "nunique"))
        .sort_values("revenue", ascending=False)
    )
    by_province["revenue"] = by_province["revenue"].round(2)

    top = customers.head(10)[
        ["customer_id", "customer_name", "customer_type", "city", "province", "revenue", "orders"]
    ]

    return {
        "items": records(customers),
        "segmentation": records(segmentation),
        "top_customers": records(top),
        "by_city": records(by_city),
        "by_province": records(by_province),
    }


def supplier_view() -> dict:
    frames = load_frames()
    purchases = frames["purchases"].merge(frames["suppliers"], on="supplier_id", how="left")
    purchases = purchases.merge(
        frames["products"][["product_id", "product_name"]],
        on="product_id",
        how="left",
    )
    performance = (
        purchases.groupby(
            ["supplier_id", "supplier_name", "supplier_type", "city", "province", "payment_terms"],
            as_index=False,
        )
        .agg(
            purchase_orders=("purchase_id", "nunique"),
            quantity=("quantity_purchased", "sum"),
            total_cost=("total_purchase_cost", "sum"),
        )
        .sort_values("total_cost", ascending=False)
    )
    performance["total_cost"] = performance["total_cost"].round(2)

    suppliers = frames["suppliers"].copy()
    suppliers["registration_date"] = pd.to_datetime(suppliers["registration_date"]).dt.strftime("%Y-%m-%d")
    suppliers = suppliers.merge(
        performance[["supplier_id", "purchase_orders", "quantity", "total_cost"]],
        on="supplier_id",
        how="left",
    )
    suppliers[["purchase_orders", "quantity", "total_cost"]] = suppliers[
        ["purchase_orders", "quantity", "total_cost"]
    ].fillna(0)

    return {
        "items": records(suppliers.sort_values("total_cost", ascending=False)),
        "performance": records(performance),
    }


def reports(report_type: str, start_date: str | None, end_date: str | None) -> dict:
    sales = filter_sales(start_date, end_date)
    if report_type == "profit":
        table = (
            sales.assign(month=sales["sale_date"].dt.to_period("M").astype(str))
            .groupby("month", as_index=False)
            .agg(revenue=("total_amount", "sum"), profit=("profit", "sum"), quantity=("quantity", "sum"))
            .sort_values("month")
        )
        table["margin"] = table.apply(
            lambda row: round((row["profit"] / row["revenue"] * 100) if row["revenue"] else 0, 2),
            axis=1,
        )
    elif report_type == "products":
        table = (
            sales.groupby(["product_id", "product_name", "category", "brand"], as_index=False)
            .agg(quantity=("quantity", "sum"), revenue=("total_amount", "sum"), profit=("profit", "sum"))
            .sort_values("revenue", ascending=False)
        )
        table["margin"] = table.apply(
            lambda row: round((row["profit"] / row["revenue"] * 100) if row["revenue"] else 0, 2),
            axis=1,
        )
    else:
        table = (
            sales.assign(month=sales["sale_date"].dt.to_period("M").astype(str))
            .groupby(["month", "customer_type"], as_index=False)
            .agg(revenue=("total_amount", "sum"), orders=("invoice_number", "nunique"), quantity=("quantity", "sum"))
            .sort_values("month")
        )

    money_cols = [c for c in ("revenue", "profit") if c in table.columns]
    table[money_cols] = table[money_cols].round(2)
    return {"report_type": report_type, "rows": records(table)}


def export_csv(report_type: str, start_date: str | None, end_date: str | None) -> tuple[str, str]:
    payload = reports(report_type, start_date, end_date)
    df = pd.DataFrame(payload["rows"])
    buffer = StringIO()
    df.to_csv(buffer, index=False)
    filename = f"autoparts_{report_type}_report.csv"
    return filename, buffer.getvalue()
