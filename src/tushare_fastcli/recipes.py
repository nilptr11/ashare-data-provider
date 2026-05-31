from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any


class RecipeError(ValueError):
    pass


@dataclass(frozen=True)
class ApiRecipe:
    api_name: str
    title: str
    default_fields: tuple[str, ...]
    default_params: dict[str, Any]
    primary_key: tuple[str, ...]
    date_field: str | None
    frequency: str

    @property
    def fields(self) -> str:
        return ",".join(self.default_fields)


def _recipe_from_dict(item: dict[str, Any]) -> ApiRecipe:
    return ApiRecipe(
        api_name=str(item["api_name"]),
        title=str(item.get("title", "")),
        default_fields=tuple(str(field) for field in item.get("default_fields", [])),
        default_params=dict(item.get("default_params", {})),
        primary_key=tuple(str(field) for field in item.get("primary_key", [])),
        date_field=item.get("date_field"),
        frequency=str(item.get("frequency", "")),
    )


def load_recipes(path: str | Path | None = None) -> dict[str, ApiRecipe]:
    if path is None:
        data_path = files("tushare_fastcli").joinpath("recipes.json")
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        name: _recipe_from_dict(item)
        for name, item in payload.get("recipes", {}).items()
    }


def get_recipe(api_name: str) -> ApiRecipe:
    recipes = load_recipes()
    try:
        return recipes[api_name]
    except KeyError as exc:
        raise RecipeError(f"未找到接口 recipe：{api_name}") from exc


def default_fields(api_name: str) -> str:
    return get_recipe(api_name).fields


def default_recipe_params(api_name: str) -> dict[str, Any]:
    return dict(get_recipe(api_name).default_params)
