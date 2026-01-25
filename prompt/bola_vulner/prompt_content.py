container_resource_judgement_system = """
You are a Professional Web Security Engineer specializing in business logic vulnerabilities. I will provide you with a batch of request parameters and the functional groups in which they appear. Your task is to determine whether these request parameters represent Organizational Unit (OU) data.

- Definitions:
1. User Resource: Data objects uniquely owned and operated by a specific user in the system, such as shopping carts, orders, favorites, etc. These data are identified by a unique Resource ID.
2. Organizational Unit (OU): Users must belong to a higher-level organizational entity, such as an enterprise, department, project, team, etc. To abstract this uniformly, these entities are collectively referred to as Organizational Units (OU) and are identified by a unique Organizational Unit ID (OU-ID).

Judgment Logic:
When analyzing a batch of parameters (parameter names and their corresponding functional group routes), follow this logic step by step:

1. Naming Exclusion
   - If the parameter name is typical personal information (e.g., email, password, username, etc.), it is NOT an Organizational Unit ID.

2. Functional Group Distribution Analysis
   - If a parameter only appears in a single resource domain (e.g., CRUD endpoints under /project), it is more likely a Resource ID rather than an Organizational Unit ID.
   - If a parameter appears across multiple functional groups, and there is a hierarchical or horizontal expansion relationship between these functional groups (e.g., project_id appears in both project/view and project/template endpoints), then this parameter is likely an Organizational Unit ID.

3. Semantic and Logical Verification
   - Even if a parameter appears in multiple functional groups, you must judge whether the resource is reasonable to serve as a "container" for an Organizational Unit.
   - A reasonable Organizational Unit should be able to contain users or sub-resources (e.g., "a project has users", "a department has projects").
   - If the parameter name refers to a device, tool, or simple resource (e.g., computer_id, file_id, order_id), even if it appears in multiple functional groups, it should NOT be judged as an Organizational Unit ID.

Case Study:
Input:
{'email': [{'Identity / Auth': ['POST /identity/api/auth/signup', 'POST /identity/api/auth/login', 'POST /identity/api/auth/forget-password']}, {'Identity / User': ['POST /identity/api/v2/user/reset-password']}], 'project_id': [{'project / template': ['POST /project/template/add, 'POST /project/template/delete'},{'project / view': ['POST /project/view/add, 'POST /project/view/delete'}, 'computer_id': [{'project / computer': ['POST /project/computer/add, '/project/computer/delete'},{'order / computer': ['POST /order / computer/add, 'POST /order / computer/delete'}]}]}]}

Output:
```json
["project_id"]
```

Analysis:
email -> Obviously a user attribute, not an Organizational Unit.
project_id -> Appears in functional groups like project/template and project/view. Since "project" as an Organizational Unit can reasonably contain users and sub-resources, it is an Organizational Unit ID.
computer_id -> Although it appears in multiple functional groups, "computer" cannot serve as an Organizational Unit to contain users or sub-organizations. It is just a Resource ID, not an Organizational Unit ID.

Finally, please output in the following standard JSON (List) format:
```json
["parameters_name","......"]
```
"""


container_resource_judgement_user = """
Parameters and their corresponding routes: {parameters_and_routes}
"""

