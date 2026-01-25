api_function_type_judge_system = """
As a Professional Web Application Developer, you need to determine the primary function of each API endpoint based on the functional group parameter documentation I provide. First, determine if the endpoint can be translated into a SQL statement; if so, combine the SQL operation type, endpoint function, parameter design, and request method to classify it into one of the following types: "add", "delete", "update", "query", or "list query".

- "add": Corresponds to data creation (usually POST, returns a new resource ID).
- "delete": Represents deleting a resource (DELETE or POST request, parameters include ID).
- "update": Represents updating data (PUT, PATCH, or POST request, passing fields to be modified).
- "query": Represents a single item query (GET request, exact match condition returns a single record).
- "list query": Used for batch querying a collection of resources (typically GET /resource or POST /resource/query, supports pagination, sorting, and filtering, returns a data list and pagination info).

**Strict Rule**: Only classify as "list query" if the response parameters contain an array structure (e.g., items[], data[], list[]) OR the request parameters contain pagination/list parameters (e.g., page, pageSize, limit, offset, size, per_page). Otherwise, classify as "query".

"List query" Example:
"GET /user/list": {
  "request_params": {
    "page": { "type": "integer", "example": 1, "required": true },
    "pageSize": { "type": "integer", "example": 10, "required": true }
  },
  "response_params": {
    "data": {
      "type": "array",
      "items": { "id": 1, "username": "Alice" }
    }
  }
}

Finally, the complete case you need to learn is:
Input:
"/user/test": (
    "POST /user/test/add": (
       "request_params": (
            "username": ( "type": "string", "example": "test_name", "required": true }
       ),    
       "response_params":(
          "code": { "type":"integer", "example":200 },
          "msg": { "type":"string", "example":"success" }
       )
    )
)

Output (Please ensure to output standard JSON format):
```json
{"POST /user/test/add":"add"}
```
"""

api_function_type_judge_user = """
Here is the functional group API data you need to judge: {api_data}
"""

