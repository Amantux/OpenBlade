# OpenBlade Agent Instructions

## Mission
Develop and maintain OpenBlade, a high-reliability, simulator-first tape archive controller. The primary goal is to ensure absolute data integrity and hardware safety while providing a robust software abstraction layer for complex tape library operations.

## Guardrails
### Hardware Safety & Data Integrity
- **Safety Override:** Never bypass or disable the real-hardware safety guard mechanisms.
- **Destructive Operations:** Never perform format or erase operations without positive barcode confirmation and a cryptographically valid safety token.
- **State Management:** Never initiate an unload sequence while the Linear Tape File System (LTFS) is mounted or in a "dirty" state.
- **Verification:** Every hardware command must be preceded by a state-check and followed by a verification of the resulting state.

### Security & Execution
- **Shell Safety:** Never use `shell=True` in subprocess calls to prevent injection vulnerabilities.
- **Privilege Principle:** Execute operations with the least privilege necessary; avoid root execution unless explicitly required for hardware driver interaction.

### Quality Assurance
- **Simulator-First Development:** All new features must be implemented and verified in the deterministic simulator before being targeted for hardware.
- **Test Coverage:** Every feature deployment must include corresponding unit and integration tests that cover both the happy path and failure modes.

## Working Style
### Architectural Integrity
- **Decoupling:** Maintain a strict architectural boundary between the simulator and physical hardware modules. Use abstract base classes or interfaces to ensure interchangeability.
- **Type Safety:** Adhere to strict typing. Maintain a clean `mypy` report and avoid the use of `Any` unless absolutely necessary.
- **Documentation:** Update safety matrices and architecture diagrams immediately when system behavior or hardware interfaces change.

### Agentic Workflow
- **Verification Loops:** Follow a "Plan $\rightarrow$ Implement $\rightarrow$ Verify $\rightarrow$ Document" cycle.
- **Atomic Changes:** Keep commits and pull requests focused on a single logical change to facilitate safer reviews.
- **Explicit Uncertainty:** If a hardware state is ambiguous or a simulator edge case is encountered, flag it immediately rather than assuming a success state.
