from dataclasses import asdict

from trading.optimizer.search import OptimizerRequest, run_auto_optimize, run_optimize


def _worker_func(req_dict: dict, calibration: dict | None) -> dict:
    req = OptimizerRequest(**req_dict)
    result = run_auto_optimize(req, calibration) if req.mode == "AUTO" else run_optimize(req, calibration)
    return asdict(result)
