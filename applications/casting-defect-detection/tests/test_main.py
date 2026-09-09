import pytest

from kelvin.krn import KRNAssetDataStream
from kelvin.message import KMessageTypeData, Message, RecommendationMsg
from kelvin.testing import KelvinAppTest, ManifestBuilder

import main
from main import app

_ASSET = "caster-1"


def _manifest():
    # camera-feed is a camera-image ICD, which rides on the "object" primitive.
    return ManifestBuilder().add_input("camera-feed", "object").add_asset(_ASSET).build()


def _image_msg(filename: str):
    """A camera-image object message, matching what the image-feed importer publishes."""
    return Message(
        type=KMessageTypeData(primitive="object", icd="camera-image"),
        resource=KRNAssetDataStream(_ASSET, "camera-feed"),
        payload={"image_filename": filename, "image_base64": "Zm9v"},
    )


@pytest.fixture(autouse=True)
def _stub_model(monkeypatch):
    """Keep TensorFlow and the .h5 model out of the tests; only the decision logic matters here."""
    monkeypatch.setattr(main, "load_model", lambda: object())


class TestOnCameraFeed:
    @pytest.mark.asyncio
    async def test_defect_publishes_fault_recommendation(self, monkeypatch) -> None:
        monkeypatch.setattr(main, "predict_image", lambda model, image_base64: "not_ok")
        harness = KelvinAppTest(app, manifest=_manifest())
        async with harness:
            await harness.publish(_image_msg("bad.png"))
            await harness.run_until_idle()

        recs = [o for o in harness.outputs if isinstance(o, RecommendationMsg)]
        assert len(recs) == 1
        assert recs[0].payload.type == "fault_detected"
        assert "bad.png" in recs[0].payload.description

    @pytest.mark.asyncio
    async def test_good_part_publishes_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(main, "predict_image", lambda model, image_base64: "ok")
        harness = KelvinAppTest(app, manifest=_manifest())
        async with harness:
            await harness.publish(_image_msg("good.png"))
            await harness.run_until_idle()

        assert [o for o in harness.outputs if isinstance(o, RecommendationMsg)] == []
