# 0001: The reference implementation is an agent, not a service

**Status:** Accepted
**Date:** 2026-11-18
**Author:** Craig Hunt

## Context

The four backend standards repositories, go, java, rust and csharp-service,
implement one shared domain as a JSON API over Postgres: tasks, signups and
inventory. That shared domain is deliberate. It is what lets a reader open two
of them side by side and compare a rule rather than a problem, and it connects
them to the browser-test repositories, which drive the same domain through a
UI.

A Python repository joining that set would, by precedent, implement the same
API.

The reason this repository exists argues the other way. Every agent in the
suite is written in Python: RoleSearcher, SEOIntelligence, ResearchRabbit,
RegWatch, ExecutiveBriefing, AitoolguideMonitor and TrackMyResumeSEO. They are
scheduled processes that fetch, decide, report and persist, and they serve no
requests at all. The conventions that matter for them are about the exit code
a scheduler reads, secrets that never touch the repository, limits on what a
stranger's host can send, and layers that stay separate under pressure.

A tasks API demonstrates none of those.

## Decision

The worked reference is an agent: it asks a package index about the versions
this project pins, decides which have fallen behind, persists what it found
with a lifecycle, and reports a digest.

Parity with the other four is broken on purpose, and the README says so in its
opening rather than leaving a reader to notice.

The agent's subject is this repository's own `requirements.txt`, which means
the reference implementation audits the reference implementation. That keeps
the example honest: a standard it fails to meet shows up in its own report.

## Consequences

**Positive.** Every standard the README states has a worked demonstration
behind it. The exit-code policy, the secret handling, the transfer limits and
the four-layer split are all visible in code that runs rather than described
in prose. A reader building a new agent can copy the shape.

**Negative.** A reader comparing this against go-standards or rust-standards
finds a different domain, so a rule cannot be compared line for line across
all five. The README names the divergence and its reason, which is the most
that can be done about it.

**Neutral.** The agent needs an index to talk to. PyPI is public and needs no
credential, so the reference runs for anybody who clones it, with no account
and no key.

## Alternatives considered

**Implement the same tasks, signups and inventory API.** Keeps strict parity,
so the five repositories can be read as one set. Rejected because the
standards this repository exists to carry would then appear as rules with no
demonstration, and a rule nobody can see working is a rule that erodes.

**Carry both: the API for parity and an agent for the standards.** Everything
demonstrated, parity kept. Rejected on cost: two implementations, two suites
and two mutation targets, both of which have to stay conformant, in a
repository whose entire value is being correct.

**Point at an existing agent rather than carrying one.** Nothing to maintain
here. Rejected because every other standards repository carries its own
reference, and because the obvious candidate, SEOIntelligence, predates the
cloud move and carries a dual database backend no new agent wants, so the
reference would teach a shape nobody should copy.

## References

- `README.md`, which states the divergence in its opening
- `DEMO-21`, which raised the question and recorded the three open points
