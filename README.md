![Kelvin Logo](logo.png)

# Welcome to Kelvin SDK - App Samples
This repository contains sample applications that demonstrate how to use the **Kelvin SDK**. 

We recommend that you start first by reading the official Kelvin Documentation on https://docs.kelvin.ai.

# Applications

| Application | Type | Level | Description |
| ----------- | ---- | ----- | ----------- |
| [AWS S3 Uploader](exporters/aws-s3-uploader/) | Exporter | Intermediate | Uploads timeseries data to an AWS S3 Bucket. |
| [Azure Data Lake Gen2 Uploader](exporters/azure-data-lake-uploader/) | Exporter | Intermediate | Uploads streaming data to Azure Data Lake Storage Gen2. |
| [Databricks Delta Table Uploader](exporters/databricks-delta-table-uploader/) | Exporter | Intermediate |  Uploads streaming data to Databricks Delta Table. |
| [Databricks Volume Uploader](exporters/databricks-volume-uploader/) | Exporter | Intermediate |  Uploads streaming data to Databricks Volume. |
| [Camera Connector](importers/camera-connector/) | Importer | Intermediate |  Publishes camera feed images to Kelvin Platform. |
| [Casting Defect Detection](applications/casting-defect-detection/) | Computer Vision (App) | Intermediate | Leverages computer vision and a Tensorflow-based model to identify and analyze manufacturing defects in casting processes. |
| [Event Detection](applications/event-detection/) | Event Detection (App) | Intermediate | Monitors streaming data to detect and respond to events exceeding pre-set thresholds by emitting a Control Change or Recommendation output. This example also leverages Asset Parameters and App Configuration to make the application more dynamic. |
| [Multi-Objective Optimization ML](applications/multi-objective-optimization-ml/) | Machine Learning (App) | Intermediate | Implements a multi-objective optimization problem using machine learning techniques. |

# Contributing
1. Fork the project.
2. Create your feature branch (git checkout -b feature/YourFeature).
3. Commit your changes (git commit -m 'Add some feature').
4. Push to the branch (git push origin feature/YourFeature).
5. Open a pull request.