parameter_normalization_system = """
As a Professional Web Security Engineer, you deeply understand the meaning of all request and response parameters in routes and can effectively align these parameters within a functional group. The judgment method is as follows:

(1) Within the same functional group, infer based on the route and parameter names. For example, if the route is `/api/product/update` and its request parameter is `product_id`, and in `/api/product/add`, the response parameter is `id`. Based on route consistency, we can infer that when the route contains the keyword "product", `id` represents the same value as `product_id`. In this case, `product_id`, which has richer semantic features, should be preserved, and the parameter named `id` should be replaced by `product_id`.

Please pay special attention to the potential naming relationship between the functional group name and the resource ID. For example, in a `cart` functional group, there are `product_id`, `order_id`, and `cart_id`. Since the group name is `cart`, if there is a parameter named `id`, it typically refers to `cart_id`.

(2) Nested Data Handling
- Path Sensitive: If a parameter is in a nested structure (e.g., `author.id`, `items[].book_id`), judge based on its path and semantics.
- Local Uniqueness: `id` in a nested structure is valid only for that nested object and is not equivalent to the top-level resource ID.
- Array/List Context: When `items[].id` is returned, you need to combine it with the array object type (e.g., whether `items` is a book list) to determine if it corresponds to `book_id`.

(3) Replacement Priority (Strictly Follow)
    ⚠️ Core Principle: Keep Specific, Replace Generic
    - Specific parameter names (e.g., `order_id`, `product_id`, `book_id`) -> `keep_pra` (Keep)
    - Generic parameter names (e.g., `id`, `identifier`, `uuid`, `guid`, `code`, `key`, `serial`, `number`) -> `replace_para` (Replace)
    
    🆕 Multiple Identifiers Handling:
    - When an API returns MULTIPLE generic identifiers for the same resource (e.g., both `id` and `uuid`), 
      include ALL of them in `replace_para`, even if they may cause redundancy.
    - Example: If resource APIs return both numeric `id` and UUID `uuid`, both should map to the specific ID name.
    - Rationale: This ensures comprehensive dependency chain coverage. The execution layer will handle 
      value selection intelligently through priority-based fallback mechanism.
    
    - Judgment Criteria:
      a) Parameters with resource type prefixes are more specific (`order_id` > `id`)
      b) Parameters consistent with route semantics are more specific (In `/orders` route, `order_id` > `id`)
      c) Parameters related to the functional group name are more specific (In `cart` group, `cart_id` > `id`)
      d) 🆕 All generic identifiers without resource context should be mapped (`id`, `uuid`, `code` all -> `xxx_id`)
    - ❌ Wrong Example: `{"keep_pra": "id", "replace_para": ["order_id"]}` # This causes parameter pool pollution!
    - ✅ Correct Example: `{"keep_pra": "order_id", "replace_para": ["id", "uuid"]}`
    - `keep_pra` (the retained parameter name) is usually the specific parameter name (e.g., `video_id`, `order_id`), not the generic parameter name (e.g., `id`, `uuid`) returned by an add-type route.
    - However, add-type routes MUST be included in the `route_name` list to ensure their returned generic parameters (e.g., `id`, `uuid`) are correctly normalized.
    - In other words: add routes return `replace_para` (to be replaced), not `keep_pra` (retained).

(3.1) Pre-output Self-check:
    ✓ Checklist (Must satisfy):
      - `keep_pra` contains underscores or prefixes (e.g., `order_id`, `video_id`)
      - `replace_para` contains ALL generic identifier fields present in responses (e.g., `id`, `uuid`, `code`, `identifier`)
      - 🆕 If response contains both `id` and `uuid`, BOTH must appear in `replace_para`
      - 🆕 Prioritize comprehensive coverage over avoiding redundancy
    ✗ Forbidden (Must NOT occur):
      - `keep_pra` should not be merely "id", "uuid", or "identifier"
      - `replace_para` should not contain names more specific than `keep_pra`
      - 🆕 Should NOT omit any generic identifier field that appears in API responses

(4) Consistency Judgment Goal
    - Only replace parameters that appear in different routes but clearly identify the SAME resource.
    - Special Note: add-type interfaces create resources and return resource identifiers, which are the basis for subsequent operations.
      - If an add interface returns a generic name (e.g., `id`) while other interfaces use a specific name (e.g., `video_id`), normalization MUST be performed.
      - The `route_name` list should contain ALL interfaces using this resource identifier, INCLUDING the add interface that created the resource.
      - This ensures the dependency chain from resource creation -> resource usage is correctly established.
    - The output format is a JSON Array, specifically a List<dict>. Inside the dict, `route_name` is the list of routes needing replacement, `parameters_name` contains `replace_para` (parameter name to replace) and `keep_pra` (parameter name to keep).

(5) Relationship between add-type interfaces and parameter normalization (Important)
    Core Principle: add-type interfaces create resources and return identifiers; these identifiers MUST participate in normalization.

    Judgment Flow:
    ① Identify resource type in the functional group (extract from group name, route path, e.g., videos, orders).
    ② Check the response parameters of the add interface (usually returns generic `id`, `identifier`).
    ③ Check request parameters of other interfaces (query/update/delete) (usually use specific `xxx_id`).
    ④ If both point to the same resource, then:
       - Include the add interface in `route_name`.
       - Set the generic parameter (`id`) as `replace_para`.
       - Set the specific parameter (`xxx_id`) as `keep_pra`.

    Pattern Recognition:
    • Pattern A: add interface returns 'id', others use 'resource_id'.
      -> Group name or route contains 'resource' keyword.
      -> Include add interface in `route_name`, `keep_pra`='resource_id', `replace_para`=['id'].

    • Pattern B: Group has only one resource type, add returns 'id', others also use 'id'.
      -> All naming is consistent, no normalization needed (or empty result).

    • Pattern C: Group has multiple resource types (e.g., videos and pictures), each has its own `xxx_id`.
      -> Handle separately; include the add interface for each resource type in the corresponding `route_name`.

    ❌ Wrong Approach:
    - Exclude add-type interfaces, causing their return values to be un-normalized and breaking the dependency chain.
    - Set the generic `id` returned by add interfaces as `keep_pra` (Violates "Keep Specific, Replace Generic").

    ✅ Correct Approach:
    - Identify resource types and parameter semantic relationships.
    - Normalize the add interface together with other interfaces using that resource.
    - Ensure parameter naming consistency covers the full resource lifecycle (Create -> Query -> Update -> Delete).

Correct Case (1):
Input:
{'api/user': [{'POST /api/user/book/create': {'request_params': ['book_name','look_id'], 'response_params': ['data[].id'],"type":"add"}}, {'GET /api/user/book/delete/{id}': {'request_params': ['book_id'], 'response_params': ['book_name'],"type":"delete"}},{'POST /api/user/order': {'request_params': ['order_name'], 'response_params': ['id'],"type":"create"}},{'GET /api/user/order/delete': {'request_params': ['order_id'], 'response_params': ['status'],"type":"delete"}]}

Output:
```json
[
    {
        "route_name":["POST /api/user/book/create","GET /api/user/book/delete/{id}"],
        "parameters_name":{"replace_para":["data[].id","id"],"keep_pra":"book_id"}
    }
]
```
Analysis: In the book class interfaces, `POST /create` returns `id`, while `GET /delete` uses `book_id`. So `id` needs to be normalized to `book_id`. The order interfaces are semantically different, so `id` in `order` is preserved (or handled separately if needed).

Correct Case (2) - Avoid Reverse Mapping:
Input:
{'workshop/api/shop/orders': [{'GET /workshop/api/shop/orders/all': {'request_params': [], 'response_params': ['orders[].id'], 'type': 'list query'}}, {'PUT /workshop/api/shop/orders/{order_id}': {'request_params': ['order_id', 'product_id'], 'response_params': ['status'], 'type': 'update'}}, {'POST /workshop/api/shop/orders/return_order': {'request_params': ['order_id'], 'response_params': ['status'], 'type': 'update'}}]}

Output:
```json
[
    {
        "route_name": ["GET /workshop/api/shop/orders/all", "PUT /workshop/api/shop/orders/{order_id}", "POST /workshop/api/shop/orders/return_order"],
        "parameters_name": {"replace_para": ["orders[].id", "id"], "keep_pra": "order_id"}
    }
]
```
Analysis: In the orders group, GET returns `orders[].id`, others use `order_id`. According to "Keep Specific, Replace Generic", keep `order_id` and replace `id`.

Correct Case (3) - Multiple Generic Identifiers (id + uuid):
Input:
{'api/v1/asset': [
    {'GET /api/v1/asset/list': {
        'request_params': [], 
        'response_params': ['items[].id', 'items[].uuid', 'items[].name'], 
        'type': 'list query'
    }}, 
    {'GET /api/v1/asset/{assetId}/details': {
        'request_params': ['assetId'], 
        'response_params': ['name', 'status'], 
        'type': 'query'
    }}, 
    {'POST /api/v1/asset/create': {
        'request_params': ['name', 'type'], 
        'response_params': ['id', 'uuid', 'assetId'], 
        'type': 'add'
    }}
]}

Output:
```json
[
    {
        "route_name": [
            "GET /api/v1/asset/list", 
            "GET /api/v1/asset/{assetId}/details", 
            "POST /api/v1/asset/create"
        ],
        "parameters_name": {
            "replace_para": ["items[].id", "id", "uuid", "assetId"],
            "keep_pra": "asset_id"
        }
    }
]
```

Analysis: 
- The asset functional group returns multiple generic identifiers: `id` (numeric) and `uuid` (UUID format)
- Path parameter uses `assetId` (camelCase variant)
- ✅ ALL identifiers (`id`, `uuid`, `assetId`) are included in `replace_para` to ensure comprehensive coverage
- This may create some redundant dependency chains, but that's acceptable - missing dependencies is worse
- The execution layer will handle value selection through priority-based fallback (specific names > generic names)
- Priority order: `asset_id` > `assetId` > `id` > `uuid`

Correct Case (4) - Multiple Identifier Types (id + code + key):
Input:
{'api/v2/ticket': [
    {'POST /api/v2/ticket/generate': {
        'request_params': ['title', 'priority'], 
        'response_params': ['id', 'code', 'key'], 
        'type': 'add'
    }}, 
    {'GET /api/v2/ticket/{ticketId}': {
        'request_params': ['ticketId'], 
        'response_params': ['title', 'priority', 'status'], 
        'type': 'query'
    }}, 
    {'POST /api/v2/ticket/validate': {
        'request_params': ['code'], 
        'response_params': ['valid'], 
        'type': 'update'
    }}
]}

Output:
```json
[
    {
        "route_name": [
            "POST /api/v2/ticket/generate",
            "GET /api/v2/ticket/{ticketId}",
            "POST /api/v2/ticket/validate"
        ],
        "parameters_name": {
            "replace_para": ["id", "code", "key", "ticketId"],
            "keep_pra": "ticket_id"
        }
    }
]
```

Analysis:
- Three different generic identifier types: `id`, `code`, `key`
- ALL are included to ensure complete dependency chain coverage
- Different APIs may use different identifier formats (numeric id vs. alphanumeric code)
- Execution layer will try them in priority order: `ticket_id` > `ticketId` > `id` > `code` > `key`

Finally, output strictly in standard JSON format:
```json
[
{"route_name":["......"],
"parameters_name":{}}
]
```
"""

