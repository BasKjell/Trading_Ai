"""Agent roles for the development orchestrator (T-025).

A role binds a provider account to a model, a model family and an effort level:

- ``codex`` / ``claude``: subscription CLIs in isolated containers; the model comes from the provider section.
- ``gateway``: any model behind agentgateway's OpenAI-compatible API. ``model`` is the gateway alias (for
  example ``local-reviewer``), never a concrete upstream model, so models are swapped in
  ``config/agentgateway.yaml`` without touching the orchestrator.

Independence rule: a reviewer must belong to a different model family than the author (AGENTS.md §2a).
"""

from __future__ import annotations

import re
from typing import Any

from coding_runner import RunnerError

PROVIDERS = ("codex", "claude", "gateway")
EFFORTS = ("low", "medium", "high")
SAFE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
RESTRICTED = {"none", "host", "bridge", "default"}
# Output budget per effort level for gateway roles (tokens). Codex/Claude receive the effort as CLI flag.
# Gateway roles with ``reasoning: true`` additionally send the effort as ``reasoning_effort``; models without
# a thinking mode (e.g. qwen3-coder, gemma3) reject that parameter, so it is off by default.
MAX_TOKENS = {"low": 2048, "medium": 4096, "high": 8192}


def validate_roles(config: dict[str, Any]) -> None:
    """Fail closed on any unknown provider, effort, alias, network or missing independent reviewer."""
    roles = config.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise RunnerError("configuration needs at least one role")
    for name, spec in roles.items():
        if not isinstance(name, str) or not SAFE.fullmatch(name) or not isinstance(spec, dict):
            raise RunnerError("invalid role name")
        if set(spec) - {"provider", "model", "family", "effort", "reasoning"}:
            raise RunnerError(f"role {name}: unknown settings")
        if spec.get("provider") not in PROVIDERS or spec.get("effort") not in EFFORTS:
            raise RunnerError(f"role {name}: provider must be codex, claude or gateway; effort low, medium or high")
        if not isinstance(spec.get("family"), str) or not SAFE.fullmatch(spec["family"]):
            raise RunnerError(f"role {name}: model family required")
        if spec["provider"] == "gateway":
            if not isinstance(spec.get("model"), str) or not SAFE.fullmatch(spec["model"]):
                raise RunnerError(f"role {name}: gateway roles need a model alias")
            if not isinstance(spec.get("reasoning", False), bool):
                raise RunnerError(f"role {name}: reasoning must be true or false")
        elif "model" in spec or "reasoning" in spec:
            raise RunnerError(f"role {name}: Codex/Claude models are set in the provider section")
    if any(spec["provider"] == "gateway" for spec in roles.values()):
        gateway = config.get("gateway")
        networks = {config[p]["network"] for p in ("codex", "claude") if isinstance(config.get(p), dict)}
        if (
            not isinstance(gateway, dict)
            or not isinstance(gateway.get("network"), str)
            or not SAFE.fullmatch(gateway["network"])
            or gateway["network"] in RESTRICTED | networks
            or not isinstance(gateway.get("url"), str)
            or not re.fullmatch(r"https?://[A-Za-z0-9_.-]+:[0-9]{1,5}", gateway["url"])
        ):
            raise RunnerError("gateway roles need their own internal Docker network and a gateway URL")
    reviewers = config.get("reviewers")
    if not isinstance(reviewers, list) or not reviewers or not all(r in roles for r in reviewers):
        raise RunnerError("reviewers must list configured roles")
    if config.get("author") not in roles:
        raise RunnerError("default author must be a configured role")
    reviewer_for(config, config["author"])


def spec(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Resolved role: provider, model (CLI model or gateway alias), family, effort."""
    try:
        role = config["roles"][name]
    except KeyError:
        raise RunnerError(f"unknown role {name}") from None
    model = role["model"] if role["provider"] == "gateway" else config[role["provider"]]["model"]
    return {**role, "name": name, "model": model, "reasoning": bool(role.get("reasoning", False))}


def reviewer_for(config: dict[str, Any], author: str) -> str:
    """First configured reviewer role from a different model family than the author."""
    family = spec(config, author)["family"].casefold()
    for name in config["reviewers"]:
        if name != author and spec(config, name)["family"].casefold() != family:
            return name
    raise RunnerError(f"no independent reviewer (different model family) for author role {author}")


ROLE_LINE = re.compile(r"^(?:Agent:\s*)?([A-Za-z0-9][A-Za-z0-9_.:-]*) / [^\n/]+ / (?:low|medium|high)\b")


def role_of(body: str) -> str | None:
    """Role from the first line of a PR body ("Agent: role / model / effort") or review ("role / model / effort: ")."""
    match = ROLE_LINE.match(body or "")
    return match.group(1) if match else None


def label(config: dict[str, Any], name: str) -> str:
    """Agent line for commits, PRs and reviews: role / model / effort."""
    role = spec(config, name)
    return f"{name} / {role['model']} / {role['effort']}"
