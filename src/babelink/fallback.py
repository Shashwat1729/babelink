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

import time

from . import prompts

# -- segment batching -------------------------------------------------------
# Separator joining small back-to-back segments into one underlying translate()
# call. Prompt templates (mt_prompt / cat_prompt / translation_prompt) are
# untouched — only the SOURCE text is joined, so batching reuses the exact
# single-segment prompt shape. If the model does not preserve the marker,
# _split_batch_output returns None and the group falls back to per-item calls.
BATCH_SEPARATOR = "\n\n<<<BABELINK_SEG>>>\n\n"
# Joined-source char budget per underlying call. Keeps small fiction segments
# (10–120 chars each) in one round trip while avoiding huge one-shot prompts.
MAX_BATCH_CHARS = 900


def _group_sources(sources: list, max_chars: int = MAX_BATCH_CHARS) -> list[list]:
    """Chunk sources so the joined text (with separators) stays under budget."""
    groups: list[list] = []
    cur: list = []
    cur_len = 0
    sep = len(BATCH_SEPARATOR)
    for s in sources:
        text = s if isinstance(s, str) else str(s if isinstance(s, str) else s.get("source", ""))
        add = len(text) + (sep if cur else 0)
        if cur and cur_len + add > max_chars:
            groups.append(cur)
            cur, cur_len = [s], len(text)
        else:
            cur.append(s)
            cur_len += add
    if cur:
        groups.append(cur)
    return groups


def _split_batch_output(text: str, expected: int) -> list[str] | None:
    """Split a joined-call reply back into per-segment results, or None."""
    if expected <= 0:
        return []
    if expected == 1:
        return [text.strip()]
    parts = text.split(BATCH_SEPARATOR)
    if len(parts) != expected:
        return None
    return [p.strip() for p in parts]


# -- provider cache (avoids re-instantiating providers per call) ------------
_PROVIDER_CACHE: dict[tuple, object] = {}


def cached_make_provider(kind: str, model: str = "", base_url: str = ""):
    """Memoized service.make_provider: one provider object per (kind, model, url).

    Ollama providers are stateless across calls (keep_alive lives server-side),
    but re-instantiation churns httpx clients and hides the warmup state.
    The cache is process-local; clear_provider_cache() resets it for tests.
    The per-call `usage` counters are reset on reuse so run statistics stay
    per-run even though the object (and its warm model residency) persists.
    """
    key = (kind, model, base_url)
    if key not in _PROVIDER_CACHE:
        from . import service as _service

        _PROVIDER_CACHE[key] = _service.make_provider(kind, model, base_url)
    prov = _PROVIDER_CACHE[key]
    if hasattr(prov, "usage") and isinstance(prov.usage, dict):
        for k in prov.usage:
            if isinstance(prov.usage.get(k), int):
                prov.usage[k] = 0
    return prov


def clear_provider_cache() -> None:
    _PROVIDER_CACHE.clear()