parameter_normalization_user = """
Functional Group Name: {params_name}
Parameter Situation: {params_data}
Please note this situation to avoid incorrect keep_pra setting: {false_reason}
"""

parameter_update_system = """
Please help me correct the key names here. Ensure the key names are keywords present in the interfaces that need replacement, and avoid being too broad to prevent matching irrelevant keywords. Pay attention to the degree of keyword matching.

Example:
Input:
Original Data:
[
{"bookId":["data[].id","book_id"]}
]
Keys to replace:
["bookId"]
Reference API Data:
{'api/user': [{'POST /api/user/book/create': {'request_params': ['book_name','look_id'], 'response_params': ['datap[].id'],"type":"add"}}, {'GET /api/user/book/delete': {'request_params': ['book_id'], 'response_params': ['book_name'],"type":"delete"}},{'POST /api/user/order': {'request_params': ['order_name'], 'response_params': ['id'],"type":"query"}}]}

Output:
```json
[
{"book":["data[].id","book_id"]}
]
```
Analysis: The key `bookId` does not appear in any route name. Observing routes with `data[].id` or `book_id` (e.g., `.../book/create`, `.../book/delete`), they all have the keyword "book". Thus, we use "book" as the key.

PS: You only need to modify the key names!
Finally, please output standard JSON format:
```json
{"reset_password":{"replace_para":"old_password","keep_pra":"new_password"}}
```
"""

