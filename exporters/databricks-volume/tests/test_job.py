"""Unit tests for the ingestion-job helpers: per-table names, create/reuse/reset (no cloud)."""
import base64
from typing import Optional

from databricks.sdk.service import jobs

import job

VOLUME = "main.telemetry.landing"
TABLE = "main.telemetry.readings"
VOLUME2 = "main.telemetry.landing2"
VOLUME2_URL = "/Volumes/main/telemetry/landing2/data/"


class _FakeJobs:
    """Stands in for w.jobs: stores created jobs, records creates and resets."""

    def __init__(self) -> None:
        self._by_id: dict[int, jobs.BaseJob] = {}
        self.creates: list[str] = []
        self.resets: list[tuple[int, jobs.JobSettings]] = []
        self._next_id = 100

    def list(self, name: Optional[str] = None, expand_tasks: Optional[bool] = None):
        return iter(j for j in self._by_id.values() if name is None or (j.settings and j.settings.name == name))

    def create(self, name=None, tasks=None, trigger=None):
        job_id = self._next_id
        self._next_id += 1
        settings = jobs.JobSettings(name=name, tasks=tasks, trigger=trigger)
        self._by_id[job_id] = jobs.BaseJob(job_id=job_id, settings=settings)
        self.creates.append(name)
        return jobs.CreateResponse(job_id=job_id)

    def reset(self, job_id: int, new_settings: jobs.JobSettings) -> None:
        self._by_id[job_id] = jobs.BaseJob(job_id=job_id, settings=new_settings)
        self.resets.append((job_id, new_settings))


class _FakeWorkspace:
    """Stands in for w.workspace: records imported code files, decoded."""

    def __init__(self) -> None:
        self.imports: list[tuple[str, str]] = []

    def mkdirs(self, path: str) -> None: ...

    def import_(self, path, overwrite, format, content) -> None:
        assert overwrite is True                       # setup must be re-runnable
        self.imports.append((path, base64.b64decode(content).decode()))


class _FakeClient:
    def __init__(self) -> None:
        self.jobs = _FakeJobs()
        self.workspace = _FakeWorkspace()


def _drift_reset(w: _FakeClient) -> jobs.JobSettings:
    """Redeploy the COPY INTO job with the changed volume (drift -> reset), then unpack the
    single reset entry, assert it updated the original job in place, and return its settings."""
    job.create_job_copy_into(w, VOLUME2, TABLE, warehouse_id="wh-1", fmt="parquet")
    (job_id, merged), = w.jobs.resets
    assert job_id == 100                               # the original job, updated in place
    return merged


