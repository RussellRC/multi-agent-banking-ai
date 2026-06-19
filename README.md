# Udacity Google Agentic AI Engineer Nanodegree - Multi-Agent Systems with Google ADK and Vertex AI Project

This repository contains the implementation for the Project of **Course 4: Multi-Agent Systems with Google ADK and Vertex AI**,
of the **Udacity Google Agentic AI Engineer** Nanodegree program.

## Getting Started

### Dependencies

* **Python 3.14+**
* **venv**: For isolated virtual environment and dependency management
* **mcp-toolbox**: To create an MCP server to connect the agent to Cloud SQL Mysql 
* **Env Keys**:
  * GOOGLE_CLOUD_PROJECT 
  * GOOGLE_CLOUD_LOCATION: us-central1 
  * GOOGLE_APPLICATION_CREDENTIALS: service-account-key.json 
  * GOOGLE_GENAI_USE_VERTEXAI=true
  * DATASTORE_PROJECT_ID 
  * DATASTORE_ENGINE_ID 
  * CLOUD_SQL_MYSQL_PROJECT 
  * CLOUD_SQL_MYSQL_REGION: us-central1 
  * CLOUD_SQL_MYSQL_INSTANCE 
  * CLOUD_SQL_MYSQL_DATABASE 
  * CLOUD_SQL_MYSQL_USER 
  * CLOUD_SQL_MYSQL_PASSWORD
  * TOOLBOX_URL


### Prerequisites

* Install [mcp-toolbox](https://github.com/googleapis/mcp-toolbox) and add it to the Path
* Set up a MySQL instance in Cloud SQL.
  * Load the data from `desposit.sql` and `loan.sql`
  * Add a user with password (`CLOUD_SQL_MYSQL_USER` and `CLOUD_SQL_MYSQL_PASSWORD`)
  * Grant it privileges to the DB
* Create a bucket in Google Cloud Storage.
  * Upload the `.pdf` files
* Set up an AI Custom Search application in Vertex AI Discovery Engine and connect it to the bucket datastore.
* Set up a Service Account with the following permissions:
  * Agent Platform User
  * Cloud SQL Client
  * Cloud SQL Instance User
  * Discovery Engine User


### Installation

**1\. Clone the repository**

**2\. Install dependencies**\
Use `venv` to create a virtual environment and install the required packages.\
From the root project directory, run the following commands:
```shell
python -m venv .venv
.venv/bin/pip install -r solution/requirements.txt
```

**3\. Add a `.env` file** inside the `/solution` directory.\
File must have the following variables:
```dotenv
# GOOGLE CLOUD CONFIGURATION
GOOGLE_CLOUD_PROJECT: your-google-cloud-project-id
GOOGLE_CLOUD_LOCATION: your-google-cloud-location (e.g., us-central1)
GOOGLE_APPLICATION_CREDENTIALS: path-to-your-service-account-key.json
GOOGLE_GENAI_USE_VERTEXAI=true

# DATASTORE
DATASTORE_PROJECT_ID: your-datastore-project-id
DATASTORE_ENGINE_ID: your-ai-application-id

# CLOUD SQL MYSQL
CLOUD_SQL_MYSQL_PROJECT: your-cloud-sql-project-id
CLOUD_SQL_MYSQL_REGION: your-cloud-sql-region (e.g., us-central1)
CLOUD_SQL_MYSQL_INSTANCE: your-cloud-sql-instance-name
CLOUD_SQL_MYSQL_DATABASE: your-cloud-sql-database-name
CLOUD_SQL_MYSQL_USER: your-cloud-sql-user-name
CLOUD_SQL_MYSQL_PASSWORD: your-cloud-sql-password
```

## Project Structure

The project is organized into the following key directories and files:

* **`starter/`**: Contains the original, **untouched** starter template code.

* **`solution/`**: Houses the developed solution

* **`evidence/`**: Stores evidence of successful application executions and test runs.


## Running the application
1\. In a separate terminal window, run the mcp toolbox from within the `project/solution/` directory.
This will initialize the mcp-toolbox server with the default options using the `tools.yaml` as config file. 
```shell
toolbox
```

2\. From the root project directory, activate the environment with `venv`
```shell
source .venv/bin/activate
```

3\. Run the application using the Google Agent Development Kit (ADK)
```shell
adk web
```
This boots up a local development UI, typically accessible in your browser at http://localhost:8000

## Built With
* **Python**: Programming Language
* **Google ADK**: Agent Development Kit
* **Vertex AI Search**: Datastore for product and store information
* **mcp-toolbox**: MCP server implementation for Cloud SQL MySQL

## License
[LICENSE](./LICENSE)