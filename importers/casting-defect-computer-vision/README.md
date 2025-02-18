# Camera Importer
Simulates image capture from a camera and publishes the image in base64 format to the Kelvin Platform. In production environments, it would interface directly with live camera feeds to acquire real-time images.

# Requirements
1. Python 3.8 or higher
2. Install Kelvin SDK: `pip3 install kelvin-sdk`
3. Install project dependencies: `pip3 install -r requirements.txt`
4. Docker (optional) for upload the application to a Kelvin Instance.

# Usage
1. Upload both applications to a Kelvin Instance: `kelvin app upload`
2. Deploy the Camera Importer application
