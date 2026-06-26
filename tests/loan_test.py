import os
import sys
import unittest
import uuid
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import respx
from dotenv import load_dotenv
from google.adk import Event
from google.genai.types import Content, Part
from httpx import Response

from loan import loan

# Add the solution directory to sys.path so we can import from it
solution_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'solution'))
sys.path.append(solution_dir)
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Load environment variables using load_dotenv
env_file = os.path.join(solution_dir, '.env')
load_dotenv(env_file)

# Resolve GOOGLE_APPLICATION_CREDENTIALS to an absolute path if it is relative
if 'GOOGLE_APPLICATION_CREDENTIALS' in os.environ:
    creds_path = os.environ['GOOGLE_APPLICATION_CREDENTIALS']
    if not os.path.isabs(creds_path):
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        abs_path = os.path.abspath(os.path.join(workspace_root, creds_path))
        if not os.path.exists(abs_path):
            abs_path = os.path.abspath(os.path.join(solution_dir, creds_path))
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = abs_path

from loan.loan import (
    get_requested_value_agent,
    RequestedValueOutput,
    outstanding_balance_agent,
    OutstandingBalanceOutput,
    policy_agent,
    PolicyAgentOutput,
    user_profile_agent,
    UserProfileOutput,
    total_value_agent,
    TotalValueOutput, loan_approval_agent
)
from utils import run_agent_with_user_messages


# A helper to simulate the async generator yielding events
async def mock_async_generator(*args, **kwargs):
    # Yield a final response event mimicking RemoteA2aAgent output
    yield Event(
        invocation_id="test-id",
        author="check_equity_agent",
        branch="main",
        content=Content(parts=[Part(text="Yes.")]),
        is_final_response=True
    )

# loan.py tests
# IMPORTANT NOTE: mcp-toolbox must be running:
# mcp-toolbox --configs solution/deposit/tools.yaml,solution/loan/tools.yaml
class LoanTest(unittest.TestCase):

    def test_get_requested_value_agent_happy_path(self):
        user_query = "I want to request a home loan of 1 million dollars"
        final_response_event: Event | None = run_agent_with_user_messages(get_requested_value_agent, user_query)
        self.assertIsNotNone(final_response_event)
        self.assertTrue(hasattr(final_response_event.content, 'parts') and len(final_response_event.content.parts) > 0)

        text = final_response_event.content.parts[0].text
        output: RequestedValueOutput = RequestedValueOutput.model_validate_json(text)
        self.assertEqual(output.loan_amount, 1000000.0)
        self.assertEqual(output.loan_type, "home")
        self.assertEqual(len(output.errors), 0)

    def test_outstanding_balance_agent_happy_path(self):
        user_query = "What is my outstanding balance?"
        final_response_event: Event | None = run_agent_with_user_messages(outstanding_balance_agent, user_query)
        self.assertIsNotNone(final_response_event)
        self.assertTrue(hasattr(final_response_event.content, 'parts') and len(final_response_event.content.parts) > 0)

        text = final_response_event.content.parts[0].text
        output: OutstandingBalanceOutput = OutstandingBalanceOutput.model_validate_json(text)
        self.assertEqual(output.total_outstanding_balance, 22183.29)

    def test_policy_agent_happy_path(self):
        user_query = (
            "Give me the policy for a loan with these details:\n"
            " - type: home improvement\n"
            " - amount: 30000"
        )
        final_response_event: Event | None = run_agent_with_user_messages(policy_agent, user_query)
        self.assertIsNotNone(final_response_event)
        self.assertTrue(hasattr(final_response_event.content, 'parts') and len(final_response_event.content.parts) > 0)

        text = final_response_event.content.parts[0].text
        output: PolicyAgentOutput = PolicyAgentOutput.model_validate_json(text)
        self.assertEqual(len(output.errors), 0)
        self.assertTrue(output.policy_found)
        self.assertEqual(output.policy.loan_type.lower(), "home improvement")
        self.assertEqual(output.policy.amount_range, "$20,000 and up")
        self.assertEqual(output.policy.debt_to_equity_ratio, 6.0)
        self.assertEqual(output.policy.minimum_customer_rating.lower(), "fair")

    def test_user_profile_agent_happy_path(self):
        user_query = "What's the customer profile of Chris Scott?"
        final_response_event: Event | None = run_agent_with_user_messages(user_profile_agent, user_query)
        self.assertIsNotNone(final_response_event)
        self.assertTrue(hasattr(final_response_event.content, 'parts') and len(final_response_event.content.parts) > 0)

        text = final_response_event.content.parts[0].text
        output: UserProfileOutput = UserProfileOutput.model_validate_json(text)
        self.assertEqual(len(output.errors), 0)
        self.assertTrue(output.customer_rating.lower() in ["great", "good"])
        self.assertEqual("Chris Scott", output.customer_name)
        self.assertGreater(len(output.evidence_summary), 0)

    def test_total_value_agent_happy_path(self):
        user_query = "I want to request a home improvement loan of 30000 dollars"
        final_response_event: Event | None = run_agent_with_user_messages(total_value_agent, user_query)
        self.assertIsNotNone(final_response_event)
        self.assertTrue(hasattr(final_response_event.content, 'parts') and len(final_response_event.content.parts) > 0)

        text = final_response_event.content.parts[0].text
        output: TotalValueOutput = TotalValueOutput.model_validate_json(text)
        self.assertEqual(len(output.errors), 0)
        self.assertEqual(output.loan_amount, 30000.0)
        self.assertEqual(output.loan_type, "home improvement")
        self.assertEqual(output.total_outstanding_balance, 22183.29)
        self.assertEqual(output.debt_to_equity_ratio, 6.0)
        self.assertTrue(output.policy_found)
        self.assertEqual(output.minimum_deposit_balance, (22183.29 + 30000.0) / 6.0)

    def test_loan_approval_agent_happy_path(self):
        """
        Tests loan approval agent happy path
        IMPORTANT NOTE: Both adk web --a2a solution and mcp-toolbox MUST be running
        """
        # Simulate the final user query asking for loan approval
        user_query = "Hi, I am Chris Scott. I would like to finalize my request for a $300 home improvement loan."

        # 2. Act: Run the loan approval agent
        final_response_event: Event | None = run_agent_with_user_messages(loan_approval_agent, user_query)

        # 3. Assert: Verify the response event is structured correctly
        self.assertIsNotNone(final_response_event)
        self.assertTrue(hasattr(final_response_event.content, 'parts') and len(final_response_event.content.parts) > 0)

        text_response = final_response_event.content.parts[0].text.lower()

        # Verify the agent followed the strict approval prompt rules:
        # - It must NOT output JSON.
        self.assertNotIn("{", text_response)

        # - Let the customer know their loan has been approved.
        self.assertIn("approved", text_response)

        # - Inform them that a loan officer will be contacting them shortly.
        self.assertIn("loan officer", text_response)


if __name__ == '__main__':
    unittest.main()
