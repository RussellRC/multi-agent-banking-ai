import logging
import os
from typing import AsyncGenerator, List

from google.adk import Agent
from google.adk.agents import LlmAgent, BaseAgent, InvocationContext, SequentialAgent
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
    generate_content_config=generate_content_config
)


# --- outstanding_balance_agent ---

class OutstandingBalanceOutput(BaseModel):
    total_outstanding_balance: float = Field(
        description="The total outstanding balance on all the customer's loans.", default=0.0)


outstanding_balance_agent = LlmAgent(
    name="outstanding_balance_agent",
    description="Returns the total outstanding balance on all the customer's loans.",
    model=model,
    instruction=load_instructions("outstanding-balance-prompt.txt"),
    tools=[
        db_client.load_tool("get_total_outstanding_balance")
    ],
    output_schema=OutstandingBalanceOutput,
    generate_content_config=generate_content_config
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
    output_schema=PolicyAgentOutput,
    generate_content_config=generate_content_config
)


# --- total_value_agent ---

class TotalValueOutput(BaseModel):
    loan_amount: float = Field(description="The total loan amount that the customer is asking for.", default=0.0)
    loan_type: str = Field(description="The type of loan that the customer is asking for.", default="")
    total_outstanding_balance: float = Field(
        description="The total outstanding balance on all of the customer's loans.", default=0.0)
    debt_to_equity_ratio: float = Field(description="The debt-to-equity ratio of the loan policy.", default=0.0)
    minimum_deposit_balance: float = Field(
        description="The minimum total deposit balance that the customer needs to qualify for the loan.",
        default=0.0)
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
        ctx.reset_sub_agent_states(self.name)

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
                        total_outstanding_balance = round(output.total_outstanding_balance, 2)
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

            # Clean up the injected event
            if loan_type and loan_amount > 0 and policy_helper_event in ctx.session.events:
                logging.debug("Removing policy helper event")
                ctx.session.events.remove(policy_helper_event)

            # Calculate minimum deposit balance
            if policy_found and debt_to_equity_ratio > 0:
                minimum_deposit_balance = (total_outstanding_balance + loan_amount) / debt_to_equity_ratio
                minimum_deposit_balance = round(minimum_deposit_balance, 2)
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

remote_deposit_agent = RemoteA2aAgent(
    name="deposit_agent",
    description="Agent that communicates with the Deposit agent remotely.",
    agent_card=f"http://localhost:8000/a2a/deposit{AGENT_CARD_WELL_KNOWN_PATH}"
)

class CheckEquityInput(BaseModel):
    minimum_deposit_balance: float = Field(
        description="The minimum total deposit balance that the customer needs to qualify for the loan.")

class CheckEquityOutput(BaseModel):
    is_balance_sufficient: bool = Field(description="Whether the customer has sufficient equity to qualify for the loan.", default=False)

check_equity_agent = Agent(
    name="check_equity_agent",
    description="Checks if the customer has sufficient equity to qualify for the loan.",
    model=model,
    instruction=load_instructions("check-equity-prompt.txt"),
    sub_agents=[remote_deposit_agent],
    input_schema=CheckEquityInput,
    output_schema=CheckEquityOutput,
    output_key="equity_check_output",
    generate_content_config=generate_content_config
)

# --- user_profile_agent ---

class UserProfileInput(BaseModel):
    customer_name: str = Field(description="The name of the customer.", default="Chris Scott")

class UserProfileOutput(BaseModel):
    customer_name: str = Field(description="The name of the customer.")
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
    input_schema=UserProfileInput,
    output_schema=UserProfileOutput,
    output_key="user_profile_output"
)


# --- loan_approval_agent ---

# Helper to check if rating is sufficient
def is_rating_sufficient(customer_rating: str | None, minimum_customer_rating: str | None) -> bool:
    """
    Returns whether the current customer rating is sufficient to qualify for the loan
    by comparing it to the minimum required rating.

    Args:
        customer_rating (str): The current customer rating, e.g., 'Excellent', 'Great', 'Good', 'Fair', 'Poor'.
        minimum_customer_rating (str): The minimum required rating, e.g., 'Excellent', 'Great', 'Good', 'Fair', 'Poor'.

    Returns (bool): True if the customer rating is sufficient, False otherwise.
    """
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
class ApprovalDecisionInput(BaseModel):
    minimum_customer_rating: str = Field(
        description="The minimum customer rating that the customer needs to qualify for a loan."
    )
    # Map directly to the output_key from user_profile_agent
    user_profile_output: UserProfileOutput = Field(
        description="The output from the user profile agent containing the customer rating."
    )
    # Map directly to the output_key from check_equity_agent
    equity_check_output: CheckEquityOutput = Field(
        description="The output from the equity check agent containing balance sufficiency."
    )

approval_report_agent = LlmAgent(
    name="approval_report_agent",
    description="Generates the final friendly approval or rejection message for the customer.",
    model=model,
    instruction=load_instructions("approval-report-prompt.txt"),
    tools=[is_rating_sufficient],
    input_schema=ApprovalDecisionInput,
    generate_content_config=generate_content_config,
)

parallel_agent = ParallelAgent(
    name="parallel_agent",
    description="Determines the customer rating and computes the minimum deposit account balance that the user needs to qualify for a loan.",
    sub_agents=[user_profile_agent, total_value_agent],
)

# Instantiate the main loan approval sub-agent
loan_approval_agent = SequentialAgent(
    name="loan_approval_agent",
    description="Orchestrates the loan approval process.",
    sub_agents=[parallel_agent, check_equity_agent, approval_report_agent],
)
