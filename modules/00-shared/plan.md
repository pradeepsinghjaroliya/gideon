# 00-shared

## Goal

Provide the common ground every other module depends on: the interface
definitions, the config loader, and logging setup. No audio/ML logic lives
here — this module is pure plumbing.

## Deliverables

- `src/shared/interfaces.py` — the `Protocol`/`ABC` classes listed in
  `../../ARCHITECTURE.md` under "Interfaces", turned into real Python code
  (use `typing.Protocol` so implementations don't need to subclass anything,
  just match the shape).
- `src/shared/config.py` — loads `config/config.yaml` into a typed structure
  (a few small `dataclasses`, one per top-level section: `AudioConfig`,
  `WakeWordConfig`, `SttConfig`, `LlmConfig`, `TtsConfig`, `TextInputConfig`,
  `OrchestratorConfig`). Each module imports only the dataclass for its own
  section.
- `src/shared/logging_setup.py` — one function `setup_logging(name: str)`
  that configures a consistent log format so every module's standalone test
  script and the orchestrator log the same way.
- `config/config.yaml` at the repo root — the actual file, seeded with the
  sketch from `ARCHITECTURE.md` (placeholder values are fine; other modules
  will fill in real values as they're built).
- Decide and document the Python packaging approach so
  `from shared.interfaces import STTEngine` works from any module's code
  without hacks (e.g. a single `pyproject.toml`/`setup.cfg` at repo root with
  an editable install, or a `PYTHONPATH` convention documented here and in
  `ARCHITECTURE.md`). Whatever you pick, every later module's plan.md assumes
  it just works — so get this right first.

## Standalone test plan

No hardware/audio involved, so this is pure unit-testable:

1. `setup_logging` produces a log line with expected format.
2. `config.py` loads the seeded `config.yaml` and produces the right
   dataclass values (including catching a missing/malformed file with a
   clear error, not a stack trace).
3. Each interface in `interfaces.py` can be satisfied by a trivial fake
   class in a test (e.g. a `FakeSTTEngine` that returns a fixed string) —
   this just proves the Protocol shapes are usable, not a real integration.

## Out of scope

- Anything audio, ML, or network related — that's every other module.
- Finalizing every config field — add fields as later modules discover they
  need them; keep this module's job to "load whatever's in the YAML into
  typed objects," not to know every field in advance.

## When done

Update `../../task.md`: check off `00-shared`, and note the packaging
approach you chose (editable install vs. PYTHONPATH) so later sessions know
how to import shared code.
