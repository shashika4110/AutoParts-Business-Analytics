-- Core KPI and drill-down queries. Run after sql/load_sqlite.py.

-- Total revenue, profit, orders, units, margin
SELECT
    ROUND(SUM(total_amount), 2) AS total_revenue,
    ROUND(SUM(profit), 2) AS total_profit,
    COUNT(DISTINCT invoice_number) AS total_orders,
    SUM(quantity) AS products_sold,
    ROUND(SUM(profit) * 100.0 / NULLIF(SUM(total_amount), 0), 2) AS profit_margin_pct
FROM sales;

-- Monthly revenue trend
SELECT
    substr(sale_date, 1, 7) AS month,
    ROUND(SUM(total_amount), 2) AS revenue,
    ROUND(SUM(profit), 2) AS profit,
    COUNT(DISTINCT invoice_number) AS orders
FROM sales
GROUP BY month
ORDER BY month;

-- Retail vs wholesale
SELECT
    c.customer_type,
    COUNT(DISTINCT s.invoice_number) AS orders,
    SUM(s.quantity) AS units,
    ROUND(SUM(s.total_amount), 2) AS revenue,
    ROUND(SUM(s.profit), 2) AS profit
FROM sales s
JOIN customers c ON c.customer_id = s.customer_id
GROUP BY c.customer_type
ORDER BY revenue DESC;

-- Best-selling products
SELECT
    p.product_id,
    p.product_name,
    p.brand,
    p.category,
    SUM(s.quantity) AS units,
    ROUND(SUM(s.total_amount), 2) AS revenue,
    ROUND(SUM(s.profit), 2) AS profit
FROM sales s
JOIN products p ON p.product_id = s.product_id
GROUP BY p.product_id, p.product_name, p.brand, p.category
ORDER BY revenue DESC
LIMIT 10;

-- Slow-moving products (sold at least once)
SELECT
    p.product_id,
    p.product_name,
    SUM(s.quantity) AS units,
    ROUND(SUM(s.total_amount), 2) AS revenue
FROM sales s
JOIN products p ON p.product_id = s.product_id
GROUP BY p.product_id, p.product_name
ORDER BY units ASC, revenue ASC
LIMIT 10;

-- Catalogue items with no sales
SELECT p.product_id, p.product_name, p.brand, p.category
FROM products p
LEFT JOIN sales s ON s.product_id = p.product_id
WHERE s.sale_id IS NULL
ORDER BY p.product_id;

-- Low-stock SKUs and reorder quantity
SELECT
    p.product_id,
    p.product_name,
    i.current_stock,
    i.minimum_stock_level,
    i.maximum_stock_level,
    (i.maximum_stock_level - i.current_stock) AS reorder_qty,
    ROUND(i.current_stock * p.unit_cost_lkr, 2) AS inventory_value
FROM inventory i
JOIN products p ON p.product_id = i.product_id
WHERE i.current_stock <= i.minimum_stock_level
ORDER BY i.current_stock ASC;

-- Supplier purchase spend
SELECT
    su.supplier_name,
    su.supplier_type,
    COUNT(DISTINCT pu.purchase_id) AS purchase_orders,
    SUM(pu.quantity_purchased) AS units,
    ROUND(SUM(pu.total_purchase_cost), 2) AS total_cost
FROM purchases pu
JOIN suppliers su ON su.supplier_id = pu.supplier_id
GROUP BY su.supplier_name, su.supplier_type
ORDER BY total_cost DESC;

-- Revenue by province
SELECT
    c.province,
    COUNT(DISTINCT s.customer_id) AS buying_customers,
    ROUND(SUM(s.total_amount), 2) AS revenue
FROM sales s
JOIN customers c ON c.customer_id = s.customer_id
GROUP BY c.province
ORDER BY revenue DESC;
