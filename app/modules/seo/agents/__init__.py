"""SEO agents sub-module (Module 34 expansion).

Ports the AI-driven SEO automation layer originally developed in the
pharmacy sandbox into Hypershop's M34 SEO module. Despite the source-tree
origin, this code is pure e-commerce SEO tooling — no pharmacy taxonomy,
no Rx/medicine/doctor concepts. The agents target keyword tracking,
landing-page copy generation, schema planning, rank monitoring, and
technical audit, all using Hypershop e-commerce internal links.

Architecture:
  - ``models``    SQLAlchemy ORM models for 6 tables (keywords, tasks,
                  agent_runs, rank_snapshots, page_audits, approval_logs)
  - ``agents``    Pure-function agent classes + OpenAI client wrapper.
                  Each agent has a deterministic ``fallback`` so the
                  module functions WITHOUT an OPENAI_API_KEY — fallback
                  output is real, usable content (not a stub).
  - ``service``   Async service layer using UnitOfWork pattern.
  - ``schemas``   Pydantic wire schemas (StrictModel).

The agent surface is mounted at /api/v1/admin/seo/agents/* by the
parent module's ``seo_api_router`` aggregator.
"""
