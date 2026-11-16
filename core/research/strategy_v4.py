"""Pure ranking primitive; production adapters belong in scripts/."""
import math


def rank_scores(scores):
    return sorted((sid for sid, score in scores.items() if math.isfinite(score)), key=lambda sid: (-scores[sid], sid))
