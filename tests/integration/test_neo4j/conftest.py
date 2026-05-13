import os

import pytest
from dotenv import load_dotenv

from due_diligence.intelligence.graph import DependencyGraph

load_dotenv()

neo4j_unavailable = not bool((os.environ.get("NEO4J_URI") or "").strip())


@pytest.fixture(scope="module")
def graph():
    g = DependencyGraph()
    yield g
    g.close()


@pytest.fixture
def clean_graph(graph):
    if graph._ensure_driver():
        with graph._driver.session() as session:
            session.run("MATCH (n:Clause) DETACH DELETE n")
    yield graph
