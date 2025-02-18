# Event Detection
This application demonstrates how to use the Kelvin SDK to detect events above a pre-defined threshold in streaming data and emit a Control Change or Recommendation. This example also leverages Dynamic/Runtime Asset Parameters.

# Requirements
1. Python 3.8 or higher
2. Install Kelvin SDK: `pip3 install kelvin-sdk`
3. Install project dependencies: `pip3 install -r requirements.txt`
4. Docker (optional) for upload the application to a Kelvin Instance.

# Usage
1. Open a new terminal and **Run** the application: `python3 main.py`
2. Open a new terminal and **Test** with synthetic data: `kelvin app test simulator`