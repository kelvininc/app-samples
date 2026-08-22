"""Databricks ingestion-job helpers, invoked once from the writer's setup().

Each helper is idempotent per target table: the job name and code file embed the Delta
table, so two exporters in the same workspace feeding different tables get separate jobs
instead of silently sharing one. An existing job whose task/trigger drifted from the
current configuration is updated in place (``jobs.reset``); an up-to-date one is reused.
The job is file-arrival triggered, so it ingests every new file the exporter uploads to
the volume's `data/` directory into the Delta table.
"""
import base64
import dataclasses
from typing import Optional

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import jobs, workspace
from kelvin.logs import logger

_JOBS_PATH = "/Kelvin/jobs"
_FILEFORMAT = {"parquet": "PARQUET", "csv": "CSV"}        # payload is scalar-JSON text in both
_CLOUDFILES_FORMAT = {"parquet": "parquet", "csv": "csv"}


def _volume_dir(volume: str) -> str:
    """`catalog.schema.volume` -> the volume's `data/` directory path."""
    catalog, schema, name = volume.split(".")
    return f"/Volumes/{catalog}/{schema}/{name}/data/"


def _signature(settings: Optional[jobs.JobSettings]) -> Optional[tuple]:
    """The parts of a job the exporter owns: task mode, code file, compute, and the trigger
    directory. Anything else (schedules, notifications, tags) is user-tunable in the
    Databricks UI: it never contributes to drift detection, and a drift reset preserves it
    (see ``_ensure_job``'s merge-then-reset)."""
    if settings is None or not settings.tasks:
        return None
    task = settings.tasks[0]
    trigger = settings.trigger
    return (
        task.sql_task.as_dict() if task.sql_task else None,
        task.spark_python_task.as_dict() if task.spark_python_task else None,
        task.existing_cluster_id,
        trigger.file_arrival.url if trigger and trigger.file_arrival else None,
    )


def _import_code(w: WorkspaceClient, path: str, code: str) -> None:
    """Import (overwrite) the job's code file so it always reflects the current
    configuration; a format or volume change after first deploy can't go stale."""
    logger.info("Importing job file to Databricks workspace", path=path)
    w.workspace.mkdirs(_JOBS_PATH)
    w.workspace.import_(
        path=path,
        overwrite=True,
        format=workspace.ImportFormat.AUTO,
        content=base64.b64encode(code.encode()).decode(),
    )


def _ensure_job(w: WorkspaceClient, settings: jobs.JobSettings) -> None:
    """Create the job if absent, reset it in place when its config drifted, reuse otherwise.

    A drift reset is merge-then-reset: it starts from the job's current settings and overlays
    only the exporter-owned fields (name/tasks/trigger), so user-added schedules, email
    notifications, and tags set in the Databricks UI survive the reset instead of being
    wiped by a bare ``reset`` that carried only the exporter's fields."""
    existing = next(iter(w.jobs.list(name=settings.name, expand_tasks=True)), None)
    if existing is None:
        job = w.jobs.create(name=settings.name, tasks=settings.tasks, trigger=settings.trigger)
        logger.info("Created ingestion job", job=settings.name, job_id=job.job_id)
    elif _signature(existing.settings) != _signature(settings):
        if existing.job_id is None:
            raise RuntimeError("listed ingestion job has no job_id")
        # Overlay only the exporter-owned fields onto the job's live settings; everything the
        # user tuned (schedule, notifications, tags, ...) rides along untouched.
        # The exporter's trigger always carries pause_status=UNPAUSED, but a user may have
        # paused the trigger in the Databricks UI. Carry the existing trigger's pause_status
        # over so a drift reset (e.g. a volume-path change) updates the trigger directory
        # without silently re-enabling a trigger the user paused.
        live = existing.settings or jobs.JobSettings()
        trigger = settings.trigger
        if trigger is not None and live.trigger is not None and live.trigger.pause_status is not None:
            trigger = dataclasses.replace(trigger, pause_status=live.trigger.pause_status)
        merged = dataclasses.replace(live, name=settings.name, tasks=settings.tasks, trigger=trigger)
        w.jobs.reset(existing.job_id, merged)   # in place: keeps job_id, run history, permissions
        logger.info("Ingestion job config drifted; updated in place", job=settings.name, job_id=existing.job_id)
    else:
        logger.info("Ingestion job already matches configuration; reusing", job=settings.name, job_id=existing.job_id)


