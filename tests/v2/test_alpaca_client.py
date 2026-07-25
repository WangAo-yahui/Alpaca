from __future__ import annotations

import unittest

from v2.data.alpaca_client import (
    create_alpaca_clients,
)
from v2.exceptions import (
    BrokerUnavailableError,
    ConfigurationError,
    LiveTradingRejected,
)


class AlpacaClientTests(unittest.TestCase):
    def test_injected_factories_receive_paper_credentials(
        self,
    ) -> None:
        calls: list[dict[str, object]] = []

        def factory(**kwargs: object) -> object:
            calls.append(kwargs)
            return object()

        clients = create_alpaca_clients(
            environ={
                "ALPACA_API_KEY": "key-value",
                "ALPACA_SECRET_KEY": "secret-value",
                "ALPACA_PAPER": "true",
            },
            trading_factory=factory,
            stock_data_factory=factory,
        )
        self.assertTrue(clients.paper)
        self.assertTrue(calls[0]["paper"])
        self.assertNotIn("paper", calls[1])

    def test_missing_credentials_is_fatal_config(
        self,
    ) -> None:
        with self.assertRaises(ConfigurationError):
            create_alpaca_clients(environ={})

    def test_live_is_rejected(self) -> None:
        with self.assertRaises(
            LiveTradingRejected
        ):
            create_alpaca_clients(
                live=True,
                paper=False,
                environ={},
            )

    def test_secret_is_not_exposed_by_factory_error(
        self,
    ) -> None:
        secret = "do-not-leak-this-secret"

        def failing_factory(
            **kwargs: object,
        ) -> object:
            raise RuntimeError(secret)

        with self.assertRaises(
            BrokerUnavailableError
        ) as context:
            create_alpaca_clients(
                environ={
                    "ALPACA_API_KEY": "key",
                    "ALPACA_SECRET_KEY": secret,
                },
                trading_factory=failing_factory,
                stock_data_factory=(
                    failing_factory
                ),
            )
        disposition = (
            context.exception.disposition()
        )
        serialized = (
            disposition.message
            + repr(disposition.details)
        )
        self.assertNotIn(secret, serialized)


if __name__ == "__main__":
    unittest.main()
