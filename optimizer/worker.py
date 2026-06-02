import multiprocessing as mp
import traceback
from dataclasses import asdict

from trading.optimizer import OptimizerRequest, run_optimize


def _entrypoint(req_dict: dict, calibration: dict | None, result_queue: mp.Queue) -> None:
    try:
        req = OptimizerRequest(**req_dict)
        result = run_optimize(req, calibration)
        result_queue.put(("ok", asdict(result)))
    except Exception:
        result_queue.put(("error", traceback.format_exc()))
