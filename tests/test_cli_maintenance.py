import argparse
import unittest

from ashare_data_provider.cli import _validate_financial_scope
from ashare_data_provider.maintenance import MaintenanceError


class CliMaintenanceTest(unittest.TestCase):
    def test_stock_pool_datasets_require_explicit_pool(self) -> None:
        args = argparse.Namespace(
            maintain_command="daily",
            include_financials=False,
            include_stock_pool_datasets=True,
            max_stocks=None,
            all_stocks_financials=True,
        )

        with self.assertRaises(MaintenanceError):
            _validate_financial_scope(args, stock_pool=None)

    def test_financials_allow_explicit_all_stocks_flag(self) -> None:
        args = argparse.Namespace(
            maintain_command="daily",
            include_financials=True,
            include_stock_pool_datasets=False,
            max_stocks=None,
            all_stocks_financials=True,
        )

        _validate_financial_scope(args, stock_pool=None)


if __name__ == "__main__":
    unittest.main()
