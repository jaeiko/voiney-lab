# Product & Engineering Roadmap: From Prototype to Commercial AI Platform

## Phase 1: Stability Hardening & Production Reliability (Immediate / Current Milestone)
- **Goal**: Transform the AX Program prototype into an unbreakable, robust lab agent MVP.
- **Key Deliverables**:
  - Comprehensive Agent Operating System documentation (`/AGENTS.md`, `/.agent/*`).
  - Rigorous engineering and product discovery audits (`docs/FINAL_ENGINEERING_AUDIT.md`, `docs/PRODUCT_IMPROVEMENT_PROPOSAL.md`).
  - Zero-flakiness test suite (640+ automated unit and integration tests passing).
  - Robust exception handling and graceful fallbacks for all external dependencies (LLM, STT, TTS, Moss, PubChem).
  - Production-ready session lifecycle management and resilient WebSocket reconnect handling.

## Phase 2: Enhanced Grounding & Multi-Modal Lab Assistance (Q3 2026)
- **Goal**: Expand real-time laboratory perception and multi-modal guidance.
- **Key Deliverables**:
  - **Dynamic Visual Grounding**: Real-time rendering of apparatus schematics, chemical safety pictograms (GHS), and titration curves.
  - **Researcher Learning & Context Mode**: On-demand verbal explanations of *why* specific steps are required and common execution pitfalls.
  - **Multi-lingual Hardening**: Full native conversational fluency and terminology grounding across Korean, English, and Vietnamese.
  - **Enhanced Noise Suppression**: Lab-tuned audio preprocessing for centrifuges, ultrasonic baths, and laminar flow hoods.

## Phase 3: Non-Linear Workflows & Multi-Day Experiment Lifecycle (Q4 2026)
- **Goal**: Support complex, conditional scientific protocols and multi-session workflows.
- **Key Deliverables**:
  - **Workflow DAG Engine**: Support conditional branching based on quantitative observations (e.g. *if pH < 6.5, branch to Step 3B*).
  - **Session Continuation & Cross-Shift Handover**: "Continue yesterday's PCR protocol" with historical observation pre-loading and pending step review.
  - **Protocol Ingestion Studio**: Automated conversion and chunking of arbitrary lab PDF/DOCX protocols with human approval workflow.
  - **Structured Lab Knowledge Layer**: Clustering anomalous observations across experiments into actionable institutional SOP improvements.

## Phase 4: Enterprise Lab Integration & Compliance (2027)
- **Goal**: Seamless deployment into regulated pharmaceutical, biotech, and university labs.
- **Key Deliverables**:
  - **ELN/LIMS Integration**: Two-way synchronization with Electronic Lab Notebooks (Benchling, LabArchives) and LIMS platforms.
  - **GLP/GMP Compliance & Audit Certification**: 21 CFR Part 11 compliant digital signatures, immutable audit logs, and role-based access control (RBAC).
  - **Supervisor Analytics Dashboard**: Lab-wide metrics on protocol adherence, step bottleneck analysis, and incident frequency heatmaps.
