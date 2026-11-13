from pathlib import Path

import pytest

from audiobook.cli import discover_chapters, main, parse_settings
from audiobook.contracts import InputError


def test_parse_settings_uses_approved_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    settings = parse_settings([])

    assert settings.input_dir == tmp_path
    assert settings.output_dir == tmp_path / "audiobook"
    assert settings.language is None
    assert settings.voice == "Dahlia"
    assert settings.model == "eleven_v4_turbo"
    assert str(settings.speed) == "1"
    assert settings.force is False
    assert settings.dry_run is False
    assert settings.sample is False
    assert settings.no_cache is False


def test_parse_settings_accepts_approved_option_overrides(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    settings = parse_settings(
        [
            "-i",
            "chapters",
            "-o",
            "rendered",
            "-l",
            "de",
            "-v",
            "voice-id",
            "-m",
            "eleven_v4",
            "-s",
            "1.25",
            "-f",
            "-n",
            "--sample",
            "--no-cache",
        ]
    )

    assert settings.input_dir == tmp_path / "chapters"
    assert settings.output_dir == tmp_path / "rendered"
    assert settings.language == "de"
    assert settings.voice == "voice-id"
    assert settings.model == "eleven_v4"
    assert str(settings.speed) == "1.25"
    assert settings.force is True
    assert settings.dry_run is True
    assert settings.sample is True
    assert settings.no_cache is True


def test_discover_chapters_sorts_numeric_prefixes_and_filename_ties(
    tmp_path: Path,
) -> None:
    for name in ["10-zed.md", "2-beta.md", "2-alpha.md"]:
        (tmp_path / name).touch()

    chapters = discover_chapters(tmp_path)

    assert [chapter.name for chapter in chapters] == [
        "2-alpha.md",
        "2-beta.md",
        "10-zed.md",
    ]


def test_discover_chapters_ignores_nested_and_nonmatching_files(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "1-hidden.md").touch()
    for name in ["README.md", "3-chapter.txt", "a-4-chapter.md"]:
        (tmp_path / name).touch()
    (tmp_path / "9-original-name.md").touch()

    chapters = discover_chapters(tmp_path)

    assert [chapter.name for chapter in chapters] == ["9-original-name.md"]


def test_discover_chapters_reports_missing_or_empty_input(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="Input directory does not exist"):
        discover_chapters(tmp_path / "missing")

    with pytest.raises(InputError, match="No numbered Markdown chapters"):
        discover_chapters(tmp_path)


def test_parse_settings_uses_local_dotenv_and_keeps_process_values(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=file-openai\nELEVENLABS_API_KEY=file-eleven\n"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "process-openai")

    settings = parse_settings([])

    assert settings.openai_api_key == "process-openai"
    assert settings.elevenlabs_api_key == "file-eleven"
    assert "process-openai" not in repr(settings)
    assert "file-eleven" not in repr(settings)


def test_parse_settings_falls_back_to_home_dotenv(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    (home / ".audiobook.env").write_text(
        "OPENAI_API_KEY=home-openai\nELEVENLABS_API_KEY=home-eleven\n"
    )

    settings = parse_settings([])

    assert settings.openai_api_key == "home-openai"
    assert settings.elevenlabs_api_key == "home-eleven"


def test_main_reports_missing_input_as_a_nonzero_command_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    exit_code = main(["--input-dir", str(tmp_path / "missing")])

    assert exit_code == 1
    assert "Input directory does not exist" in capsys.readouterr().err


def test_main_reports_missing_credentials_without_leaking_values(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-secret-value")
    (tmp_path / "1-chapter.md").touch()

    exit_code = main([])

    output = capsys.readouterr().err
    assert exit_code == 1
    assert "OPENAI_API_KEY" in output
    assert "test-secret-value" not in output


def test_main_requires_elevenlabs_for_audio_generation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    (tmp_path / "1-chapter.md").touch()

    exit_code = main([])

    assert exit_code == 1
    assert "ELEVENLABS_API_KEY is required" in capsys.readouterr().err


def test_main_reports_empty_narration_as_actionable_language_error(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-elevenlabs")
    (tmp_path / "1-chapter.md").touch()

    exit_code = main([])

    assert exit_code == 1
    error = capsys.readouterr().err
    assert "Could not detect a language" in error
    assert "--language" in error


def test_help_lists_all_approved_options(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    output = capsys.readouterr().out
    assert exit_info.value.code == 0
    for option in [
        "--input-dir",
        "--output-dir",
        "--language",
        "--voice",
        "--model",
        "--speed",
        "--force",
        "--dry-run",
        "--sample",
        "--no-cache",
    ]:
        assert option in output


def test_parse_settings_rejects_non_positive_speed(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    with pytest.raises(SystemExit) as exit_info:
        parse_settings(["--speed", "0"])

    assert exit_info.value.code == 2
    assert "must be a finite positive number" in capsys.readouterr().err


def test_parse_settings_rejects_unapproved_speech_models(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    with pytest.raises(SystemExit) as exit_info:
        parse_settings(["--model", "unapproved-model"])

    output = capsys.readouterr().err
    assert exit_info.value.code == 2
    assert "eleven_v4_turbo" in output
    assert "eleven_v4" in output
