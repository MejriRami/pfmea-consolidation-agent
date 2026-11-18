"""
Layer 2 — AI agents (spec §2, §5). Every agent here is narrow-scope,
schema-constrained, and confidence-scored; low confidence routes to the
human queue (Layer 3) rather than guessing. Never used for AP — that stays
a Layer 1 table lookup (see rules.get_ap).

NOTE ON THE PACKAGE NAME: this is `pfmea_agents`, not `agents`. The spec's
Layer 2 architecture is built on the OpenAI Agents SDK, whose own top-level
import *is* `agents` (`from agents import Agent, Runner`). A same-named
local package at the repo root would shadow that SDK the instant repo root
is on sys.path, breaking every `from agents import ...` inside this
package's own modules. Keep the name distinct.
"""
