"""Minimal Agent entry point for the Granola Skill benchmark.

The benchmark runner imports this file without starting a server.  It uses the
same Agent factory as ``co ai`` so ``/co-granola`` resolves through the default
Skill catalog; the benchmark itself supplies synthetic prompts and eval runs
stay non-live unless the caller explicitly opts in.
"""

from connectonion import host
from connectonion.cli.co_ai.agent import create_agent


host(lambda: create_agent(role=None))
