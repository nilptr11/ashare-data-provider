import unittest

from tushare_fastcli.recipes import default_fields, default_recipe_params, get_recipe, load_recipes


class RecipesTest(unittest.TestCase):
    def test_load_recipes_reads_core_metadata(self) -> None:
        recipes = load_recipes()

        self.assertIn("daily", recipes)
        self.assertEqual(recipes["daily"].primary_key, ("ts_code", "trade_date"))
        self.assertEqual(recipes["daily"].date_field, "trade_date")

    def test_default_fields_returns_comma_separated_fields(self) -> None:
        self.assertEqual(
            default_fields("stk_limit"),
            "ts_code,trade_date,up_limit,down_limit",
        )

    def test_default_recipe_params_returns_copy(self) -> None:
        params = default_recipe_params("stock_basic")
        params["list_status"] = "D"

        self.assertEqual(default_recipe_params("stock_basic")["list_status"], "L")

    def test_get_recipe_exposes_field_string(self) -> None:
        recipe = get_recipe("trade_cal")

        self.assertEqual(recipe.fields, "exchange,cal_date,is_open,pretrade_date")


if __name__ == "__main__":
    unittest.main()
