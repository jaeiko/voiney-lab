# AGENTS.md — Agent Operating System Guidelines

Welcome to **Voice Workflow Agent (Voice Workflow Guide Lab Copilot)**.
This document is the root entrypoint and operating manual for all autonomous coding agents, AI pair programmers, and engineering contributors working on this repository.

---

## 1. Prime Directive

> **Voice Workflow Agent is a hands-free AI Workflow Agent for laboratory research, not a generic conversational chatbot.**
> The central domain entity is **Workflow State**, strictly governed by server-owned deterministic gates and approved protocol definitions.

### Non-Negotiable Operating Principles:
1. **Grounded Operation**: The agent relies exclusively on approved knowledge sources (approved SOPs, safety catalogs, verified protocols). Unsupported or missing information must be explicitly communicated as unsupported. The agent NEVER hallucinates chemical properties, SOP steps, exposure limits, or lab numbers.
2. **Human-Centered Safety**: The agent NEVER makes safety-critical decisions, NEVER authorizes experiment restart after an anomaly, NEVER overrides PPE requirements, and NEVER replaces human lab managers or Principal Investigators (PIs).
3. **Workflow-First Integrity**: The conversational layer (STT/LLM/TTS) is an interface to the server-owned Workflow State Machine. Model output cannot directly mutate workflow state; mutations must pass through strict validation gates, exact allow-lists, and deterministic preconditions.
4. **Zero Unnecessary Rewrites**: Preserve all existing working behaviors, regression tests, and canonical contracts. Prefer minimal, surgical, safe changes over sweeping rewrites.

---

## 2. Agent Documentation Suite (`/.agent/`)

All agents MUST consult the specialized manuals in `/.agent/` for detailed rules, architecture blueprints, testing protocols, and design systems:

| Document | Purpose |
|---|---|
| [`product_context.md`](file:///home/student/voice-ai-course/voice-workflow-agent/.agent/product_context.md) | Product vision, personas (undergraduates, graduate researchers, lab managers, PIs), user journeys, and non-functional requirements. |
| [`architecture.md`](file:///home/student/voice-ai-course/voice-workflow-agent/.agent/architecture.md) | Full architectural blueprint: Voice pipeline, Agent LLM orchestration, Workflow engine, Data layer, Worker handoff, and Frontend. |
| [`coding_rules.md`](file:///home/student/voice-ai-course/voice-workflow-agent/.agent/coding_rules.md) | Strict engineering guidelines: type annotations, immutability, error handling, backward compatibility, and style constraints. |
| [`testing_strategy.md`](file:///home/student/voice-ai-course/voice-workflow-agent/.agent/testing_strategy.md) | Testing requirements: unit tests, integration tests, latency benchmarks, interruption tests, and coverage criteria. |
| [`security_rules.md`](file:///home/student/voice-ai-course/voice-workflow-agent/.agent/security_rules.md) | Security policies: approved knowledge boundary, sensitive research data handling, input sanitization, and audit trails. |
| [`evaluation_strategy.md`](file:///home/student/voice-ai-course/voice-workflow-agent/.agent/evaluation_strategy.md) | Evaluation metrics: grounding accuracy, intent classification F1, tool selection precision, and handoff reliability. |
| [`product_improvement_strategy.md`](file:///home/student/voice-ai-course/voice-workflow-agent/.agent/product_improvement_strategy.md) | Framework for product discovery, user pain point prioritization, feasibility scoring, and feature staging. |
| [`roadmap.md`](file:///home/student/voice-ai-course/voice-workflow-agent/.agent/roadmap.md) | Strategic engineering and product roadmap from prototype to enterprise-grade wet-lab platform. |

---

## 3. Quick Reference for Developers & Agents

### Development Environment & Commands
```bash
# Activate virtual environment
source .venv/bin/activate

# Run complete test suite
pytest -q

# Run fast unit tests only
pytest tests/test_server_helpers.py tests/test_completion_intent.py tests/test_procedures.py

# Start Voice Workflow Agent Server
uvicorn voice_workflow_agent.server:app --host 0.0.0.0 --port 8000 --reload

# Start Asynchronous Handoff Worker
python -m voice_workflow_agent.worker
```

### Key Architectural File Map
- **Core Server & Routing**: [`src/voice_workflow_agent/server.py`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/server.py)
- **Agent Persona & LLM Loop**: [`src/voice_workflow_agent/brain.py`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/brain.py)
- **Multi-Brain Roles**: [`src/voice_workflow_agent/multi_brain.py`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/multi_brain.py)
- **Deterministic Tools**: [`src/voice_workflow_agent/tools.py`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/tools.py)
- **Workflow State Controller**: [`src/voice_workflow_agent/procedures.py`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/procedures.py)
- **Intent Classification Gates**: [`src/voice_workflow_agent/completion_intent.py`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/completion_intent.py)
- **Audit Reports & Event Ledger**: [`src/voice_workflow_agent/experiment_reports.py`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/experiment_reports.py)
- **Safety Handoff Worker**: [`src/voice_workflow_agent/worker.py`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/worker.py)
- **Frontend Cockpit Dashboard**: [`src/voice_workflow_agent/static/index.html`](file:///home/student/voice-ai-course/voice-workflow-agent/src/voice_workflow_agent/static/index.html)
