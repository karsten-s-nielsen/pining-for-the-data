# 0010 — Faithful-Feed Mimicry

## Status

Accepted

## Date

2026-08-12

## Context

A commercial StatsBomb 360 delivery arrived as a club file drop. Three of its five
files were raw feed arrays; two were pandas column-orient dumps produced by
`statsbombpy`'s default `fmt="dataframe"` mode, which flattens the feed's nested
objects.

That forced a question with no precedent here: when the delivered copy and the
provider's own published format disagree, which one does this API serve?

It matters because this repo is a **mock provider API**. The downstream consumer
builds its parser against what we serve, and had not yet seen the real format. A
flattened artifact would have taught it a contract that breaks the day a
full-fidelity copy arrives — a Hyrum's Law trap of our own making.

## Decision

**A provider-native substructure served by this API represents the provider's
format, not the format of the pipeline that happened to deliver our copy.**

Three rules bound it.

1. **Scope.** This governs the *content* of provider-native structures. It does
   **not** govern the artifact envelope — how many entities an artifact holds and
   how it is scoped. That is ADR 0008's role vocabulary, which is match-scoped by
   construction. Without this boundary a future provider could cite whichever half
   suited it.

2. **Honesty.** Structure may be **reconstructed**; values may never be
   **invented**. A field the delivery dropped is emitted as `null`. A
   plausible-looking synthesised id is indistinguishable from a real one
   downstream, which makes it worse than an absence.

3. **The inference boundary.** Inference **from inside the delivery** is permitted;
   inference **from outside it** is not. The delivery is a single coherent export
   of one fixture, so a disagreement inside it is a contradiction and can be made
   to raise. An outside source carries no such guarantee — it may simply describe
   something else, and agreeing or disagreeing with it proves nothing either way.

This is not a licence to compose. Merging two delivered files is legitimate only
when the feed itself co-locates their contents — as StatsBomb's match object
natively contains `competition{}` and `season{}`. Merging files the feed keeps
apart would be synthesis, and is forbidden.

## Consequences

**Positive:** A consumer's parser is correct on day one and stays correct when a
fuller delivery arrives. Reconstruction rules are explicit and testable, and the
honesty rule makes gaps visible rather than plausible.

**Negative / accepted:** Onboarding a delivery costs a reconstruction step and a
set of fail-loud join/resolution rules rather than a byte-for-byte passthrough.
Reconstruction depends on the delivering tool's conventions (for StatsBomb, a
`statsbombpy` rendering), so a tooling change can break a join — mitigated by
failing loud rather than guessing, and by recording the tool version in the
operator's delivery note.

**Reversal cost:** Low for a future provider (convention, applied per provider at
ingest). Moderate for one already published — changing a served shape is a consumer
break.

## Alternatives Considered

- **Byte-for-byte passthrough of whatever arrives.** Rejected: publishes an
  artifact of the export tooling, and the resulting shape becomes a contract we
  would have to break later.
- **Serve the delivered shape and let each consumer normalise.** Rejected: pushes
  the same reconstruction onto every consumer, and each would do it differently.
- **Reconstruct fully, inferring missing ids from the provider's public catalogue.**
  Rejected: the public catalogue describes different seasons, so an inferred id
  could be wrong in a way nothing in the delivery can detect. This is the case
  rule 3 exists to forbid.

## See Also

- Spec: `docs/superpowers/specs/2026-08-12-statsbomb-commercial-360-owner-tier-design.md`
- ADR 0008 (role-aligned artifact keys — governs the envelope this ADR excludes)
- ADR 0009 (restricted tier under an existing public provider)
- Implementation: `src/formats/statsbomb.py`, `scripts/upload_statsbomb_club.py`
