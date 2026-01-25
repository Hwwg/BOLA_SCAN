# BOLASCAN

**BOLASCAN** is an automated testing tool for detecting BOLA (Broken Object Level Authorization) vulnerabilities in web applications. It leverages LLM-based API dependency analysis and multi-account testing matrices to identify horizontal privilege escalation issues.

## Features

- **Automated API Analysis**: Automatically analyzes OpenAPI specifications and tags API operations
- **LLM-Powered Dependency Chain Construction**: Uses Large Language Models to infer API dependencies and data flows
- **Parameter Normalization**: Intelligently extracts and normalizes API parameters across different endpoints
- **Test Case Generation**: Combines dependency chains with browser interaction data to generate realistic test cases
- **Horizontal BOLA Detection**: Employs multi-account testing matrices to detect cross-user access control vulnerabilities
- **Web Automation**: Built-in Puppeteer-based module for automated web interaction and HTTP traffic capture

## System Requirements

- **Python**: Version 3.8 or higher
- **Node.js**: Version 14.0 or higher
- **Operating System**: Linux, macOS, or Windows (Linux/macOS recommended)
- **Memory**: Minimum 4GB RAM recommended
- **Network**: Access to LLM API service (OpenAI or compatible)

## Installation

### 1. Clone the Repository

```bash
git clone https://github.com/Hwwg/BOLA_SCAN.git
cd BOLA_SCAN
```

### 2. Install Python Dependencies

```bash
pip install -r requirements.txt
```

Main dependencies:
- `openai>=1.0.0` - For LLM API calls
- `requests>=2.31.0` - For HTTP request sending
- `urllib3>=2.0.0` - For URL processing

### 3. Install Node.js Dependencies

```bash
cd automated_click
npm install
cd ..
```

### 4. Install Global Tools

```bash
npm install -g openapi2postmanv2
```

This tool converts OpenAPI specifications to Postman format.

## Configuration

### 1. Configure LLM API Key

Set the following environment variables (recommended to add to `.bashrc` or `.zshrc`):

```bash
export BOLASCAN_LLM_API_KEY="your-openai-api-key"
export BOLASCAN_LLM_BASE_URL="https://api.openai.com/v1"  # Optional, for custom LLM service
```

### 2. Configure Test Parameters

Edit `utils/dependency_cc/main.py` (lines 182-203) to configure:

```python
# Specify the project name to test
project_name = "crapi"  # Change to your project name

# Configure target application URL
url = "http://your-target-app-url:port/"

# Configure authentication for two test accounts
auth_type = {
    "test_account": {
        "auth": {
            "authorization": "Bearer your-test-account-token"
        }
    },
    "data_account": {
        "auth": {
            "authorization": "Bearer your-data-account-token"
        }
    }
}
```

### 3. Prepare OpenAPI Specification

Place the target application's OpenAPI specification file (JSON or YAML format) in `cache/{project_name}/` directory, named as `{project_name}_openapi.json`.

## Usage

### Run Complete Detection Workflow

```bash
python utils/dependency_cc/main.py
```

The tool will automatically execute the following steps:

1. **API Type Tagging**: Analyzes OpenAPI specification and tags each API operation type (CRUD, etc.)
2. **Parameter Normalization**: Extracts and normalizes API parameters
3. **Dependency Chain Construction**: Uses LLM inference to build API dependency relationships
4. **Test Case Generation**: Combines dependency chains with browser click data to generate test requests
5. **Horizontal BOLA Detection**: Uses multi-account testing matrix to detect cross-user access control vulnerabilities

### View Detection Results

After execution, results will be saved at:

- **Main Results**: `cache/{project_name}/bola_horizontal_results.json`
  - Contains all detected BOLA vulnerability information
  - Includes vulnerability severity assessment and exploitation methods

- **Detailed Logs**: `cache/{project_name}/horizontal_results/`
  - `all_acount_execution_results.json` - Execution results for all accounts
  - `execution_progress.json` - Execution progress statistics
  - `judgement_progress.json` - Vulnerability judgment progress
  - `llm_usage.json` - LLM usage statistics

## Test Dataset

You can use the following open-source web applications for validation:

- **crAPI**: https://github.com/OWASP/crAPI

Sample test data (OpenAPI specifications, browser click logs) are included in the `cache/` and `automated_click/` directories for experiment reproduction.

## Project Structure

```
BOLASCAN/
├── utils/                      # Core detection modules
│   ├── dependency_cc/          # Dependency chain construction
│   │   ├── main.py            # Main entry point
│   │   └── src/               # Core algorithms
│   └── bola_vulner/           # BOLA vulnerability detection
│       └── horizontal/        # Horizontal privilege escalation detection
├── scripts/                    # Utility scripts
│   ├── api_doc.py             # API documentation processing
│   ├── jsontools.py           # JSON utilities
│   └── refine_api_groups.py   # API grouping refinement
├── prompt/                     # Prompt templates for LLM
├── gptreply/                   # LLM interface wrapper
│   └── gpt_con.py             # GPT connection handler
├── automated_click/            # Web automation module
│   ├── index.js               # Main automation script
│   ├── modules/               # Automation modules
│   └── llm/                   # LLM integration for automation
├── cache/                      # Test data and results cache
│   └── crapi/                 # crAPI test data
└── requirements.txt            # Python dependencies
```

## Example Output

Detection result JSON format (simplified example from `cache/crapi/bola_horizontal_results.json`):

```json
{
  "resource_id": {
    "identity/api/v2/vehicle": {
      "vehicleId": {
        "cross": [
          {
            "GET:/identity/api/v2/vehicle/{vehicleId}/location": {
              "conclusion": "BOLA Found",
              "reason": "The Attacker was able to access the Victim's vehicle location data without permission.",
              "test_type": {
                "category": "resource_id",
                "case_type": "overprivilege",
                "position_mode": "single",
                "value_source": "A",
                "strategy": "BOLA_SingleLoc_Query_Target:A",
                "group_name": "identity/api/v2/vehicle",
                "param_name": "vehicleId"
              },
              "details": {
                "data": {
                  "request_params": {
                    "method": "GET",
                    "url": "http://10.15.196.160:8888/identity/api/v2/vehicle/4bae9968-ec7f-4de3-a3a0-ba1b2ab5e5e5/location"
                  },
                  "response_params": {
                    "carId": "4bae9968-ec7f-4de3-a3a0-ba1b2ab5e5e5",
                    "vehicleLocation": {
                      "latitude": "37.746880",
                      "longitude": "-84.301460"
                    }
                  }
                },
                "test": {
                  "request_params": {
                    "method": "GET",
                    "url": "http://10.15.196.160:8888/identity/api/v2/vehicle/4bae9968-ec7f-4de3-a3a0-ba1b2ab5e5e5/location"
                  },
                  "response_params": {
                    "carId": "4bae9968-ec7f-4de3-a3a0-ba1b2ab5e5e5",
                    "vehicleLocation": {
                      "latitude": "37.746880",
                      "longitude": "-84.301460"
                    }
                  }
                }
              }
            }
          }
        ]
      }
    }
  }
}
```