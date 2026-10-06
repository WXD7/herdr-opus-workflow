"""Resolve acceptance executables before paying for a run; never execute a shell string."""
import os
from pathlib import Path
import shutil


def resolve_checks(commands, cwd):
    resolved = []
    for command in commands:
        argv = list(command)
        executable = argv[0]
        # These two spellings have the same POSIX test semantics. Preserve both
        # requested and actual argv in evidence instead of editing a frozen spec.
        if executable in ('/usr/bin/test', '/bin/test') and not Path(executable).is_file():
            executable = shutil.which('test') or ''
        elif '/' not in executable:
            executable = shutil.which(executable) or ''
        elif not Path(executable).is_absolute():
            executable = str(Path(cwd) / executable)
        if not executable or not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise ValueError('Acceptance executable unavailable before launch: ' + command[0])
        argv[0] = executable
        resolved.append({'requested_argv': command, 'argv': argv})
    if not resolved:
        raise ValueError('At least one independent acceptance command is required before launch')
    return resolved