class TestCopyInto:
    """COPY INTO job: name/code per table, reuse when unchanged, reset on drift."""

    def test_job_name_and_code_path_derive_from_table(self) -> None:
        """The job name and SQL file embed the target table, so two exporters
        feeding different tables in one workspace can't collide."""
        w = _FakeClient()
        job.create_job_copy_into(w, VOLUME, TABLE, warehouse_id="wh-1", fmt="parquet")
        assert w.jobs.creates == [f"Kelvin - COPY INTO - {TABLE}"]
        (code_path, code), = w.workspace.imports
        assert code_path == "/Kelvin/jobs/copy_into_main_telemetry_readings.sql"
        assert f"COPY INTO {TABLE}" in code and "FILEFORMAT = PARQUET" in code

    def test_existing_matching_job_is_reused(self) -> None:
        """A second setup with the same config creates nothing and resets nothing."""
        w = _FakeClient()
        job.create_job_copy_into(w, VOLUME, TABLE, warehouse_id="wh-1", fmt="parquet")
        job.create_job_copy_into(w, VOLUME, TABLE, warehouse_id="wh-1", fmt="parquet")
        assert len(w.jobs.creates) == 1 and w.jobs.resets == []

    def test_drifted_config_resets_job_in_place(self) -> None:
        """A changed volume (new trigger directory) updates the existing job via
        jobs.reset instead of leaving a stale one or creating a duplicate."""
        w = _FakeClient()
        job.create_job_copy_into(w, VOLUME, TABLE, warehouse_id="wh-1", fmt="parquet")
        merged = _drift_reset(w)
        assert len(w.jobs.creates) == 1                # same name -> no second job
        assert merged.trigger.file_arrival.url == VOLUME2_URL

    def test_drift_reset_preserves_user_set_fields(self) -> None:
        """A schedule and tags a user added in the Databricks UI survive a drift reset:
        merge-then-reset overlays only the exporter's name/tasks/trigger onto the job's
        live settings, so user-tuned fields ride along untouched."""
        w = _FakeClient()
        job.create_job_copy_into(w, VOLUME, TABLE, warehouse_id="wh-1", fmt="parquet")
        # Simulate a user tuning the job in the Databricks UI after first deploy.
        live = w.jobs._by_id[100].settings
        assert live is not None
        live.tags = {"team": "reliability"}
        live.schedule = jobs.CronSchedule(quartz_cron_expression="0 0 * * * ?", timezone_id="UTC")
        merged = _drift_reset(w)
        assert merged.tags == {"team": "reliability"}                     # user field survived
        assert merged.schedule.quartz_cron_expression == "0 0 * * * ?"    # user field survived
        # exporter-owned field still updated to the new volume:
        assert merged.trigger.file_arrival.url == VOLUME2_URL

    def test_drift_reset_preserves_user_paused_trigger(self) -> None:
        """A trigger a user paused in the Databricks UI stays paused across a drift reset:
        the merge carries the existing trigger's pause_status over instead of forcing
        UNPAUSED, while the drifted field (the trigger directory) still updates."""
        w = _FakeClient()
        job.create_job_copy_into(w, VOLUME, TABLE, warehouse_id="wh-1", fmt="parquet")
        # Simulate a user pausing the file-arrival trigger in the Databricks UI.
        live = w.jobs._by_id[100].settings
        assert live is not None and live.trigger is not None
        live.trigger.pause_status = jobs.PauseStatus.PAUSED
        merged = _drift_reset(w)
        assert merged.trigger.pause_status == jobs.PauseStatus.PAUSED     # user pause survived
        # exporter-owned field still updated to the new volume:
        assert merged.trigger.file_arrival.url == VOLUME2_URL

    def test_format_change_rewrites_the_sql_file(self) -> None:
        """A format flip after first deploy overwrites the SQL so FILEFORMAT can't go stale."""
        w = _FakeClient()
        job.create_job_copy_into(w, VOLUME, TABLE, warehouse_id="wh-1", fmt="parquet")
        job.create_job_copy_into(w, VOLUME, TABLE, warehouse_id="wh-1", fmt="csv")
        assert "FILEFORMAT = CSV" in w.workspace.imports[-1][1]

    def test_two_tables_get_two_jobs(self) -> None:
        """Different target tables produce independent jobs and code files."""
        w = _FakeClient()
        job.create_job_copy_into(w, VOLUME, TABLE, warehouse_id="wh-1", fmt="parquet")
        job.create_job_copy_into(w, VOLUME, "main.telemetry.other", warehouse_id="wh-1", fmt="parquet")
        assert len(w.jobs.creates) == 2 and w.jobs.resets == []
        assert len({p for p, _ in w.workspace.imports}) == 2


class TestAutoloader:
    """Auto Loader job: same create/reuse/reset semantics on a Spark task."""

    def test_job_name_and_code_path_derive_from_table(self) -> None:
        w = _FakeClient()
        job.create_job_autoloader(w, VOLUME, TABLE, cluster_id="cl-1", fmt="csv")
        assert w.jobs.creates == [f"Kelvin - Auto Loader - {TABLE}"]
        (code_path, code), = w.workspace.imports
        assert code_path == "/Kelvin/jobs/autoloader_main_telemetry_readings.py"
        assert f".toTable('{TABLE}')" in code and '"cloudFiles.format", "csv"' in code

    def test_existing_matching_job_is_reused(self) -> None:
        w = _FakeClient()
        job.create_job_autoloader(w, VOLUME, TABLE, cluster_id="cl-1", fmt="parquet")
        job.create_job_autoloader(w, VOLUME, TABLE, cluster_id="cl-1", fmt="parquet")
        assert len(w.jobs.creates) == 1 and w.jobs.resets == []

    def test_cluster_change_resets_job_in_place(self) -> None:
        """A new cluster_id counts as drift and updates the job via jobs.reset."""
        w = _FakeClient()
        job.create_job_autoloader(w, VOLUME, TABLE, cluster_id="cl-1", fmt="parquet")
        job.create_job_autoloader(w, VOLUME, TABLE, cluster_id="cl-2", fmt="parquet")
        assert len(w.jobs.creates) == 1
        (job_id, new_settings), = w.jobs.resets
        assert new_settings.tasks[0].existing_cluster_id == "cl-2"
