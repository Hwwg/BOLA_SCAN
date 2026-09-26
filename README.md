# BOLASCAN

**BOLASCAN** is an automated testing tool for detecting BOLA (Broken Object Level Authorization) vulnerabilities in web applications. It leverages LLM-based API dependency analysis and multi-account testing matrices to identify horizontal privilege escalation issues.

## System Requirements

- **Python**: Version 3.8 or higher
- **Node.js**: Version 14.0 or higher
- **Operating System**: Linux, macOS, or Windows (Linux/macOS recommended)
- **Memory**: Minimum 4GB RAM recommended
- **Network**: Access to LLM API service (OpenAI or compatible)

## Installation

### 1. Prepare the Submitted Artifact

Extract the artifact archive provided with the submission and open a terminal in
its top-level directory, which contains `README.md`, `run_scan.py`, and
`requirements.txt`. Run the installation commands below from that directory.

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
Original annotation records from the two independent annotators are in
`evaluation/original_labeling/v1/` and `evaluation/original_labeling/v2/`.
Finding-level vulnerability records, masked CVE references, and vulnerability
validation labels are in `evaluation/per_project_vuln/`.

In [original_vul_validation_results.csv](evaluation/per_project_vuln/original_vul_validation_results.csv),
`label_result_v1` and `label_result_v2` record the independent judgments of
Annotator A and Annotator B, respectively. `label_result_v3` records the final
label after adjudication by a third annotator, Annotator C, who resolves
disagreements between the first two annotators. These columns represent human
annotation and adjudication, not LLM votes or experiment runs. Annotator identities
are anonymized for double-blind review.

