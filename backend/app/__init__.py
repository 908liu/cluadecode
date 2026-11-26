"""Multi-user document Q&A knowledge base backend."""
from __future__ import annotations

import sys
import types


def _ensure_optional_grpc_exporter() -> None:
    """Allow Chroma to import when the native gRPC extension is unavailable.

    Chroma imports the OTLP gRPC span exporter at import time even though this
    project does not enable OpenTelemetry. Some Windows code-integrity policies
    block grpcio's unsigned ``cygrpc`` extension; in that case substitute an
    inert exporter module so the local (Rust-backed) Chroma client still works.
    """
    try:
        import opentelemetry.exporter.otlp.proto.grpc.trace_exporter  # noqa: F401
    except ImportError:
        module = types.ModuleType("opentelemetry.exporter.otlp.proto.grpc.trace_exporter")

        class OTLPSpanExporter:  # pragma: no cover - only reached when gRPC is blocked
            def __init__(self, *args: object, **kwargs: object) -> None:
                raise RuntimeError("OTLP gRPC exporter is unavailable on this system")

        module.OTLPSpanExporter = OTLPSpanExporter  # type: ignore[attr-defined]
        sys.modules[module.__name__] = module


_ensure_optional_grpc_exporter()