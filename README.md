# audiobook

Python project for turning text files into narrated audiobooks. The narration
workflow and Replicate model interface will be specified later.

## Setup

Install [uv](https://docs.astral.sh/uv/), then run `uv sync`. The project pins
Python 3.13 in `.python-version` and uses `uv` for dependencies and commands.

Set `REPLICATE_API_KEY` in a `.env` file in the working directory (copy
`.env.example` as a starting point). When that file is absent, the environment
loader tries `~/.audiobook.env` instead. Existing process environment values
take precedence over values in either file. Neither secrets file is tracked.

The internal `audiobook._env.load_environment()` helper implements this lookup.
The eventual application entry point should call it before reading configuration.

## Checks

```sh
uv run ruff format src tests
uv run ruff check src tests
uv run pytest
uv run ty check src tests
```

`tests/` is reserved for behavior tests once the public interface is agreed.