parameter_update_user = """
Parameters to replace keys:
{update_para}
Original Data:
{original_data}
Reference API Data:
{params_data}
"""


parameter_generation_system = """
As a Professional Web Developer, you are proficient in naming conventions of fields under various request routes and incoming parameter types. Now, based on the parameters missing values in the following request parameters, please generate a Python script to modify the request package. Please note:

(1) If the request requires file upload, you should write a Python script to create a file directly using built-in functions like `open()`, then write it into the request package according to the format.
(2) Ultimately, ensure this request body meets the sending requirements of the current route and can be successfully sent via `requests.request()`.
(3) Consider whether each parameter in the request package needs to be set, e.g., content-type, based on different requests.
(4) When constructing parameters, ensure the data type is accurate. For example, if type is int, construct a number, not a string.
(5) When constructing data, ensure the semantic type is accurate. For example, if the parameter name is `phone`, it implies an 11-digit mobile number.

Case-1:
Input:
Route: /identity/api/auth/signup
Request Package: {'method': 'POST', 'url': '...', 'headers': {...}, 'json': {'email': '...', 'name': '', 'number': '', 'password': '...'}}
Parameters to construct: ["name","number"]
Parameter Types: 'parameters_type': "{'name': {'type': 'string', 'example': 'Cristobal.Weissnat', 'required': True}, 'number': {'type': 'string', 'example': '6915656974', 'required': True},'account': {'type': 'int', 'example': 11, 'required': True}}"

Output:
```python 
def parameters_generator(request_packages):
    request_packages["headers"]["Content-Type"] = "application/json"
    request_packages["json"]["name"] = "test"
    request_packages["json"]["number"] = "1234567890"
    request_packages["json"]["account"] = 11
    return request_packages
```

Case-2 (File Upload):
Input:
Route: /identity/api/upload
Request Package: {'method': 'POST', 'headers': {...}, 'file':''}
Parameters to construct: ["file"]
Parameter Types: 'parameters_type': "{'file': {'type': 'string', 'format':'binary'}}"

Output:
```python 
def parameters_generator(request_packages):
    import io
    import requests
    from PIL import Image

    # 1. Create a simple JPG image in memory
    img = Image.new("RGB", (100, 100), color=(255, 0, 0))

    # 2. Write to BytesIO instead of disk
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    buf.seek(0)
    
    # File upload should use 'files' field
    if "files" not in request_packages:
        request_packages["files"] = {}
    request_packages["files"]["file"] = ("test.jpg", buf, "image/jpeg")
    
    # Remove 'json' field if present to avoid conflict
    if "json" in request_packages:
        del request_packages["json"]
    return request_packages
```
Analysis: You need to generate a Python function named `parameters_generator()`. When creating a file, use Python built-ins or libraries to create a non-persistent file object for upload. Ensure the final request package is valid for file upload!

PS: (1) The request will be sent using `requests.request(**request_packages)`, so ensure the request body is compliant.
(2) If request parameters are `params`, please also change `json` to `params` in the function body.

Based on the above examples, generate the specific Python function code and return in the following format:
```python
def parameters_generator(request_packages):
    xxx
```
"""

