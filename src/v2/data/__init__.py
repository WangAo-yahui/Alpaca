"""WA Trader v2 data-access and normalization layer.

Every network-facing function accepts injected clients so tests and later
offline simulations never need real Alpaca credentials.
"""

from v2.data.alpaca_client import (
    AlpacaClients,
    create_alpaca_clients,
)

__all__ = [
    "AlpacaClients",
    "create_alpaca_clients",
]
