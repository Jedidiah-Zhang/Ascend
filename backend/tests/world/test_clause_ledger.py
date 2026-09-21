"""条款↔证据对账门禁测试（WC-11.2）。

- 对账表与契约正文、验收判据码、证据文件一致；
- 判别力：未知条款 / 悬空检查码 / 缺证据文件 / partial 无 note 必须被抓住。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "research" / "acceptance"))
sys.path.insert(0, str(ROOT / "backend"))

import clause_evidence  # noqa: E402
import clause_ledger  # noqa: E402


class TestClauseLedger:
    def test_ledger_is_consistent(self):
        assert clause_ledger.validate() == ()

    def test_every_contract_clause_is_registered(self):
        declared = set(clause_ledger.contract_clauses())
        registered = {entry.clause for entry in clause_evidence.CLAUSES}
        assert declared == registered

    def test_partial_entries_have_notes(self):
        for entry in clause_evidence.CLAUSES:
            if entry.status != "covered":
                assert entry.note.strip(), entry.clause


class TestLedgerDiscrimination:
    def _validate_with(self, monkeypatch, entries):
        monkeypatch.setattr(clause_evidence, "CLAUSES", entries)
        return clause_ledger.validate()

    def test_unknown_clause_caught(self, monkeypatch):
        broken = clause_evidence.CLAUSES + (
            replace(clause_evidence.CLAUSES[0], clause="WC-99.9"),
        )
        issues = self._validate_with(monkeypatch, broken)
        assert any("未知条款" in issue for issue in issues)

    def test_missing_clause_caught(self, monkeypatch):
        issues = self._validate_with(
            monkeypatch, clause_evidence.CLAUSES[1:],
        )
        assert any("条款未登记" in issue for issue in issues)

    def test_dangling_check_code_caught(self, monkeypatch):
        broken = tuple(
            replace(entry, checks=("XX-0",))
            if entry.status == "covered" and entry.checks
            else entry
            for entry in clause_evidence.CLAUSES
        )
        issues = self._validate_with(monkeypatch, broken)
        assert any("未知检查码" in issue for issue in issues)

    def test_missing_evidence_file_caught(self, monkeypatch):
        broken = (
            replace(
                clause_evidence.CLAUSES[0],
                evidence=("backend/tests/world/no_such_test.py",),
            ),
        ) + clause_evidence.CLAUSES[1:]
        issues = self._validate_with(monkeypatch, broken)
        assert any("证据文件不存在" in issue for issue in issues)

    def test_partial_without_note_caught(self, monkeypatch):
        broken = tuple(
            replace(entry, note="")
            if entry.status == "partial"
            else entry
            for entry in clause_evidence.CLAUSES
        )
        issues = self._validate_with(monkeypatch, broken)
        assert any("必须给出 note" in issue for issue in issues)
