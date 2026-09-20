"""新验收 runner 测试（P2-3c-2）— 判据全绿 + 变异探针全部检出。

判据与变异定义在 ``research/acceptance/world_checks.py`` 与
``run_acceptance.py``；本测试保证 runner 的判别力不是徒有其表。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "research" / "acceptance"))
sys.path.insert(0, str(ROOT / "backend"))

import run_acceptance  # noqa: E402
import world_checks  # noqa: E402


class TestAcceptanceChecks:
    def test_all_checks_pass(self):
        results = [check() for check in world_checks.ALL_CHECKS]
        failures = [
            f"{result.code}: {result.detail}"
            for result in results
            if not result.passed
        ]
        assert not failures, "; ".join(failures)

    def test_check_codes_unique(self):
        codes = [check().code for check in world_checks.ALL_CHECKS]
        assert len(set(codes)) == len(codes)

    def test_manifest_binds_identity(self):
        manifest = run_acceptance.build_manifest()
        program = world_checks._world_program()
        assert manifest["world_program_identity"] == program.identity
        assert manifest["module_digests"] == dict(program.module_digests)


class TestMutationProbes:
    def test_all_mutations_caught(self):
        import inspect

        failures = []
        for name, mutate, check in run_acceptance.MUTATIONS:
            target, (attr, value) = mutate()
            original = inspect.getattr_static(target, attr)
            setattr(target, attr, value)
            try:
                caught = not check().passed
            except Exception:
                caught = True
            finally:
                setattr(target, attr, original)
            if not caught:
                failures.append(name)
        assert not failures, failures
