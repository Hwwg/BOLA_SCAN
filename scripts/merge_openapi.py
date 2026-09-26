#!/usr/bin/env python3
import json
import sys
import os
from copy import deepcopy


def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def normalize_schema(obj):
    # remove descriptive-only fields to reduce false conflicts
    if isinstance(obj, dict):
        return {k: normalize_schema(v) for k, v in obj.items() if k not in {'description', 'example', 'examples'}}
    if isinstance(obj, list):
        return [normalize_schema(v) for v in obj]
    return obj


def deep_replace_refs(obj, rename_map):
    # rename $ref for schemas according to rename_map
    if isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            new_obj[k] = deep_replace_refs(v, rename_map)
        if '$ref' in obj and isinstance(obj['$ref'], str):
            ref = obj['$ref']
            if ref.startswith('#/components/schemas/'):
                name = ref.split('/')[-1]
                if name in rename_map:
                    new_obj['$ref'] = f"#/components/schemas/{rename_map[name]}"
        return new_obj
    elif isinstance(obj, list):
        return [deep_replace_refs(v, rename_map) for v in obj]
    else:
        return obj


def service_name_from_path(path):
    base = os.path.basename(path)
    name = os.path.splitext(base)[0]
    # e.g., mall-oms_OpenAPI -> mall_oms
    name = name.replace('-','_').replace('OpenAPI','').strip('_')
    return name or 'svc'

def path_prefix_from_path(path):
    """Return a URL path prefix derived from input filename.
    Example: mall-oms_OpenAPI.json -> 'mall-oms'
    Only applies when the filename starts with 'mall-'.
    """
    base = os.path.basename(path)
    name = os.path.splitext(base)[0]
    # take segment before first underscore
    prefix = name.split('_')[0]
    if prefix.startswith('mall-'):
        return prefix
    return None


def merge_openapi(docs, service_names, path_prefixes):
    combined = {
        'openapi': '3.0.1',
        'info': {
            'title': 'Youlai Mall Combined API',
            'version': '1.0.0',
            'description': 'Combined OpenAPI from: ' + ', '.join(service_names)
        },
        'servers': [],
        'security': [],
        'tags': [],
        'paths': {},
        'components': {
            'schemas': {},
            # keep structure open for potential future merges
            'responses': {},
            'parameters': {},
            'requestBodies': {},
            'securitySchemes': {},
        }
    }

    existing_schema_defs = {}

    # helper to add unique items to lists with simple dedup logic
    def add_unique_server(server):
        if not isinstance(server, dict):
            return
        if not any(s.get('url') == server.get('url') for s in combined['servers']):
            combined['servers'].append(server)

    def add_unique_security(sec):
        # security is a list of dicts
        if not isinstance(sec, list):
            return
        for item in sec:
            if isinstance(item, dict) and item not in combined['security']:
                combined['security'].append(item)

    def add_unique_tags(tags):
        if not isinstance(tags, list):
            return
        for t in tags:
            if t not in combined['tags']:
                combined['tags'].append(t)

    for doc, svc, prefix in zip(docs, service_names, path_prefixes):
        # servers
        for srv in doc.get('servers', []):
            add_unique_server(srv)

        # security
        add_unique_security(doc.get('security', []))

        # tags
        add_unique_tags(doc.get('tags', []))

        # components: schemas
        rename_map = {}
        schemas = doc.get('components', {}).get('schemas', {})
        for name, schema in schemas.items():
            if name not in combined['components']['schemas']:
                combined['components']['schemas'][name] = schema
                existing_schema_defs[name] = normalize_schema(schema)
                rename_map[name] = name
            else:
                # conflict: compare normalized shape
                if normalize_schema(schema) == existing_schema_defs[name]:
                    # identical, no rename
                    rename_map[name] = name
                else:
                    # rename using service prefix, ensure uniqueness
                    candidate = f"{svc}_{name}"
                    i = 2
                    while candidate in combined['components']['schemas']:
                        candidate = f"{svc}_{name}_{i}"
                        i += 1
                    combined['components']['schemas'][candidate] = schema
                    existing_schema_defs[candidate] = normalize_schema(schema)
                    rename_map[name] = candidate

        # components: copy other sections with minimal conflict handling
        comps = doc.get('components', {})
        for section in ['responses', 'parameters', 'requestBodies', 'securitySchemes']:
            sec_map = comps.get(section, {})
            if not isinstance(sec_map, dict):
                continue
            for k, v in sec_map.items():
                if k not in combined['components'][section]:
                    combined['components'][section][k] = v
                else:
                    # conflict: prefer existing, unless identical
                    if combined['components'][section][k] != v:
                        new_k = f"{svc}_{k}"
                        j = 2
                        while new_k in combined['components'][section]:
                            new_k = f"{svc}_{k}_{j}"
                            j += 1
                        combined['components'][section][new_k] = v

        # rewrite $ref in entire doc according to rename_map
        doc_rewritten = deep_replace_refs(doc, rename_map)

        # apply path prefix if present (e.g., '/mall-oms')
        paths_source = doc_rewritten.get('paths', {})
        if prefix:
            prefixed_paths = {}
            url_prefix = '/' + prefix.strip('/')
            for p, path_item in paths_source.items():
                p_norm = p if p.startswith('/') else '/' + p
                new_p = url_prefix + p_norm
                prefixed_paths[new_p] = path_item
            paths_to_merge = prefixed_paths
        else:
            paths_to_merge = paths_source

        # merge paths
        for path, path_item in paths_to_merge.items():
            if path not in combined['paths']:
                combined['paths'][path] = path_item
                continue
            # path exists: merge by HTTP method; conflict resolution by namespacing path
            existing = combined['paths'][path]
            for method, operation in path_item.items():
                if method in existing:
                    # conflict: create service-namespaced path to retain both operations
                    namespaced_path = f"/{svc}{path}"
                    combined.setdefault('paths', {})
                    combined['paths'].setdefault(namespaced_path, {})
                    combined['paths'][namespaced_path][method] = operation
                else:
                    existing[method] = operation

    return combined


def main():
    if len(sys.argv) < 6:
        print('Usage: python scripts/merge_openapi.py <output_path> <input1> <input2> <input3> <input4>')
        sys.exit(1)
    output_path = sys.argv[1]
    inputs = sys.argv[2:]

    docs = []
    svcs = []
    prefixes = []
    for p in inputs:
        doc = load_json(p)
        docs.append(doc)
        svcs.append(service_name_from_path(p))
        prefixes.append(path_prefix_from_path(p))

    combined = merge_openapi(docs, svcs, prefixes)
    save_json(output_path, combined)
    print(f'Combined OpenAPI written to: {output_path}')


if __name__ == '__main__':
    main()