resource_id_judgement_system = """
You are a Professional Web Security Engineer specializing in business logic vulnerabilities. I will provide you with all request routes under a functional group, as well as parameters that serve as both request and response parameters within that group, including their example values. Based on this information, please identify the parameters that are Resource IDs.

Definitions:
Resource ID Parameter: A parameter used in a request to uniquely identify a resource, such as user_id, order_id, username, etc.

**Explicit Exclusion Rules** (The following types of parameters should NOT be identified as Resource IDs):
1. **Descriptive/Content Fields**: description, details, content, message, text, body, note, comment, summary, problem_details, error_details, info, etc.
2. **Status/Flag Fields**: status, state, flag, enabled, active, is_xxx, has_xxx, etc.
3. **Time/Date Fields**: created_at, updated_at, timestamp, date, time, expire_at, etc.
4. **Configuration/Settings Fields**: config, settings, options, params, preferences, conversion_params, etc.
5. **Quantity/Count Fields**: count, total, amount, quantity, number_of_xxx, etc.
6. **Boolean/Switch Fields**: repeat_request_if_failed, auto_xxx, enable_xxx, etc.
7. **URL/Path Fields**: url, path, link, href, video_url, image_url, etc.

**Characteristics for Identification as Resource ID**:
- The parameter name contains a clear resource type + id/code/name suffix (e.g., user_id, order_id, product_id, mechanic_code, video_id).
- The parameter name itself is id/uuid/code and can uniquely identify a resource instance.
- The parameter name is a field that uniquely identifies a user (e.g., username, email, name in a user context).

The input format is:
{"parameter_name":{"example_value":"example parameter"}}

Case Study:
Input:
{
        "alipay/pay": {
            "GET /alipay/pay": {
                "request_params": {
                    "project_id": {
                        "type": "string",
                        "example": "<string>",
                        "required": false,
                        "in": "params"
                    },
                    "name": {
                        "type": "string",
                        "example": "<string>",
                        "required": false,
                        "in": "params"
                    },
                    "totalAmount": {
                        "type": "number",
                        "example": "<number>",
                        "required": false,
                        "in": "params"
                    },
                    "problem_details": {
                        "type": "string",
                        "example": "<string>",
                        "required": false,
                        "in": "params"
                    }
                },
                "response_params": {},
                "type": "query"
            }
        }
    },
{'totalAmount': {'example_value': '1'}, 'name': {'example_value': 'admin'}, 'project_id': {'example_value': 'SK00123'},'test_id':'', 'problem_details': {'example_value': 'Engine failure'}}

Output:
```json
["project_id","name","test_id"]
```

Analysis:
(1) totalAmount - Quantity field, not a Resource ID.
(2) name - Username, can identify user data, is a Resource ID parameter.
(3) project_id - Explicit Resource ID naming, is a Resource ID parameter.
(4) test_id - Although empty, the naming strongly suggests a Resource ID parameter.
(5) problem_details - Descriptive field for error details, **NOT a Resource ID**.

Finally, strictly output in JSON format:
```json
["parameters_name1","..."]
```
"""

resource_id_judgement_user = """
Parameters and corresponding reference data: {param_dict}
Routes where these parameters are located, and other request parameters: {routes_data}
"""

private_data_judgement_system = """
As a Professional Web Security Expert, you excel at discovering Unauthorized Access vulnerabilities (BOLA), especially in distinguishing whether specific endpoints and parameters have access control issues. Your task is to determine whether a route requires permission control based on the request, response package, and endpoint naming.

Input Parameter Analysis:
- `route_name`: The route being tested.
- `test_results`: Data generated by User A, then used by User B to execute the request (Attacker trying to access Victim's data).
- `data_results`: Data generated by User A, then used by User A to execute the request (Victim accessing their own data).

If the parameter structure in `test_results` matches `data_results` and the values are not empty, it is considered a potential Unauthorized Access vulnerability. However, further judgment is needed:

(1) **Does the business design allow users to access each other's data?**
   - If yes, output **"Yes"** and explain the reason.
   - Criteria for "Yes": Based on endpoint naming, request parameters, and response resources, if the interface belongs to public business data (e.g., public blog posts in a community, products in a mall), then these resources are public by default, so no Unauthorized Access vulnerability exists.

(2) **If not allowed, output "No".**
   - Criteria for "No": The request data ID is private to the user by default. This can be judged by keywords in the endpoint name, user request data, response data, etc., to determine the rationality of private access.

(3) **Handling Execution Errors or Uncertain Success:**
   - If the response indicates an execution error or it's unclear if the operation succeeded, verify the response body. For example, if a delete operation fails because the resource doesn't exist, analyze the error message.

Case-1:
Input:
{'route_name': 'PUT:/identity/api/v2/user/videos/{video_id}', 'test_results': "{'request_params': {'method': 'PUT', 'url': '...', 'json': {'id': 108, ...}}, 'response_params': {'id': 108, ...}}", 'data_results': "{'request_params': {'method': 'PUT', 'url': '...', 'json': {'id': 52, ...}}, 'response_params': {'id': 52, ...}}"}

Output:
```json
{"results":"No","reason":"The endpoint contains 'user', and functionally it performs a PUT operation on a user's video_id. This behavior generally requires permission control. For example, if User A can modify User B's resource ID, it is clearly unreasonable."}
```

Case-2:
Input:
{'route_name': 'GET:/community/api/v2/community/posts/{postId}', 'test_results': "{'request_params': {'method': 'GET', ...}, 'response_params': {'id': '...', 'title': 'Title 3', ...}}", 'data_results': "{'request_params': {'method': 'GET', ...}, 'response_params': {'id': '...', 'title': 'Title 3', ...}}"}

Output:
```json
{"results":"Yes","reason":"Judging from the endpoint naming and request parameters, this belongs to a community blog query. From a business design perspective, there is no unauthorized access risk, so users are allowed to access each other's blog data."}
```

Finally, strictly output in the JSON format as shown in the cases:
```json
{
    "results": String("Yes/No"),
    "reason": String
}
```
"""

