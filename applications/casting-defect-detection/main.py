import asyncio
import io

from kelvin.application import KelvinApp
from kelvin.krn import KRNAsset
from kelvin.logs import logger
from kelvin.message import AssetDataMessage, Recommendation

app = KelvinApp()

_MODEL_PATH = "model/inspection_of_casting_products.h5"
_model = None                                    # loaded once in on_connect


def load_model():
    """Load the Keras model. TensorFlow is imported here, not at module scope, so the app
    stays importable (and testable) without the heavy dependency loaded."""
    import tensorflow

    return tensorflow.keras.models.load_model(_MODEL_PATH)


def predict_image(model, image_base64: str) -> str:
    """Classify a base64-encoded casting image; return "ok" or "not_ok"."""
    import base64

    import numpy as np
    import tensorflow
    from PIL import Image

    image = Image.open(io.BytesIO(base64.b64decode(image_base64))).convert("L").resize((300, 300))
    img_array = np.expand_dims(tensorflow.keras.preprocessing.image.img_to_array(image), axis=0) / 255.0
    prediction = model.predict(img_array)
    # Binary classifier with a sigmoid output layer: > 0.5 is a good part.
    return "ok" if prediction[0][0] > 0.5 else "not_ok"


@app.on_connect
async def on_connect() -> None:
    """Load the model once, off the event loop, before any frames arrive."""
    global _model
    logger.info("Loading computer vision model", path=_MODEL_PATH)
    _model = await asyncio.to_thread(load_model)
    logger.info("Computer vision model loaded")


@app.stream(inputs=["camera-feed"])
async def on_camera_feed(msg: AssetDataMessage) -> None:
    """Classify each incoming casting image; recommend a fault when a defect is found."""
    asset = msg.resource.asset
    image = msg.payload                          # camera-image object: {image_filename, image_base64, ...}
    filename = image["image_filename"]

    # Inference is blocking, so run it in a thread to keep the event loop responsive.
    result = await asyncio.to_thread(predict_image, _model, image["image_base64"])
    logger.info("Prediction result", asset=asset, image=filename, result=result)

    if result == "not_ok":
        await app.publish(
            Recommendation(
                resource=KRNAsset(asset),
                type="fault_detected",
                description=f"Defect detected in the casting product with image: {filename}",
            )
        )


if __name__ == "__main__":
    app.run()
