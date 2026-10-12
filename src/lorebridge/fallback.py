"""Ordered provider failover chains: model decisions with graceful degradation.

Policy (documented here so CLI, web UI and evals resolve identically):

- Translation engine chain (default): Hy-MT2 via Ollama first (best
  zh/ja->en fiction quality on CPU), then a general LLM translating through
  the generic prompt as a DEGRADED last resort. A degraded paragraph with a
  QA flag beats an aborted book: every segment still gets translated, reviewed
  and exported, and provenance records which engine served it.
- Helper chain (terms/refine/review/summaries): the configured general LLM.
  No fallback: when the helper is down, term suggestion returns what it has
  and translation proceeds with an empty glossary (see terminology).
- Refusals are TERMINAL (never failed over): a refusal is a content verdict,
  and retrying it on another model is a jailbreak retry by another name
  (spec section 63). Only transport/5xx/provider errors fail over.
- The chain is stateless: every call tries members in order. No health
  cooldowns, no sticky state to go stale and misroute a recovered provider.

Provenance: `served_by` names the member that served the last call, and
`usage["failovers"]` counts how many calls fell through to a later member.
The pipeline records the chain description in the model run; operators see
exactly how much of a book came from a fallback engine.
"""
from __future__ import annotations

from . import prompts


class ChainProvider:
    """Drop-in provider that tries members in order until one serves."""

    def __init__(self, members: list, name: str = "") -> None:
        if not members:
            raise ValueError("chain needs at least one member")
        self.members = list(members)
        self.name = name or ("chain[" + ",".join(getattr(m, "name", "?") for m in members) + "]")
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                      "refusals": 0, "failovers": 0}
        self.served_by: str = getattr(members[0], "name", "?")

    @property
    def translation_only(self) -> bool:
        """A chain is translation-only only when EVERY member is: one general
        LLM in the chain means non-translation tasks have somewhere to go."""
        return all(bool(getattr(m, "translation_only", False)) for m in self.members)

    @property
    def primary(self):
        """The engine: first member, the one that serves when healthy. Task
        routing (cross-checks, capability decisions) keys off the primary,
        never the degraded tail."""
        return self.members[0]

    def _run(self, task: str, *args, **kwargs):
        """Try each member in order. Returns (member, result). Provider errors
        fall through; refusals propagate immediately (terminal verdict)."""
        err: Exception | None = None
        for i, member in enumerate(self.members):
            try:
                result = getattr(member, task)(*args, **kwargs)
            except prompts.RefusalError:
                self.served_by = getattr(member, "name", "?")
                raise
            except prompts.ProviderError as exc:
                err = exc
                self.usage["failovers"] += 1
                continue
            self.served_by = getattr(member, "name", "?")
            self._fold_usage(member)
            self.usage["calls"] += 1
            return member, result
        raise prompts.ProviderError(f"{self.name}: all {len(self.members)} members failed: {err}") from err

    def _fold_usage(self, member) -> None:
        for k in ("prompt_tokens", "completion_tokens", "refusals"):
            try:
                self.usage[k] += getattr(member, "usage", {}).get(k, 0) or 0
            except (AttributeError, TypeError):
                pass

    # -- provider surface (same duck type as ChatProvider/MockProvider) --------

    def translate(self, source: str, **kw):
        _, result = self._run("translate", source, **kw)
        return result

    def refine(self, source: str, draft: str, style: str = "", **kw):
        _, result = self._run("refine", source, draft, style, **kw)
        return result

    def review(self, source: str, translation: str):
        _, result = self._run("review", source, translation)
        return result

    def suggest_terms(self, items, source_lang: str = "zh", target_lang: str = "en"):
        _, result = self._run("suggest_terms", items, source_lang, target_lang)
        return result

    def summarize(self, text: str, target_lang: str = "en"):
        _, result = self._run("summarize", text, target_lang)
        return result

    def describe(self) -> dict:
        """Provenance snapshot for model runs and job results."""
        return {"chain": self.name, "members": [getattr(m, "name", "?") for m in self.members],
                "served_by": self.served_by, "failovers": self.usage["failovers"],
                "calls": self.usage["calls"]}


def maybe_chain(engine, helper):
    """Wrap a translation-only engine with its general-LLM helper as failover.
    Extracted so the web job path and resolve_chains share one policy (was
    duplicated inline in web.py): general engines stand alone, same-named
    helpers add nothing, so both return the engine unchanged."""
    if getattr(engine, "translation_only", False) and getattr(helper, "name", "") != getattr(engine, "name", ""):
        return ChainProvider(
            [engine, helper],
            name=f"translate[{getattr(engine, 'name', '?')}+fallback:{getattr(helper, 'name', '?')}]")
    return engine


def resolve_chains(kind: str = "hymt", model: str = "", base_url: str = "",
                   helper_model: str = "", make=None, source_lang: str = "", target_lang: str = "en") -> tuple:
    """Build (translator_chain, helper) per the module policy.

    `make` injects the provider factory (service.make_provider by default;
    tests pass a stub). The translator chain is [requested engine] plus, when
    the engine is translation-only, the general helper model as a degraded
    translator of last resort. General-LLM engines stand alone: failing over
    a general translator to another general translator buys nothing and hides
    outages, so the chain stays single-member and honest errors surface.
    """
    from . import service as _service

    make = make or _service.make_provider
    if kind == "auto":  # direction-aware default engine (service.default_engine); explicit kinds bypass it
        kind, auto_model = _service.default_engine(source_lang, target_lang)
        model = model or auto_model
    engine = make(kind, model, base_url)
    helper = _service.helper_for(engine, kind, base_url, helper_model)
    translator = maybe_chain(engine, helper)
    return translator, helper
