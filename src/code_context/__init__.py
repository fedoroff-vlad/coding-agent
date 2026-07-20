"""code-context — the RAG-first MCP server for the coding-agent contour.

Gives an agent *hands in the codebase* (search_code / get_file / find_usages / get_deps /
find_convention / search_docs) over a derived pgvector index, so each LLM call sees only the
minimally sufficient slice instead of the whole repo. See ``plans/REFERENCE.md`` §4.
"""

__version__ = "0.0.1"
