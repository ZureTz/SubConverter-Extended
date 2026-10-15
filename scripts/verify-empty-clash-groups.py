#!/usr/bin/env python3
"""Verify empty-group conversion through HTTP and a real Mihomo executable.

Requires PyYAML. Prepare fixtures, serve them where the converter can reach
them, then run again without --prepare-only. All nodes are synthetic.
"""
import argparse
import base64
import json
from pathlib import Path
import re
import shutil
import subprocess
import urllib.parse
import urllib.request

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', default='http://127.0.0.1:25501')
    parser.add_argument('--fixture-url', required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--acl4ssr-config', type=Path, required=True)
    parser.add_argument('--mihomo', type=Path)
    parser.add_argument('--geoip', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    fixtures = args.work_dir / 'fixtures'
    fixtures.mkdir(parents=True, exist_ok=True)
    fixture_url = args.fixture_url.rstrip('/')
    base = 'mixed-port: 17890\nmode: rule\nlog-level: warning\n'
    (fixtures / 'base.yaml').write_text(base)
    full = args.acl4ssr_config.read_text()
    definitions = [line for line in full.splitlines()
                   if line.startswith('custom_proxy_group=')]
    countries = [line.split('=', 1)[1].split('`') for line in definitions
                 if line.split('`')[0].endswith('节点') and '`url-test`' in line]
    country_names = {parts[0] for parts in countries}
    assert len(countries) == 23
    group_config = '\n'.join(definitions)
    prefix = ('[custom]\nenable_rule_generator=true\n'
              'overwrite_original_rules=true\n'
              f'clash_rule_base={fixture_url}/base.yaml\n')
    (fixtures / 'acl-groups.ini').write_text(
        prefix + 'ruleset=🚀 节点选择,[]FINAL\n' + group_config + '\n')
    (fixtures / 'acl-full.ini').write_text(full)
    edge_groups = [
        'Root`select`[]Grandparent`[]Empty`[]Direct`[]Matched',
        'Grandparent`select`[]Parent',
        'Parent`fallback`[]Empty`http://www.gstatic.com/generate_204`300',
        'Empty`url-test`^NO_MATCH$`http://www.gstatic.com/generate_204`300',
        'Direct`select`[]DIRECT',
        'Reject`select`[]REJECT',
        'Matched`url-test`^HK-01$`http://www.gstatic.com/generate_204`300',
    ]
    (fixtures / 'edges.ini').write_text(
        prefix + 'ruleset=Root,[]FINAL\n' + '\n'.join(
            'custom_proxy_group=' + group for group in edge_groups) + '\n')
    (fixtures / 'provider.yaml').write_text(yaml.safe_dump({'proxies': [
        {'name': 'HK-01', 'type': 'ss', 'server': '127.0.0.1', 'port': 10000,
         'cipher': 'aes-128-gcm', 'password': 'test-password'}]}))
    if args.prepare_only:
        print(f'Prepared fixtures in {fixtures}')
        return
    if not args.mihomo:
        parser.error('--mihomo is required for verification')
    outputs = args.work_dir / 'results'
    outputs.mkdir(exist_ok=True)
    if args.geoip:
        shutil.copyfile(args.geoip, outputs / 'Country.mmdb')
    auth = base64.urlsafe_b64encode(b'aes-128-gcm:test-password').decode().rstrip('=')

    def links(names):
        return '|'.join(f'ss://{auth}@127.0.0.1:{10000+i}#' + urllib.parse.quote(name)
                        for i, name in enumerate(names))

    def convert(label, config, names, provider=False):
        url = fixture_url + '/provider.yaml' if provider else links(names)
        query = urllib.parse.urlencode({
            'target': 'clash', 'url': url, 'config': fixture_url + '/' + config,
            'emoji': 'false', 'new_name': 'true', 'expand': 'true', 'insert': 'false',
        })
        with urllib.request.urlopen(args.endpoint.rstrip('/') + '/sub?' + query,
                                    timeout=300) as response:
            text = response.read().decode()
        data = yaml.safe_load(text)
        assert isinstance(data, dict) and data.get('proxy-groups'), text[:500]
        groups = {g['name']: g for g in data['proxy-groups']}
        valid = set(groups) | {p['name'] for p in data.get('proxies', [])} | {
            'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'COMPATIBLE'}
        for group in groups.values():
            assert group.get('proxies') or group.get('use'), group
            assert set(group.get('proxies', [])) <= valid, group
        if config.startswith('acl-') and not provider:
            expected = {parts[0] for parts in countries
                        if any(re.search(parts[2], n) for n in names)}
            assert set(groups) & country_names == expected, (label, set(groups), expected)
            for name in expected:
                assert groups[name]['proxies'] != ['DIRECT']
        path = outputs / (label + '.yaml')
        path.write_text(text)
        # The provider URL is reachable inside Docker. Supply its synthetic
        # contents locally for offline core validation without changing output.
        for p in data.get('proxy-providers', {}).values():
            provider_path = outputs / p['path']
            provider_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(fixtures / 'provider.yaml', provider_path)
        checked = subprocess.run([str(args.mihomo), '-t', '-d', str(outputs),
                                  '-f', str(path)], capture_output=True, text=True)
        (outputs / (label + '.log')).write_text(checked.stdout + checked.stderr)
        assert checked.returncode == 0, checked.stdout + checked.stderr
        print(f'PASS {label}: {len(groups)} groups; mihomo -t passed', flush=True)
        return groups

    cases = 0
    convert('no-country-match', 'acl-groups.ini', ['Unclassified-01']); cases += 1
    convert('partial-match', 'acl-groups.ini', ['香港-01', '日本-01', 'US-01']); cases += 1
    samples = [parts[2][1:].split('|')[0] + '-01' for parts in countries]
    for index, sample in enumerate(samples):
        convert(f'country-{index+1:02d}', 'acl-groups.ini', [sample]); cases += 1
    convert('all-countries', 'acl-groups.ini', samples); cases += 1
    edges = convert('nested-empty-groups', 'edges.ini', ['HK-01']); cases += 1
    assert not {'Empty', 'Parent', 'Grandparent'} & set(edges)
    assert edges['Root']['proxies'] == ['Direct', 'Matched']
    assert edges['Direct']['proxies'] == ['DIRECT']
    assert edges['Reject']['proxies'] == ['REJECT']
    dynamic = convert('provider-groups', 'edges.ini', [], provider=True); cases += 1
    assert 'Empty' in dynamic and dynamic['Empty'].get('use')
    convert('acl-online-full', 'acl-full.ini', ['香港-01', '日本-01', 'US-01']); cases += 1
    (outputs / 'summary.json').write_text(json.dumps({'passed': cases}, indent=2))
    print(f'All {cases} conversion and Mihomo checks passed.')


if __name__ == '__main__':
    main()
