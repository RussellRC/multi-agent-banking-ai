import logging
import os
from typing import AsyncGenerator, List

from google.adk import Agent
from google.adk.agents import LlmAgent, BaseAgent, InvocationContext
from google.adk.agents.parallel_agent import ParallelAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent, AGENT_CARD_WELL_KNOWN_PATH
from google.adk.events import Event
from google.genai import types
from google.genai.types import HttpOptions, HttpRetryOptions
from pydantic import BaseModel, Field
from toolbox_core import ToolboxSyncClient

from loan.datastore import datastore_search_tool


def load_instructions(prompt_file: str):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    instruction_file_path = os.path.join(script_dir, prompt_file)
    with open(instruction_file_path, "r") as f:
        return f.read()


# Set up the tools that we will be using for the root agent
toolbox_url = os.environ.get("TOOLBOX_URL", "http://127.0.0.1:5000")
print(f"Connecting to Toolbox at {toolbox_url}")
db_client = ToolboxSyncClient(toolbox_url)

# TODO: Create loan_info_tool (stage 2)

# Create agent that gets the requested loan value (stage 4)
model = "gemini-2.5-flash"

generate_content_config = types.GenerateContentConfig(
    http_options=HttpOptions(
        retry_options=HttpRetryOptions(initial_delay=1, attempts=3),
    )
)


    # --- get_requested_value_agent ---

class RequestedValueOutput(BaseModel):
    loan_amount: float = Field(description="The total loan amount that the customer is asking for.", default=0.0)
    loan_type: str = Field(description="The type of loan that the customer is asking for.", default="")
    errors: List[str] = Field(
        description="List of errors that happened while processing the request. Empty if no errors were found.",
        default_factory=list)


get_requested_value_agent = LlmAgent(
    name="get_requested_value_agent",
    description="Take the request from the customer and determine what type of loan they want and how much they are asking for.",
    model=model,
    instruction=load_instructions("loan-request-prompt.txt"),
    tools=[],
    output_schema=RequestedValueOutput,
)


# --- outstanding_balance_agent ---

class OutstandingBalanceOutput(BaseModel):
    total_outstanding_balance: float = Field(
        description="The total outstanding balance on all of the customer's loans.", default=0.0)


outstanding_balance_agent = LlmAgent(
    name="outstanding_balance_agent",
    description="Returns the total outstanding balance on all of the customer's loans.",
    model=model,
    instruction=load_instructions("outstanding-balance-prompt.txt"),
    tools=[
        db_client.load_tool("get_total_outstanding_balance")
    ],
    output_schema=OutstandingBalanceOutput
)


# --- policy_agent ---

class LoanPolicy(BaseModel):
    loan_type: str = Field(description="The type of loan policy, e.g. 'auto', 'personal', 'home improvement'.")
    amount_range: str | None = Field(
        description="Human-readable description of the amount range of the loan policy, e.g. 'under $10,000', '$10,000 and up'.",
        default=None)
    debt_to_equity_ratio: float = Field(description="The debt-to-equity ratio of the loan policy", default=0.0)
    minimum_customer_rating: str = Field(
        description="The minimum customer rating of the loan policy, e.g., 'Fair', 'Good', 'Great', 'Poor'.",
        default="")


class PolicyAgentOutput(BaseModel):
    policy_found: bool = Field(description="Whether or not a policy was found for the given loan type and amount.",
                               default=False)
    policy: LoanPolicy | None = Field(description="Loan Policy details", default=None)
    errors: List[str] = Field(
        description="List of errors that happened while processing the request. Empty if no errors were found.",
        default_factory=list)


policy_agent = LlmAgent(
    name="policy_agent",
    description="Searches the policy guidelines to extract the evaluation criteria for a loan based on the type and the amount requested.",
    model=model,
    instruction=load_instructions("policy-prompt.txt"),
    tools=[
        datastore_search_tool
    ],
    generate_content_config=generate_content_config,
    output_schema=PolicyAgentOutput
)


# --- total_value_agent ---

