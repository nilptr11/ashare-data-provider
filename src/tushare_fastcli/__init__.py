"""Tushare 快速调用工具。"""

from .client import TushareCallError, TushareCaller, TushareError
from .provider import (
    TushareInterfaceSelectionError,
    TusharePermissionError,
    TushareProvider,
    TushareProviderError,
    TushareUnknownInterfaceError,
)
from .recipes import (
    ApiRecipe,
    RecipeError,
    default_fields,
    default_recipe_params,
    get_recipe,
    load_recipes,
)
from .registry import InterfaceEntry, InterfaceRegistry, load_registry

__all__ = [
    "ApiRecipe",
    "InterfaceEntry",
    "InterfaceRegistry",
    "RecipeError",
    "TushareCallError",
    "TushareCaller",
    "TushareError",
    "TushareInterfaceSelectionError",
    "TusharePermissionError",
    "TushareProvider",
    "TushareProviderError",
    "TushareUnknownInterfaceError",
    "default_fields",
    "default_recipe_params",
    "get_recipe",
    "load_recipes",
    "load_registry",
]
