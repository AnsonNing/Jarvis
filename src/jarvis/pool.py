"""Frozen compatible histories with family-disjoint policy cohorts."""

from typing import Literal

from pydantic import Field, model_validator

from jarvis.contracts import Budget, IntegrityError, Record
from jarvis.discovery import Collector, compatibility, verify_tree
from jarvis.identity import digest
from jarvis.storage import Store
from jarvis.suite import DEV, FRESH, HELDOUT, Fixture


class DreamProtocol(Record):
    kind: Literal["DreamProtocol"] = "DreamProtocol"
    development: tuple[Fixture, ...] = tuple(
        Fixture(family=f, variant=v) for f in DEV for v in range(2)
    )
    heldout: tuple[Fixture, ...] = tuple(
        Fixture(family=f, variant=v) for f in HELDOUT for v in range(2)
    )
    fresh: tuple[Fixture, ...] = tuple(
        Fixture(family=f, variant=v) for f in FRESH for v in range(2)
    )
    budget: Budget = Budget(
        max_actions=3, max_tool_calls=3, max_compute_units=3, max_wall_seconds=300
    )
    minimum_trees: int = Field(default=10, ge=10, le=100, strict=True)
    minimum_families: int = Field(default=3, ge=3, le=20, strict=True)
    target: float = Field(default=0.9, ge=0.9, le=0.9)
    memory_arm: Literal["empty-in-both-arms"] = "empty-in-both-arms"
    selection: Literal["target-attainment_then-tool-cost_then-complexity"] = (
        "target-attainment_then-tool-cost_then-complexity"
    )

    @model_validator(mode="after")
    def split_groups(self) -> "DreamProtocol":
        cohorts = [self.development, self.heldout, self.fresh]
        families = [{f.family for f in cohort} for cohort in cohorts]
        if any(not c or len({f.identity for f in c}) != len(c) for c in cohorts):
            raise ValueError("empty cohort or duplicate fixture")
        if any(families[i] & families[j] for i in range(3) for j in range(i)):
            raise ValueError("correlated family overlap across policy cohorts")
        if (
            len(self.development) + len(self.heldout) < self.minimum_trees
            or len(families[0] | families[1]) < self.minimum_families
        ):
            raise ValueError("insufficient predeclared history target")
        return self


class PoolMember(Record):
    fixture: Fixture
    cohort: Literal["development", "heldout"]
    tree_ref: str


class HistoryPool(Record):
    kind: Literal["HistoryPool"] = "HistoryPool"
    protocol: DreamProtocol
    compatibility: str
    members: tuple[PoolMember, ...]
    collection_id: str


def verify_pool(store: Store, ref: str) -> HistoryPool:
    pool = HistoryPool.model_validate(store.artifacts.get(ref))
    sealed = [e for e in store.events(pool.collection_id) if e["event_type"] == "POOL_SEALED"]
    if len(sealed) != 1 or sealed[0]["payload"]["pool_ref"] != ref:
        raise IntegrityError("pool is not the canonical sealed collection")
    expected = {
        (cohort, f.identity)
        for cohort in ("development", "heldout")
        for f in getattr(pool.protocol, cohort)
    }
    actual = {(m.cohort, m.fixture.identity) for m in pool.members}
    if (
        expected != actual
        or len(actual) != len(pool.members)
        or pool.compatibility != compatibility(pool.protocol.budget)
    ):
        raise IntegrityError("incomplete or incompatible frozen pool")
    for member in pool.members:
        tree = verify_tree(store, member.tree_ref)
        if tree.fixture != member.fixture or tree.compatibility != pool.compatibility:
            raise IntegrityError("pool tree identity mismatch")
    return pool


class PoolBuilder:
    def __init__(self, store: Store):
        self.store = store

    def create(self, protocol: DreamProtocol | None = None, *, omit: tuple[str, ...] = ()) -> str:
        protocol = protocol or DreamProtocol()
        return self.store.create(
            {
                "kind": "HistoryPoolCollection",
                "protocol": protocol.model_dump(mode="json"),
                "protocol_id": digest(protocol),
                "omit": omit,
            }
        )

    def run(self, collection: str) -> str:
        with self.store.campaign_lock(collection):
            manifest = self.store.manifest(collection)
            protocol = DreamProtocol.model_validate(manifest["protocol"])
            sealed = [e for e in self.store.events(collection) if e["event_type"] == "POOL_SEALED"]
            if sealed:
                ref = str(sealed[0]["payload"]["pool_ref"])
                verify_pool(self.store, ref)
                return ref
            members = []
            for cohort in ("development", "heldout"):
                for fixture in getattr(protocol, cohort):
                    mapped = [
                        e["payload"]
                        for e in self.store.events(collection)
                        if e["event_type"] == "TREE_REGISTERED"
                        and e["payload"]["fixture_id"] == fixture.identity
                    ]
                    if mapped:
                        child = mapped[0]["collection_id"]
                    else:
                        child = Collector(self.store).create(
                            fixture, budget=protocol.budget, omit=tuple(manifest.get("omit", ()))
                        )
                        self.store.append(
                            collection,
                            "TREE_REGISTERED",
                            {
                                "fixture_id": fixture.identity,
                                "collection_id": child,
                                "cohort": cohort,
                            },
                        )
                    tree_ref = Collector(self.store).run(child)
                    members.append(PoolMember(fixture=fixture, cohort=cohort, tree_ref=tree_ref))
            pool = HistoryPool(
                protocol=protocol,
                compatibility=compatibility(protocol.budget),
                members=tuple(members),
                collection_id=collection,
            )
            ref = self.store.artifacts.put(pool)
            self.store.append(collection, "POOL_SEALED", {"pool_ref": ref})
            verify_pool(self.store, ref)
            self.store.set_status(collection, "CLOSED")
            return ref
