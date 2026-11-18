"""
Layer 1 — deterministic rules engine (spec §2, Phase 1).

Never an LLM. Everything here is a pure function, table lookup, or
validator, fully unit-tested against the Phase 0 ground truth in
./ground_truth/.
"""
from .ap_lookup import APLookupError, get_ap, table_size
from .conservative_rating import ConservativeRating, Rating, select_conservative_rating
from .dedup_key import build_dedup_key
from .schema import ValidationResult, validate_normalized_line
from .vocabulary import THEME_VOCABULARY, UNCLASSIFIED, load_vocabulary

__all__ = [
    "get_ap",
    "table_size",
    "APLookupError",
    "build_dedup_key",
    "Rating",
    "ConservativeRating",
    "select_conservative_rating",
    "THEME_VOCABULARY",
    "UNCLASSIFIED",
    "load_vocabulary",
    "validate_normalized_line",
    "ValidationResult",
]
