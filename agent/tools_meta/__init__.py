"""Meta-tier tools (P14.6).

Always-on, format-agnostic tools that route to capability-tier
implementations or external infrastructure. The model gets these by
default in every turn. When it hits an unfamiliar file type or external
app, it calls ``show_relevant_tools(task_summary)`` to discover the
narrower capability-tier tools that are appropriate.

Subset shipped initially (v0):
  - show_relevant_tools     — router. Returns name+description list.

Planned (deferred — see docs/conversation.md P14.6):
  - read_anything           — dispatches by file extension
  - edit_anything           — dispatches by file extension + op
  - render_anything         — formats → image / PNG / SVG
  - verify_anything         — wraps agent.acceptance.oracles registry
  - shell                   — already covered by existing BashTool
"""
