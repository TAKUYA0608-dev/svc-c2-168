"""AgentCore Platform v1.0"""

# `config/agent.yaml` declares `module: "src.graph"` + `class:
# "CatalogueChangeHandoverSummarizerAgent"`, so the registry resolves the class off this package.
# Without the re-export the manifest resolves to an AttributeError at load time — a failure that
# surfaces only when the registry loads the agent, never in the tests.
from src.graph.graph import (  # noqa: F401
    CatalogueChangeHandoverSummarizerAgent,
    Graph,
)
