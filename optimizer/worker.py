"""Worker process entry point for the optimizer.

The function must be importable at module level so that multiprocessing.spawn
can pickle and call it in a fresh subprocess.
"""


def worker_entry(job_id: str, request_dict: dict) -> None:
    """Runs in a fresh spawned subprocess.

    Imports are deferred to this function so the module itself can be imported
    cheaply by the parent process without pulling in heavy dependencies.
    """
    from datetime import UTC, datetime

    import core.database as db
    import core.logging as logging
    from trading.optimizer import OptimizerRequest, run_optimize

    def _now() -> datetime:
        return datetime.now(UTC)

    db.update_optimizer_job(job_id, status="running", started_at=_now())
    pair = request_dict.get("pair", "")
    mode = request_dict.get("mode", "")
    logging.info(f"Optimizer job {job_id} started ({pair} {mode})", to_telegram=True)

    try:
        req = OptimizerRequest(**request_dict)
        result = run_optimize(req)
        db.update_optimizer_job(
            job_id,
            status="completed",
            ended_at=_now(),
            result_json=result.to_json(),
        )
        logging.info(
            f"Optimizer job {job_id} completed: pair={result.pair} robust_pnl={result.best_robust_pnl:.2f}%",
            to_telegram=True,
        )
    except Exception as e:
        db.update_optimizer_job(job_id, status="failed", ended_at=_now(), error=str(e))
        logging.error(f"Optimizer job {job_id} failed: {e}", to_telegram=True)
