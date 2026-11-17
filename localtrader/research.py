from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TimeSplit:
    train: slice
    validation: slice
    purge: slice
    embargo: slice


def walk_forward_splits(
    length: int,
    train_size: int,
    validation_size: int,
    purge_size: int = 5,
    embargo_size: int = 5,
    step_size: int | None = None,
) -> list[TimeSplit]:
    """Chronological expanding-window splits with explicit purge and embargo gaps."""
    if min(length, train_size, validation_size) <= 0 or min(purge_size, embargo_size) < 0:
        raise ValueError("split sizes must be valid")
    step = step_size or validation_size
    if step <= 0:
        raise ValueError("step_size must be positive")
    splits: list[TimeSplit] = []
    train_end = train_size
    while train_end + purge_size + validation_size <= length:
        validation_start = train_end + purge_size
        validation_end = validation_start + validation_size
        embargo_end = min(length, validation_end + embargo_size)
        splits.append(TimeSplit(
            train=slice(0, train_end), purge=slice(train_end, validation_start),
            validation=slice(validation_start, validation_end),
            embargo=slice(validation_end, embargo_end),
        ))
        train_end += step
    return splits


def development_validation_holdout(length: int, holdout_fraction: float = 0.20) -> tuple[slice, slice]:
    if length < 10 or not 0.05 <= holdout_fraction <= 0.40:
        raise ValueError("invalid holdout request")
    boundary = int(length * (1 - holdout_fraction))
    return slice(0, boundary), slice(boundary, length)

