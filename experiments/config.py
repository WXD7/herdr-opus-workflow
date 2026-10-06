"""One validated configuration contract for CLI, browser and MCP tools. No model calls."""
import copy
import hashlib
import json
import re
from pathlib import Path

MODELS = {'claude-opus-5-5': 'Opus 5.5', 'claude-fable-5': 'Fable 5'}
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')
RULE_FILES = ('skills/dispatch-codex/SKILL.md', 'skills/_shared/plan.md',
              'skills/_shared/supervise.md', 'skills/dispatch-codex/references/driver.md')
DEFAULT_PROFILE = {'model': 'claude-opus-5-5', 'effort': 'max', 'workflow_ref': 'working-tree',
                   'subagents': True, 'max_depth': 3, 'max_subagents': 20}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def integer(value, lo, hi, field):
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f'{field} must be an integer in [{lo}, {hi}]')
    return value


def text(value, field, limit=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\0' in value:
        raise ValueError(f'{field}: nonempty text required (maximum {limit} characters)')
    return value.strip()


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,39}', value):
        raise ValueError('Invalid identifier')
    return value


def argv(value, field):
    if not isinstance(value, list) or not value or len(value) > 100:
        raise ValueError(f'{field} must be an argv array, not a shell string')
    return [text(v, field, 8192) for v in value]


def profile(raw):
    if not isinstance(raw, dict) or set(raw) - set(DEFAULT_PROFILE):
        raise ValueError('Unknown profile fields')
    p = DEFAULT_PROFILE | raw
    if p['model'] not in MODELS or p['effort'] not in EFFORTS:
        raise ValueError('Use an explicit supported model ID and effort; aliases/fallbacks are not accepted')
    if type(p['subagents']) is not bool:
        raise ValueError('subagents must be a boolean')
    integer(p['max_depth'], 1, 10, 'max_depth')
    integer(p['max_subagents'], 1, 20, 'max_subagents')
    ref = text(p['workflow_ref'], 'workflow_ref', 200)
    if ref.startswith('-') or any(c.isspace() for c in ref):
        raise ValueError('Invalid workflow ref')
    return p


def validate(raw):
    if not isinstance(raw, dict):
        raise ValueError('Configuration must be an object')
    allowed = {'title', 'repo', 'base_ref', 'task', 'acceptance', 'checks', 'groups',
               'max_parallel', 'timeout_minutes', 'services', 'baseline'}
    if set(raw) - allowed:
        raise ValueError('Unknown configuration fields: ' + ', '.join(sorted(set(raw) - allowed)))
    result = {'title': text(raw.get('title'), 'title', 160),
              'repo': str(Path(text(raw.get('repo'), 'repo')).expanduser().resolve()),
              'base_ref': text(raw.get('base_ref', 'HEAD'), 'base_ref', 200),
              'task': text(raw.get('task'), 'task', 32000)}
    if result['base_ref'].startswith('-'):
        raise ValueError('Invalid base_ref')
    acceptance = raw.get('acceptance', [])
    if not isinstance(acceptance, list) or not acceptance or len(acceptance) > 50:
        raise ValueError('Define shared acceptance criteria before dispatch')
    result['acceptance'] = [text(v, 'acceptance', 4000) for v in acceptance]
    checks = raw.get('checks', [])
    if not isinstance(checks, list) or len(checks) > 20:
        raise ValueError('Too many acceptance commands')
    result['checks'] = [argv(v, 'checks') for v in checks]
    groups = raw.get('groups')
    if not isinstance(groups, list) or not groups:
        raise ValueError('At least one configuration group is required')
    # No fixed group-count cap: request size and explicitly chosen concurrency bound resources.
    result['groups'], ids = [], set()
    for group in groups:
        if not isinstance(group, dict) or set(group) - {'id', 'label', 'profile'}:
            raise ValueError('Invalid group')
        gid = identifier(group.get('id'))
        if gid.lower() in ids:
            raise ValueError('Group identifiers must be unique (case insensitive)')
        ids.add(gid.lower())
        result['groups'].append({'id': gid, 'label': text(group.get('label', gid), 'label', 80),
                                 'profile': profile(group.get('profile', {}))})
    result['baseline'] = raw.get('baseline', result['groups'][0]['id'])
    if result['baseline'] not in [g['id'] for g in result['groups']]:
        raise ValueError('The baseline must name a group')
    result['max_parallel'] = integer(raw.get('max_parallel', 1), 1, len(groups), 'max_parallel')
    result['timeout_minutes'] = integer(raw.get('timeout_minutes', 120), 1, 1440, 'timeout_minutes')
    services, names = raw.get('services', []), set()
    if not isinstance(services, list) or len(services) > 20:
        raise ValueError('Invalid service list')
    result['services'] = []
    for service in services:
        if not isinstance(service, dict) or set(service) - {'name', 'argv', 'port_env', 'health_path', 'preferred_port'}:
            raise ValueError('Invalid service fields')
        name = identifier(service.get('name'))
        if name.upper() in names:
            raise ValueError('Service names must be unique')
        names.add(name.upper())
        env = service.get('port_env', name.upper().replace('-', '_') + '_PORT')
        if not isinstance(env, str) or not re.fullmatch(r'(?:PORT|[A-Z][A-Z0-9_]*_PORT)', env) or env.startswith(('HERDR_', 'CLAUDE_', 'ANTHROPIC_', 'CODEX_', 'OPENAI_', 'OTEL_', 'LD_', 'DYLD_', 'PYTHON')):
            raise ValueError('Use a service-specific port environment variable')
        health = service.get('health_path', '/')
        if not isinstance(health, str) or not health.startswith('/') or health.startswith('//') or '\r' in health or '\n' in health:
            raise ValueError('health_path must be a local HTTP path')
        port = service.get('preferred_port', 0)
        integer(port, 0, 65535, 'preferred_port')
        if 0 < port < 1024:
            raise ValueError('Use an unprivileged port or 0 for OS allocation')
        result['services'].append({'name': name, 'argv': argv(service.get('argv'), 'service argv'),
                                   'port_env': env, 'health_path': health, 'preferred_port': port})
    if len({s['port_env'] for s in result['services']}) != len(result['services']):
        raise ValueError('Each service needs a distinct port_env')
    return result


