"""Who the learner is, as far as the backend is concerned.

This id partitions every course, quiz attempt and progress row, so it has to be stable for the
same person across conversations. The AAD object id is; the channel-scoped `id` is not, and
using it would give the same person a fresh set of courses in every chat.
"""

from __future__ import annotations

from typing import Any


def learner_id(activity: Any) -> str:
    sender = getattr(activity, "from_property", None)
    return getattr(sender, "aad_object_id", None) or getattr(sender, "id", "") or "unknown"
