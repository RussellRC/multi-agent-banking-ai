import os

from dotenv import load_dotenv
from google.adk import Agent
from google.adk.sessions import InMemorySessionService
from google.genai import types
from toolbox_core import ToolboxSyncClient

load_dotenv(dotenv_path="../.env")

# Configure short-term session to use the in-memory service
session_service = InMemorySessionService()

# Read the instructions from a file in the same
# directory as this agent.py file.
script_dir = os.path.dirname(os.path.abspath(__file__))
instruction_file_path = os.path.join(script_dir, "agent-prompt.txt")
with open(instruction_file_path, "r") as f:
  instruction = f.read()

# Set up the tools that we will be using for the root agent
toolbox_url = os.environ.get("TOOLBOX_URL", "http://127.0.0.1:5000")
print(f"Connecting to Toolbox at {toolbox_url}")
db_client = ToolboxSyncClient( toolbox_url )

tools=[
  db_client.load_tool("get_balance"),
  db_client.load_tool("get_account_transactions"),
  db_client.load_tool("check_minimum_balance"),
  db_client.load_tool("list_accounts"),
]

# Use the Gemini 2.5 Flash model since it performs quickly
# and handles the processing well.
model = "gemini-2.5-flash"

# Create our agent
root_agent = Agent(
  name="deposit_agent",
  description="An agent that answers questions about your deposit accounts.",
  instruction=instruction,
  model=model,
  tools=tools,
  generate_content_config=types.GenerateContentConfig(
    http_options=types.HttpOptions(
      retry_options=types.HttpRetryOptions(initial_delay=1, attempts=5),
    )
  )
)
