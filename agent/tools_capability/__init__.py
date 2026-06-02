"""Capability-tier tools (P14.6).

These are NOT exposed by default. The model gets them via
``show_relevant_tools(task_summary)`` from the meta tier, which returns a
narrowed-down subset matching the current task. Each subpackage groups
tools by domain (obsidian, klayout, office, etc.). Within a domain the
tools are concrete, format-specific actions — the opposite of the meta
tier's generic dispatchers.
"""
