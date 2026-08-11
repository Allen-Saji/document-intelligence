# Retrieval pipeline

Phase 3 connects safe indexed chunks to a tenant-scoped evidence packet. It is
an application implementation and evaluation foundation, not a claim that the
product has a deployed query API or a release-sized retrieval dataset.

## Retrieval flow

1. A verified tenant scope supplies organization, workspace, and authorized
   corpus identifiers.
2. The service prepares a search question, retaining quoted and technical
   identifiers. A configured conversation rewriter can turn a follow-up into a
   standalone question.
3. Independent lexical and dense OpenSearch requests apply the same mandatory
   authorization filters.
4. Returned hits are validated again in the application. A foreign,
   unauthorized, or unsearchable hit fails closed.
5. Reciprocal-rank fusion combines lexical and dense rankings without treating
   their raw scores as comparable. An optional semantic scorer can rerank the
   fused candidates; exact-term coverage is the deterministic fallback.
6. Nearby authorized chunks can be expanded after ranking. Source diversity and
   a whole-chunk character budget build the final evidence packet.

Each run contains content-free candidate ranks plus the index and pipeline
versions, which lets an authorized operator explain or replay ranking decisions.

## Selection rule

The evaluation contracts report recall at five, reciprocal rank, latency, and
category-level recall. A more complex candidate must improve reciprocal rank,
not lower recall, and remain within the declared latency budget before it is
selected over a simpler baseline.

The current real parser-derived benchmark remains constrained: on its nine
searchable text cases, dense retrieval outperformed the prior hybrid query and
the CrossEncoder added roughly one second per query. Dense retrieval therefore
remains the selected small-corpus baseline. The 14-case retrieval fixture is a
versioned construction seed, not the planned 250-case release evaluation set.

## Verification

Tests cover tenant filters in both OpenSearch branches, adversarial foreign-hit
rejection, deterministic fusion, exact-identifier preservation, model scorer
validation, source diversity, adjacent-context de-duplication, whole-chunk
packing, result traces, and metric-selection rejection when quality does not
improve.