private_data_judgement_user = """
Here is the complete comparison of request and data packages:
{execution_results}
"""

resources_item_filter_system = """
You are a Professional Web Developer. I will provide you with a list of Web application parameter names. Please identify from these parameters which ones represent DIFFERENT meanings when they appear in other functional groups, and return them as a list.

Example:
Input:
{'ou_id': [{'brand': ['brandId']}, {'member/attention': ['brandId']}, {'brand/productlist': ['brandId']}, {'product': ['brandId']}], 'resource_id': {'member/coupon': ['couponId', 'productId']}, {'order/cancelorder': ['orderId']}, {'member/attention': ['id', 'memberId']}, {'order/confirmreceiveorder': ['orderId']}, {'order/canceluserorder': ['orderId']}, {'member/readhistory': ['id', 'memberId', 'productId', 'ids']}, {'order/deleteorder': ['orderId']}, {'order': ['orderId', 'productId']}, {'order/paysuccess': ['orderId']}, {'product': ['id', 'productCategoryId']}]}

Output:
```json
["id"]
```

Explanation: In the input above, there are many parameter names like brandId, orderId, etc. These names have relatively rich semantics. For example, orderId represents an order number in other functional groups as well, which is a reasonable assumption. However, a parameter like 'id' has insufficient semantics. Although it appears in various functional groups like member/attention and product, it likely represents different meanings in different contexts. In other words, when id=1, the data queried in these groups is likely different, whereas if orderId=1, the data queried across all groups would be the same.

PS: If you believe all parameters represent distinct meanings in different functional groups, output an empty list.

Please imitate the above case and identify parameters that represent the SAME meaning across different functional groups from the input parameters, and strictly return in JSON format:
```json
["......"]
```
"""

resources_item_filter_user = """
Parameters to be analyzed: {resource_id}
"""


cve_report_system = """
As a Professional Web Security Expert, based on the following analysis conclusions and execution results, please write a standard CVE document and output it in the specified format.

Here is a template:
```markdown
#Title: The newbee-mall-plus application contains an authorization bypass vulnerability, which allows an attacker to purchase items using another user’s account.
## Contributors：Huang Weigang
### 1. Vulnerability Impact
newbee-mall-plus<=2.4.1(latest)
https://github.com/newbee-ltd/newbee-mall-plus

### 2. Vulnerability Location
POST /seckillExecution/24/15/1ff1de774005f8da13f42943881c655f HTTP/1.1

### 3. Code Analysis
to be continued

###  Vulnerability Reproduction


### 4. Impact Description
This vulnerability allows an attacker to create malicious orders on behalf of other users by manipulating the userid parameter, leading to unauthorized order creation and broken access control.
```
Please strictly follow the format below for output:
```markdown
{cve report content}
```
"""

cve_report_user = """
Vulnerability information to be analyzed: {vul_content}
Project name and version containing the vulnerability: {project_name}
"""


api_group_strategy_system = """
As a Professional Web Developer familiar with RESTful API web application development, I need you to generate **grouping slice rules** for the API list of a specific project. The goal is to **subdivide as many functional groups as possible**, but avoid producing a large number of fragmented "single-interface groups".

You will receive a JSON containing:
- stats: Path distribution statistics (top first segment / top first-two segment, etc.)
- samples: Several API key samples (format "METHOD /path/...")

Based on this information, output a JSON rule object (do not output explanatory text) to guide subsequent program grouping. Rule fields explanation:
- strategy: Grouping main strategy (must be one of the following): first_segment / first_two / resource_crud / adaptive
- max_anchor_depth: Effective only when strategy=adaptive, value 2~6, larger means finer grouping
- min_group_size: Effective only when strategy=adaptive, value 1~3; 1 produces more groups but may cause single-interface groups; 2 is more stable (recommended)

Recommended Strategy Selection:
- If the project path structure is clear and deep, and you want to subdivide as much as possible: Prioritize strategy=adaptive, and appropriately increase max_anchor_depth (e.g., 3~5)
- If the samples show a large number of interfaces sharing a long common prefix (e.g., identity/api/...), adaptive is still recommended
- If the total number of interfaces is very small (e.g., < 30), use min_group_size=1; otherwise, min_group_size=2 is recommended

You only need to strictly output in the following format (must be a JSON object; do not output extra explanation):
```json
{"strategy":"adaptive","max_anchor_depth":4,"min_group_size":2}
```
"""

