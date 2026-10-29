"""Write one proposal review project. The caller supplies the text."""

from __future__ import annotations

import json
from pathlib import Path

from autopaperreview.model_exec import DEFAULT_MODEL_TIMEOUT_SECONDS


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def write_project(
    project: Path,
    *,
    project_id: str,
    proposal_text: str,
    pool: list[dict],
    cutoff_date: str,
    blocked_ids: list[str],
    prompts: Path,
    model: str,
    base_url: str,
    use_model: bool,
) -> Path:
    """Write ``review.toml`` plus the proposal and the prior-work pool.

    ``use_model`` selects the OpenAI-compatible adapter. Tests leave it false
    and stop after the pool and the prompt packet.
    """
    project.mkdir(parents=True, exist_ok=True)
    (project / "manuscript.txt").write_text(proposal_text.rstrip() + "\n", encoding="utf-8")
    (project / "pool.json").write_text(
        json.dumps({"papers": pool}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    blocked = ", ".join(_toml_string(item) for item in blocked_ids)
    model_stage = ""
    if use_model:
        model_stage = f"""
[[stages]]
id = "review"
type = "execute_review"
depends_on = ["prompt_packets"]
[stages.params]
adapter = "autopaperreview.model_exec:OpenAICompatibleAdapter"
base_url = {_toml_string(base_url)}
model = {_toml_string(model)}
api_key_env = "APR_MODEL_API_KEY"
allow_remote = true
requires_network = true
network_scope = "fulltext"
judgments = ["contributions", "dimensions", "fatal"]
timeout_seconds = {int(DEFAULT_MODEL_TIMEOUT_SECONDS)}
"""
    config = f"""schema_version = 1

[project]
id = {_toml_string(project_id)}
title = "Proposal novelty check"
source = "manuscript.txt"
run_root = ".runs"
network_policy = "{"allow" if use_model else "deny"}"

[[routes]]
id = "M7"
name = "Per-contribution evidence and retrieval audit"
kind = "prompt"
prompt = {_toml_string(str(prompts / "proposal_audit_v1.md"))}
prompt_version = "1.0"

[[stages]]
id = "search"
type = "related_work_search"
[stages.params]
mode = "pool"
pool_path = "pool.json"
cutoff_date = {_toml_string(cutoff_date)}
blocked_ids = [{blocked}]
requires_network = false

[[stages]]
id = "prompt_packets"
type = "prompt_packet"
depends_on = ["search"]
[stages.params]
route_ids = ["M7"]
include_source_text = true
include_literature = true
input_mode = "proposal"
{model_stage}"""
    path = project / "review.toml"
    path.write_text(config, encoding="utf-8")
    return path
