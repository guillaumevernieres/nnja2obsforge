"""mapping.yaml: obsForge kinds and spaces with their NNJA sources."""

import fnmatch
import os

import yaml

DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'mapping.yaml')
KIND_KEYS = ('var', 'aliases', 'dir', 'epoch', 'meta', 'valid', 'err', 'window', 'preqc',
             'profile_reject', 'err_fixed', 'lat_range')
SOURCE_KEYS = {'source', 'files', 'cover', 'reader'}
WINDOW = {'cycles': [0, 6, 12, 18], 'half': 3, 'redate_error_ratio': 0.0, 'closed': False}


class Space:
    """One obsForge output (e.g. insitu_temp_profile_argo) and its NNJA sources."""

    def __init__(self, name, spec, kinds):
        bad = set(spec) - set(KIND_KEYS) - {'kind', 'enabled', 'note', 'sources'}
        bad |= {k for x in spec.get('sources') or [] for k in x} - SOURCE_KEYS
        if bad:
            raise ValueError(f'{name}: unknown keys {sorted(bad)} (unquoted comma?)')
        if spec.get('kind') not in kinds:
            raise ValueError(f"{name}: unknown kind {spec.get('kind')!r}")
        self.name = name
        self.kind_name = spec['kind']
        kind = dict(kinds[self.kind_name])
        kind.update({k: spec[k] for k in KIND_KEYS if k in spec})
        missing = [k for k in ('var', 'aliases', 'dir', 'epoch', 'meta') if k not in kind]
        if missing:
            raise ValueError(f'{name}: kind {self.kind_name} lacks {missing}')
        kind['window'] = dict(WINDOW, **(kind.get('window') or {}))
        kind.setdefault('valid', [-1e30, 1e30])
        kind.setdefault('err', 1.0)
        kind.setdefault('preqc', [0])
        self.kind = kind
        self.enabled = spec.get('enabled', True)
        self.note = spec.get('note', '')
        self.sources = spec.get('sources') or []
        for s in self.sources:
            s.setdefault('reader', 'ioda')
            s.setdefault('cover', [-3, 3])
            s.setdefault('files', ['.*'])

    @property
    def window(self):
        return self.kind['window']


def load(path=DEFAULT):
    with open(path) as f:
        m = yaml.safe_load(f)
    return [Space(n, s, m['kinds']) for n, s in m['spaces'].items()]


def select(spaces, patterns=None, include_disabled=False):
    """Spaces whose name matches any of the glob patterns (default: every enabled one).
    A space named exactly is used even when disabled."""
    if not patterns:
        return [s for s in spaces if s.enabled or include_disabled]
    out = []
    for s in spaces:
        exact = s.name in patterns
        if exact or (any(fnmatch.fnmatch(s.name, p) for p in patterns)
                     and (s.enabled or include_disabled)):
            out.append(s)
    return out


def table(spaces):
    """Markdown table of the mapping."""
    rows = ['| obsForge name | dir | variable | NNJA source(s) | notes |',
            '|---|---|---|---|---|']
    for s in spaces:
        src = '<br>'.join(f"`{x['source']}`" for x in s.sources)
        note = s.note + ('' if s.enabled else ' (off by default)')
        rows.append(f"| `{s.name}` | {s.kind['dir']} | {s.kind['var']} | {src} | {note.strip()} |")
    return '\n'.join(rows)
