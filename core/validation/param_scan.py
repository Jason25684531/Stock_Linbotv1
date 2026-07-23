from __future__ import annotations

import itertools


def parameter_scan(parameters: dict[str, list], evaluate):
    keys = list(parameters)
    return [{**dict(zip(keys, values)), **evaluate(**dict(zip(keys, values)))} for values in itertools.product(*(parameters[key] for key in keys))]
