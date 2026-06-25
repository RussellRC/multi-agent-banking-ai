import logging
import os
from typing import AsyncGenerator, List

from google.adk import Agent
from google.adk.agents import SequentialAgent, LlmAgent, BaseAgent, InvocationContext
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent, AGENT_CARD_WELL_KNOWN_PATH
from google.adk.events import Event, EventActions
from google.genai import types
from google.genai.types import Content, Part, HttpOptions, HttpRetryOptions
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

generate_content_config=types.GenerateContentConfig(
    http_options=HttpOptions(
        retry_options=HttpRetryOptions(initial_delay=1, attempts=3),
    )
)

# --- get_requested_value_agent ---

class RequestedValueOutput(BaseModel):
    loan_amount: float = Field(description="The total loan amount that the customer is asking for.", default=0.0)
    loan_type: str = Field(description="The type of loan that the customer is asking for.", default="")
    errors: List[str] = Field(description="List of errors that happened while processing the request. Empty if no errors were found.", default_factory=list)

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
    total_outstanding_balance: float = Field(description="The total outstanding balance on all of the customer's loans.", default=0.0)

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
    amount_range: str | None = Field(description="Human-readable description of the amount range of the loan policy, e.g. 'under $10,000', '$10,000 and up'.", default=None)
    debt_to_equity_ratio: float = Field(description="The debt-to-equity ratio of the loan policy", default=0.0)
    minimum_customer_rating: str = Field(description="The minimum customer rating of the loan policy, e.g., 'Fair', 'Good', 'Great', 'Poor'.", default="")

class PolicyAgentOutput(BaseModel):
    policy_found: bool = Field(description="Whether or not a policy was found for the given loan type and amount.", default=False)
    policy: LoanPolicy | None = Field(description="Loan Policy details", default=None)
    errors: List[str] = Field(description="List of errors that happened while processing the request. Empty if no errors were found.", default_factory=list)

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
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # TODO: Implement
        pass

# TODO: Instantiate the TotalValueAgent to use (stage 4)

# TODO: Create agent that checks user's equity (stage 4)
check_equity_agent = RemoteA2aAgent(
    name="check_equity_agent",
    agent_card=f"http://localhost:8000/a2a/deposit{AGENT_CARD_WELL_KNOWN_PATH}"
)

class UserProfileOutput(BaseModel):
    customer_name: str = Field(description="The name of the customer.", default="")
    customer_rating: str | None = Field(description="The determined rating of the customer, e.g., 'Excellent', 'Great', 'Good', 'Fair', 'Poor'.", default=None)
    evidence_summary: str = Field(description="A brief summary of the findings, e.g., 'Frequent late payments, 2-3 times per year'", default="")
    errors: List[str] = Field(description="List of errors that happened while determining the customer rating. Empty if no errors were found.", default_factory=list)

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

# TODO: Create agent that uses these three agents to approve or not approve of a loan