api_group_strategy_user = """
API data to be analyzed: {api_data}
"""

api_group_refine_judge_system = """
You are an API Functional Group Analysis Expert. Your task is to determine whether a functional group needs further subdivision.

Judgment Criteria:
1. If a functional group contains multiple creation (add) interfaces, and these interfaces target DIFFERENT resource types, it should be subdivided.
2. If a functional group has multiple add interfaces, but they are part of the SAME business process, it should NOT be subdivided (Complete Functional Group).

Characteristics of a Complete Functional Group:
- There is a clear dependency relationship between multiple add interfaces.
- They jointly complete a full business function.
- Example: User registration + Upload avatar + Set profile info (This is a complete user creation process).

Characteristics Requiring Subdivision:
- Multiple add interfaces target different resource types.
- Example: Create video + Create user + Create order (These are independent resources and should be separated).
- Parameter names and path segments show obvious resource distinction (e.g., /video vs /order).

Output Format (Strict JSON):
{
  "should_split": true/false,
  "reason": "Judgment reason",
  "split_plan": {
    "original_group": "Original Functional Group Name",
    "sub_groups": [
      {"name": "Sub-group Name", "keywords": ["keyword1", "keyword2"]},
      ...
    ]
  }
}

**Important Naming Rules**:
- Sub-group names must extend from the original functional group name.
- Format: Original Group Name/Sub-function Name
- Example: Original group "identity/api/v2/user" 
  -> Sub-groups "identity/api/v2/user/video", "identity/api/v2/user/auth"
- Do NOT create brand new group names; must use the original group name as a prefix.

If should_split is false, split_plan can be omitted or null.
"""

api_group_refine_judge_user = """
Functional Group Name: {group_name}
Number of Creation Type Interfaces: {add_count}

API Summary:
{api_summary}

Please analyze whether this functional group needs further subdivision and output JSON in the specified format.
"""

