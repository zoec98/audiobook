"""Command-line configuration for the audiobook application."""

import argparse
import os
import re
import sys
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path

from audiobook._env import load_environment
from audiobook.contracts import (
    SUPPORTED_SPEECH_MODELS,
    AudiobookError,
    InputError,
    RunSettings,
)

_CHAPTER_NAME = re.compile(r"([0-9]+)-[a-zA-Z-]+\.md")


def _decimal(value: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not result.is_finite() or result <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return result


class _DefaultsAndExamplesHelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """Show option defaults while preserving the example command layout."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audiobook",
        description=(
            "Turn numbered Markdown chapters into an audiobook. "
            "Use --dry-run to prepare annotations and estimates without speech."
        ),
        formatter_class=_DefaultsAndExamplesHelpFormatter,
        epilog=(
            "Examples:\n\n"
            "  uv run audiobook --input-dir ./chapters --dry-run\n\n"
            "  uv run audiobook --input-dir ./chapters --sample\n\n"
            "  uv run audiobook --input-dir ./chapters --model eleven_v4 --language en\n\n"
            "  uv run audiobook --input-dir ./chapters --no-cache"
        ),
    )
    parser.add_argument(
        "--input-dir",
        "-i",
        type=Path,
        default=Path.cwd(),
        help="directory of immediate numbered Markdown chapter files",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path.cwd() / "audiobook",
        help="directory for completed chapter MP3s",
    )
    parser.add_argument(
        "--language",
        "-l",
        help="supported language code; detected from prose by default",
    )
    parser.add_argument(
        "--voice",
        "-v",
        default="Dahlia",
        help="exact account voice name or ID",
    )
    parser.add_argument(
        "--model",
        "-m",
        choices=SUPPORTED_SPEECH_MODELS,
        default="eleven_v4_turbo",
        help="ElevenLabs speech model",
    )
    parser.add_argument(
        "--speed",
        "-s",
        type=_decimal,
        default=Decimal(1),
        help="pitch-preserving playback tempo; must be finite and positive",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="continue after reporting source-validation errors",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="save annotations and plans without speech synthesis; Luna usage may apply",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="synthesize only the first chunk of the first chapter",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="refresh cached work and regenerate completed chapter outputs",
    )
    return parser


def parse_settings(argv: Sequence[str] | None = None) -> RunSettings:
    """Load environment files and parse the approved public command options."""
    args = _build_parser().parse_args(argv)
    load_environment()
    return RunSettings(
        input_dir=args.input_dir.expanduser().resolve(),
        output_dir=args.output_dir.expanduser().resolve(),
        language=args.language,
        voice=args.voice,
        model=args.model,
        speed=args.speed,
        force=args.force,
        dry_run=args.dry_run,
        sample=args.sample,
        no_cache=args.no_cache,
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        elevenlabs_api_key=os.environ.get("ELEVENLABS_API_KEY"),
    )


def discover_chapters(input_dir: Path) -> tuple[Path, ...]:
    """Return immediate matching chapters in numeric and filename order."""
    directory = input_dir.expanduser().resolve()
    if not directory.exists():
        raise InputError(f"Input directory does not exist: {directory}")
    if not directory.is_dir():
        raise InputError(f"Input path is not a directory: {directory}")

    matches: list[tuple[int, str, Path]] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        match = _CHAPTER_NAME.fullmatch(path.name)
        if match:
            matches.append((int(match.group(1)), path.name, path))
    matches.sort(key=lambda item: (item[0], item[1]))
    chapters = tuple(item[2] for item in matches)
    if not chapters:
        raise InputError(
            "No numbered Markdown chapters found in "
            f"{directory}; expected names like 1-chapter-name.md"
        )
    return chapters


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command and run the audiobook workflow."""
    try:
        settings = parse_settings(argv)
        from audiobook.application import run

        return run(settings)
    except AudiobookError as error:
        print(f"audiobook: error: {error}", file=sys.stderr)
        return 1
