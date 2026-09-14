from dataclasses import asdict

from trading.optimizer.search import OptimizerRequest, run_optimize


def _worker_func(req_dict: dict, calibration: dict | None) -> dict:
    return asdict(run_optimize(OptimizerRequest(**req_dict), calibration))
