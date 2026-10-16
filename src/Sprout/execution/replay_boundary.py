"""Reserved replay markers are a fail-closed restriction, never an authorization."""


def isolated_replay_task(task) -> bool:
    metadata = task.metadata
    return "replay_plan" in metadata or "replay_source_task_id" in metadata
