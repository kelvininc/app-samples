import base64

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs, workspace


# SQL Job
def create_job_copy_into(w: WorkspaceClient, volume: str, table: str, warehouse_id: str) -> str:

    # Check if job already exists
    job_name = "Kelvin - COPY INTO"
    jobs_list = w.jobs.list(name=job_name)

    if len(list(jobs_list)) > 0:
        print(f"Skipping '{job_name}' job creation because it already exists")
        return

    # Create volume path
    catalog_name, schema_name, volume_name = volume.split(".")
    volume_path = f"/Volumes/{catalog_name}/{schema_name}/{volume_name}/data/"

    # Generate code
    code = f"""COPY INTO {table}
FROM '{volume_path}'
FILEFORMAT = PARQUET"""

    # Upload code to workspace
    jobs_path = "/Kelvin/jobs"
    code_path = f"{jobs_path}/copy_into.sql"

    print(f"Importing SQL file to Databricks Workspace path: '{code_path}'")

    w.workspace.mkdirs(jobs_path)
    w.workspace.import_(
        path=code_path,
        overwrite=True,
        format=workspace.ImportFormat.AUTO,
        content=base64.b64encode(code.encode()).decode(),
    )

    # Create sql task
    sql_task = jobs.SqlTask(
        file=jobs.SqlTaskFile(
            path=code_path,
            source=jobs.Source.WORKSPACE,
        ),
        warehouse_id=warehouse_id,
    )

    # Create job
    job = w.jobs.create(
        name=job_name,
        tasks=[
            jobs.Task(
                task_key="kelvin-copy-into",
                sql_task=sql_task,
            ),
        ],
        trigger=jobs.TriggerSettings(
            pause_status=jobs.PauseStatus.UNPAUSED,
            file_arrival=jobs.FileArrivalTriggerConfiguration(url=volume_path),
        ),
    )

    print(f"Successfully created '{job_name}' job with job_id: '{job.job_id}'")


# Python Job
def create_job_autoloader(w: WorkspaceClient, volume: str, table: str, cluster_id: str) -> str:
    # Check if job already exists
    job_name = "Kelvin - Auto Loader"
    jobs_list = w.jobs.list(name=job_name)

    if len(list(jobs_list)) > 0:
        print(f"Skipping '{job_name}' job creation because it already exists")
        return

    # Create volume path
    catalog_name, schema_name, volume_name = volume.split(".")
    volume_path = f"/Volumes/{catalog_name}/{schema_name}/{volume_name}/data/"

    # Generate code
    checkpoint_path = f"/Volumes/{catalog_name}/{schema_name}/{volume_name}/checkpoints/"

    code = f"""from pyspark.sql.types import StructType, StructField, StringType, TimestampNTZType, DoubleType

# Define Schema
schema = StructType(
  [
    StructField("timestamp", TimestampNTZType(), True),
    StructField("asset", StringType(), True),
    StructField("datastream", StringType(), True),
    StructField("payload", DoubleType(), True),
  ]
)

# Start the streaming query
(spark.readStream
  .schema(schema)
  .format("cloudFiles")
  .option("cloudFiles.format", "parquet")
  .load('{volume_path}')
  .writeStream
  .option("checkpointLocation", '{checkpoint_path}')
  .trigger(availableNow=True)
  .toTable('{table}')
)
"""

    # Upload code to workspace
    jobs_path = "/Kelvin/jobs"
    code_path = f"{jobs_path}/autoloader.py"

    print(f"Importing Python file to Databricks Workspace path: '{code_path}'")

    w.workspace.mkdirs(jobs_path)
    w.workspace.import_(
        path=code_path,
        overwrite=True,
        format=workspace.ImportFormat.AUTO,
        content=base64.b64encode(code.encode()).decode(),
    )

    # Create python task
    python_task = jobs.SparkPythonTask(
        python_file=code_path,
        source=jobs.Source.WORKSPACE,
    )

    # Create job
    job = w.jobs.create(
        name=job_name,
        tasks=[
            jobs.Task(
                task_key="kelvin-autoloader",
                spark_python_task=python_task,
                existing_cluster_id=cluster_id,
            ),
        ],
        trigger=jobs.TriggerSettings(
            pause_status=jobs.PauseStatus.UNPAUSED,
            file_arrival=jobs.FileArrivalTriggerConfiguration(url=volume_path),
        ),
    )

    print(f"Successfully created Autoloader Job '{job_name}' with job_id: '{job.job_id}'")
