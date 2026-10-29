"""Construct the SDK's evidence variants without performing external requests."""

from datetime import datetime, timedelta

from kelvin.krn import KRNAssetDataStream
from kelvin.message.evidences import (
    BarChart,
    Chart,
    DataExplorer,
    DataExplorerSelector,
    Dynacard,
    Evidence,
    IFrame,
    Image,
    LineChart,
    Markdown,
)


def build_evidences(asset: str, timestamp: datetime) -> list[Evidence]:
    """Build examples for each evidence kind at the supplied event time."""
    return [
        Markdown(
            title="Temperature limit",  # Optional title; markdown is required.
            markdown="Motor temperature exceeded 80 C.",
        ),
        DataExplorer(
            title="Temperature history",  # Title, times, and selectors are required.
            start_time=timestamp - timedelta(minutes=5),
            end_time=timestamp,
            selectors=[
                DataExplorerSelector(
                    resource=KRNAssetDataStream(asset, "temperature_c"),
                    agg="mean",  # Optional aggregation; omitted means no selection here.
                    time_bucket="1m",  # Optional integer plus time unit.
                )
            ],
        ),
        BarChart(
            title="Temperature by sample",
            timestamp=timestamp,
            x_axis={"categories": ["First", "Second"]},
            y_axis={"title": {"text": "Temperature (C)"}},
            series=[{"name": "Temperature", "data": [79, 81]}],
        ),
        LineChart(
            title="Temperature trend",
            timestamp=timestamp,
            x_axis={"type": "linear"},
            y_axis={"title": {"text": "Temperature (C)"}},
            series=[{"name": "Temperature", "data": [[0, 79], [1, 81]]}],
        ),
        Dynacard(
            title="Pump load and position",  # Paired position/load samples.
            timestamp=timestamp,
            series=[{"name": "Pump cycle", "data": [[0, 100], [1, 120], [0, 100]]}],
        ),
        Chart(
            title="Temperature comparison",
            timestamp=timestamp,
            chart={"type": "column"},  # Generic chart accepts extra chart options.
            series=[{"name": "Temperature", "data": [79, 81]}],
        ),
        Image(
            title="Motor inspection",
            url="https://maintenance.example.com/motors/motor-01.jpg",  # Required HTTP(S) URL.
            description="Inspection photo for the motor.",
            timestamp=timestamp,
        ),
        IFrame(
            title="Motor maintenance record",
            url="https://maintenance.example.com/motors/motor-01",  # Required HTTP(S) URL.
        ),
    ]