def differences(config):
    base = next(g['profile'] for g in config['groups'] if g['id'] == config['baseline'])
    return {g['id']: {k: {'baseline': base[k], 'value': v}
                     for k, v in g['profile'].items() if base[k] != v} for g in config['groups']}


def apply_patch(config, operations):
    """Structured changes are shared by natural-language tools and browser controls."""
    result = copy.deepcopy(config)
    if not isinstance(operations, list) or not 1 <= len(operations) <= 100:
        raise ValueError('Provide 1–100 explicit operations')
    for op in operations:
        if not isinstance(op, dict):
            raise ValueError('Invalid operation')
        action = op.get('op')
        groups = {g['id'].lower(): g for g in result['groups']}
        if action == 'parallel' and set(op) == {'op', 'value'}:
            result['max_parallel'] = op['value']
        elif action == 'baseline' and set(op) == {'op', 'group'}:
            result['baseline'] = groups[op['group'].lower()]['id']
        elif action == 'clone' and set(op) <= {'op', 'group', 'id', 'label'}:
            new = copy.deepcopy(groups[op['group'].lower()])
            new.update(id=op['id'], label=op.get('label', op['id']))
            result['groups'].append(new)
        elif action == 'remove' and set(op) == {'op', 'group'}:
            gid = groups[op['group'].lower()]['id']
            result['groups'] = [g for g in result['groups'] if g['id'] != gid]
            if result['baseline'] == gid and result['groups']:
                result['baseline'] = result['groups'][0]['id']
            result['max_parallel'] = min(result['max_parallel'], len(result['groups']))
        elif action == 'profile' and set(op) == {'op', 'group', 'values'}:
            group = groups[op['group'].lower()]
            group['profile'] = profile(group['profile'] | op['values'])
        else:
            raise ValueError('Unsupported operation')
    return validate(result)


def parse_instruction(instruction):
    """Small, explicit local grammar. Arbitrary language is mapped by Dot to apply_patch.

    Never silently ignore an unparsed clause and never invoke an LLM to edit a form.
    """
    instruction = text(instruction, 'instruction', 2000)
    operations = []
    for clause in re.split(r'[；;\n]+', instruction):
        clause = clause.strip().rstrip('。')
        match = re.fullmatch(r'(?:复制|clone)\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:组)?\s*(?:为|到|to)\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:组)?', clause, re.I)
        if match:
            operations.append({'op': 'clone', 'group': match[1], 'id': match[2]}); continue
        match = re.fullmatch(r'(?:同时运行|并行|parallel)\s*(\d+)\s*(?:组)?', clause, re.I)
        if match:
            operations.append({'op': 'parallel', 'value': int(match[1])}); continue
        match = re.fullmatch(r'(?:删除|移除|remove)\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:组)?', clause, re.I)
        if match:
            operations.append({'op': 'remove', 'group': match[1]}); continue
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_-]*)\s*(?:组)?\s*(?:改为|设为|用|=)\s*(.+)', clause, re.I)
        if not match:
            raise ValueError('无法完整解析。示例：复制 A 为 D；D 用 Fable 5 medium；并行 2 组。复杂修改可由 Dot 调用结构化配置工具。')
        gid, value = match[1], match[2].strip()
        values = {}
        for pattern, model in ((r'(?:claude-)?opus[- ]?5[.\-]5', 'claude-opus-5-5'),
                               (r'(?:claude-)?fable[- ]?5(?![.\d])', 'claude-fable-5')):
            if re.search(pattern, value, re.I):
                values['model'] = model
                value = re.sub(pattern, '', value, flags=re.I).strip()
        found = re.fullmatch(r'(?:思考强度\s*)?(low|medium|high|xhigh|max)', value, re.I)
        if found:
            values['effort'] = found[1].lower(); value = ''
        if value:
            found = re.fullmatch(r'(?:文档|workflow)\s+([\w./-]+)', value, re.I)
            if found:
                values['workflow_ref'] = found[1]; value = ''
        if value in ('关闭子代理', '关闭子 Agent'):
            values['subagents'] = False; value = ''
        elif value in ('开启子代理', '开启子 Agent'):
            values['subagents'] = True; value = ''
        if value or not values:
            raise ValueError('无法完整解析这一组配置：' + clause)
        operations.append({'op': 'profile', 'group': gid, 'values': values})
    return operations


def environment(p):
    p = profile(p)
    return {'CLAUDE_CODE_SUBAGENT_MODEL': p['model'], 'CLAUDE_CODE_SUBAGENT_MODEL_FORCE': '1',
            'CLAUDE_CODE_EFFORT_LEVEL': p['effort'],
            'CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH': str(p['max_depth']),
            'CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS': str(p['max_subagents'])}
