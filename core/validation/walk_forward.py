from __future__ import annotations


def walk_forward_folds(length: int, train_size: int, test_size: int, expanding: bool = False,
                       parameters=None, train_metrics=None, test_metrics=None):
    if min(length, train_size, test_size) <= 0:
        raise ValueError("lengths must be positive")
    folds, train_start, train_end = [], 0, train_size - 1
    while train_end + test_size < length:
        test_start, test_end = train_end + 1, train_end + test_size
        if train_end >= test_start:
            raise ValueError("train_end must be before test_start")
        folds.append({
            "fold": len(folds) + 1,
            "train_start": train_start, "train_end": train_end,
            "test_start": test_start, "test_end": test_end,
            "parameters": parameters or {},
            "train_metrics": train_metrics or {},
            "test_metrics": test_metrics or {},
        })
        train_start = 0 if expanding else train_start + test_size
        train_end += test_size
    return folds