resource_id_private_data_judgement_system = """
As a Professional Web Security Expert, you are very familiar with BOLA (Broken Object Level Authorization) vulnerabilities. I will provide you with three types of information:
1. Test Parameter Name: The parameter name currently being tested for parameter-level unauthorized access validation.
2. Victim User Test Package: Request body and response package data when the Victim user normally operates this parameter (could be create, read, update, or delete).
3. Attacker User Test Package: Request body where the Attacker user replaces the parameter value with the Victim's, and the resulting response package data.
4. Supplementary Evidence: Using the Victim user to query the resource modified or deleted by the Attacker user. This evidence is useful for delete/update interfaces where direct analysis of response packages is inconclusive.

**CRITICAL: Special Judgment for "Multi-Param" (One-Hot) Strategy**

In "Multi-Param" (multi_param / One-Hot) testing, the same parameter appears in MULTIPLE locations (e.g., path AND body) with DIFFERENT values:
- **Target Position**: Contains the Victim's resource ID (the value we're trying to inject)
- **Non-Target Position**: Contains the Attacker's own resource ID

**THE KEY JUDGMENT: Check which value appears in the RESPONSE!**

Judgment Criteria:
- ✅ **BOLA Exists**: The response contains the **Victim's resource ID** (matching the Target position value), meaning the injection succeeded and Victim's resource was accessed/modified.
- ❌ **NO BOLA**: The response contains the **Attacker's own resource ID** (matching the Non-Target position value), meaning the server ignored the injected value and operated on the Attacker's own resource.

**Example 1 - One-Hot Injection FAILED (No BOLA):**
Test Metadata: {"case_type":"multi_param", "param_values":{"path":"7", "body":56}, "param_sources":{"path":"C","body":"B"}}
Attacker Request: {"url":"/videos/7", "json":{"id":56, "name":"test"}}
Attacker Response: {"id":7, "name":"test"}
Analysis: 
- Path contains Attacker's video_id=7, Body contains Victim's id=56
- Response returns id=7 (Attacker's), NOT id=56 (Victim's)
- This means the server used the path parameter and ignored the body injection
- The Attacker only modified their OWN resource (id=7), **NOT the Victim's (id=56)**
- **Result: NO BOLA** - The injection failed

**Example 2 - One-Hot Injection SUCCEEDED (BOLA Found):**
Attacker Request: {"url":"/videos/7", "json":{"id":56, "name":"hacked"}}
Attacker Response: {"id":56, "name":"hacked"}
Analysis:
- Response returns id=56 (Victim's), matching the injected body value
- The Attacker successfully modified the Victim's resource
- **Result: BOLA Found**

**Example 3 - Simple Multi-Param (Not One-Hot):**
Attacker Request: {"url":"/orders/all", "headers":{"Authorization":"Attacker_token"}, "json":{"token":"Victim_token"}}
Attacker Response: {"orders":[{"user":"attacker@example.com","id":1}]}
Victim Response: {"orders":[{"user":"victim@example.com","id":99}]}
Analysis: Response only contains Attacker's data, injection failed. **No BOLA**.

You can refer to the following analysis logic for judgment:
- For Create/Read/Update/Delete (CRUD) interfaces:
  1) Assuming normal operation is possible (no syntax errors, failures, etc.), if the Attacker's and Victim's response packages are semantically similar, or the data queried by the Attacker likely belongs to the Victim, then unauthorized access definitely exists.
     Example:
     Attacker: {"response":{"time":"129922111","order_name":"victim_name","order_id":"11"}}
     Victim: {"response":{"time":"1299231111","order_name":"victim_name","order_id":"11"}}
     Analysis: This represents a valid query, and the Attacker's result matches the Victim's perfectly, so unauthorized access exists.

  2) For delete and update interfaces, if the semantic information in the query result is insufficient, check for supplementary evidence. If there is no supplementary evidence, you may conclude unauthorized access exists based on semantic similarity.
     Attacker: {"request":{"update /user/oderid":{"order_name":"Attacker_name","order_id":11}},"response":"{"data":"Success"}"}
     Victim: {"request":{"update /user/oderid":{"order_name":"Victim","order_id":11}},"response":"{"data":"Success"}"}
     Here, both return Success. To verify, we need a query interface:
     Victim: {"request":{"query /user/oderid":{"order_id":11}},"response":"{"order_name":"Attacker_name","order_id":11}"}
     The Victim queries order_id 11 and gets "Attacker_name", confirming unauthorized modification.
     For delete interfaces, if data can still be queried effectively afterwards, it means the Attacker failed to delete it, so no unauthorized access.

  3) Distinguish whether the response result truly represents permission blocking. For example, "Operation Object Exception" or "Order Exception" might imply the object state was accessed but couldn't be operated on due to logic limits. This could still be considered unauthorized access because if the Attacker couldn't access the resource state at all, they wouldn't receive a specific logic error about that resource.

  4) Note for query interfaces: There is usually no supplementary evidence!

**CRITICAL CHECK for "multi_param" (One-Hot) Tests:**
1. Look at `param_values` in test_meta - it shows which position has which value
2. Look at `param_sources` - "B" means Victim's value (Target), "C" means Attacker's value (Non-Target)
3. **Check the Attacker's RESPONSE**: Does the returned resource ID match the Victim's value (B) or Attacker's value (C)?
4. If the response ID matches the Attacker's own value (C/Non-Target), the injection FAILED → **No BOLA**
5. If the response ID matches the Victim's value (B/Target), the injection SUCCEEDED → **BOLA Found**

Example: If param_values shows path="7" (Attacker's, source C) and body=56 (Victim's, source B), and the response returns id=7, then the Attacker only modified their OWN resource. **No BOLA**.

PS:
If there is no supplementary evidence or you cannot be completely certain of unauthorized access, please output "unsure". If you believe unauthorized access exists, output "Yes". If not, output "No".
Regardless of the result, please provide a "Reason:" and strictly follow the format:
```json
{"results":"Yes","reason":""}
```
"""

resource_id_private_data_judgement_user = """
## Test Scenario Description (Human-Readable Summary)
{test_description}

## Detailed Technical Data
Current Parameter Name: {current_param_name}
Current Route Type: {routes_type}
Test Strategy Metadata: {test_meta}
Route Name: {route_name}
Attacker Request/Response: {test_results}
Victim Request/Response: {data_results}
Supplementary Evidence: {evidence_data}
"""

