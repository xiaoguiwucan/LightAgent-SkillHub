"""Expose the social-media-downloader regression suite to Hub CI discovery."""

import importlib.util
from pathlib import Path

EVALUATION = Path(__file__).resolve().parents[1] / "evaluations" / "social-media-downloader" / "test_media_task.py"
SPEC = importlib.util.spec_from_file_location("social_media_downloader_evaluation", EVALUATION)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)

MediaTaskTest = MODULE.MediaTaskTest
