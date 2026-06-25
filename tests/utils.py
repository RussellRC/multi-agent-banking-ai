import asyncio

from google.adk import Event
from google.adk.agents import BaseAgent
from google.adk.runners import InMemoryRunner


def run_agent_with_user_messages(agent: BaseAgent, user_messages: str) -> Event | None:
    runner = InMemoryRunner(agent=agent)
    response_events = asyncio.run(runner.run_debug(user_messages=user_messages))

    final_response_event = None
    for event in response_events:
        if event.is_final_response():
            final_response_event = event

    return final_response_event