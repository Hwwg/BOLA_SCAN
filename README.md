# BOLASCAN

**BOLASCAN** is an automated testing tool for detecting BOLA (Broken Object Level Authorization) vulnerabilities in web applications. It leverages LLM-based API dependency analysis and multi-account testing matrices to identify horizontal privilege escalation issues.

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

`run_scan.py` also recognizes provider-specific keys such as `OPENAI_API_KEY`,
`DEEPSEEK_API_KEY`, and `DASHSCOPE_API_KEY` for the built-in model adapters.

### 2. Create Local `project.json`

Runtime target URLs, credentials, account tokens, and click-collection settings are
loaded from `project.json`. This file is intentionally ignored by Git because it
usually contains secrets.

Start from the template:

```bash
cp project.example.json project.json
```

Then edit `project.json`:

```json
{
  "projects": {
    "crapi": {
      "project_name": "crapi",
      "model": "gpt-4o-mini",
      "openapi_doc": "cache_gpt-4o-mini/crapi/crapi_openapi.json",
      "url": "http://your-target-app/",
      "login_url": "http://your-target-app/",
      "click_account": {
        "username": "attacker@example.com",
        "password": "replace-with-password",
        "fallback_auth": {
          "token": "replace-with-token",
          "token_header": "Authorization",
          "token_prefix": "Bearer "
        }
      },
      "test_account": {
        "auth": {
          "Authorization": "Bearer replace-with-attacker-token"
        }
      },
      "data_account": {
        "auth": {
          "Authorization": "Bearer replace-with-victim-token"
        }
      }
    }
  }
}
```

### 3. Prepare OpenAPI Specification

Place the target application's OpenAPI specification file in the selected cache
directory and point `openapi_doc` to it. For model-specific runs, BOLASCAN uses
`cache_<model>/<project>/` by default, for example:

```text
cache_gpt-4o-mini/crapi/crapi_openapi.json
```

This repository includes a sanitized crAPI sample cache under
`cache_gpt-4o-mini/crapi/` for reproducing pipeline outputs without committing
live credentials.

## Usage

### Run Complete Detection Workflow

```bash
python run_scan.py --project crapi --bola-only
```

The tool will automatically execute the following steps:

1. **API Type Tagging**: Analyzes OpenAPI specification and tags each API operation type (CRUD, etc.)
2. **Parameter Normalization**: Extracts and normalizes API parameters
3. **Dependency Chain Construction**: Uses LLM inference to build API dependency relationships
4. **Test Case Generation**: Combines dependency chains with browser click data to generate test requests
5. **Horizontal BOLA Detection**: Uses multi-account testing matrix to detect cross-user access control vulnerabilities

Common partial-run modes:

```bash
# Only collect browser-click traffic into the project cache
python run_scan.py --project crapi --collect-only

# Build CADS/dependency-chain artifacts without running the final BOLA test
python run_scan.py --project crapi --depen-gen --model gpt-4o-mini

# Reuse existing horizontal execution evidence and rerun only final semantic judgment
python run_scan.py --project crapi --horizontal-judgement-only --model gpt-4o-mini

# Run batch dependency generation across configured projects
python scripts/batch_depen_gen.py --model gpt-4o-mini --parallel-projects 3
```

### View Detection Results

After execution, results will be saved at:

- **Main Results**: `cache_<model>/{project_name}/bola_horizontal_results.json`
  - Contains all detected BOLA vulnerability information
  - Includes vulnerability severity assessment and exploitation methods

- **Detailed Logs**: `cache_<model>/{project_name}/horizontal_results/`
  - `all_acount_execution_results.json` - Execution results for all accounts
  - `execution_progress.json` - Execution progress statistics
  - `judgement_progress.json` - Vulnerability judgment progress
  - `llm_usage.json` - LLM usage statistics

## Test Dataset

You can use the following open-source web applications for validation:

- **crAPI**: https://github.com/OWASP/crAPI

