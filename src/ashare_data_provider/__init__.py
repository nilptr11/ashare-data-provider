"""A 股数据 Provider 与 CLI。"""

from .client import TushareCallError, TushareCaller, TushareError
from .events import (
    AStockEventDependencyError,
    AStockEventError,
    AStockEventFetchError,
    NOTICE_CATEGORIES,
    auto_periods,
    fetch_forecast,
    fetch_notice,
)
from .news import (
    DEFAULT_NEWS_SOURCES,
    TushareNewsError,
    TushareNewsFetchError,
    TushareNewsParseError,
    build_news_records,
    crawl_tushare_news,
    load_tushare_cookie,
    normalize_news_sources,
    parse_news_page,
)
from .news_store import (
    merge_news_date_partitions,
    news_date_partition_path,
    news_record_date,
    normalize_news_date,
    partition_news_records_by_date,
    read_news_date_partitions,
)
from .provider import (
    TushareInterfaceSelectionError,
    TusharePermissionError,
    AShareProvider,
    AShareProviderError,
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
from .research_context import build_research_context
from .research_summary import build_research_summary, load_research_context, render_research_summary_markdown
from .schemas import ApiParameter, ApiSchema, SchemaError, get_api_schema, load_api_schemas
from .source_policy import blocked_tushare_apis, load_source_policy, resolve_gap_sources

__all__ = [
    "ApiParameter",
    "ApiSchema",
    "ApiRecipe",
    "AStockEventDependencyError",
    "AStockEventError",
    "AStockEventFetchError",
    "DEFAULT_NEWS_SOURCES",
    "NOTICE_CATEGORIES",
    "InterfaceEntry",
    "InterfaceRegistry",
    "RecipeError",
    "SchemaError",
    "TushareCallError",
    "TushareCaller",
    "TushareError",
    "TushareInterfaceSelectionError",
    "TushareNewsError",
    "TushareNewsFetchError",
    "TushareNewsParseError",
    "TusharePermissionError",
    "AShareProvider",
    "AShareProviderError",
    "TushareUnknownInterfaceError",
    "auto_periods",
    "build_news_records",
    "build_research_context",
    "build_research_summary",
    "blocked_tushare_apis",
    "crawl_tushare_news",
    "default_fields",
    "default_recipe_params",
    "fetch_forecast",
    "fetch_notice",
    "get_api_schema",
    "get_recipe",
    "load_tushare_cookie",
    "load_api_schemas",
    "load_recipes",
    "load_registry",
    "load_research_context",
    "load_source_policy",
    "merge_news_date_partitions",
    "news_date_partition_path",
    "news_record_date",
    "normalize_news_date",
    "normalize_news_sources",
    "partition_news_records_by_date",
    "parse_news_page",
    "read_news_date_partitions",
    "render_research_summary_markdown",
    "resolve_gap_sources",
]
