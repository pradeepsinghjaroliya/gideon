"""Agentic LLM layer: provider registry + tool registry backing a single
`pydantic_ai.Agent`. See `plan.md` and `../../docs/ARCHITECTURE.md`.

`llm_client.agentic_client.AgenticClient` is the only consumer -
`07-orchestrator` never imports from here directly.
"""
