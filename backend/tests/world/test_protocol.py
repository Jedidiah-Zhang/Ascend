"""模块接入协议门禁测试。

- 全部生产模块与练兵切片（含全新 momentum）必须零缺口；
- 判别力：人为破坏的模块必须报出对应类别；
- 豁免不腐烂：terrain 的登记豁免必须仍是承重的（移除豁免即出现缺口）。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ascend.world.evidence import protocol
from ascend.world.evidence.protocol import (
    ProtocolIssue,
    module_protocol_issues,
)
from ascend.world.meta.declarations import (
    InstanceDecl,
    MechanismDecl,
    ModulePack,
    Parent,
    SlotDecl,
    ValueDomain,
    When,
    Witness,
)
from ascend.world.modules import (
    clock,
    conservation,
    harvest,
    momentum,
    terrain,
    weather,
    worldgen,
)
from ascend.world.modules.weather import engine_inputs

#: 接入协议门禁覆盖的模块（toy 为测试夹具，不在此列）。
_PROTOCOL_MODULES = (
    clock.MODULE,
    engine_inputs.MODULE,
    weather.MODULE,
    worldgen.MODULE,
    terrain.MODULE,
    conservation.MODULE,
    harvest.MODULE,
    momentum.MODULE,
)


class TestProtocolGate:
    @pytest.mark.parametrize(
        "pack", _PROTOCOL_MODULES, ids=lambda pack: pack.id,
    )
    def test_module_has_no_issues(self, pack):
        assert module_protocol_issues(pack) == ()


class TestProtocolDiscrimination:
    def test_broken_module_reports_categories(self):
        """缺说明/证据/方程/边界/模数/研究元数据必须逐项报出。"""
        broken = ModulePack(
            id="drill.broken",
            version="1",
            instances=(
                InstanceDecl(id="global", kind="global", identity="singleton"),
            ),
            slots=(
                SlotDecl(
                    id="broken.value", on="global", persist="state",
                    domain=ValueDomain(kind="int", bits=64),
                    writer="broken.step", initial=0,
                ),
            ),
            mechanisms=(
                MechanismDecl(
                    id="broken.step", output="broken.value",
                    parents=(Parent("broken.value", "previous", lag=1),),
                    impl=lambda ctx: ctx.parent("previous"),
                    when=When("phase", "step"),
                    witnesses=(
                        Witness("b0", {"previous": 0}, (0,)),
                        Witness("b1", {"previous": 1}, (1,)),
                    ),
                ),
            ),
        )
        codes = {issue.code for issue in module_protocol_issues(broken)}
        assert codes == {
            "module.notes", "module.evidence", "slot.metadata",
            "mechanism.equation", "mechanism.boundary_cases",
            "parent.modulus", "parent.valid_domain",
        }

    def test_exemptions_are_load_bearing(self, monkeypatch):
        """terrain 的登记豁免必须仍是承重的：移除豁免即出现缺口。"""
        monkeypatch.setattr(protocol, "PROTOCOL_EXEMPTIONS", {})
        issues = module_protocol_issues(terrain.MODULE)
        assert issues
        assert all(
            issue.code in ("parent.modulus", "parent.valid_domain")
            for issue in issues
        )

    def test_exemptions_only_cover_registered_modules(self):
        assert set(protocol.PROTOCOL_EXEMPTIONS) == {"terrain"}

    def test_issue_str_is_readable(self):
        assert str(ProtocolIssue("module.notes", "drill.x")) == \
            "module.notes: drill.x"
