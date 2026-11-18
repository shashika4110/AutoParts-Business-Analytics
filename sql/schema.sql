-- SQLite schema for AutoParts Business Analytics
-- Import CSVs after creating these tables, or run: python sql/load_sqlite.py

PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS sales;
DROP TABLE IF EXISTS purchases;
DROP TABLE IF EXISTS inventory;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS suppliers;

CREATE TABLE products (
    product_id TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    category TEXT NOT NULL,
    brand TEXT NOT NULL,
    compatible_vehicle_make TEXT,
    part_number TEXT,
    unit_cost_lkr REAL NOT NULL,
    retail_price_lkr REAL NOT NULL,
    wholesale_price_lkr REAL NOT NULL,
    reorder_level INTEGER NOT NULL,
    is_active TEXT
);

CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    customer_type TEXT NOT NULL CHECK (customer_type IN ('Retail', 'Wholesale')),
    city TEXT,
    district TEXT,
    province TEXT,
    contact_number_synthetic TEXT,
    registration_date TEXT NOT NULL
);

CREATE TABLE suppliers (
    supplier_id TEXT PRIMARY KEY,
    supplier_name TEXT NOT NULL,
    contact_person TEXT,
    city TEXT,
    district TEXT,
    province TEXT,
    supplier_type TEXT,
    payment_terms TEXT,
    registration_date TEXT NOT NULL
);

CREATE TABLE inventory (
    inventory_id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products (product_id),
    current_stock INTEGER NOT NULL,
    minimum_stock_level INTEGER NOT NULL,
    maximum_stock_level INTEGER NOT NULL,
    last_updated_date TEXT NOT NULL
);

CREATE TABLE purchases (
    purchase_id TEXT PRIMARY KEY,
    purchase_date TEXT NOT NULL,
    supplier_id TEXT NOT NULL REFERENCES suppliers (supplier_id),
    product_id TEXT NOT NULL REFERENCES products (product_id),
    quantity_purchased INTEGER NOT NULL,
    unit_cost REAL NOT NULL,
    total_purchase_cost REAL NOT NULL
);

CREATE TABLE sales (
    sale_id TEXT PRIMARY KEY,
    invoice_number TEXT NOT NULL,
    sale_date TEXT NOT NULL,
    product_id TEXT NOT NULL REFERENCES products (product_id),
    customer_id TEXT NOT NULL REFERENCES customers (customer_id),
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    unit_cost REAL NOT NULL,
    discount REAL NOT NULL,
    total_amount REAL NOT NULL,
    profit REAL NOT NULL
);

CREATE INDEX idx_sales_date ON sales (sale_date);
CREATE INDEX idx_sales_product ON sales (product_id);
CREATE INDEX idx_sales_customer ON sales (customer_id);
CREATE INDEX idx_purchases_supplier ON purchases (supplier_id);
CREATE INDEX idx_inventory_product ON inventory (product_id);
