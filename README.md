![Kelvin Logo](logo.png)

# Welcome to Kelvin SDK - App Samples
This repository contains sample applications that demonstrate how to use the **Kelvin SDK**. 

We recommend that you start first by reading the official Kelvin Documentation on https://docs.kelvin.ai.

# Sample Applications

## 🚀 SmartApps

| Application                                                                      | Domain               | Level        | Description                                                                                         |
|--------------------------------------------------------------------------------- |----------------------|--------------|-----------------------------------------------------------------------------------------------------|
| [Casting Defect Detection](applications/casting-defect-detection/)               | Computer Vision      | Advanced     | Uses a TensorFlow-based model to identify and analyze manufacturing defects in casting processes.   |
| [Event Detection](applications/event-detection/)                                 | Event Detection      | Beginner     | Monitors streaming data for threshold-crossing events and emits Control Changes or Recommendations. |
| [Multi-Objective Optimization ML](applications/multi-objective-optimization-ml/) | Machine Learning     | Advanced     | Solves multi-objective optimization problems using ML techniques.                                   |

## 📥 Importers

| Application                                     | Level        | Description                                          |
|-------------------------------------------------|--------------|------------------------------------------------------|
| [Camera Connector](importers/camera-connector/) | Intermediate | Publishes camera-feed images to the Kelvin Platform. |
| [MQTT Connector](importers/mqtt-connector/)     | Intermediate | Publishes MQTT messages to the Kelvin Platform.      |

## 📤 Exporters

| Application                                                                   | Level         | Description                                             |
|-------------------------------------------------------------------------------|---------------|---------------------------------------------------------|
| [AWS S3 Uploader](exporters/aws-s3-uploader/)                                 | Intermediate  | Uploads time-series data to an AWS S3 Bucket.           |
| [Azure Data Lake Gen2 Uploader](exporters/azure-data-lake-uploader/)          | Intermediate  | Uploads streaming data to Azure Data Lake Storage Gen2. |
| [Databricks Delta Table Uploader](exporters/databricks-delta-table-uploader/) | Intermediate  | Uploads streaming data to Databricks Delta Table.       |
| [Databricks Volume Uploader](exporters/databricks-volume-uploader/)           | Intermediate  | Uploads streaming data to a Databricks Volume.          |
| [Resnet Custom Actions](exporters/resnet-custom-actions/)                     | Intermediate  | Exports custom actions into Resnet API.                 |


## 🤖 Docker

| Application                                    | Level        | Description                                                      |
|------------------------------------------------|--------------|------------------------------------------------------------------|
| [Mosquitto MQTT](docker/mosquitto-mqtt/)       | Beginner     | Mosquitto MQTT Broker                                            |


# Contributing
1. Fork the project.
2. Create your feature branch (git checkout -b feature/YourFeature).
3. Commit your changes (git commit -m 'Add some feature').
4. Push to the branch (git push origin feature/YourFeature).
5. Open a pull request.