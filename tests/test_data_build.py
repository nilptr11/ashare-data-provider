from argparse import Namespace
import json

import pandas as pd
import pytest

from ashare_research.cli import _build_dataset, _enrich_member_frame, main
from ashare_research.connectors import TushareConnector
from ashare_research.connectors.tushare import configure_tushare_proxy
from ashare_research.datasets.catalog import DatasetCatalog
from ashare_research.marts.publisher import MartPublisher
from ashare_research.marts.reader import MartReader
from ashare_research.raw_store import RawStore


class FakeTushareClient:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def query(self, api_name, fields=None, **params):
        self.calls.append({"api_name": api_name, "fields": fields, "params": params})
        return self.frame


def test_tushare_connector_fetches_dataframe_with_metadata():
    frame = pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": "20260623", "close": 10.0}])
    client = FakeTushareClient(frame)

    response = TushareConnector(client=client).fetch("daily", {"trade_date": "20260623"}, fields=["ts_code", "close"])

    assert response.source == "tushare"
    assert response.api_name == "daily"
    assert response.rows == 1
    assert response.fields == ("ts_code", "close")
    assert client.calls[0]["fields"] == "ts_code,close"


def test_tushare_connector_passes_timeout_to_sdk(monkeypatch):
    from tushare.pro import client as ts_client

    calls = []
    original_url = ts_client.DataApi._DataApi__http_url

    def fake_pro_api(token, timeout=30):
        calls.append({"token": token, "timeout": timeout})
        return FakeTushareClient(pd.DataFrame())

    monkeypatch.delenv("TUSHARE_PROXY_URL", raising=False)
    monkeypatch.setattr("tushare.pro_api", fake_pro_api)

    try:
        client = TushareConnector(token="token-1", timeout=7)._build_client()
    finally:
        ts_client.DataApi._DataApi__http_url = original_url

    assert isinstance(client, FakeTushareClient)
    assert calls == [{"token": "token-1", "timeout": 7}]


def test_configure_tushare_proxy_updates_sdk_endpoint(monkeypatch):
    import ashare_research.connectors.tushare as tushare_module
    from tushare.pro import client as ts_client

    original_url = ts_client.DataApi._DataApi__http_url
    proxy_url = "https://proxy.example.com/tushare"

    try:
        monkeypatch.setattr(tushare_module, "_TUSHARE_DEFAULT_HTTP_URL", None)
        configure_tushare_proxy(proxy_url)

        assert ts_client.DataApi._DataApi__http_url == proxy_url

        configure_tushare_proxy(None)

        assert ts_client.DataApi._DataApi__http_url == original_url
    finally:
        ts_client.DataApi._DataApi__http_url = original_url


def test_raw_store_and_mart_publisher_write_lineage(tmp_path):
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260623",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "pct_chg": 1.0,
                "vol": 100.0,
                "amount": 1000.0,
            }
        ]
    )
    response = TushareConnector(client=FakeTushareClient(frame)).fetch("daily", {"trade_date": "20260623"})

    raw_path = RawStore(tmp_path).write_response(response)
    mart_path = MartPublisher(tmp_path, DatasetCatalog.builtin()).publish(
        "daily",
        response.frame,
        partition={"trade_date": "20260623"},
        source={"kind": "tushare", "api_name": "daily", "raw_path": str(raw_path)},
    )

    assert (raw_path / "request.json").exists()
    assert (raw_path / "response.jsonl").exists()
    meta = json.loads((mart_path / "_meta.json").read_text(encoding="utf-8"))
    assert meta["quality_status"] == "ok"
    assert meta["source"]["raw_path"] == str(raw_path)


