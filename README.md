# AGCL

**Tagline:** Run a lightweight orchestrator on your workstation that composes powerful local compute with cloud connectors, multi-agent toolkits, and training hooks—without standing up ad hoc glue for every experiment.

## Overview

AGCL is a **Python-first hybrid control plane** for **AI researchers**, **agent engineers**, and **infra-oriented builders** who want one surface to orchestrate **local compute**, **Docker**, **Kubernetes**, **GCP**, **Azure**, **JSONBin**, and **parallel multi-agent toolkits** while keeping **API spend** in check. This repository is at **early scaffold** stage: the CLI and package layout are in place so development can proceed against `plan.md` and the forthcoming technical spec.

## Getting Started

**Prerequisites:** Python **3.13+** and [**uv**](https://docs.astral.sh/uv/) (recommended). Heavy ML dependencies are declared in `pyproject.toml`; install only what you need for the slice you are working on.

Install dependencies and verify the CLI:

```bash
uv sync --extra dev
uv run agcl --version
```

You should see `0.1.0` printed.

## Usage

Show CLI help (current scaffold):

```bash
uv run agcl --help
```

Run tests:

```bash
uv run pytest
```

Further workflows will be documented as features land; high-level intent lives in [`plan.md`](plan.md).

## License

This project is licensed under the MIT License — see [LICENSE](LICENSE).
