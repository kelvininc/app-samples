
# Welcome to Kelvin SDK - App Samples
This repository contains sample applications that demonstrate how to use the **Kelvin SDK**. 

We recommend that you start first by reading the official Kelvin Documentation on https://docs.kelvininc.com.

# Applications

| Application | Type | Level | Description |
| ----------- | ---- | ----- | ----------- |
| [Event Detection (Simple)](event-detection-simple/) | Event Detection | Beginner | This application demonstrates how to use the Kelvin SDK to detect events above a pre-defined threshold in streaming data. |
| [Event Detection (Complex)](event-detection-complex/) | Event Detection | Intermediate | This application demonstrates how to use the Kelvin SDK to detect events above a pre-defined threshold in streaming data. This example leverages Asset Parameters and App Parameters to make the application more dynamic. |

# Requirements
1. Python 3.8 or higher
2. Install Kelvin SDK: `pip install kelvin-sdk`
3. Install project dependencies: `pip install -r requirements.txt`
4. Docker (optional) for upload the application to a Kelvin Instance.

# Usage
1. Run the application: `python main.py`
2. Open a new terminal and test with synthetic data: `kelvin app test simulator`

# Contributing
1. Fork the project.
2. Create your feature branch (git checkout -b feature/YourFeature).
3. Commit your changes (git commit -m 'Add some feature').
4. Push to the branch (git push origin feature/YourFeature).
5. Open a pull request.
