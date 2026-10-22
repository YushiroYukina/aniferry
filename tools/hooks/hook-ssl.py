"""Retain auxiliary trust-policy data required by the selected Python runtime.

Standard python.org builds do not require this file. Some managed runtimes
load it in SSLContext.__new__; omitting it breaks HTTPS before any connection.
Preserve the runtime policy unchanged instead of replacing the SSL context.
"""
from pathlib import Path
import ssl

datas=[]
source=Path(ssl.__file__)
policy_name='codex-rejected-cas.pem'
if policy_name in source.read_text(encoding='utf-8'):
    policy=source.with_name(policy_name)
    if not policy.is_file():
        raise RuntimeError('The selected Python runtime is missing its required SSL policy data')
    datas.append((str(policy),'.'))
