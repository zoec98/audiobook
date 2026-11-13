"""Locate and load the project's environment file."""

from pathlib import Path

from dotenv import load_dotenv


def load_environment() -> None:
    """Load local settings, falling back to the user's home settings file."""
    local_env = Path.cwd() / ".env"
    env_file = local_env if local_env.is_file() else Path.home() / ".audiobook.env"
    if env_file.is_file():
        load_dotenv(dotenv_path=env_file)
