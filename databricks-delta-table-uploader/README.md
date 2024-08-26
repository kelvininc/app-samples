# Databricks Delta Table Uploader
This application demonstrates the use of the Kelvin SDK for uploading streaming data to a Databricks Delta Table.

The streaming data is first batched, then exported as a Pandas Dataframe, and subsequently inserted into the table.

# Architecture Diagram
The following diagram illustrates the architecture of the solution:

![Architecture](./assets/architecture-diagram.jpg)

# Requirements
1. Python 3.9 or higher
2. Install Kelvin SDK: `pip3 install kelvin-sdk`
3. Install project dependencies: `pip3 install -r requirements.txt`
4. Docker (optional) for upload the application to a Kelvin Instance.

# Usage
1. Define Databricks environment variables:
```
export DATABRICKS_SERVER_HOSTNAME="..."
export DATABRICKS_HTTP_PATH="..."
export DATABRICKS_TABLE_NAME="..."
```

Using Databricks personal access token authentication:
```
export DATABRICKS_ACCESS_TOKEN="..."
```

Using OAuth machine-to-machine (M2M) authentication:
```
export DATABRICKS_CLIENT_ID="..."
export DATABRICKS_CLIENT_SECRET="..."
```

2. **Run** the application: `python3 main.py`
3. Open a new terminal and **Test** with synthetic data: `kelvin app test simulator`

# Secrets
To deploy this application in a Kelvin Cluster you need to create the following secrets:

```
kelvin secret create databricks-server-hostname --value <server-hostname>
kelvin secret create databricks-http-path --value <http-path>
kelvin secret create databricks-table-name --value <table-name>
```

Using Databricks personal access token authentication:
```
kelvin secret create databricks-access-token --value <token>
```

Using OAuth machine-to-machine (M2M) authentication:
```
kelvin secret create databricks-client-id --value <client-id>
kelvin secret create databricks-client-secret --value <client-secret>
```