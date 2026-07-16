# Relationships

Typed, directed edges between Concepts in the ontology graph
(`obsidian.ontology.enums.OntologyRelationshipType`). This is the enum the
`ConceptGraph`, `OntologyManager`/`OntologyValidator`, and
`ActivationSpreader` actually use — see
[`docs/architecture/ONTOLOGY_SPEC.md`](../../docs/architecture/ONTOLOGY_SPEC.md)
for the full ontology design.

- `is_a`
- `part_of`
- `uses`
- `depends_on`
- `created_by`
- `located_in`
- `related_to`
- `supports`

## Note

Earlier drafts of this doc listed a different set (`contradicts`,
`belongs_to`, `caused_by`, `implements`, `mentions`, `supersedes`) that
doesn't match the implemented enum above. That list was closer to the old
`obsidian.core.enums.RelationshipType`, a separate, unused enum attached to
a legacy `Memory` dataclass that wasn't part of the live pipeline and has
since been deleted (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md)). If a
relationship type below doesn't cover a case you need, add it to
`OntologyRelationshipType` directly rather than relying on the old list.
