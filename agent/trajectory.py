"""Trajectory saving utilities and static helpers.

_convert_to_trajectory_format stays as an AIAgent method (batch_runner.py
calls agent._convert_to_trajectory_format). Only the static helpers and
the file-write logic live here.
"""

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def trajectory_dir() -> str:
    """Where trajectories land. Set HERMES_TRAJECTORY_DIR to move it.

    The default filenames below are RELATIVE, so before this existed every trajectory was
    written into whatever directory the process happened to start in. For a CLI run that is
    merely untidy; for the gateway and the cron scheduler — which start wherever launchd put
    them — it scatters training data across the disk, or drops it when the cwd is not
    writable. Training data you cannot find is not training data.

    An absolute ``filename`` passed by a caller (the batch runner names its own output) is
    left exactly where the caller asked for it.
    """
    return os.environ.get(
        "HERMES_TRAJECTORY_DIR",
        os.path.join(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")),
                     "trajectories"),
    )


def convert_scratchpad_to_think(content: str) -> str:
    """Convert <REASONING_SCRATCHPAD> tags to <think> tags."""
    if not content or "<REASONING_SCRATCHPAD>" not in content:
        return content
    return content.replace("<REASONING_SCRATCHPAD>", "<think>").replace("</REASONING_SCRATCHPAD>", "</think>")


def has_incomplete_scratchpad(content: str) -> bool:
    """Check if content has an opening <REASONING_SCRATCHPAD> without a closing tag."""
    if not content:
        return False
    return "<REASONING_SCRATCHPAD>" in content and "</REASONING_SCRATCHPAD>" not in content


def save_trajectory(trajectory: List[Dict[str, Any]], model: str,
                    completed: bool, filename: str = None):
    """Append a trajectory entry to a JSONL file.

    Args:
        trajectory: The ShareGPT-format conversation list.
        model: Model name for metadata.
        completed: Whether the conversation completed successfully.
        filename: Override output filename. Defaults to trajectory_samples.jsonl
                  or failed_trajectories.jsonl based on ``completed``.
    """
    if filename is None:
        filename = os.path.join(
            trajectory_dir(),
            "trajectory_samples.jsonl" if completed else "failed_trajectories.jsonl")
        try:
            os.makedirs(os.path.dirname(filename), exist_ok=True)
        except Exception as e:
            logger.warning("Trajectory dir %s unusable: %s", os.path.dirname(filename), e)

    entry = {
        "conversations": trajectory,
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "completed": completed,
    }

    try:
        with open(filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("Trajectory saved to %s", filename)
    except Exception as e:
        logger.warning("Failed to save trajectory: %s", e)