class TotalValueOutput(BaseModel):
    loan_amount: float = Field(description="The total loan amount that the customer is asking for.", default=0.0)
    loan_type: str = Field(description="The type of loan that the customer is asking for.", default="")
    total_outstanding_balance: float = Field(
        description="The total outstanding balance on all of the customer's loans.", default=0.0)
    debt_to_equity_ratio: float = Field(description="The debt-to-equity ratio of the loan policy", default=0.0)
    minimum_deposit_balance: float = Field(
        description="The minimum deposit account balance needed to support the loan request.", default=0.0)
    policy_found: bool = Field(description="Whether or not a policy was found for the given loan type and amount.",
                               default=False)
    errors: List[str] = Field(description="List of errors that occurred.", default_factory=list)


class TotalValueAgent(BaseAgent):
    """
    A custom agent that, based on the total outstanding balance and the debt-to-equity ratio determined by the sub-agents,
    computes what the minimum deposit account balance needs to be
    """

    get_requested_value_agent: Agent
    outstanding_balance_agent: Agent
    policy_agent: Agent

    def __init__(
            self,
            name: str,
            get_requested_value_agent: Agent,
            outstanding_balance_agent: Agent,
            policy_agent: Agent,
    ):
        super().__init__(
            name=name,
            get_requested_value_agent=get_requested_value_agent,
            outstanding_balance_agent=outstanding_balance_agent,
            policy_agent=policy_agent,
            sub_agents=[get_requested_value_agent, outstanding_balance_agent, policy_agent],
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        loan_amount = 0.0
        loan_type = ""
        errors = []

        # 1. Run get_requested_value_agent
        async for event in self.get_requested_value_agent.run_async(ctx):
            yield event
            if event.is_final_response() and event.content and event.content.parts:
                try:
                    text = event.content.parts[0].text
                    output = RequestedValueOutput.model_validate_json(text)
                    loan_amount = output.loan_amount
                    loan_type = output.loan_type
                    if output.errors:
                        errors.extend(output.errors)
                except Exception as e:
                    logging.error(f"Error parsing RequestedValueOutput: {e}")
                    errors.append(f"Error parsing requested loan details: {e}")

        # Initialize downstream variables with safe defaults
        total_outstanding_balance = 0.0
        debt_to_equity_ratio = 0.0
        policy_found = False
        minimum_customer_rating = ""
        minimum_deposit_balance = 0.0

        # FAST FAIL: Only proceed if Step 1 extracted the required details without errors
        if not errors:
            # 2. Run outstanding_balance_agent
            async for event in self.outstanding_balance_agent.run_async(ctx):
                yield event
                if event.is_final_response() and event.content and event.content.parts:
                    try:
                        text = event.content.parts[0].text
                        output = OutstandingBalanceOutput.model_validate_json(text)
                        total_outstanding_balance = output.total_outstanding_balance
                    except Exception as e:
                        logging.error(f"Error parsing OutstandingBalanceOutput: {e}")
                        errors.append(f"Error parsing outstanding loan balance: {e}")

            # 3. Run policy_agent
            if loan_type and loan_amount > 0:
                helper_text = f"Give me the policy for a loan with these details:\n - type: {loan_type}\n - amount: {loan_amount}"
                policy_helper_event = Event(
                    invocation_id=ctx.invocation_id,
                    author="user",
                    branch=ctx.branch,
                    content=types.Content(parts=[types.Part(text=helper_text)])
                )
                ctx.session.events.append(policy_helper_event)

            async for event in self.policy_agent.run_async(ctx):
                yield event
                if event.is_final_response() and event.content and event.content.parts:
                    try:
                        text = event.content.parts[0].text
                        output = PolicyAgentOutput.model_validate_json(text)
                        if output.policy_found and output.policy:
                            debt_to_equity_ratio = output.policy.debt_to_equity_ratio
                            minimum_customer_rating = output.policy.minimum_customer_rating
                            policy_found = True
                        if output.errors:
                            errors.extend(output.errors)
                    except Exception as e:
                        logging.error(f"Error parsing PolicyAgentOutput: {e}")
                        errors.append(f"Error parsing policy details: {e}")

            # Calculate minimum deposit balance
            if policy_found and debt_to_equity_ratio > 0:
                minimum_deposit_balance = (total_outstanding_balance + loan_amount) / debt_to_equity_ratio
            else:
                if not policy_found:
                    errors.append("No policy was found to determine the debt-to-equity ratio.")

        # Store in session state (safely writes defaults if we failed fast)
        ctx.session.state["loan_amount"] = loan_amount
        ctx.session.state["loan_type"] = loan_type
        ctx.session.state["total_outstanding_balance"] = total_outstanding_balance
        ctx.session.state["debt_to_equity_ratio"] = debt_to_equity_ratio
        ctx.session.state["minimum_deposit_balance"] = minimum_deposit_balance
        ctx.session.state["minimum_customer_rating"] = minimum_customer_rating
        ctx.session.state["policy_found"] = policy_found

        # Construct and yield the final TotalValueOutput event
        output = TotalValueOutput(
            loan_amount=loan_amount,
            loan_type=loan_type,
            total_outstanding_balance=total_outstanding_balance,
            debt_to_equity_ratio=debt_to_equity_ratio,
            minimum_deposit_balance=minimum_deposit_balance,
            policy_found=policy_found,
            errors=errors
        )

        final_event = Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            content=types.Content(parts=[types.Part(text=output.model_dump_json())])
        )
        yield final_event