Sanitized crAPI artifacts are included in `cache_gpt-4o-mini/crapi/` for
experiment reproduction. Runtime credentials and live target URLs should stay in
local `project.json`, not in Git.

## Evaluation

The evaluation entry point and labeled test sets live in `evaluation/`.
Generated summaries, mismatch reports, and plots are written to
`evaluation/outputs/` by default and are intentionally ignored by Git.

```bash
python evaluation/eval_all_experiments.py --models gpt-4o-mini
```

## Project Structure

```
BOLASCAN/
├── utils/                      # Core detection modules
│   ├── dependency_cc/          # Dependency chain construction
│   │   ├── main.py            # Main entry point
│   │   └── src/               # Core algorithms
│   └── bola_vulner/           # BOLA vulnerability detection
│       └── horizontal/        # Horizontal privilege escalation detection
├── scripts/                    # Runtime and dependency-generation scripts
│   ├── api_doc.py             # API documentation processing
│   ├── jsontools.py           # JSON utilities
│   └── refine_api_groups.py   # API grouping refinement
├── evaluation/                 # Evaluation entry point and labeled test sets
│   ├── eval_all_experiments.py
│   └── manual_label_*.csv
├── prompt/                     # Prompt templates for LLM
├── gptreply/                   # LLM interface wrapper
│   └── gpt_con.py             # GPT connection handler
├── automated_click/            # Web automation module
│   ├── index.js               # Main automation script
│   ├── modules/               # Automation modules
│   └── llm/                   # LLM integration for automation
├── cache_gpt-4o-mini/          # Sanitized sample cache for gpt-4o-mini
│   └── crapi/                 # crAPI sample artifacts
├── project.example.json        # Local project.json template
└── requirements.txt            # Python dependencies
```
 
## Example Output

Detection result JSON format (simplified example from `cache_gpt-4o-mini/crapi/bola_horizontal_results.json`):

