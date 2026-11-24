"""运行原回归与换端核验；任一失败以非零退出。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMMANDS = (
    ('原回归', [sys.executable, '-B', 'check_project.py']),
    ('单端演练', [sys.executable, '-B', 'workbench.py', 'demo']),
    ('V1 换端对照', [sys.executable, '-B', 'handoff_fixture.py']),
    ('换端演练', [sys.executable, '-B', '-m', 'handoff.drill']),
)


def main() -> int:
    failures = []
    for label, command in COMMANDS:
        print(f'== {label}: {" ".join(command[1:])}', flush=True)
        completed = subprocess.run(command, cwd=ROOT)
        if completed.returncode != 0:
            failures.append(label)
    if failures:
        print(f'VERIFY FAILED: {", ".join(failures)}', file=sys.stderr)
        return 1
    print(f'VERIFY OK: {len(COMMANDS)} 项检查通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
