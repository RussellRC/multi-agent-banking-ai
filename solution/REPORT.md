# REPORT

---
## Agent Information

### 1. Manager Agent (`solution/manager/agent.py`)
The central orchestrator for the bank. It routes general inquiries to:
* **Deposit Agent**: For balances and transactions
* **Loan Agent** For all things related to loans

### 2. Deposit Agent (`solution/deposit/agent.py`)
Handles account balance and transaction history queries using SQL tools.

### 3. Loan Agent (`solution/loan/agent.py`)
Specialized in loan management.
* For existing loans, it retrieves details directly via SQL tools.
* For new loan requests, it delegates the complex evaluation process to the **Loan Approval Agent**.

### 3. Loan Approval Agent (`solution/loan/loan.py`)
A `SequentialAgent` that orchestrates the loan approval process:
- **Parallel Step** executes simultaneously:
    - `Total Value Agent` to compute the minimum deposit account balance that the user needs to qualify for a loan.
    - `User Profile Agent` to determine customer rating.
- **Verification Step**: Triggers the `Deposit Agent` (`Check Equity Agent`) via A2A to confirm if the user's current cash balance meets the calculated requirement.
- **Reporting Step**: Uses the `Approval Report Agent` to generate a final, customer-friendly decision message.

### 4. Specialized Sub-Agents
- **User Profile Agent**: Extracts customer ratings by cross-referencing loan officer summaries with official bank criteria in the Datastore.
- **Total Value Agent**: Orchestrates policy searches and calculation of the necessary deposit balance based on debt-to-equity ratios. Uses the following sub-agents:
  - **`get_requested_value_agent`**: Takes the request from the customer and determines what type of loan they want and how much they are asking for.
  - **`outstanding_balance_agent`**: Retrieves the total outstanding balance on all the customer's loans.
  - **`policy_agent`**: Searches the Datastore for the policy guidelines to extract the evaluation criteria for a loan based on the type and the amount requested.

### Architecture
See [DIAGRAM.md](DIAGRAM.md)

---
## Results Evaluation

### Key Strengths & Demonstrated Capabilities

**Precise Single-Domain Inquiries**
- The agent flawlessly executed explicit account balance retrievals and loan queries. See:
  - `thread-001`
  - `thread-004`
  - `thread-011`
  - `thread-012`
- The integration with SQL data tools allowed for accurate financial reporting.

**Conversational Clarification Loops**
- In scenarios with missing parameters, such as unspecified account types, the agent effectively paused the workflow, 
requested clarification politely, and successfully resumed execution once the user provided the input. See:
- `thread-002`
- `thread-003`,

**Strict Regulatory & Role Boundaries**
- The agent robustly enforced its structural limitations.
- When prompted to add funds, hand out money, or facilitate third-party asset purchases, 
it gracefully declined while cleanly identifying its role as an informational banking assistant. See:
- `thread-006`
- `thread-007`
- `thread-008`

**Solid End-to-End Orchestration**
- On the core targeted happy path (`thread-010`), the **Manager Agent** seamlessly routed to the **Loan Agent**,
triggering the **Loan Approval Agent** pipeline to synthesize data, match policy, check equity, and issue a friendly approval message exactly as instructed.


### Identified Weaknesses & Failure Modes

**The Semantic Gap (Implicit Intent Failure)**
The agent relies heavily on explicit keywords and struggles with conceptual inference:
- In `thread-005` it failed to bridge contextual concepts, unable to link "vacation activity" to a "vacation account"
- In `thread-008`or an "off-road 4x4" to an "auto loan" request.


### Suggestions for future improvements
**Intent parsing**
- Upgrade the **Manager Agent** prompt with Few-Shot Lexical Examples to explicitly map synonyms (e.g., "4x4/vehicle" $\rightarrow$ Auto Loan; "vacation trip/hotel" $\rightarrow$ Vacation Account).

**Data Aggregation**
- Introduce a Global Summary Tool at the Manager Agent level.
This would allow the orchestrator to fetch a high-level overview of balances and loan accounts simultaneously without forcing a hard sequential handoff.

---

## Agentic Risks & Mitigations

### 1. System Leakage
* **Risk:** Agents can expose internal routing failures or mention "specialized agents" to the end user.
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