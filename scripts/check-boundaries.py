"""Verify trust boundaries in the resolved Compose config (reads `docker compose config --format json` on stdin).

Encodes architecture sections 12 and 24: which service may hold which secret, volume
and network. A failure here means a deployment change widened a boundary.
"""

import json
import sys

config = json.load(sys.stdin)
services: dict[str, dict] = config["services"]
networks: dict[str, dict] = config.get("networks", {})
errors: list[str] = []


def check(ok: bool, message: str) -> None:
    if not ok:
        errors.append(message)


def secrets_of(name: str) -> set[str]:
    return {s["source"] if isinstance(s, dict) else s for s in services[name].get("secrets", [])}


def nets_of(name: str) -> set[str]:
    return set(services[name].get("networks") or [])


def volumes_of(name: str) -> list[dict]:
    return services[name].get("volumes") or []


# Only the ingress service publishes ports, and only on loopback until reviewed TLS exists.
for name, service in services.items():
    for port in service.get("ports") or []:
        check(name == "web", f"{name} publishes a host port; only the ingress may")
        check(port.get("host_ip") == "127.0.0.1", f"{name} port is not bound to loopback")

# Every service: no privileged mode, host networking, docker socket, or added capabilities.
for name, service in services.items():
    check(not service.get("privileged"), f"{name} is privileged")
    check(service.get("network_mode") not in ("host",), f"{name} uses host networking")
    check(not service.get("cap_add"), f"{name} adds Linux capabilities")
    for volume in volumes_of(name):
        check("docker.sock" not in str(volume.get("source", "")), f"{name} mounts the Docker socket")
    if name not in ("db",):
        check(service.get("read_only") is True, f"{name} root filesystem is writable")
        check(service.get("cap_drop") == ["ALL"], f"{name} does not drop all capabilities")

# Only internal networks other than the ingress one: no direct egress for internal services.
for name, network in networks.items():
    if name != "ingress":
        check(network.get("internal") is True, f"network {name} is not internal")

# Model-facing orchestrator: its own key only, no storage, no DB network.
check(secrets_of("orchestrator") == {"orchestrator_key"}, "orchestrator secrets are not exactly its key")
check(not volumes_of("orchestrator"), "orchestrator mounts a volume")
check(nets_of("orchestrator") <= {"ai_control", "inference"}, "orchestrator reaches a non-AI network")
check("data" not in nets_of("orchestrator"), "orchestrator can reach the database network")

# Parser handles hostile bytes: no secrets, no storage, one private network.
check(not secrets_of("parser"), "parser holds secrets")
check(not volumes_of("parser"), "parser mounts a volume")
check(nets_of("parser") == {"parser"}, "parser network is not exactly the parser network")

# Indexer: only the ingestion job key; no storage; never the database network.
check(secrets_of("indexer") == {"ingestion_key"}, "indexer secrets are not exactly its key")
check(not volumes_of("indexer"), "indexer mounts a volume")
check("data" not in nets_of("indexer"), "indexer can reach the database network")

# Database stays on its private network; only the backend (and migrations) join it.
check(nets_of("db") == {"data"}, "db is on a network other than data")
check(not services["db"].get("ports"), "db publishes a port")
for name in services:
    if "data" in nets_of(name):
        check(name in ("backend", "db", "migrate"), f"{name} can reach the database network")

# Only the backend writes managed documents.
for name in services:
    for volume in volumes_of(name):
        if volume.get("source") == "document_data":
            check(name == "backend", f"{name} mounts the document volume")
            check(not volume.get("read_only"), "backend document volume is read-only (unexpected)")

# Web ingress holds no secrets and no storage.
check(not secrets_of("web") and not volumes_of("web"), "web ingress holds secrets or volumes")

if errors:
    print("BOUNDARY CHECK FAILED")
    for error in errors:
        print(" -", error)
    sys.exit(1)
print(f"Boundary check passed ({len(services)} services, {len(networks)} networks).")