def test_cli_data_build_uses_connector_raw_store_and_publisher(monkeypatch, capsys, tmp_path):
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260623",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "pre_close": 10.0,
                "change": 0.5,
                "pct_chg": 5.0,
                "vol": 100.0,
                "amount": 1000.0,
            }
        ]
    )

    created_kwargs = []

    class FakeConnector:
        def __init__(self, **kwargs):
            created_kwargs.append(kwargs)
            self.kwargs = kwargs

        def fetch(self, api_name, params, fields=None):
            return TushareConnector(client=FakeTushareClient(frame)).fetch(api_name, params, fields)

    monkeypatch.setattr("ashare_research.cli.TushareConnector", FakeConnector)

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "data",
            "build",
            "daily",
            "--trade-date",
            "20260623",
            "--timeout",
            "7",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"] == "daily"
    assert payload["rows"] == 1
    assert payload["quality_status"] == "ok"
    assert payload["quality"]["status"] == "ok"
    assert created_kwargs[0]["timeout"] == 7
    assert (tmp_path / "mart" / "daily" / "trade_date=20260623" / "part.parquet").exists()
    assert (tmp_path / "raw" / "tushare" / "daily").exists()


@pytest.mark.parametrize("command", ["build", "update"])
def test_cli_data_build_and_update_reject_field_overrides(command, capsys, tmp_path):
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--data-dir",
                str(tmp_path),
                "data",
                command,
                "daily",
                "--trade-date",
                "20260623",
                "--fields",
                "ts_code,trade_date",
            ]
        )

    assert error.value.code == 2
    assert "unrecognized arguments: --fields" in capsys.readouterr().err


def test_data_build_ignores_programmatic_fields_override(monkeypatch, tmp_path):
    calls = []
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260623",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "pre_close": 10.0,
                "change": 0.5,
                "pct_chg": 5.0,
                "vol": 100.0,
                "amount": 1000.0,
            }
        ]
    )

    class FakeConnector:
        def __init__(self, **kwargs):
            pass

        def fetch(self, api_name, params, fields=None):
            calls.append(tuple(fields or ()))
            return TushareConnector(client=FakeTushareClient(frame)).fetch(api_name, params, fields)

    monkeypatch.setattr("ashare_research.cli.TushareConnector", FakeConnector)

    args = Namespace(
        dataset="daily",
        trade_date="20260623",
        snapshot_date=None,
        exchange=None,
        period=None,
        publish_date=None,
        start_date=None,
        end_date=None,
        param=[],
        fields="ts_code,trade_date",
        token=None,
        proxy_url=None,
        timeout=30,
        env_file=None,
        refresh=False,
    )

    payload = _build_dataset(args, MartReader(tmp_path, DatasetCatalog.builtin()))

    assert payload["quality_status"] == "ok"
    assert calls == [DatasetCatalog.builtin().require("daily").default_fields]


def test_enrich_member_frame_maps_con_code_to_stock_ts_code():
    spec = DatasetCatalog.builtin().require("ths_member")
    driver = pd.DataFrame([{"ts_code": "885800.TI", "name": "消费电子"}])
    frame = pd.DataFrame([{"ts_code": "885800.TI", "con_code": "000016.SZ", "con_name": "深康佳A"}])

    output = _enrich_member_frame(frame, spec, driver, "ts_code", "name")

    assert output.iloc[0]["_driver_ts_code"] == "885800.TI"
    assert output.iloc[0]["_driver_name"] == "消费电子"
    assert output.iloc[0]["ts_code"] == "000016.SZ"
    assert output.iloc[0]["name"] == "深康佳A"


