"""Typed failures for the graph service. Never used as HTTP Cypher errors."""

from __future__ import annotations


class GraphError(ValueError):
    """A graph write or resolution was refused."""


class UnresolvedEntity(GraphError):
    pass


class AmbiguousEntity(GraphError):
    pass


class ProvenanceRequired(GraphError):
    pass


class RelationshipConflict(GraphError):
    pass


class GraphUnavailable(RuntimeError):
    """Neo4j is down or unconfigured. Postgres is unaffected."""


class UnsafeNeo4jTarget(RuntimeError):
    """A test tried to use the developer Neo4j instance."""


class EvidenceInsufficient(GraphError):
    """Candidate evidence does not explicitly state the relationship."""
