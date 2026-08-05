"""Task generation and dataset I/O."""

from __future__ import annotations

from .generate import FAMILIES, PARTITIONS, GeneratorConfig, generate_partition, generate_suite
from .task import Oracle, Task, iter_partitions, read_jsonl, write_jsonl

__all__ = [
    "FAMILIES",
    "PARTITIONS",
    "GeneratorConfig",
    "Oracle",
    "Task",
    "generate_partition",
    "generate_suite",
    "iter_partitions",
    "read_jsonl",
    "write_jsonl",
]