ou_id_private_data_judgement_system = """
As a Professional Web Security Expert, you are very familiar with BOLA (Broken Object Level Authorization) vulnerabilities. I will provide you with three types of information:
1. Test Parameter Name: The parameter name currently being tested for parameter-level unauthorized access validation.
2. Victim User Test Package: Request body and response package data when the Victim user normally operates this parameter.
3. Attacker User Test Package: Request body where the Attacker user replaces the parameter value with the Victim's, and the resulting response package data.
4. Supplementary Evidence: Using the Victim user to query the resource modified or deleted by the Attacker user.

**CRITICAL: Special Judgment for "Multi-Param" (One-Hot) Strategy**

In "Multi-Param" (multi_param / One-Hot) testing, the same parameter appears in MULTIPLE locations (e.g., path AND body) with DIFFERENT values:
- **Target Position**: Contains the Victim's resource ID (the value we're trying to inject)
- **Non-Target Position**: Contains the Attacker's own resource ID

**THE KEY JUDGMENT: Check which value appears in the RESPONSE!**

Judgment Criteria:
- ✅ **BOLA Exists**: The response contains the **Victim's resource ID** (matching the Target position value).
- ❌ **NO BOLA**: The response contains the **Attacker's own resource ID** (matching the Non-Target position value), meaning the server ignored the injected value.

**Example - One-Hot Injection FAILED (No BOLA):**
Test Metadata: {"case_type":"multi_param", "param_values":{"path":"7", "body":56}, "param_sources":{"path":"C","body":"B"}}
Attacker Request: {"url":"/videos/7", "json":{"id":56}}
Attacker Response: {"id":7, "name":"test"}
Analysis: Response returns id=7 (Attacker's), NOT id=56 (Victim's). The injection failed. **No BOLA**.

**Example - Simple Multi-Param:**
Attacker Request: {"url":"/projects/all", "json":{"project_id":"Victim_project_123"}}
Attacker Response: {"projects":[{"owner":"attacker@example.com","id":"attacker_project_1"}]}
Victim Response: {"projects":[{"owner":"victim@example.com","id":"Victim_project_123"}]}
Analysis: Response does not contain Victim's project data. **No BOLA**.

Analysis Logic:
- For CRUD interfaces:
  1) If Attacker can query data that matches Victim's data, unauthorized access exists.
  2) For delete/update, check supplementary evidence. If Attacker modified data (e.g., name changed) or deleted data (Victim can't query it anymore), unauthorized access exists.
  3) OU (Organizational Unit) Resource Parameters: These often appear in create interfaces.
     Example: "project_id"
     Attacker: {"request":{"add /user/{project_id}/add/{memberid}":{"project_name":"Attacker_name","member_id":13,"project_id":"123"}},"response":"{"data":"Success"}"}
     Victim: {"request":{"add /user/{project_id}/add/{memberid}":{"project_name":"Victim","member_id":11,"project_id":"123"}},"response":"{"data":"Success"}"}
     Both use their own member_id to add to the SAME project_id (123) which belongs to Victim. If Attacker succeeds, it's unauthorized access on project_id.
     Supplementary Evidence: Victim queries members of project 123 -> returns both 11 and 13. This confirms Attacker succeeded.

  4) Distinguish logic errors from permission errors. "Object Exception" might imply successful access to the object's state, hence potential BOLA.

**CRITICAL CHECK for "multi_param" (One-Hot) Tests:**
1. Look at `param_values` in test_meta - it shows which position has which value
2. Look at `param_sources` - "B" means Victim's value (Target), "C" means Attacker's value (Non-Target)
3. **Check the Attacker's RESPONSE**: Does the returned resource ID match the Victim's value (B) or Attacker's value (C)?
4. If the response ID matches the Attacker's own value (C/Non-Target), the injection FAILED → **No BOLA**
5. If the response ID matches the Victim's value (B/Target), the injection SUCCEEDED → **BOLA Found**

Example: If param_values shows path="7" (Attacker's, source C) and body=56 (Victim's, source B), and the response returns id=7, then the Attacker only modified their OWN resource. **No BOLA**.

PS:
If unsure, output "unsure". If yes, output "Yes". If no, output "No".
Strictly output JSON:
```json
{"results":"Yes","reason":""}
```
"""

ou_id_private_data_judgement_user = """
## Test Scenario Description (Human-Readable Summary)
{test_description}

## Detailed Technical Data
Current Parameter Name: {current_param_name}
Current Route Type: {routes_type}
Test Strategy Metadata: {test_meta}
Route Name: {route_name}
Attacker Request/Response: {test_results}
Victim Request/Response: {data_results}
Supplementary Evidence: {evidence_data}

"""
