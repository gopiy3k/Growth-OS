"""Prompt Registry access (Amendment 2).

Prompts are NEVER embedded in collector code. They live in an external,
versioned registry (docs/collector/prompts/registry.json). The collector
executes prompt *definitions* only: it loads prompt_id + version, renders
the template, and hashes the rendered text.

Prompt evolution = edit the registry (new version) + update the collection
reference. Collector code is untouched.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.identity import compute_prompt_hash

DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "collector"
    / "prompts"
    / "registry.json"
)


@dataclass(frozen=True)
class PromptDef:
    prompt_id: str
    version: str
    description: str
    template: str
    variables: tuple
    prompt_hash: str

    def render(self, **variables) -> str:
        """Render the template. Variables are substituted by .format(); a
        template with no placeholders returns verbatim."""
        try:
            return self.template.format(**variables)
        except (KeyError, IndexError):
            # Template uses no/literal braces — return as-is.
            return self.template

    def render_hash(self, **variables) -> str:
        return compute_prompt_hash(self.render(**variables))


class PromptRegistry:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else DEFAULT_REGISTRY_PATH
        self._data = self._load()

    def _load(self) -> dict:
        with self.path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def get(self, prompt_id: str, version: str) -> PromptDef:
        for p in self._data.get("prompts", []):
            if p["prompt_id"] == prompt_id and p["version"] == version:
                return PromptDef(
                    prompt_id=p["prompt_id"],
                    version=p["version"],
                    description=p.get("description", ""),
                    template=p["template"],
                    variables=tuple(p.get("variables", [])),
                    prompt_hash=compute_prompt_hash(p["template"]),
                )
        raise KeyError(
            f"prompt not found in registry: {prompt_id}@{version} "
            f"(registry={self.path})"
        )

    def registry_hash_for(self, prompt_id: str, version: str) -> str:
        """The prompt_hash the registry declares for this id@version.

        Used by the collector to assert the rendered prompt matches the
        registered definition (tamper/spoof check).
        """
        return self.get(prompt_id, version).prompt_hash