def test_cli_data_build_dc_member_publishes_real_stock_codes(monkeypatch, capsys, tmp_path):
    catalog = DatasetCatalog.builtin()
    MartPublisher(tmp_path, catalog).publish(
        "dc_index",
        pd.DataFrame([{"trade_date": "20260623", "ts_code": "BK1184.DC", "name": "人形机器人"}]),
        partition={"trade_date": "20260623"},
        source={"kind": "test"},
    )
    calls = []

    class FakeConnector:
        def __init__(self, **kwargs):
            pass

        def fetch(self, api_name, params, fields=None):
            calls.append({"api_name": api_name, "params": dict(params), "fields": tuple(fields or ())})
            frame = pd.DataFrame(
                [
                    {
                        "trade_date": params["trade_date"],
                        "ts_code": params["ts_code"],
                        "con_code": "002117.SZ",
                        "name": "东港股份",
                    }
                ]
            )
            return TushareConnector(client=FakeTushareClient(frame)).fetch(api_name, params, fields)

    monkeypatch.setattr("ashare_research.cli.TushareConnector", FakeConnector)

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "data",
            "build",
            "dc_member",
            "--trade-date",
            "20260623",
            "--stock",
            "BK1184.DC",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"] == "dc_member"
    assert payload["quality_status"] == "ok"
    assert calls[0]["fields"] == catalog.require("dc_member").default_fields

    frame = pd.read_parquet(tmp_path / "mart" / "dc_member" / "trade_date=20260623" / "part.parquet")
    assert frame.iloc[0]["_driver_ts_code"] == "BK1184.DC"
    assert frame.iloc[0]["_driver_name"] == "人形机器人"
    assert frame.iloc[0]["ts_code"] == "002117.SZ"
    assert frame.iloc[0]["name"] == "东港股份"


def test_cli_data_update_is_build_alias(monkeypatch, capsys, tmp_path):
    frame = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "trade_date": "20260623",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "pre_close": 10.0,
                "change": 0.5,
                "pct_chg": 5.0,
                "vol": 100.0,
                "amount": 1000.0,
            }
        ]
    )

    class FakeConnector:
        def __init__(self, **kwargs):
            pass

        def fetch(self, api_name, params, fields=None):
            return TushareConnector(client=FakeTushareClient(frame)).fetch(api_name, params, fields)

    monkeypatch.setattr("ashare_research.cli.TushareConnector", FakeConnector)

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "data",
            "update",
            "daily",
            "--trade-date",
            "20260623",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["dataset"] == "daily"


def test_cli_data_build_expands_index_daily_variants(monkeypatch, capsys, tmp_path):
    calls = []

    class FakeConnector:
        def __init__(self, **kwargs):
            pass

        def fetch(self, api_name, params, fields=None):
            calls.append(dict(params))
            frame = pd.DataFrame(
                [
                    {
                        "ts_code": params["ts_code"],
                        "trade_date": params["trade_date"],
                        "close": 1000.0,
                        "open": 990.0,
                        "high": 1010.0,
                        "low": 980.0,
                        "pre_close": 995.0,
                        "change": 5.0,
                        "pct_chg": 0.5,
                        "vol": 100.0,
                        "amount": 1000.0,
                    }
                ]
            )
            return TushareConnector(client=FakeTushareClient(frame)).fetch(api_name, params, fields)

    monkeypatch.setattr("ashare_research.cli.TushareConnector", FakeConnector)

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "data",
            "build",
            "index_daily",
            "--trade-date",
            "20260623",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"] == "index_daily"
    assert payload["rows"] == 6
    assert len(payload["raw_paths"]) == 6
    assert {call["ts_code"] for call in calls} == {
        "000001.SH",
        "000300.SH",
        "000905.SH",
        "000852.SH",
        "399001.SZ",
        "399006.SZ",
    }
    assert (tmp_path / "mart" / "index_daily" / "trade_date=20260623" / "part.parquet").exists()


