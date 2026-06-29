# REPORT

---
## Agent Information

### 1. Manager Agent (`solution/manager/agent.py`)
The central orchestrator for the bank. It routes general inquiries to:
* **Deposit Agent**: For balances and transactions
* **Loan Agent** For all things related to loans

### 2. Loan Agent (`solution/loan/agent.py`)
Specialized in loan management.
* For existing loans, it retrieves details directly via SQL tools.
* For new loan requests, it delegates the complex evaluation process to the **Loan Approval Agent**.

### 3. Loan Approval Agent (`solution/loan/loan.py`)
A highly complex orchestrator that manages a multi-step verification pipeline:
- **Parallel Step** executes simultaneously:
    - `Total Value Agent` to compute required cash balance threshold
    - `User Profile Agent` to determine customer rating.
- **Verification Step**: Triggers the `Deposit Agent` (`Check Equity Agent`) via A2A to confirm if the user's current cash balance meets the calculated requirement.
- **Reporting Step**: Uses the `Approval Report Agent` to generate a final, customer-friendly decision message.

### 4. Specialized Sub-Agents
- **Deposit Agent**: Handles account balance and transaction history queries using SQL tools.
- **User Profile Agent**: Extracts customer ratings by cross-referencing loan officer summaries with official bank criteria in the Datastore.
- **Total Value Agent**: Orchestrates policy searches and calculation of necessary deposit buffers based on debt-to-equity ratios.

### Architecture
See [DIAGRAM.md](DIAGRAM.md)

---
## Results Evaluation

### Key Strengths & Demonstrated Capabilities

**Precise Single-Domain Inquiries**
- The agent flawlessly executed explicit account balance retrievals and transaction queries (e.g., `thread-001` and `thread-011`).
- The integration with SQL and data tools allowed for accurate financial reporting.

**Conversational Clarification Loops**
- In scenarios with missing parameters (such as unspecified account types in `thread-002` and `thread-003`),
the agent effectively paused the workflow, requested clarification politely, and successfully resumed execution once the user provided the input.

**Strict Regulatory & Role Boundaries**
- The agent robustly enforced its structural limitations.
- When prompted to add funds (`thread-006`), hand out money (`thread-007`), or facilitate third-party asset purchases (`thread-008`),
it gracefully declined while cleanly identifying its role as an informational banking assistant.

**Solid End-to-End Orchestration**
- On the core targeted happy path (`thread-010`), the **Manager Agent** seamlessly routed to the **Loan Agent**,
triggering the **Loan Approval Agent** pipeline to synthesize data, match policy, check equity, and issue a friendly approval message exactly as instructed.


### Identified Weaknesses & Failure Modes

**The Semantic Gap (Implicit Intent Failure)**
- The agent relies heavily on explicit keywords and struggles with conceptual inference.
- In `thread-004`, it failed to differentiate that a user asking for a "recent payment" on a deposit account likely meant an incoming paycheck returning an outgoing transaction instead.
- In `thread-005` and `thread-008`, it failed to bridge contextual concepts, unable to link "vacation activity" to a "vacation account," or an "off-road 4x4" to an "auto loan" request.

**Routing Limitations & System Leakage**
- In `thread-009`, the Manager Agent recognized a new loan request but failed to route it, explicitly leaking an undesirable systemic error to the user ("I am currently unable to route your request to our loan agent").
- The system cannot handle composite requests. In `thread-015`, when asked for both account balances and loan structures, the orchestrator broke character, explicitly revealing its internal agentic architecture ("These are handled by two different specialized agents") rather than handling the delegation abstractly.


### Suggestions for future improvements
**Intent parsing**
- Upgrade the **Manager Agent** prompt with Few-Shot Lexical Examples to explicitly map synonyms (e.g., "4x4/vehicle" $\rightarrow$ Auto Loan; "vacation trip/hotel" $\rightarrow$ Vacation Account).

**Abstraction**
- Revise system prompts to strictly forbid mentioning "agents," "specialists," or "routing failures."
- The model must fallback to a unified, monolithic corporate voice.

**Data Aggregation**
- Introduce a Global Summary Tool at the Manager Agent level.
This would allow the orchestrator to fetch a high-level overview of balances and loan accounts simultaneously without forcing a hard sequential handoff.

---

## Agentic Risks & Mitigations

### 1. System Leakage
* **Risk:** Agents can expose internal routing failures or mention "specialized agents" to the end user (`thread-015`).
* **Mitigation:** Add system-level prompts forcing a single, unified "bank representative" voice that never references the underlying software architecture.

### 2. Cascading Pipeline Failures
* **Risk:** Running downstream agents using incomplete or corrupted data from an earlier failed step can result in wasting API tokens and execution time.
* **Mitigation:** Implement a Fail-Fast Pattern. Use Pydantic schemas to validate data at each step; if an early extraction agent flags missing data, immediately short-circuit the execution with a return and ask the user for details.

### 3. Tool Over-Privilege
* **Risk:** Users bypassing financial guardrails via prompt injection to mutate database states (e.g., attempting to forcefully add funds).
* **Mitigation:** Enforce read-only tools for public conversational agents. High-risk actions (like transferring money) should only create a pending draft that requires a Human-in-the-Loop admin approval.

**4. Non-Deterministic Behavior**
* **Risk:** LLMs are inherently non-deterministic. They can sometimes misinterpret identical intents because of subtle variations in human phrasing (`thread-004`).
* **Mitigation:**
  * Use few-shot examples to anchor semantic understanding
  * For operations requiring strict deterministic execution, shift the logic into backend tools rather than relying on the LLM, implementing a fat-tool, thin-agent architecture.
  * Maintain a lightweight testing script asserting that specific benchmark prompts always map to the correct backend tools.