def create_job_copy_into(w: WorkspaceClient, volume: str, table: str, warehouse_id: str, fmt: str = "parquet") -> None:
    """SQL-warehouse job: COPY INTO the Delta table from the volume, parsing payload to VARIANT.
    FILEFORMAT matches the exporter's upload format so the job reads what the writer wrote."""
    job_name = f"Kelvin - COPY INTO - {table}"
    volume_path = _volume_dir(volume)
    fileformat = _FILEFORMAT[fmt]
    # DuckDB writes CSV with a header row; tell COPY INTO so it maps columns by name and
    # doesn't ingest the header as data. (Parquet is self-describing; no option needed.)
    format_options = "\nFORMAT_OPTIONS ('header' = 'true')" if fmt == "csv" else ""
    code = (
        f"COPY INTO {table}\n"
        f"FROM (\n"
        f"    SELECT timestamp, asset, datastream, parse_json(payload) AS payload\n"
        f"    FROM '{volume_path}'\n"
        f")\n"
        f"FILEFORMAT = {fileformat}"
        f"{format_options}"
    )

    # Per-table code file: exporters feeding different tables can't clobber each other's SQL.
    code_path = f"{_JOBS_PATH}/copy_into_{table.replace('.', '_')}.sql"
    _import_code(w, code_path, code)

    _ensure_job(w, jobs.JobSettings(
        name=job_name,
        tasks=[
            jobs.Task(
                task_key="kelvin-copy-into",
                sql_task=jobs.SqlTask(
                    file=jobs.SqlTaskFile(path=code_path, source=jobs.Source.WORKSPACE),
                    warehouse_id=warehouse_id,
                ),
            ),
        ],
        trigger=jobs.TriggerSettings(
            pause_status=jobs.PauseStatus.UNPAUSED,
            file_arrival=jobs.FileArrivalTriggerConfiguration(url=volume_path),
        ),
    ))


def create_job_autoloader(w: WorkspaceClient, volume: str, table: str, cluster_id: str, fmt: str = "parquet") -> None:
    """Python (Auto Loader) job: stream new files from the volume into the Delta table.
    Runs on an existing all-purpose cluster (Auto Loader needs Spark, not a SQL warehouse).
    cloudFiles.format matches the exporter's upload format so the job reads what the writer wrote."""
    job_name = f"Kelvin - Auto Loader - {table}"
    catalog, schema, name = volume.split(".")
    volume_path = f"/Volumes/{catalog}/{schema}/{name}/data/"
    checkpoint_path = f"/Volumes/{catalog}/{schema}/{name}/checkpoints/"
    cloudfiles_format = _CLOUDFILES_FORMAT[fmt]
    # DuckDB writes CSV with a header row; tell the CSV reader so columns map by name and the
    # header isn't read as data. (Parquet is self-describing; the option is a harmless no-op.)
    header_option = '\n  .option("header", "true")' if fmt == "csv" else ""

    code = f"""from pyspark.sql.functions import expr
from pyspark.sql.types import StructType, StructField, StringType, TimestampNTZType

# payload is scalar-JSON text in the uploaded files; parse_json turns it back into a VARIANT.
schema = StructType(
  [
    StructField("timestamp", TimestampNTZType(), True),
    StructField("asset", StringType(), True),
    StructField("datastream", StringType(), True),
    StructField("payload", StringType(), True),
  ]
)

(spark.readStream
  .schema(schema)
  .format("cloudFiles")
  .option("cloudFiles.format", "{cloudfiles_format}"){header_option}
  .load('{volume_path}')
  .withColumn("payload", expr("parse_json(payload)"))
  .writeStream
  .option("checkpointLocation", '{checkpoint_path}')
  .trigger(availableNow=True)
  .toTable('{table}')
)
"""

    # Per-table code file: exporters feeding different tables can't clobber each other's code.
    code_path = f"{_JOBS_PATH}/autoloader_{table.replace('.', '_')}.py"
    _import_code(w, code_path, code)

    _ensure_job(w, jobs.JobSettings(
        name=job_name,
        tasks=[
            jobs.Task(
                task_key="kelvin-autoloader",
                spark_python_task=jobs.SparkPythonTask(python_file=code_path, source=jobs.Source.WORKSPACE),
                existing_cluster_id=cluster_id,
            ),
        ],
        trigger=jobs.TriggerSettings(
            pause_status=jobs.PauseStatus.UNPAUSED,
            file_arrival=jobs.FileArrivalTriggerConfiguration(url=volume_path),
        ),
    ))
