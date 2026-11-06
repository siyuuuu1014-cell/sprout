"""Dedicated explicit commit workflow; mutation activities never auto-retry."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class SproutExperimentCommitWorkflow:
    @workflow.run
    async def run(self, request: dict) -> dict:
        try:
            return await workflow.execute_activity("run_experiment_commit", request,
                start_to_close_timeout=timedelta(seconds=120),
                retry_policy=RetryPolicy(maximum_attempts=1))
        except Exception:
            return {"applied": False, "execution_confirmed": False,
                    "reason": "commit_result_unconfirmed", "automatic_retry": False}
