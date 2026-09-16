"""Read-only QuickBooks Desktop 2021 bridge (qbXML / QBXMLRP2)."""

from .connection import QuickBooksConnection
from .service import QuickBooksService

__all__ = ["QuickBooksConnection", "QuickBooksService"]
__version__ = "0.1.0"
