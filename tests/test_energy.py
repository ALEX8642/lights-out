import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from lights_out.energy import (
    CsvSource,
    DrillEnergy,
    HomeAssistantSource,
    integrate,
    price_batch,
)

T0 = datetime(2026, 3, 3, 2, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_integrate_holds_each_reading_until_the_next() -> None:
    samples = [(at(0), 100.0), (at(60), 300.0), (at(120), 100.0)]
    # 60 s at 100 W + 60 s at 300 W = 6000 + 18000 J = 24000 J = 6.667 Wh
    assert integrate(samples, at(0), at(120)) == pytest.approx(24000 / 3600)


def test_integrate_clips_to_the_window_and_uses_the_reading_in_force_at_its_start() -> None:
    samples = [(at(0), 100.0), (at(60), 300.0)]
    # window 30..90: 30 s at 100 W (reading from t=0 still in force) + 30 s at 300 W
    assert integrate(samples, at(30), at(90)) == pytest.approx((3000 + 9000) / 3600)


def test_integrate_with_no_reading_before_the_window_starts_at_the_first_reading() -> None:
    samples = [(at(60), 200.0)]
    assert integrate(samples, at(0), at(120)) == pytest.approx(200 * 60 / 3600)


def test_integrate_with_no_readings_is_none() -> None:
    assert integrate([], at(0), at(60)) is None


def summary_file(tmp_path: Path) -> Path:
    rows = [
        {"scenario": "a", "temperature": 0.0, "repeat": 1, "correct": True,
         "started": at(0).isoformat(), "ended": at(60).isoformat(), "wall_seconds": 60.0},
        {"scenario": "a", "temperature": 0.0, "repeat": 2, "correct": False,
         "started": at(60).isoformat(), "ended": at(120).isoformat(), "wall_seconds": 60.0},
    ]  # fmt: skip
    path = tmp_path / "summary.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_price_batch_per_drill_and_above_idle(tmp_path: Path) -> None:
    csv = tmp_path / "power.csv"
    csv.write_text(f"time,watts\n{at(-10).isoformat()},250\n{at(60).isoformat()},130\n")
    priced = price_batch(summary_file(tmp_path), CsvSource(csv), idle_watts=120.0)
    assert [(p.scenario, p.repeat, p.correct, p.seconds) for p in priced] == [
        ("a", 1, True, 60.0),
        ("a", 2, False, 60.0),
    ]
    assert [p.wh for p in priced] == pytest.approx([250 / 60, 130 / 60])
    assert [p.wh_above_idle for p in priced] == pytest.approx([130 / 60, 10 / 60])
    assert isinstance(priced[0], DrillEnergy)


def test_the_csv_source_rejects_bad_rows(tmp_path: Path) -> None:
    csv = tmp_path / "power.csv"
    csv.write_text("time,watts\nnot-a-time,5\n")
    with pytest.raises(ValueError, match="line 2"):
        CsvSource(csv).samples(at(0), at(60))


class FakeHA:
    def __init__(self, states: list[dict[str, str]]) -> None:
        self.requests: list[tuple[str, dict[str, list[str]], str]] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                parts = urlsplit(self.path)
                fake.requests.append(
                    (parts.path, parse_qs(parts.query), self.headers.get("Authorization", ""))
                )
                body = json.dumps([states]).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        ).start()


@pytest.fixture
def ha() -> Iterator[FakeHA]:
    fake = FakeHA(
        [
            {"state": "150.5", "last_changed": at(0).isoformat()},
            {"state": "unavailable", "last_changed": at(10).isoformat()},
            {"state": "260", "last_changed": at(20).isoformat()},
        ]
    )
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


def test_home_assistant_source_reads_history(ha: FakeHA) -> None:
    source = HomeAssistantSource(ha.url, "sensor.plug_power", "tok-123")
    assert source.samples(at(0), at(60)) == [(at(0), 150.5), (at(20), 260.0)]
    path, query, auth = ha.requests[0]
    assert path.startswith("/api/history/period/")
    assert query["filter_entity_id"] == ["sensor.plug_power"]
    assert "end_time" in query
    assert auth == "Bearer tok-123"


def test_home_assistant_source_needs_http() -> None:
    with pytest.raises(ValueError, match="http"):
        HomeAssistantSource("file:///x", "sensor.p", "t")


def test_cli_energy_with_a_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from lights_out.cli import main

    summary_file(tmp_path)
    csv = tmp_path / "power.csv"
    csv.write_text(f"time,watts\n{at(-10).isoformat()},250\n{at(60).isoformat()},130\n")
    code = main(["energy", str(tmp_path), "--csv", str(csv), "--idle-watts", "120"])
    assert code == 0
    out = capsys.readouterr().out
    assert "a" in out and "1/2" in out
    # total 380/60 Wh over one correct drill
    assert f"{380 / 60:.2f}" in out
    lines = (tmp_path / "energy.csv").read_text().splitlines()
    assert lines[0].startswith("scenario,temperature,repeat,correct,seconds,wh,wh_above_idle")
    assert len(lines) == 3


def test_cli_energy_needs_one_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from lights_out.cli import main

    summary_file(tmp_path)
    assert main(["energy", str(tmp_path), "--idle-watts", "120"]) == 2
    assert "one of --csv or --ha-url" in capsys.readouterr().err
