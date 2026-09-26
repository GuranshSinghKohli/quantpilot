from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.domain.enums import RelationshipType
from app.domain.schemas import CompanyIn, RelationshipIn, TechnologyIn
from app.repositories.relationships import derive_status

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "current,stances,expected",
    [
        ("asserted", [], "asserted"),
        ("asserted", ["context"], "asserted"),
        ("asserted", ["supports"], "supported"),
        ("supported", ["supports", "contradicts"], "disputed"),
        ("asserted", ["contradicts"], "disputed"),
        ("retracted", ["supports"], "retracted"),
    ],
)
def test_relationship_status_follows_evidence(current, stances, expected):
    assert derive_status(current, stances) == expected


def test_relationship_needs_two_entities_and_ordered_validity():
    a = uuid.uuid4()
    with pytest.raises(ValidationError):
        RelationshipIn(subject_entity_id=a, relationship_type=RelationshipType.USES, object_entity_id=a, observed_at=NOW)
    with pytest.raises(ValidationError):
        RelationshipIn(
            subject_entity_id=a,
            relationship_type=RelationshipType.USES,
            object_entity_id=uuid.uuid4(),
            observed_at=NOW,
            valid_from=NOW,
            valid_to=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )


def test_relationship_vocabulary_is_closed():
    with pytest.raises(ValidationError):
        RelationshipIn(subject_entity_id=uuid.uuid4(), relationship_type="LIKES", object_entity_id=uuid.uuid4(), observed_at=NOW)


def test_company_identifiers_are_normalized():
    company = CompanyIn(legal_name="  NVIDIA Corporation ", ticker="nvda", exchange="nasdaq", cik="1045810")
    assert (company.legal_name, company.ticker, company.exchange, company.cik) == (
        "NVIDIA Corporation",
        "NVDA",
        "NASDAQ",
        "0001045810",
    )
    with pytest.raises(ValidationError):
        CompanyIn(legal_name="X", cik="12ab")


def test_technology_slug_is_kebab_case():
    TechnologyIn(name="High-bandwidth memory", slug="hbm3e")
    with pytest.raises(ValidationError):
        TechnologyIn(name="HBM", slug="HBM 3E")
