from implementation import run

from jarvis.research_skills import diagnostic_case


def self_test():
    value = run(diagnostic_case().model_dump(mode="json"))
    assert value["status"] == "OBSERVED"
    assert value["evidence_level"] == "inventory"


def test_declared_protocol():
    self_test()
