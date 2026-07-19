# Multi-Agent Monitoring — Token Accounting

Glossary for how this project accounts for and reports token usage across the different models a run uses. (The orchestration roles — advisor / investigator / implementer — are defined in `SKILL.md`.)

## Language

**Provider**:
A model source whose token usage the monitor records — Claude, cursor, or grok. Each reports usage differently.
_Avoid_: vendor, backend

**Billed units**:
Token counts as actually billed — input re-counted on every API call (including cache-reads) and summed across all calls. Claude, cursor, and grok ≥ 0.2.103 report these; they are comparable across providers.
_Avoid_: "tokens used", "total tokens" (ambiguous about billed vs conversation)

**Conversation units**:
Legacy grok (≤ 0.2.93) only — a context-size snapshot (the `totalTokens` gauge) at the last turn, not a sum of billed calls, and not comparable to billed units. Appears only in old runs, and is always marked estimated.
_Avoid_: treating it as a session total

**Uncached**:
Input tokens processed once at full price — billed input minus cache-read (`tokens_in − cache-read`); the real input work. Uncached, cache-read, and output are three disjoint parts that sum to the billed total.
_Avoid_: "input" (overloaded — was used for both this and total input)

**Cache-read**:
Input tokens served from cache on re-read — a cheap subset of billed input, and the bulk of a multi-turn agent's tokens.
_Avoid_: "cached" when it could be confused with cache-writes (which fold into uncached)

**Output**:
Generated / completion tokens. No provider inflates these, so they are the fair measure of work done.
_Avoid_: "generated tokens" as a separate term
