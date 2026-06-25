from ashare_research.datasets.catalog import DatasetCatalog


def test_builtin_catalog_contains_core_market_contracts():
    catalog = DatasetCatalog.builtin()
    specs = catalog.list()

    assert len(specs) == 52

    daily = catalog.require("daily")

    assert daily.partition_keys == ("trade_date",)
    assert "ts_code" in daily.required_columns
    assert daily.units["amount"] == "千元"

    ths_index = catalog.require("ths_index")
    assert ths_index.partition_keys == ("snapshot_date",)
    assert "trade_date" not in ths_index.required_columns

    index_member_all = catalog.require("index_member_all")
    assert index_member_all.page_limit == 3000
    assert {"l1_code", "l2_code", "l3_code", "ts_code"} <= set(index_member_all.required_columns)

    ths_member = catalog.require("ths_member")
    assert ths_member.required_columns == ("_driver_ts_code", "ts_code")
    assert {"ts_code", "con_code", "con_name"} <= set(ths_member.default_fields)

    dc_member = catalog.require("dc_member")
    assert dc_member.required_columns == ("_driver_ts_code", "ts_code")
    assert {"trade_date", "ts_code", "con_code", "name"} <= set(dc_member.default_fields)

    hsgt_top10 = catalog.require("hsgt_top10")
    assert {"amount", "net_amount", "buy", "sell"} <= set(hsgt_top10.required_columns)
    assert {"amount", "net_amount", "buy", "sell"} <= set(hsgt_top10.default_fields)
    assert hsgt_top10.analysis_columns == ("amount",)
    assert hsgt_top10.units["amount"] == "元"


def test_catalog_discovery_marks_unregistered_mart(tmp_path):
    mart_root = tmp_path / "mart"
    (mart_root / "custom_dataset").mkdir(parents=True)

    rows = DatasetCatalog.builtin().discover(mart_root)
    discovered = {row["name"]: row for row in rows}

    assert discovered["daily"]["registered"] is True
    assert discovered["daily"]["has_mart"] is False
    assert discovered["custom_dataset"]["registered"] is False
    assert discovered["custom_dataset"]["has_mart"] is True