A label of `1` means that the candidate finding is confirmed as a vulnerability;
`0` means that it is not confirmed. Of the 72 validation records, the final
adjudicated labels confirm 56 findings and reject 16. Before adjudication,
Annotators A and B agree on 68 of 72 records (94.44%; Cohen's kappa = 0.852),
with four disagreements. Agreement is computed from `label_result_v1` and
`label_result_v2`, not from either annotator's agreement with the adjudicated
labels.

The BOLASCAN per-finding request-response test records are in `evaluation/per_project_vuln/`:
[bola_horizontal_results.json](evaluation/per_project_vuln/bola_horizontal_results.json)
covers the 56 ground-truth findings, and
[bola_horizontal_results_fp.json](evaluation/per_project_vuln/bola_horizontal_results_fp.json)
covers the 16 false positives (`label_result_v3=0`).

[bola_horizontal_results_sidp_benchmark.json](evaluation/per_project_vuln/bola_horizontal_results_sidp_benchmark.json)
contains controlled cases with expected SIDP-related outcomes. In this file,
`BOLA Not Found (middle)` means that the candidate passed the rule-based checks
but was rejected at the LLM-based constrained semantic alignment stage because
the evidence did not support unauthorized access. Such records are excluded
from the final vulnerability findings; `middle` does not indicate vulnerability
severity or LLM confidence.

For this JSON file, `labeling.v3` validates the recorded conclusion: `1` confirms
that conclusion, rather than always indicating a vulnerability. Read it together
with `conclusion`, as follows:

| `conclusion` | `labeling.v3` | Count | Interpretation |
| --- | --- | --- | --- |
| `BOLA Found` | `1` | 3 | True positives reported by both configurations |
| `BOLA Not Found (middle)` | `1` | 4 | Rule-based false positives correctly rejected by constrained semantic alignment |
| `BOLA Found` | `0` | 1 | False positive retained by full BOLASCAN, also reported by the rule-based stage |

Precision is `TP / (TP + FP)`. The rule-based stage alone reports all eight
candidates, yielding `3 / (3 + 5) = 37.5%` precision. Full BOLASCAN excludes the
four `middle` records and reports four findings, yielding
`3 / (3 + 1) = 75%` precision. Both configurations retain the same three true
positives. Unlike `label_result_v3` in the validation CSV described above,
`labeling.v3=1` in this JSON does not by itself mean that a vulnerability exists.

Generated summaries, mismatch reports, and plots are written to
`evaluation/outputs/` by default and are intentionally ignored by Git.

```bash
python evaluation/eval_all_experiments.py --models gpt-4o-mini
```

## Sanitized Vulnerability Audit

The table below displays all fields from
[ground_truth_vuln.csv](evaluation/per_project_vuln/ground_truth_vuln.csv), including
`root cause`. CVE identifiers remain masked. Root causes are described according
to the Identifier Substitution Model. Counts refer to finding records, not distinct
CVEs. mall and mall-swarm are separate systems.

| id | Project | type | Api | parameters | status | CVE | root cause |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | TIME_SEA_PLUS | TIP | `POST /pay/alipay/status/{orderId}` | `orderId` | accepted | `CVE-2025-12***` | Payment-status lookup does not check whether the order selected by the substituted orderId is within the current requester's authorized scope. |
| 2 | newbee-mall-plus | SIDP-side | `POST /seckillExecution/24/15/1ff1de774005f8da13f42943881c655f` | `userId` | accepted | `CVE-2025-12***` | The purchasing user identified by userId is not bound to the current requester through an authorization check. With seckillId and md5 fixed, substituting userId allows a flash-sale purchase to be executed as another user. |
| 3 | mall-swarm | TIP | `POST /cart/update/attr` | `id` | accepted | `CVE-2025-13***` | Cart-item updates do not check whether the item selected by the substituted id belongs to the current requester. |
| 4 | mall-swarm | TIP | `GET /order/detail/{orderId}` | `orderId` | accepted | `CVE-2025-13***` | Order-detail lookup does not check whether the current requester is authorized to access the order selected by the substituted orderId. |
| 5 | mall-swarm | TIP | `POST /order/cancelOrder` | `orderId` | accepted | `CVE-2025-13***` | Before queuing an order cancellation, the handler does not check whether the current requester may cancel the order selected by the substituted orderId. |
| 6 | mall-swarm | TIP | `POST /order/cancelUserOrder` | `orderId` | accepted | `CVE-2025-13***` | Order cancellation does not check whether the order selected by the substituted orderId belongs to the current requester. |
| 7 | mall-swarm | TIP | `POST /order/paySuccess` | `orderId` | accepted | `CVE-2025-13***` | Before marking an order as paid, the handler does not check whether the current requester may modify the order selected by the substituted orderId and its payment status. |
| 8 | mall-swarm | TIP | `POST /member/readHistory/delete HTTP/1.1` | `ids` | accepted | `CVE-2025-14***` | Browsing-history deletion does not check whether each record selected by the substituted ids belongs to the current requester. |
| 9 | mall-swarm | TIP | `POST /member/address/update/{id}` | `memberId` | merged |  | Address updates check ownership of the original address but do not check whether the requester may reassign it to another member by substituting memberId. |
| 10 | youlai-mall | TIP | `GET /mall-ums/app-api/v1/addresses/{addressId}` | `addressId` | accepted | `CVE-2025-14***` | Shipping-address lookup does not check whether the current requester may access the address selected by the substituted addressId. |
| 11 | youlai-mall | SIDP-side | `PUT /mall-ums/app-api/v1/addresses/{addressId}` | `id` | merged |  | With the path addressId fixed, substituting only the body id still updates the address selected by the substituted id. The fixed addressId is ignored, and neither consistency between the two identifiers nor ownership of the target address is checked. |
| 12 | youlai-mall | TIP | `DELETE /mall-ums/app-api/v1/addresses/{ids}` | `ids` | merged |  | Shipping-address deletion does not check whether each address selected by the substituted ids belongs to the current requester. |
| 13 | youlai-mall | TIP | `GET /mall-ums/app-api/v1/members/{memberId}/openid` | `memberId` | accepted | `CVE-2025-14***` | Before returning a member's openid, the handler does not check whether the current requester may access the member information selected by the substituted memberId. |
| 14 | youlai-mall | TIP | `PUT /mall-ums/app-api/v1/members/{memberId}/balances/_deduct` | `memberId` | merged |  | The amount-based controller handler directly updates the balance of the account selected by the substituted memberId without checking the requester's authority over that account; it also lacks positive-amount validation, allowing negative amounts to increase the balance. |
| 15 | youlai-mall | TIP | `DELETE /mall-oms/app-api/v1/orders/{orderId}` | `orderId` | accepted | `CVE-2025-14***` | The deleteOrder handler checks that the selected order exists and is unpaid or canceled, but does not verify that the order selected by the substituted orderId belongs to the current requester before deleting it. |
| 16 | youlai-mall | TIP | `POST /mall-oms/app-api/v1/orders/payment` | `orderSn` | accepted | `CVE-2025-15***` | In the BALANCE payment branch, the substituted orderSn selects another user's order and causes that order owner's balance to be debited without checking the current requester's authority over the order or payment account. |
| 17 | youlai-mall | TIP | `GET /mall-ums/app-api/v1/members/openid/{openid}` | `openid` | accepted | `CVE-2025-14***` | Returning member authentication information selected by a substituted openid does not require a check of the current requester's authority to access that member's private information. |
| 18 | youlai-mall | TIP | `PUT /mall-ums/app-api/v1/members/{memberId}/balances/_deduct` | `memberId` | accepted | `CVE-2025-15***` | The balance-based service handler deducts the supplied balance from the account selected by the substituted memberId without checking whether the current requester is authorized to debit that account. |
| 19 | youlai-mall | TIP | `GET /mall-ums/app-api/v1/members/mobile/{mobile}` | `mobile` | accepted | `CVE-2025-15***` | Returning member authentication information selected by a substituted mobile does not require a check of the current requester's authority to access that member's private information. |
| 20 | youlai-mall | TIP | `POST /mall-ums/app-api/v1/members/openid/{openid}` | `openid` | merged |  | Returning member authentication information selected by a substituted openid does not require a check of the current requester's authority to access that member's private information. |
| 21 | youlai-mall | TIP | `DELETE /mall-oms/app-api/v1/orders/{orderId}` | `orderId` | merged |  | The deleteById handler directly removes the order selected by the substituted orderId through removeById, without checking order status or verifying that the order belongs to the current requester. |
| 22 | youlai-mall | TIP | `POST /mall-oms/app-api/v1/orders/payment` | `orderSn` | accepted | `CVE-2025-15***` | In the WX_JSAPI payment branch, the substituted orderSn selects another user's order and allows a third-party payment session to be initiated and payment information to be obtained without checking the current requester's authority over that order. |
| 23 | JeecgBoot | SIDP-side | `GET /sys/sysDepartRole/list?deptId` | `deptId` | accepted | `CVE-2025-15***` | Substituting only deptId still allows roles in the substituted department to be queried because the handler does not check whether that department is within the current requester's authorized scope. |
| 24 | JeecgBoot | SIDP-side | `GET /sys/sysDepartRole/getDeptRoleList?departId=...&userId=` | `departId` | accepted | `CVE-2025-15***` | With userId fixed, substituting only departId still returns roles for the substituted department. The fixed userId is ignored rather than used to constrain the query, and the current requester's authority to access that department is not checked. |
| 25 | JeecgBoot | SIDP-side | `GET /sys/sysDepartRole/getDeptRoleByUserId?userId=...&departId=...` | `userId; departId` | accepted | `CVE-2025-15***` | With userId fixed, substituting only departId still allows that user's roles in the substituted department to be queried. Although the query retains the user-role and role-department constraints, it does not check whether the current requester may access that user's role information in that department. |
| 26 | JeecgBoot | SIDP-side | `GET /sys/sysDepartRole/datarule/{permissionId}/{departId}/{roleId}` | `permissionId; departId; roleId` | accepted | `CVE-2025-15***` | With permissionId and roleId fixed, substituting only departId still returns the data-rule selections for the fixed role and permission because the handler does not check whether roleId belongs to the substituted department. |
| 27 | JeecgBoot | SIDP-side | `GET /sys/sysDepartPermission/datarule/{permissionId}/{departId}` | `permissionId; departId` | accepted | `CVE-2025-15***` | With permissionId fixed, substituting only departId still returns the permission's data rules even when that permission is not assigned to the substituted department. The permissionId-to-departId assignment is not enforced. |
| 28 | JeecgBoot | SIDP-side | `GET /sys/sysDepartPermission/list?departId=...&pageNo=1&pageSize=10` | `departId` | accepted | `CVE-2025-15***` | With pagination parameters unchanged, substituting only departId still allows the substituted department's permission list to be queried because the current requester's authority to access that department is not checked. |
| 29 | JeecgBoot | SIDP-side | `GET /sys/permission/queryDepartPermission?departId=...` | `departId` | accepted | `CVE-2025-15***` | Substituting only departId still allows the substituted department's permission information to be queried because the current requester's authority to access that department is not checked. |
| 30 | JeecgBoot | SIDP-side | `GET /sys/position/getPositionUserList?positionId=...&pageNo=1&pageSize=10` | `positionId` | accepted | `CVE-2025-15***` | With pagination parameters unchanged, substituting only positionId still allows the substituted position's user list to be queried because the handler does not check whether that position is within the current requester's authorized tenant or department scope. |
| 31 | JeecgBoot | SIDP-side | `POST /jeecgboot/sys/position/savePositionUser?positionId=...&userIds=...` | `positionId; userIds` | merged |  | With userIds fixed, substituting only positionId still allows those users to be added to the substituted position because the handler does not check whether the position and fixed users are within the organizational scope the current requester may manage. |
| 32 | JeecgBoot | SIDP-side | `DELETE /jeecgboot/sys/position/removePositionUser?positionId=...&userIds=...` | `positionId; userIds` | merged |  | With userIds fixed, substituting only positionId still allows existing associations between those users and the substituted position to be deleted because the handler does not check whether the current requester may remove those users from that position. |
| 33 | JeecgBoot | SIDP-side | `POST /jeecgboot/sys/sysDepartPermission/datarule` | `departId` | merged |  | With permissionId and dataRuleIds fixed, substituting only the body departId directly saves the fixed data rules whenever the substituted department already has the menu-permission assignment. The handler does not check whether the requester may manage that department or apply those rules within its scope. |
| 34 | JeecgBoot | SIDP-side | `POST /jeecgboot/sys/sysDepartRole/datarule` | `roleId` | merged |  | With permissionId and dataRuleIds fixed, substituting only the body roleId directly saves the fixed data rules whenever the substituted role already has the menu-permission assignment. The handler does not check whether the requester may manage that role or whether those rules are within the scope allowed for its department. |
| 35 | JeecgBoot | SIDP-side | `POST /jeecgboot/sys/sysDepartPermission/saveDeptRolePermission` | `roleId` | merged |  | With permissionIds and lastpermissionIds fixed, substituting only the body roleId still adds or removes permission assignments for the substituted role according to the differences between the two fixed lists. The handler does not check whether the requester may manage that role or whether those permissions are within its department's authorized scope. |
| 36 | gin_vue_admin | TIP | `GET /customer/customer` | `ID` | merged |  | Customer lookup does not check whether the customer selected by the substituted ID is within the data scope the current requester may access. |
| 37 | gin_vue_admin | TIP | `PUT /customer/customer` | `ID` | merged |  | Customer updates do not check whether the customer selected by the substituted ID is within the data scope the current requester may modify. |
| 38 | gin_vue_admin | TIP | `DELETE /customer/customer` | `ID` | merged |  | Customer deletion does not check whether the customer selected by the substituted ID is within the data scope the current requester may delete. |
| 39 | gin_vue_admin | TIP | `GET /sysOperationRecord/findSysOperationRecord` | `ID` | merged |  | Operation-log lookup does not check whether the record selected by the substituted ID is within the data scope the current requester may access. |
| 40 | gin_vue_admin | TIP | `POST /fileUploadAndDownload/editFileName` | `ID` | merged |  | File renaming does not check whether the file selected by the substituted ID is within the data scope the current requester may modify. |
| 41 | gin_vue_admin | TIP | `DELETE /sysOperationRecord/deleteSysOperationRecord` | `ID` | merged |  | Operation-log deletion does not check whether the record selected by the substituted ID is within the data scope the current requester may delete. |
| 42 | gin_vue_admin | TIP | `DELETE /sysOperationRecord/deleteSysOperationRecordByIds` | `ids` | merged |  | Batch deletion of operation logs does not check whether each record selected by the substituted ids is within the data scope the current requester may delete. |
| 43 | pybbs | TIP | `GET /api/user/{username}` | `username` | accepted | `CVE-2025-12***` | User-profile lookup by username lacks access controls for private fields such as tokens and contact information, exposing another user's private information in the returned profile. |
| 44 | openemr | TIP | `PUT /api/patient/{pid}/encounter/{eid}/soap_note/{sid}` | `sid` | merged |  | SOAP-note updates select the target solely by the substituted sid without checking whether the record belongs to the authorized pid and eid context in the request. |
| 45 | openemr | TIP | `PUT /api/patient/{pid}/encounter/{eid}/vital/{vid}` | `vid` | merged |  | Vital-sign updates do not check whether the record selected by the substituted vid belongs to the authorized pid and eid context in the request. |
| 46 | openemr | TIP | `DELETE /api/patient/{pid}/appointment/{eid}` | `eid` | merged |  | Appointment deletion selects the target solely by the substituted eid, ignoring the path pid and failing to check whether the appointment belongs to that patient. |
| 47 | openemr | TIP | `PUT /api/patient/{pid}/dental_issue/{did}` | `did` | merged |  | Dental-record updates do not check whether the object selected by the substituted did belongs to the authorized patient and dental-record scope in the request. |
| 48 | openemr | TIP | `PUT /api/patient/{pid}/surgery/{sid}` | `sid` | merged |  | Surgery-record updates do not check whether the object selected by the substituted sid belongs to the authorized patient and surgery-record scope in the request. |
| 49 | openemr | TIP | `PUT /api/patient/{puuid}/medical_problem/{muuid}` | `muuid` | merged |  | Medical-problem updates select the target solely by the substituted muuid, ignoring the path puuid and failing to check whether the record belongs to that patient. |
| 50 | mall | TIP | `POST /cart/update/attr HTTP/1.1` | `id` | merged |  | Cart-item updates do not check whether the item selected by the substituted id belongs to the current requester. |
| 51 | mall | TIP | `GET /order/detail/{orderId}` | `orderId` | merged |  | Order-detail lookup does not check whether the current requester is authorized to access the order selected by the substituted orderId. |
| 52 | mall | TIP | `POST /order/cancelOrder` | `orderId` | merged |  | Before queuing an order cancellation, the handler does not check whether the current requester may cancel the order selected by the substituted orderId. |
| 53 | mall | TIP | `POST /order/cancelUserOrder` | `orderId` | merged |  | Order cancellation does not check whether the order selected by the substituted orderId belongs to the current requester. |
| 54 | mall | TIP | `POST /order/paySuccess` | `orderId` | merged |  | Before marking an order as paid, the handler does not check whether the current requester may modify the order selected by the substituted orderId and its payment status. |
| 55 | mall | TIP | `POST /member/readHistory/delete HTTP/1.1` | `ids` | accepted | `CVE-2025-13***` | Browsing-history deletion does not check whether each record selected by the substituted ids belongs to the current requester. |
| 56 | mall | TIP | `POST /member/address/update/{id}` | `memberId` | accepted | `CVE-2025-15***` | Address updates check ownership of the original address but do not check whether the requester may reassign it to another member by substituting memberId. |

## Project Structure

```
BOLASCAN/
├── run_scan.py                         # Scan pipeline entry point
├── test_project/                       # Versions and API documents for 14 projects
│   ├── versions.md                     # Project version inventory
│   └── openapi/                        # OpenAPI/Swagger documents and mall Postman input
├── utils/                              # Dependency construction and BOLA detection
│   ├── dependency_cc/                  # Executable dependency chain construction
│   │   ├── main.py                     # Dependency construction entry point
│   │   └── src/                        # Dependency construction algorithms
│   └── bola_vulner/
│       └── horizontal/                 # Substitution probes and evidence-based judgment
├── scripts/                            # Pipeline execution and preprocessing helpers
│   ├── api_doc.py                      # API documentation processing
│   ├── jsontools.py                    # JSON processing utilities
│   └── refine_api_groups.py            # API functional grouping refinement
├── evaluation/                         # Evaluation code, labels, and vulnerability records
│   ├── eval_all_experiments.py          # Evaluation entry point
│   ├── manual_label_*.csv              # Final grouping, type, mapping, and CADS pair labels
│   ├── identifier_parameters_all_projects.csv  # Final identifier parameter labels
│   ├── original_labeling/              # Original independent annotation records
│   │   ├── v1/                         # First annotator: grouping, types, mappings, identifiers, CADS
│   │   └── v2/                         # Second annotator: same annotation tasks
│   └── per_project_vuln/               # Finding-level records and vulnerability validation labels
│       ├── ground_truth_vuln.csv       # 56 records with status, masked CVE references, and root causes
│       ├── original_vul_validation_results.csv  # 72 records with v1/v2/v3 judgment labels
│       ├── bola_horizontal_results.json     # Request-response test records for 56 findings
│       └── bola_horizontal_results_fp.json  # Request-response test records for 16 false positives
├── prompt/                             # LLM prompt templates and output schemas
├── gptreply/                           # LLM request, response parsing, and retry handling
│   └── gpt_con.py                      # LLM client wrapper
├── automated_click/                    # Browser automation and request collection
│   ├── index.js                        # Browser automation entry point
│   ├── modules/                        # Browser interaction modules
│   └── llm/                            # LLM-assisted browser interaction
├── cache_gpt-4o-mini/                   # Sanitized sample pipeline artifacts
│   └── crapi/                          # crAPI requests, responses, and detector decisions
├── project.example.json                # Local project configuration template
└── requirements.txt                    # Python dependencies
```

## Artifacts

The following checklist locates the materials provided for artifact assessment.
A check mark indicates that the material is included. Findings refer to the 56
manually confirmed vulnerabilities; the validation set also contains 16 false
positives. Finding records can be located by project and finding ID, with the API,
identifier parameter, and branch context described in the finding record and its
`root cause`. CVE identifiers remain masked to preserve double-blind review and
support responsible disclosure.

Within each project, findings are deduplicated by **API–parameter–branch**. The
API includes the HTTP method and normalized route; the parameter is the tested
identifier parameter; and the branch is the authorization-relevant implementation
or business branch described in the root cause, such as `BALANCE` versus
`WX_JSAPI` payment. Repeated detections of the same combination across the five
runs are merged into one finding. Aggregate vulnerability counts use the union of
validated findings across runs. Multiple findings may share one CVE, so findings
and distinct CVEs are counted separately.

| Required material | Provided | File location |
| --- | :---: | --- |
| Project, API, parameters, vulnerability type, and status for all 56 findings | ✓ | [ground_truth_vuln.csv](evaluation/per_project_vuln/ground_truth_vuln.csv); [Sanitized Vulnerability Audit](#sanitized-vulnerability-audit). |
| Sanitized request and response records for each finding | ✓ | [bola_horizontal_results.json](evaluation/per_project_vuln/bola_horizontal_results.json), containing records for all 56 findings. |
| Root causes and review notes for each finding | ✓ | The `root cause` field in [ground_truth_vuln.csv](evaluation/per_project_vuln/ground_truth_vuln.csv), together with the corresponding evidence in [bola_horizontal_results.json](evaluation/per_project_vuln/bola_horizontal_results.json). |
| Anonymized CVE information and finding-to-CVE correspondence for the 27 CVEs | ✓ | [ground_truth_vuln.csv](evaluation/per_project_vuln/ground_truth_vuln.csv); [bola_horizontal_results.json](evaluation/per_project_vuln/bola_horizontal_results.json). Locate the corresponding records by finding ID or by API, parameter, and branch within each project. |
| Number of annotators, independent labeling, and third-annotator adjudication procedure | ✓ | [Evaluation](#evaluation). |
| Original records from the two independent annotators | ✓ | [original_labeling/v1/](evaluation/original_labeling/v1/), [original_labeling/v2/](evaluation/original_labeling/v2/), and [original_vul_validation_results.csv](evaluation/per_project_vuln/original_vul_validation_results.csv). |
| Inter-annotator agreement metrics | ✓ | [Evaluation](#evaluation) reports vulnerability-judgment agreement and Cohen's kappa; the two [original annotation sets](evaluation/original_labeling/) support recomputation for the other labeling tasks. |
| True-positive, false-positive, and precision accounting data | ✓ | [original_vul_validation_results.csv](evaluation/per_project_vuln/original_vul_validation_results.csv): 56 true positives and 16 false positives across 72 records, yielding 77.78% precision. |
| LLM misjudgments (false positives) and human validation data | ✓ | [bola_horizontal_results_fp.json](evaluation/per_project_vuln/bola_horizontal_results_fp.json); [original_vul_validation_results.csv](evaluation/per_project_vuln/original_vul_validation_results.csv). |
| Full prompt templates | ✓ | [Dependency-chain prompts](prompt/dependency_chain/prompt_content.py); [vulnerability-judgment prompts](prompt/bola_vulner/prompt_content.py). |
| LLM output formats and examples | ✓ | [Example Output](#example-output); [horizontal_vuln.py](utils/bola_vulner/horizontal/horizontal_vuln.py). |
| LLM configuration and voting parameter k | ✓ | [run_scan.py](run_scan.py), [project.example.json](project.example.json), and [horizontal_vuln.py](utils/bola_vulner/horizontal/horizontal_vuln.py). Current final-judge defaults are `k=3` and temperature `0`. |
| Failure handling and retry policies | ✓ | [gpt_con.py](gptreply/gpt_con.py); [horizontal_vuln.py](utils/bola_vulner/horizontal/horizontal_vuln.py). |
| Tested project versions | ✓ | [test_project/versions.md](test_project/versions.md). |
| API documents used for each project | ✓ | [test_project/openapi/](test_project/openapi/), covering all 14 projects, including mall's Postman input and Swagger reference. |

Final human labels for functional grouping, operation types, parameter mappings,
CADS dependency pairs, and identifier parameters are also available in
[evaluation/](evaluation/). A sanitized crAPI pipeline sample, including collected
requests, account execution records, and detector decisions, is available in
[cache_gpt-4o-mini/crapi/](cache_gpt-4o-mini/crapi/).

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
