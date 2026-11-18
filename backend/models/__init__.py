"""Import all models so SQLAlchemy and Alembic see the complete metadata."""

from models.catalog import Category, Customer, Inventory, Product, Supplier
from models.identity import Role, User
from models.operations import AuditLog, Expense, ReturnItem, ReturnRecord, StockMovement
from models.purchasing import Purchase, PurchaseItem, SupplierPayment
from models.sales import Payment, Sale, SaleItem

__all__ = [
    "AuditLog",
    "Category",
    "Customer",
    "Expense",
    "Inventory",
    "Payment",
    "Product",
    "Purchase",
    "PurchaseItem",
    "ReturnItem",
    "ReturnRecord",
    "Role",
    "Sale",
    "SaleItem",
    "StockMovement",
    "Supplier",
    "SupplierPayment",
    "User",
]
