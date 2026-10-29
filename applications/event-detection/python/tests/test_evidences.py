"""Validate each evidence model inside a serialized Recommendation."""

from datetime import datetime, timezone

from evidence_examples import build_evidences
from kelvin.krn import KRNAsset, KRNAssetDataStream
from kelvin.message import Recommendation


class TestEvidenceExamples:
    """Check all supported evidence kinds and their serialized payloads."""

    def test_all_evidence_types(self) -> None:
        """A complete Recommendation retains all eight distinct variants."""
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        evidences = build_evidences("motor-01", timestamp)
        result = Recommendation(
            resource=KRNAsset("motor-01"), type="decrease_speed", evidences=evidences
        ).to_message()
        assert [evidence.type for evidence in result.payload.evidences] == [
            "markdown",
            "data-explorer",
            "bar-chart",
            "line-chart",
            "dynacard",
            "chart",
            "image",
            "iframe",
        ]
        assert result.payload.evidences[1].payload.selectors[
            0
        ].resource == KRNAssetDataStream("motor-01", "temperature_c")