# Instantiate the TotalValueAgent to use
total_value_agent = TotalValueAgent(
    name="total_value_agent",
    get_requested_value_agent=get_requested_value_agent,
    outstanding_balance_agent=outstanding_balance_agent,
    policy_agent=policy_agent,
)

# --- check_equity_agent ---

check_equity_agent = RemoteA2aAgent(
    name="check_equity_agent",
    agent_card=f"http://localhost:8000/a2a/deposit{AGENT_CARD_WELL_KNOWN_PATH}"
)


# --- user_profile_agent ---

class UserProfileOutput(BaseModel):
    customer_name: str = Field(description="The name of the customer.", default="")
    customer_rating: str | None = Field(
        description="The determined rating of the customer, e.g., 'Excellent', 'Great', 'Good', 'Fair', 'Poor'.",
        default=None)
    evidence_summary: str = Field(
        description="A brief summary of the findings, e.g., 'Frequent late payments, 2-3 times per year'", default="")
    errors: List[str] = Field(
        description="List of errors that happened while determining the customer rating. Empty if no errors were found.",
        default_factory=list)


user_profile_agent = LlmAgent(
    name="user_profile_agent",
    description="Searches the unstructured data store for the customer profile and determine their customer rating.",
    model=model,
    instruction=load_instructions("user-profile-base-prompt.txt"),
    tools=[
        datastore_search_tool
    ],
    generate_content_config=generate_content_config,
    output_schema=UserProfileOutput
)


# --- loan_approval_agent ---

# Helper to check if rating is sufficient
def is_rating_sufficient(customer_rating: str | None, minimum_customer_rating: str | None) -> bool:
    if not customer_rating or not minimum_customer_rating:
        return False
    rating_rank = {
        "excellent": 5,
        "great": 4,
        "good": 3,
        "fair": 2,
        "poor": 1
    }
    c_rating = customer_rating.lower().strip()
    m_rating = minimum_customer_rating.lower().strip()

    if "or better" in m_rating:
        m_rating = m_rating.replace("or better", "").strip()

    return rating_rank.get(c_rating, 0) >= rating_rank.get(m_rating, 0)


# --- approval_report_agent ---
approval_report_agent = LlmAgent(
    name="approval_report_agent",
    description="Generates the final friendly approval or rejection message for the customer.",
    model=model,
    instruction=load_instructions("approval-report-prompt.txt"),
    tools=[],
    generate_content_config=generate_content_config,
)


