import asyncio
import base64
import io
import json

import numpy as np
import tensorflow
from kelvin.application import KelvinApp, filters
from kelvin.message import Recommendation, String
from kelvin.message.krn import KRNAsset
from PIL import Image


def predict_image(model, image_base64: str):
    # Decode the base64 string
    image_data = base64.b64decode(image_base64)
    image = Image.open(io.BytesIO(image_data))

    # Convert the image to grayscale
    image = image.convert("L")  # Convert to grayscale

    # Resize the image to the target size
    target_size = (300, 300)  # Adjust this to your model's expected input size
    image = image.resize(target_size)

    # Convert the image to a numpy array and preprocess
    img_array = tensorflow.keras.preprocessing.image.img_to_array(image)
    img_array = np.expand_dims(img_array, axis=0)  # Create a batch
    img_array /= 255.0  # Scale image values to [0,1]

    # Make a prediction
    prediction = model.predict(img_array)

    # Model outputs a binary classification with a sigmoid activation function in the last layer
    if prediction[0][0] > 0.5:
        return "ok"
    else:
        return "not_ok"


async def main() -> None:

    # Load the computer vision model
    print("Loading computer vision model")
    model = tensorflow.keras.models.load_model("model/inspection_of_casting_products.h5")
    print("Computer vision model loaded")

    # Creating instance of Kelvin App Client
    app = KelvinApp()

    # Connect the App Client
    await app.connect()

    # Create a queue to receive the data
    queue: asyncio.Queue[String] = app.filter(filters.input_equals("camera-feed"))

    while True:
        # Get the message from the queue
        msg = await queue.get()

        try:
            # Parse the message
            image_info = json.loads(msg.payload)
            image_filename = image_info["image_filename"]
            image_base64 = image_info["image_base64"]

            # Predict
            print(f"Predicting image: '{image_filename}'")
            result = predict_image(model, image_base64)
            print(f"Prediction result for image 'image_filename': {result}")

            # Send recommendation if need
            if result == "not_ok":
                await app.publish(
                    Recommendation(
                        resource=KRNAsset(msg.resource.asset),
                        type="fault_detected",
                        description=f"Defect detected in the casting product with image: {image_filename}",
                    )
                )
        except Exception as e:
            print(f"Error: {e}")

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
