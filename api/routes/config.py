from fastapi import APIRouter, HTTPException

import core.config_store as config_store
from api.schemas import PairConfig, PairConfigPatch
from core.config import PAIRS

router = APIRouter(tags=["config"])


def _to_model(pair: str, flat: dict) -> PairConfig:
    return PairConfig(pair=pair, **flat)


@router.get("/config", response_model=list[PairConfig])
def get_config() -> list[PairConfig]:
    return [_to_model(pair, flat) for pair, flat in config_store.get_all().items()]


@router.get("/config/{pair}", response_model=PairConfig)
def get_config_pair(pair: str) -> PairConfig:
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Unknown pair: {pair}")
    return _to_model(pair, config_store.get_pair(pair))


@router.patch("/config/{pair}", response_model=PairConfig)
def patch_config_pair(pair: str, patch: PairConfigPatch) -> PairConfig:
    if pair not in PAIRS:
        raise HTTPException(status_code=404, detail=f"Unknown pair: {pair}")
    fields = patch.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update")
    try:
        typed = config_store.apply_patch(pair, fields, updated_by="api")
    except config_store.ConfigValidationError as e:
        raise HTTPException(status_code=422, detail="; ".join(e.errors)) from e
    return _to_model(pair, typed)
