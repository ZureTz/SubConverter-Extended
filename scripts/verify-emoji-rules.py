#!/usr/bin/env python3
"""Check first-match PCRE2 emoji rules; optionally test HTTP output with Mihomo.

Local checks require pcre2grep and Python 3.11+. HTTP checks additionally require
PyYAML; pass --endpoint, --mihomo and --work-dir to exercise the real converter.
Only synthetic Shadowsocks nodes are used. No connections to them are made.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import tomllib
import urllib.parse
import urllib.request

# Hand-picked names exercise cities, country codes, Chinese variants and priority.
SAMPLES = [
    ('🇲🇾', ['Malaysia', '马来西亚', '馬來西亞', '吉隆坡', 'Kuala Lumpur',
              'Kuala-Lumpur', 'Penang', '檳城', 'Johor Bahru', '新山', 'MY01', 'MYS_02', 'node_MY_03']),
    ('🇳🇿', ['新西兰', 'New Zealand', '紐西蘭', 'NZ02']),
    ('🇳🇬', ['尼日利亚', 'Nigeria', 'NG01']),
    ('🇧🇩', ['Bangladesh', '孟加拉', 'Dhaka', 'BD01']),
    ('🇳🇵', ['Nepal', '尼泊爾', 'Kathmandu', 'NP01']),
    ('🇰🇭', ['Cambodia', '金邊', 'KH01']),
    ('🇲🇲', ['Myanmar', '緬甸', 'Yangon', 'MM01']),
    ('🇱🇹', ['Lithuania', '立陶宛', 'LT01']),
    ('🇸🇰', ['Slovakia', '斯洛伐克', 'SK01']),
    ('🇿🇦', ['South Africa', '南非', 'Cape Town', 'ZA01']),
    ('🇺🇦', ['Ukraine', '烏克蘭', 'Kyiv', 'UA01']),
    ('🇱🇦', ['Laos', '老挝', '寮國', 'Vientiane', 'LA01']),
    ('🇧🇳', ['Brunei', '文莱', 'BN01']),
    ('🇱🇰', ['Sri Lanka', '斯里蘭卡', 'Colombo', 'LK01']),
    ('🇧🇹', ['Bhutan', '不丹', 'BT01']),
    ('🇲🇻', ['Maldives', '馬爾代夫', 'MV01']),
    ('🇺🇿', ['Uzbekistan', '塔什干', 'UZ01']),
    ('🇹🇯', ['Tajikistan', '杜尚別', 'TJ01']),
    ('🇹🇲', ['Turkmenistan', '土庫曼斯坦', 'TM01']),
    ('🇦🇲', ['Armenia', '亚美尼亚', 'AM01']),
    ('🇧🇭', ['Bahrain', '巴林', 'BH01']),
    ('🇶🇦', ['Qatar', '卡達', 'Doha', 'QA01']),
    ('🇴🇲', ['Oman', '阿曼', 'Muscat', 'OM01']),
    ('🇰🇼', ['Kuwait', '科威特', 'KW01']),
    ('🇮🇶', ['Iraq', '伊拉克', 'IQ01']),
    ('🇱🇧', ['Lebanon', '貝魯特', 'LB01']),
    ('🇩🇿', ['Algeria', '阿爾及利亞', 'DZ01']),
    ('🇬🇭', ['Ghana', '迦納', 'GH01']),
    ('🇲🇺', ['Mauritius', '模里西斯', 'MU01']),
    ('🇸🇨', ['Seychelles', '塞席爾', 'SC01']),
    ('🇲🇬', ['Madagascar', '馬達加斯加', 'MG01']),
    ('🇹🇿', ['Tanzania', '坦桑尼亚', 'TZ01']),
    ('🇺🇬', ['Uganda', '烏干達', 'UG01']),
    ('🇷🇼', ['Rwanda', '盧安達', 'RW01']),
    ('🇧🇴', ['Bolivia', '玻利維亞', 'BO01']),
    ('🇵🇾', ['Paraguay', '巴拉圭', 'PY01']),
    ('🇬🇹', ['Guatemala', '瓜地馬拉', 'GT01']),
    ('🇯🇲', ['Jamaica', '牙買加', 'JM01']),
    ('🇫🇯', ['Fiji', '斐濟', 'FJ01']),
    ('🇵🇬', ['Papua New Guinea', '巴布亚新几内亚', 'PG01']),
    ('🇻🇪', ['Venezuela', '委內瑞拉', 'VE01']),
    # Existing destinations and ambiguous substrings must keep their flags.
    ('🇷🇴', ['Romania']), ('🇦🇺', ['Australia']), ('🇸🇬', ['Singapore', 'SG01']),
    ('🇭🇰', ['Hong Kong', 'HK01']), ('🇨🇳', ['Taiwan', 'TW01']),
    ('🇯🇵', ['Japan', 'JP01']), ('🇺🇸', ['United States', 'US01']),
    ('🇬🇧', ['United Kingdom']), ('🇰🇷', ['South Korea']), ('🇰🇵', ['North Korea']),
    ('🇩🇪', ['Germany']), ('🇨🇦', ['Canada']), ('🇫🇷', ['France']),
    ('🇻🇦', ['Vatican', '梵蒂岡']), ('🇹🇭', ['Thailand']), ('🇻🇳', ['Vietnam']), ('🇮🇩', ['Indonesia']),
    ('🇮🇳', ['India']), ('🏳️‍🌈', ['Malaysia 剩余流量', 'Qatar expire']),
    ('', ['MYSELF', 'DUMMY', 'PREMIUM', 'EQUAL', 'Laoshi', 'Roman', 'Africa', 'mystery-01']),
]


def cases():
    result = {}
    for flag, names in SAMPLES:
        for name in names:
            for variant in (name, name.lower(), name.upper(), 'node-' + name):
                assert variant not in result or result[variant] == flag
                result[variant] = flag
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pcre2grep', default='pcre2grep')
    parser.add_argument('--endpoint')
    parser.add_argument('--mihomo', type=Path)
    parser.add_argument('--work-dir', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    rules = tomllib.loads((root / 'base/snippets/emoji.toml').read_text())['emoji']
    txt = [line.rsplit(',', 1) for line in
           (root / 'base/snippets/emoji.txt').read_text().splitlines() if line.strip()]
    assert txt == [[r['match'], r['emoji']] for r in rules], 'TXT/TOML rules differ'
    expected = cases()
    names = list(expected)
    actual = dict.fromkeys(names, '')
    matched = set()
    for rule in rules:
        result = subprocess.run([args.pcre2grep, '-u', '-n', '--', rule['match']],
                                input='\n'.join(names) + '\n', text=True, capture_output=True)
        assert result.returncode in (0, 1), (rule, result.stderr)
        for line in result.stdout.splitlines():
            name = names[int(line.split(':', 1)[0]) - 1]
            if name not in matched:
                actual[name] = rule['emoji']
                matched.add(name)
    errors = [(name, expected[name], actual[name]) for name in names
              if expected[name] != actual[name]]
    assert not errors, errors
    print(f'PASS: {len(names)} first-match PCRE2 cases; {len(rules)} rules identical in TXT/TOML')
    if not args.endpoint:
        return
    if not args.mihomo or not args.work_dir:
        parser.error('--endpoint requires --mihomo and --work-dir')
    import yaml
    args.work_dir.mkdir(parents=True, exist_ok=True)
    auth = base64.urlsafe_b64encode(b'aes-128-gcm:emoji-test-password').decode().rstrip('=')
    proxies = []

    def convert(batch, enabled):
        links = '|'.join(f'ss://{auth}@127.0.0.1:{11000+i}#' + urllib.parse.quote(name)
                         for i, name in enumerate(batch))
        query = urllib.parse.urlencode({'target': 'clash', 'url': links, 'list': 'true',
                                        'emoji': str(enabled).lower(), 'insert': 'false',
                                        'expand': 'true', 'new_name': 'true'})
        with urllib.request.urlopen(args.endpoint.rstrip('/') + '/sub?' + query, timeout=90) as response:
            data = yaml.safe_load(response.read())
        wanted = [(expected[name] + ' ' if enabled and expected[name] else '') + name
                  for name in batch]
        got = [node['name'] for node in data['proxies']]
        assert got == wanted, list(zip(wanted, got))
        return data['proxies']

    for offset in range(0, len(names), 12):
        proxies.extend(convert(names[offset:offset+12], True))
    convert(names[:12], False)
    config = args.work_dir / 'emoji.yaml'
    config.write_text(yaml.safe_dump({'mixed-port': 17890, 'mode': 'rule',
                                     'proxies': proxies, 'rules': ['MATCH,DIRECT']}, allow_unicode=True))
    result = subprocess.run([str(args.mihomo), '-t', '-d', str(args.work_dir), '-f', str(config)],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    summary = {'cases': len(names), 'rules': len(rules), 'emoji_disabled': 'passed', 'mihomo': 'passed'}
    (args.work_dir / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f'PASS: {len(names)} HTTP node names, emoji=false, real Mihomo configuration validation')


if __name__ == '__main__':
    main()
