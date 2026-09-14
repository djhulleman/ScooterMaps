import networkx as nx

from app.bicycle_routing import BicycleSettings, _build_graph
from app.bicycle_rules import evaluate_bicycle_access


def test_cycleway_and_residential_are_usable():
    assert evaluate_bicycle_access({"highway": "cycleway", "bicycle": "designated"}).allowed
    decision = evaluate_bicycle_access({"highway": "residential"})
    assert decision.allowed
    assert decision.place == "rustige rijbaan"


def test_motorway_and_explicit_bicycle_ban_are_rejected():
    assert not evaluate_bicycle_access({"highway": "motorway"}).allowed
    assert not evaluate_bicycle_access({"highway": "cycleway", "bicycle": "no"}).allowed


def test_steps_require_dismount():
    decision = evaluate_bicycle_access({"highway": "steps"})
    assert decision.allowed
    assert decision.dismount
    assert decision.speed_kmh < 5


def test_bicycle_oneway_exception_adds_reverse_edge():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=4.9, y=52.37)
    graph.add_node(2, x=4.91, y=52.37)
    graph.add_edge(1, 2, highway="residential", length=100, oneway="yes", **{"oneway:bicycle": "no"})
    legal = _build_graph(graph, BicycleSettings())
    assert legal.has_edge(1, 2)
    assert legal.has_edge(2, 1)


def test_hard_barrier_blocks_bicycle_unless_tagged_for_bikes():
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=4.9, y=52.37, barrier="stile")
    graph.add_node(2, x=4.91, y=52.37)
    graph.add_edge(1, 2, highway="cycleway", length=100)
    assert not _build_graph(graph, BicycleSettings()).has_edge(1, 2)

    allowed = nx.MultiDiGraph()
    allowed.add_node(1, x=4.9, y=52.37, barrier="stile", bicycle="yes")
    allowed.add_node(2, x=4.91, y=52.37)
    allowed.add_edge(1, 2, highway="cycleway", length=100)
    assert _build_graph(allowed, BicycleSettings()).has_edge(1, 2)
