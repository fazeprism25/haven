"""Shared local-config loader for Manager AI and the benchmark judge.

Real config files (e.g. ``config/manager_ai.env``) are git-ignored -- only
the ``*.env.example`` templates next to this file are committed. Loading
is best-effort and never overrides an already-set OS environment variable
(``override=False``), so anyone configuring these services purely via OS
env vars (e.g. in CI or a container) sees no change in behaviour: this
only fills in values that aren't already set.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_CONFIG_DIR = Path(__file__).resolve().parent


def load_env_file(filename: str) -> None:
    """Load ``config/<filename>`` into ``os.environ`` if that file exists."""
    path = _CONFIG_DIR / filename
    if path.exists():
        load_dotenv(path, override=False)


def load_manager_ai_env() -> None:
    """Load ``config/manager_ai.env`` (see manager_ai.*.env.example)."""
    load_env_file("manager_ai.env")


def load_benchmark_judge_env() -> None:
    """Load ``config/benchmark_judge.env`` (see benchmark_judge.qwen.env.example)."""
    load_env_file("benchmark_judge.env")