parameter_generation_user = """
Request Route: {route_path}
Request Method: {request_method}
Parameters to construct test data for: {parameters_name}
Parameter Type Information: {parameters_type}
"""

parameters_fills_system = """
As a Professional Web Developer, you need to determine if a specified interface needs additional response parameters added, and output the response body after adding them.
I will give you two sets of data:
1. The add-type interface along with its request and response parameters.
2. All interfaces and their request/response parameters under the current functional group.

If the response package of the add-type interface does not have a resource ID response parameter, analyze the request parameters of other interfaces in the group and output the parameter name you think should be added.

Case:
Input:
Add-Type Interface:
{'POST /brand/create':{'request_params': {'logo':{'type': 'string', ...}}, 'response_params': {'code': ..., 'message': ...}, 'type': 'add'}}
Other Interfaces:
{'POST /brand/delete/{id}':{'request_params': {'id':{'type': 'integer', ...}}, ...}, 'POST /brand/update/{id}':{'request_params': {'id':{'type': 'integer', ...}}, ...}}

Output:
```json
{'id':{'type': 'integer', 'example': 'string', 'required': True}}
```
Analysis: Other interfaces use `id` for resource localization. The add interface should return an `id` to identify the creation result, but currently only returns a status code, so `id` needs to be added.
"""

parameters_fills_user = """
Add-Type Interface and its parameters: {apis_data_add}
Other Interfaces in the group: {apis_data}
"""


api_matched_judgement_system = """
As a Professional Web Developer, you can skillfully determine which interfaces belong to the same functional group based on naming conventions and parameter composition. I will send you two types of functional interfaces: `groups_without_add` and `groups_only_add`. You need to determine which functional groups from these two categories can be secondarily grouped into the same functional group, and place their `group_name` in a List.

Case:
Input:
'groups_without_add': {
  {'group_name':"goods", 'apis':{'Get /api/v1/goods/{goods_id}':{'request_params':{"goods_id"},"response_params":{"data"}}}}
  {'group_name':"order", 'apis':{'Get /api/v1/order/{order_id}':{'request_params':{"order_id"},"response_params":{"data"}}, "update /api/v1/order/{order_id}':...}}
  {'group_name':"payorder", 'apis':{'Get /api/v1/payorder':{'request_params':{"order_id","goods_id"},"response_params":{"data"}}}}
}
groups_only_add:{
  {'group_name':"saveOrder", 'apis':{'Get /api/v1/goods/saveorder':{'request_params':{"goods_id","car_id"},"response_params":{"data"}}}}
}

Output:
```json
["saveOrder","order","payorder"]
```
Analysis: `saveOrder` (create order) likely belongs with `order` and `payorder` based on naming and parameters.

PS: Ensure group names are exactly correct (case-sensitive)!
Strictly output in JSON format:
```json
["....."]
```
"""

api_matched_judgement_user = """
apis: {to_be_matched}
"""

api_description_generation_system = """
As a Professional Web Developer, please generate a short description for the current functional group's APIs.

Example:
Input:
   "Community / Posts": {
            "GET /community/api/v2/community/posts/{postId}","type":"query"
            "POST /community/api/v2/community/posts","type":"add"
            }
Output:
The interfaces in this functional group are used to create and query community blog posts.
"""

api_description_generation_user = """
Functional Group APIs to analyze: {api_data}
"""

api_group_similarity_combine_system = """
You are a Professional Web Developer. I will give you a list of Web application functional groups and their descriptions. Please tell me which functional groups should be merged.
Requirement for merger:
(1) Merged groups should be a collection of CRUD operations for a specific function (e.g., Order Creation/Deletion can be merged with Order Query/Update).

Case:
Input:
{
  "order/detail":"Mainly used for order query",
  "member":"Mainly used for member CRUD",
  "member/detail":"Mainly used to query member details",
  "order/paysuccess":"Mainly used to complete order payment"
}
Output:
```json
[["order/detail","order/paysuccess"],["member","member/detail"]]
```
Analysis: "order/detail" and "order/paysuccess" belong to the same function (Order). "member" and "member/detail" also belong together.

PS: Output merged groups in a multi-dimensional list.
Strictly output in JSON format:
```json
[["order/detail","order/paysuccess"],["member","member/detail"]]
```
"""

api_group_similarity_combine_user = """
Functional Groups to analyze: {api_description}
"""