# --- loan_approval_agent ---
class LoanApprovalAgent(BaseAgent):
    """
    A custom agent that coordinates the sub-agents to evaluate, verify, and report
    on a customer's loan approval request.
    """
    total_value_agent: TotalValueAgent
    check_equity_agent: RemoteA2aAgent
    user_profile_agent: Agent
    approval_report_agent: Agent
    parallel_agent: ParallelAgent

    def __init__(
            self,
            name: str,
            total_value_agent: TotalValueAgent,
            check_equity_agent: RemoteA2aAgent,
            user_profile_agent: Agent,
            approval_report_agent: Agent
    ):
        local_parallel_agent = ParallelAgent(
            name="parallel_agent",
            sub_agents=[total_value_agent, user_profile_agent]
        )

        super().__init__(
            name=name,
            total_value_agent=total_value_agent,
            check_equity_agent=check_equity_agent,
            user_profile_agent=user_profile_agent,
            approval_report_agent=approval_report_agent,
            parallel_agent=local_parallel_agent,
            sub_agents=[local_parallel_agent, approval_report_agent],
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        ctx.reset_sub_agent_states(self.name)

        has_processing_errors = False # Track our error state

        # 1. Run total_value_agent and user_profile_agent in parallel
        async for event in self.parallel_agent.run_async(ctx):
            yield event
            if event.is_final_response() and event.content and event.content.parts:
                text = event.content.parts[0].text

                # Check User Profile Agent
                if event.author == "user_profile_agent":
                    try:
                        output = UserProfileOutput.model_validate_json(text)
                        ctx.session.state["customer_name"] = output.customer_name
                        ctx.session.state["customer_rating"] = output.customer_rating
                        if output.errors:
                            logging.error(f"UserProfile errors: {output.errors}")
                            has_processing_errors = True
                    except Exception as e:
                        logging.error(f"Error parsing UserProfileOutput: {e}")
                        has_processing_errors = True

                # Check Total Value Agent
                elif event.author == "total_value_agent":
                    try:
                        output = TotalValueOutput.model_validate_json(text)
                        ctx.session.state["minimum_deposit_balance"] = output.minimum_deposit_balance
                        ctx.session.state["policy_found"] = output.policy_found
                        if output.errors:
                            logging.error(f"TotalValue errors: {output.errors}")
                            has_processing_errors = True
                    except Exception as e:
                        logging.error(f"Error parsing TotalValueOutput: {e}")
                        has_processing_errors = True

        is_equity_sufficient = False

        # 2. Only run check_equity_agent if previous steps succeeded
        if not has_processing_errors:
            minimum_deposit_balance = ctx.session.state.get("minimum_deposit_balance", 0.0)

            equity_check_event_text = (
                f"Please check if the sum of all deposit account balances is greater than {minimum_deposit_balance:.2f}.\n"
                "Answer 'yes' or 'no'."
            )
            ctx.session.events.append(Event(
                invocation_id=ctx.invocation_id,
                author="user",
                branch=ctx.branch,
                content=types.Content(parts=[types.Part(text=equity_check_event_text)]),
            ))

            async for event in self.check_equity_agent.run_async(ctx):
                yield event
                if event.is_final_response() and event.content and event.content.parts:
                    response_text = event.content.parts[0].text.lower()
                    if "yes" in response_text:
                        is_equity_sufficient = True

        ctx.session.state["is_equity_sufficient"] = is_equity_sufficient

        # 3. Determine loan approval (Auto-reject if there were processing errors)
        approved = False
        if not has_processing_errors:
            policy_found = ctx.session.state.get("policy_found", False)
            minimum_customer_rating = ctx.session.state.get("minimum_customer_rating", "")
            customer_rating = ctx.session.state.get("customer_rating", "")

            if policy_found and is_equity_sufficient:
                if is_rating_sufficient(customer_rating, minimum_customer_rating):
                    approved = True

        ctx.session.state["loan_approved"] = approved

        # 4. Run approval_report_agent to get the friendly response
        if has_processing_errors:
            # Explicitly tell the reporting agent it was a system error
            prompt_text = (
                "The loan request could not be evaluated. The decision is: REJECTED. "
                "Please inform the customer that their application could not be processed "
                "at this time due to an internal system processing error, and politely ask them to try again later."
            )
        else:
            # Standard evaluation result
            decision_str = "APPROVED" if approved else "REJECTED"
            prompt_text = f"The loan request has been evaluated. The decision is: {decision_str}."

        ctx.session.events.append(Event(
            invocation_id=ctx.invocation_id,
            author="user",
            branch=ctx.branch,
            content=types.Content(
                parts=[types.Part(text=prompt_text)]
            ),
        ))

        async for event in self.approval_report_agent.run_async(ctx):
            yield event

# Instantiate the main loan approval sub-agent
loan_approval_agent = LoanApprovalAgent(
    name="loan_approval_agent",
    total_value_agent=total_value_agent,
    check_equity_agent=check_equity_agent,
    user_profile_agent=user_profile_agent,
    approval_report_agent=approval_report_agent,
)
