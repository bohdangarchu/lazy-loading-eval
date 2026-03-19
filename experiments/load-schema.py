#!/usr/bin/env python3
"""Usage: eval "$(python3 /path/to/load-schema.py /path/to/schema.yaml)"
Outputs shell export statements for all schema values."""
import yaml, sys

s = yaml.safe_load(open(sys.argv[1]))
imgs = s.get('images', {})

def v(val):
    return "'" + str(val).replace("'", "'\\''") + "'"

pairs = [
    ('REGISTRY_NODE',         s.get('registry_node', '')),
    ('BASE_IMAGE',            s.get('base_image', '')),
    ('SPLITS',                s.get('splits', '')),
    ('REFRESH_INDEX',         s.get('refresh_index', '')),
    ('IMG_BASE_NAME',         imgs.get('base', {}).get('name', '')),
    ('IMG_STARGZ_NAME',       imgs.get('stargz', {}).get('name', '')),
    ('IMG_STARGZ_TAG',        imgs.get('stargz', {}).get('tag', '')),
    ('IMG_2DFS_NAME',         imgs.get('2dfs', {}).get('name', '')),
    ('IMG_2DFS_TAG',          imgs.get('2dfs', {}).get('tag', '')),
    ('IMG_2DFS_PATH',         imgs.get('2dfs', {}).get('registry_path', '')),
    ('IMG_2DFS_STARGZ_NAME',  imgs.get('2dfs_stargz', {}).get('name', '')),
    ('IMG_2DFS_STARGZ_TAG',   imgs.get('2dfs_stargz', {}).get('tag', '')),
    ('IMG_2DFS_STARGZ_PATH',  imgs.get('2dfs_stargz', {}).get('registry_path', '')),
]

for key, val in pairs:
    print(f'export {key}={v(val)}')