def warmup(target, timeout: float = 120) -> dict | float | None:
    """Warm one provider or a whole ChainProvider so engine+helper stay resident.

    For a chain this warms every member in order and returns
    {member_name: seconds}. For a bare provider it returns seconds (or None
    when the provider has no warm path, e.g. MockProvider). Warming both
    engine and helper right before a run removes the cold model-load from
    the first segment and the interleave thrash cost entirely (keep_alive
    30m then holds both).
    """
    if isinstance(target, ChainProvider):
        out: dict = {}
        for m in target.members:
            out[getattr(m, "name", "?")] = warmup(m, timeout=timeout)
        return out
    fn = getattr(target, "warm_up", None)
    if callable(fn):
        t0 = time.time()
        try:
            fn(timeout=timeout)
        except TypeError:
            fn()  # some warm_up signatures take no args
        return time.time() - t0
    chat = getattr(target, "chat", None)
    if callable(chat):
        t0 = time.time()
        chat("", "hi", max_tokens=1)
        return time.time() - t0
    return None


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

    def warmup(self, timeout: float = 120) -> dict:
        """Warm every member (engine+helper) so alternation keeps them resident."""
        return warmup(self, timeout=timeout)

    def translate_batch(self, sources: list, **kw) -> list[str]:
        """Translate a list of segments, preserving order, with per-item failover.

        `sources` is a list of source strings (or dicts with a "source" key
        plus translate kwargs — the dict items carry their own glossary etc.,
        but batching only joins items that share identical kwargs; items with
        differing kwargs are translated per-item).

        Strategy per group (see _group_sources budget):
        1. If the primary exposes translate_batch, delegate to it.
        2. Else try one joined translate() call on the primary (single HTTP
           round trip for the whole group) and split the reply.
        3. On refusal, provider-error or split mismatch, fall back to
           per-item calls through _run — so one bad item never poisons the
           rest of the batch: good items stay on the primary, only the bad
           item fails over (or raises). translate() semantics are unchanged:
           refusals stay terminal per item, provider errors fail over per item.
        """
        if not sources:
            return []
        if len(sources) == 1:
            item = sources[0]
            if isinstance(item, dict):
                extra = {k: v for k, v in item.items() if k != "source"}
                return [self.translate(item.get("source", ""), **{**kw, **extra})]
            return [self.translate(item, **kw)]
        results: list[str] = []
        for group in _group_sources(list(sources)):
            results.extend(self._translate_group(group, **kw))
        return results

    def _translate_group(self, group: list, **kw) -> list[str]:
        member = self.primary
        supports_batch = bool(getattr(member, "supports_batch", True))
        # 1. provider-native batching (preferred where allowed)
        batch_fn = getattr(member, "translate_batch", None)
        if supports_batch and callable(batch_fn):
            try:
                res = batch_fn(group, **kw)
                if isinstance(res, list) and len(res) == len(group):
                    self.served_by = getattr(member, "name", "?")
                    self._fold_usage(member)
                    self.usage["calls"] += 1
                    return list(res)
            except (prompts.ProviderError, prompts.RefusalError):
                pass  # fall through to generic joined / per-item
        # 2. generic single-call join on the primary (1 HTTP round trip)
        if supports_batch and len(group) > 1:
            joined = BATCH_SEPARATOR.join(
                g if isinstance(g, str) else str(g.get("source", "") if isinstance(g, dict) else g)
                for g in group
            )
            try:
                out = member.translate(joined, **kw)
                self.served_by = getattr(member, "name", "?")
                self._fold_usage(member)
                self.usage["calls"] += 1
                parts = _split_batch_output(out if isinstance(out, str) else str(out), len(group))
                if parts is not None:
                    return parts
            except (prompts.ProviderError, prompts.RefusalError):
                # Joined attempt failed (primary down OR one poison item).
                # Do not fail the whole group: per-item below isolates it.
                pass
        # 3. per-item fallback: full failover chain, one item at a time
        results: list[str] = []
        for item in group:
            if isinstance(item, dict):
                src = item.get("source", "")
                _, res = self._run("translate", src, **{**kw, **{k: v for k, v in item.items() if k != "source"}})
            else:
                _, res = self._run("translate", item, **kw)
            results.append(res)
        return results

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

    `make` injects the provider factory (process-cached providers by default;
    tests pass a stub). The translator chain is [requested engine] plus, when
    the engine is translation-only, the general helper model as a degraded
    translator of last resort. General-LLM engines stand alone: failing over
    a general translator to another general translator buys nothing and hides
    outages, so the chain stays single-member and honest errors surface.
    Default factories are memoized (cached_make_provider) so repeat calls
    reuse the same provider objects instead of re-instantiating per call.
    """
    from . import service as _service

    if make is not None:
        factory = make
        use_cache = False
    else:
        factory = cached_make_provider
        use_cache = True
    if kind == "auto":  # direction-aware default engine (service.default_engine); explicit kinds bypass it
        kind, auto_model = _service.default_engine(source_lang, target_lang)
        model = model or auto_model
    engine = factory(kind, model, base_url)
    if use_cache:
        helper_key = ("__helper__", getattr(engine, "name", "?"), kind, base_url, helper_model)
        if helper_key not in _PROVIDER_CACHE:
            _PROVIDER_CACHE[helper_key] = _service.helper_for(engine, kind, base_url, helper_model)
        helper = _PROVIDER_CACHE[helper_key]
    else:
        helper = _service.helper_for(engine, kind, base_url, helper_model)
    translator = maybe_chain(engine, helper)
    return translator, helper
