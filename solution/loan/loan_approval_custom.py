import logging
from typing import AsyncGenerator

from google.adk import Agent, Event
from google.adk.agents import BaseAgent, ParallelAgent, InvocationContext
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.genai import types

from loan.loan import TotalValueAgent, UserProfileOutput, TotalValueOutput, is_rating_sufficient


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

        has_processing_errors = False
        validation_errors = [] # Specifically track missing data messages

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
                        logging.error(f"Error parsing UserProfileOutput: {str(e)}")
                        has_processing_errors = True

                # Check Total Value Agent
                elif event.author == "total_value_agent":
                    try:
                        output = TotalValueOutput.model_validate_json(text)
                        ctx.session.state["minimum_deposit_balance"] = output.minimum_deposit_balance
                        ctx.session.state["policy_found"] = output.policy_found
                        if output.errors:
                            logging.error(f"TotalValue errors: {output.errors}")
                            validation_errors.extend(output.errors) # Capture the specific messages
                            has_processing_errors = True
                    except Exception as e:
                        logging.error(f"Error parsing TotalValueOutput: {str(e)}")
                        has_processing_errors = True

        # Early conversational exit
        # If the get_requested_value_agent reported missing info (e.g., "Missing loan amount"),
        # we ask the user for it and halt this approval attempt.
        if validation_errors:
            error_details = " ".join(validation_errors)
            clarification_msg = (
                f"I need a bit more information before I can process your loan application: {error_details}. "
                "Could you please clarify?"
            )
            yield Event(
                invocation_id=ctx.invocation_id,
                author=self.name,
                branch=ctx.branch,
                content=types.Content(parts=[types.Part(text=clarification_msg)])
            )
            return  # EXIT THE GENERATOR ENTIRELY! Do not run the equity check or approval report.


        # 2. Only run check_equity_agent if previous steps succeeded completely
        is_equity_sufficient = False
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