```json
  {
                        "GET:/workshop/api/mechanic/mechanic_report": {
                            "conclusion": "BOLA Found",
                            "reason": "The evidence shows that the attacker successfully retrieved data for the victim-owned object identified by report_id '1', indicating unauthorized access.",
                            "evidence_semantic": {
                                "structured_evidence": {
                                    "user_api": {
                                        "method": "GET",
                                        "route": "/workshop/api/mechanic/mechanic_report",
                                        "api_key": "GET:/workshop/api/mechanic/mechanic_report",
                                        "operation_type": "query"
                                    },
                                    "tested_identifier_parameter": "report_id",
                                    "identifier_category": "resource_id",
                                    "probing_strategy": {
                                        "case_type": "overprivilege",
                                        "strategy": "BOLA_SingleLoc_Query_Target:A",
                                        "position_mode": "single",
                                        "target_position": null,
                                        "value_source": "A",
                                        "comparison_source": null,
                                        "non_target_source": null
                                    },
                                    "identifier_values": {
                                        "injected_victim_or_container_value": "1",
                                        "victim_reference_value": "1",
                                        "attacker_own_value": null,
                                        "container_a_value": null,
                                        "container_b_or_comparison_value": null,
                                        "param_values_by_location": {
                                            "path": null,
                                            "query": "1",
                                            "body": null,
                                            "header": null
                                        },
                                        "param_sources_by_location": {
                                            "query": "A"
                                        },
                                        "alias_values": {
                                            "report_id": {
                                                "position": "query",
                                                "value": "1"
                                            }
                                        },
                                        "alias_sources": {
                                            "report_id": "A"
                                        }
                                    },
                                    "attacker_observation": {
                                        "request_params": {
                                            "method": "GET",
                                            "url": "http://example.test/workshop/api/mechanic/mechanic_report",
                                            "params": {
                                                "report_id": "1"
                                            },
                                            "json": {},
                                            "data": {},
                                            "files": {}
                                        },
                                        "response_params": {
                                            "id": 1,
                                            "mechanic": {
                                                "id": 1,
                                                "mechanic_code": "TRAC_JHN",
                                                "user": {
                                                    "email": "jhon@example.com",
                                                    "number": ""
                                                }
                                            },
                                            "vehicle": {
                                                "id": 5,
                                                "vin": "6NBBY70FWUM324316",
                                                "owner": {
                                                    "email": "admin@example.com",
                                                    "number": "9010203040"
                                                }
                                            },
                                            "problem_details": "My car Audi - RS7 is having issues.\nCan you give me a call on my mobile 9010203040,\nOr send me an email at admin@example.com\nThanks,\nAdmin.\n",
                                            "status": "cancelled",
                                            "created_on": "29 April, 2026, 07:17:42",
                                            "updated_on": null,
                                            "comments": []
                                        },
                                        "execution_status": {
                                            "api_key": "GET:/workshop/api/mechanic/mechanic_report",
                                            "status": "success",
                                            "status_code": 200,
                                            "request_url": "http://example.test/workshop/api/mechanic/mechanic_report",
                                            "request_data": {
                                                "report_id": "1"
                                            }
                                        },
                                        "business_code": "cancelled"
                                    },
                                    "victim_or_control_observation": {
                                        "request_params": {
                                            "method": "GET",
                                            "url": "http://example.test/workshop/api/mechanic/mechanic_report",
                                            "params": {
                                                "report_id": "1"
                                            },
                                            "json": {},
                                            "data": {},
                                            "files": {}
                                        },
                                        "response_params": {
                                            "id": 1,
                                            "mechanic": {
                                                "id": 1,
                                                "mechanic_code": "TRAC_JHN",
                                                "user": {
                                                    "email": "jhon@example.com",
                                                    "number": ""
                                                }
                                            },
                                            "vehicle": {
                                                "id": 5,
                                                "vin": "6NBBY70FWUM324316",
                                                "owner": {
                                                    "email": "admin@example.com",
                                                    "number": "9010203040"
                                                }
                                            },
                                            "problem_details": "My car Audi - RS7 is having issues.\nCan you give me a call on my mobile 9010203040,\nOr send me an email at admin@example.com\nThanks,\nAdmin.\n",
                                            "status": "cancelled",
                                            "created_on": "29 April, 2026, 07:17:42",
                                            "updated_on": null,
                                            "comments": []
                                        },
                                        "execution_status": {
                                            "api_key": "GET:/workshop/api/mechanic/mechanic_report",
                                            "status": "success",
                                            "status_code": 200,
                                            "request_url": "http://example.test/workshop/api/mechanic/mechanic_report",
                                            "request_data": {
                                                "report_id": "1"
                                            }
                                        },
                                        "business_code": "cancelled"
                                    },
                                    "follow_up_evidence": {},
                                    "evidence_features": {
                                        "attacker_status_code": 200,
                                        "victim_status_code": 200,
                                        "attacker_status": "success",
                                        "victim_status": "success",
                                        "response_parameter_names_match": true,
                                        "victim_response_param_names": [
                                            "comments",
                                            "created_on",
                                            "id",
                                            "mechanic",
                                            "problem_details",
                                            "status",
                                            "updated_on",
                                            "vehicle"
                                        ],
                                        "attacker_response_param_names": [
                                            "comments",
                                            "created_on",
                                            "id",
                                            "mechanic",
                                            "problem_details",
                                            "status",
                                            "updated_on",
                                            "vehicle"
                                        ],
                                        "has_follow_up_evidence": false,
                                        "has_attacker_response_payload": true,
                                        "has_victim_response_payload": true
                                    }
                                },
                                "unauthorized_access_question": "Does the evidence show that the attacker, using attacker credentials, accessed or operated on the victim-owned object identified by injected identifier value `1` through `report_id` on `GET:/workshop/api/mechanic/mechanic_report`? For this query/list-query operation, focus on whether the attacker response reveals victim-owned object data or object-specific state for the injected identifier.",
                                "llm_decision": {
                                    "is_public_resource_or_parameter": false,
                                    "public_reason": "",
                                    "evidence_matches_unauthorized_semantics": true,
                                    "confidence": "high",
                                    "matched_evidence": [
                                        "The attacker accessed the report with ID 1, which is owned by the victim, and the response includes details specific to that report."
                                    ],
                                    "reason": "The evidence shows that the attacker successfully retrieved data for the victim-owned object identified by report_id '1', indicating unauthorized access."
                                }
                            },
                            "test_type": {
                                "category": "resource_id",
                                "case_type": "overprivilege",
                                "position_mode": "single",
                                "value_source": "A",
                                "strategy": "BOLA_SingleLoc_Query_Target:A",
                                "group_name": "other/mechanic",
                                "param_name": "report_id",
                                "target_position": null,
                                "param_sources": {
                                    "query": "A"
                                },
                                "param_values": {
                                    "path": null,
                                    "query": "1",
                                    "body": null,
                                    "header": null
                                },
                                "param_alias_sources": {
                                    "report_id": "A"
                                },
                                "param_alias_values": {
                                    "report_id": {
                                        "position": "query",
                                        "value": "1"
                                    }
                                }
                            },
                            "api_info": {
                                "data": {
                                    "method": "GET",
                                    "route": "/workshop/api/mechanic/mechanic_report"
                                },
                                "test": {
                                    "method": "GET",
                                    "route": "/workshop/api/mechanic/mechanic_report"
                                }
                            },
                            "details": {
                                "data": {
                                    "request_params": {
                                        "method": "GET",
                                        "url": "http://example.test/workshop/api/mechanic/mechanic_report",
                                        "params": {
                                            "report_id": "1"
                                        },
                                        "json": {},
                                        "data": {},
                                        "files": {}
                                    },
                                    "response_params": {
                                        "id": 1,
                                        "mechanic": {
                                            "id": 1,
                                            "mechanic_code": "TRAC_JHN",
                                            "user": {
                                                "email": "jhon@example.com",
                                                "number": ""
                                            }
                                        },
                                        "vehicle": {
                                            "id": 5,
                                            "vin": "6NBBY70FWUM324316",
                                            "owner": {
                                                "email": "admin@example.com",
                                                "number": "9010203040"
                                            }
                                        },
                                        "problem_details": "My car Audi - RS7 is having issues.\nCan you give me a call on my mobile 9010203040,\nOr send me an email at admin@example.com\nThanks,\nAdmin.\n",
                                        "status": "cancelled",
                                        "created_on": "29 April, 2026, 07:17:42",
                                        "updated_on": null,
                                        "comments": []
                                    }
                                },
                                "test": {
                                    "request_params": {
                                        "method": "GET",
                                        "url": "http://example.test/workshop/api/mechanic/mechanic_report",
                                        "params": {
                                            "report_id": "1"
                                        },
                                        "json": {},
                                        "data": {},
                                        "files": {}
                                    },
                                    "response_params": {
                                        "id": 1,
                                        "mechanic": {
                                            "id": 1,
                                            "mechanic_code": "TRAC_JHN",
                                            "user": {
                                                "email": "jhon@example.com",
                                                "number": ""
                                            }
                                        },
                                        "vehicle": {
                                            "id": 5,
                                            "vin": "6NBBY70FWUM324316",
                                            "owner": {
                                                "email": "admin@example.com",
                                                "number": "9010203040"
                                            }
                                        },
                                        "problem_details": "My car Audi - RS7 is having issues.\nCan you give me a call on my mobile 9010203040,\nOr send me an email at admin@example.com\nThanks,\nAdmin.\n",
                                        "status": "cancelled",
                                        "created_on": "29 April, 2026, 07:17:42",
                                        "updated_on": null,
                                        "comments": []
                                    }
                                }
                            }
                        }
                    }
```