def test_cli_data_build_hsgt_top10_requests_amount_fields(monkeypatch, capsys, tmp_path):
    calls = []

    class FakeConnector:
        def __init__(self, **kwargs):
            pass

        def fetch(self, api_name, params, fields=None):
            calls.append({"params": dict(params), "fields": tuple(fields or ())})
            market_type = str(params["market_type"])
            frame = pd.DataFrame(
                [
                    {
                        "trade_date": params["trade_date"],
                        "ts_code": "600519.SH" if market_type == "1" else "000001.SZ",
                        "name": "样本股",
                        "close": 100.0,
                        "change": 1.0,
                        "rank": 1,
                        "market_type": market_type,
                        "amount": 1000.0,
                        "net_amount": None,
                        "buy": None,
                        "sell": None,
                    }
                ]
            )
            return TushareConnector(client=FakeTushareClient(frame)).fetch(api_name, params, fields)

    monkeypatch.setattr("ashare_research.cli.TushareConnector", FakeConnector)

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "data",
            "build",
            "hsgt_top10",
            "--trade-date",
            "20260623",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"] == "hsgt_top10"
    assert payload["rows"] == 2
    assert payload["quality_status"] == "ok"
    assert {call["params"]["market_type"] for call in calls} == {"1", "3"}
    assert all({"amount", "net_amount", "buy", "sell"} <= set(call["fields"]) for call in calls)

    frame = pd.read_parquet(tmp_path / "mart" / "hsgt_top10" / "trade_date=20260623" / "part.parquet")
    assert {"amount", "net_amount", "buy", "sell", "_variant"} <= set(frame.columns)


def test_cli_data_update_publishes_akshare_notice(monkeypatch, capsys, tmp_path):
    from ashare_research import events

    def fake_fetch_notice(**kwargs):
        return [
            {
                "id": "notice-1",
                "content_hash": "h1",
                "dedupe_key": "d1",
                "event_type": "notice",
                "source_kind": "akshare_notice",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "title": "公告",
                "notice_type": "财务报告",
                "publish_date": "2026-06-24",
                "url": "https://example.com",
                "fetched_at": "2026-06-24T20:00:00+08:00",
                "raw": {},
            }
        ]

    monkeypatch.setattr(events, "fetch_notice", fake_fetch_notice)

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "data",
            "update",
            "a_stock_notice",
            "--end-date",
            "20260624",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"] == "a_stock_notice"
    assert payload["rows"] == 1
    assert (tmp_path / "mart" / "a_stock_notice" / "publish_date=2026-06-24" / "part.parquet").exists()


def test_cli_data_update_publishes_empty_akshare_notice_partition(monkeypatch, capsys, tmp_path):
    from ashare_research import events

    monkeypatch.setattr(events, "fetch_notice", lambda **kwargs: [])

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "data",
            "update",
            "a_stock_notice",
            "--end-date",
            "20260624",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"] == "a_stock_notice"
    assert payload["rows"] == 0
    check = MartReader(tmp_path).check_dataset("a_stock_notice", as_of="20260624")
    assert check.status == "ready"
    assert check.partition == {"publish_date": "2026-06-24"}


def test_cli_data_update_publishes_earnings_forecast(monkeypatch, capsys, tmp_path):
    from ashare_research import events

    monkeypatch.setattr(
        events,
        "fetch_forecast",
        lambda **kwargs: [
            {
                "id": "forecast-1",
                "content_hash": "h1",
                "dedupe_key": "d1",
                "event_type": "forecast",
                "source_kind": "akshare_yjyg_em",
                "period": "20260331",
                "stock_code": "000001",
                "stock_name": "平安银行",
                "metric": "净利润",
                "forecast_type": "预增",
                "change_range": "10%",
                "publish_date": "2026-06-24",
                "change_summary": "增长",
                "change_reason": "经营改善",
                "fetched_at": "2026-06-24T20:00:00+08:00",
                "raw": {},
            }
        ],
    )

    exit_code = main(
        [
            "--data-dir",
            str(tmp_path),
            "data",
            "update",
            "earnings_forecast",
            "--end-date",
            "20260624",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dataset"] == "earnings_forecast"
    assert payload["rows"] == 1
    assert (tmp_path / "mart" / "earnings_forecast" / "publish_date=2026-06-24" / "part.parquet").exists()
