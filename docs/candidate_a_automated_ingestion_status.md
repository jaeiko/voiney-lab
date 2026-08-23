# Candidate A automated-ingestion status

Automated PDF ingestion for Candidate A is deferred for the current development
slice. This is a scheduling decision made after repeated evidence-grounding
friction; it is not a conclusion that automated ingestion is technically
impossible.

The last actual live analysis reached strict domain decoding, then failed
recursive evidence validation at `protocol.materials[0].evidence`. The cited
page was page 2 and the sanitized failure category was
`excerpt_not_present_after_normalization`. The invalid analysis was not
returned, persisted, or accepted.

A later offline prompt audit found missing exact-evidence instructions and
strengthened them. That strengthened prompt was not live-revalidated because
the next execution stopped on an older whitespace-sensitive test assertion.
The assertion has now been repaired and verified offline. No further Candidate
A live attempt is authorized in this development slice.

Future ingestion work may evaluate structural source-span identifiers. Current
cascade development instead uses a separately curated, development-only
fixture. The fixture is not production ingestion, database persistence, human
or production approval, or final protocol acceptance.
