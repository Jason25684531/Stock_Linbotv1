from __future__ import annotations


def split_is_oos(values, train_ratio: float = 0.7, *, dates=None, split_date=None, segments=None):
    """Split observations by ratio, a fixed date, or explicit IS/OOS segments."""
    if segments is not None:
        if dates is None:
            raise ValueError("dates are required for multi-segment splits")
        return [
            ([value for date, value in zip(dates, values) if start <= date <= end],
             [value for date, value in zip(dates, values) if oos_start <= date <= oos_end])
            for start, end, oos_start, oos_end in segments
        ]
    if split_date is not None:
        if dates is None:
            raise ValueError("dates are required for a fixed-date split")
        split = next((index for index, date in enumerate(dates) if date >= split_date), len(values))
    else:
        if not 0 < train_ratio < 1:
            raise ValueError("train_ratio must be between 0 and 1")
        split = int(len(values) * train_ratio)
    if split == 0 or split == len(values):
        raise ValueError("both IS and OOS require at least one observation")
    return values[:split], values[split:]
