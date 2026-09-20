#!/usr/bin/env python3
"""新声明 → 研究投影（equations.json 同 schema）。

事实源 = ``WorldProgram``（``ascend.world.compile``）；本模块把声明投影为
研究侧 schema（nodes/mechanisms/parameters/edges/variables/declaration），
供 ``gen_lean``/``graph_check``/``verify_equations`` 等消费。

版本摘要由新声明计算（equation_version = 机制摘要；resolved_version =
方程 + 参数 + 边界组合），与旧注册表摘要不同——迁移即新世界身份（WC-1.2）。
"""

from __future__ import annotations

import json
from typing import Mapping

from ascend.world import Schedule, WorldSpec, compile_world
from ascend.world.kernel import digest_object
from ascend.world.meta.declarations import (
    MechanismDecl,
    ModulePack,
    ParameterDecl,
    Parent,
    SlotDecl,
)
from ascend.world.meta.validate import mechanism_digest
from ascend.world.modules import weather, worldgen
from ascend.world.modules.pipeline import PIPELINE_PHASES
from ascend.world.modules.weather.core import WIRED_OUTPUTS

SCHEMA_VERSION = 3
DECLARATION_ID = "ascend.world.scalar_formulas"
DECLARATION_VERSION = "1"
SLICE_BOUNDARY = (
    "Generation programs (continent / hydrology / tile / weather field) "
    "are declared in ascend/world/generation.py with content fingerprints "
    "(sources + constants + version) and sampling protocols; their "
    "outputs enter this slice as declared boundary inputs. Content data "
    "(data/*.json) remains content rather than algorithm: changing it "
    "changes parameters, not the declaration."
)

_WEATHER_DEPS = (
    "ascend/world/modules/weather/equations.py",
    "ascend/world/kernel/fixed.py",
    "ascend/world/kernel/tables.py",
    "ascend/world/kernel/diurnal.py",
    "ascend/world/kernel/frozen_tables.py",
    "ascend/config.py",
    "data/world.json",
)
_WORLDGEN_DEPS = _WEATHER_DEPS + (
    "ascend/world/modules/worldgen/equations.py",
    "ascend/world/modules/worldgen/gen_fixed.py",
    "ascend/space/climate.py",
    "ascend/space/biome.py",
    "data/climate.json",
)


def build_program() -> object:
    """编译标量公式切片（世界生成 + 天气）。"""
    return compile_world(
        WorldSpec(
            modules=(worldgen.MODULE, weather.MODULE),
            schedule=Schedule(phases=PIPELINE_PHASES),
        )
    )


def project(program: object) -> dict[str, object]:
    """WorldProgram → 研究投影字典。"""
    nodes = {
        slot.id: _node(slot, program)
        for slot in sorted(program.slots.values(), key=lambda item: item.id)
    }
    mechanisms = {
        mechanism.id: _mechanism(mechanism, program)
        for mechanism in sorted(
            program.mechanisms.values(), key=lambda item: item.id
        )
    }
    parameters = {
        declaration.id: _parameter(declaration, program)
        for declaration in _parameters(program)
    }
    variables = {
        slot_id: _variable(node)
        for slot_id, node in nodes.items()
    }
    edges = [
        _edge(mechanism, parent, program)
        for mechanism in sorted(
            program.mechanisms.values(), key=lambda item: item.id
        )
        for parent in mechanism.parents
    ]
    declaration = {
        "id": DECLARATION_ID,
        "version": DECLARATION_VERSION,
        "microstep_order": list(PIPELINE_PHASES),
        "slice_boundary": SLICE_BOUNDARY,
        "wired_nodes": sorted(WIRED_OUTPUTS),
    }
    declaration["hash"] = digest_object(
        {
            "declaration": declaration,
            "nodes": nodes,
            "mechanisms": mechanisms,
            "parameters": parameters,
            "edges": edges,
        }
    )
    return {
        "comment": (
            "AUTO-GENERATED from the Ascend world declaration "
            "(ascend.world.compile; modules: worldgen + weather)."
        ),
        "declaration": declaration,
        "edges": edges,
        "exogenous_sources": {},
        "mechanisms": mechanisms,
        "nodes": nodes,
        "parameters": parameters,
        "schema_version": SCHEMA_VERSION,
        "variables": variables,
        "version": SCHEMA_VERSION,
    }


def to_json(program: object | None = None) -> str:
    """稳定 JSON 文本（生成物）。"""
    return json.dumps(
        project(program or build_program()),
        ensure_ascii=False,
        indent=1,
        sort_keys=True,
    ) + "\n"


# ── 节点 / 变量 ─────────────────────────────────────────────────


def _node(slot: SlotDecl, program: object) -> dict[str, object]:
    instance = program.instances[slot.on]
    if instance.kind == "global":
        instance_domain = {
            "axes": [],
            "creation": "world_initialization",
            "destruction": "world_teardown",
            "kind": "global_singleton",
        }
    else:
        instance_domain = {
            "axes": list(instance.axes),
            "creation": "chunk_registration",
            "destruction": "chunk_unregistration",
            "kind": "spatial_field",
        }
    if slot.persist == "external":
        boundary = _boundary_update(slot)
        state = boundary["state"]
        update = boundary["update"]
    else:
        state = {
            "in_world_state": slot.persist == "state",
            "reconstruction": slot.writer or "not_applicable",
        }
        update = {
            "merge_rule": "single_writer",
            "microstep": _phase_of(slot.writer, program),
            "schedule": slot.schedule,
            "when_not_updated": (
                "retain_previous_value"
                if slot.persist == "state"
                else "recompute_on_demand"
            ),
            "writer": slot.writer or "",
        }
    return {
        "access": {
            "interventions": list(slot.access_interventions),
            "observation_protocols": list(slot.observation_protocols),
            "research_trace": slot.research_trace,
        },
        "instance_domain": instance_domain,
        "math": {
            "error_budget": slot.epsilon,
            "metric": slot.metric,
            "valid_domain": slot.valid_domain,
        },
        "origin": "mechanism" if slot.persist != "external" else "slice_boundary",
        "role": slot.role,
        "state": state,
        "update": update,
        "value": {
            "bounds": (
                [slot.domain.minimum, slot.domain.maximum]
                if slot.domain.minimum is not None
                and slot.domain.maximum is not None
                else None
            ),
            "choices": list(slot.domain.choices),
            "kind": _value_kind(slot.domain.kind),
            "missing": "forbidden",
            "quantization": slot.quantization,
            "unit": slot.domain.unit,
        },
    }


def _boundary_update(slot: SlotDecl) -> dict[str, object]:
    if slot.schedule == "on_clock_advance":
        return {
            "state": {
                "in_world_state": True,
                "reconstruction": "not_applicable:world_clock_state",
            },
            "update": {
                "merge_rule": "single_writer",
                "microstep": "weather.frame_input",
                "schedule": slot.schedule,
                "when_not_updated": "retain_previous_value",
                "writer": "time.world_clock",
            },
        }
    generated = slot.external_writer.startswith("space.")
    writer = (
        slot.external_writer
        or slot.external_source
        or f"outside_slice:{slot.id}"
    )
    return {
        "state": {
            "in_world_state": generated,
            "reconstruction": slot.external_source or writer,
        },
        "update": {
            "merge_rule": "single_writer",
            "microstep": (
                "world.gen_input" if generated else "weather.frame_input"
            ),
            "schedule": slot.schedule,
            "when_not_updated": (
                "retain_previous_value"
                if generated
                else "recompute_on_demand"
            ),
            "writer": writer,
        },
    }


def _boundary_microstep(slot: SlotDecl) -> str:
    if slot.external_writer.startswith("space."):
        return "world.gen_input"
    return "weather.frame_input"


def _value_kind(kind: str) -> str:
    if kind == "int":
        return "integer"
    return kind


def _variable(node: Mapping[str, object]) -> dict[str, object]:
    value = node["value"]
    return {
        "bounds": value["bounds"],  # type: ignore[index]
        "domain": (
            "continuous"
            if value["kind"] == "float"  # type: ignore[index]
            else "discrete"
        ),
        "eps": node["math"]["error_budget"],  # type: ignore[index]
        "exogenous": node["origin"] == "slice_boundary",
    }


def _phase_of(mechanism_id: str | None, program: object) -> str:
    if not mechanism_id:
        return ""
    mechanism = program.mechanisms.get(mechanism_id)
    return mechanism.when.key if mechanism is not None else ""


# ── 参数 / 机制 / 边 ────────────────────────────────────────────


def _parameters(program: object) -> list[ParameterDecl]:
    declarations: dict[str, ParameterDecl] = {}
    for pack in program.modules:
        for declaration in pack.parameters:
            declarations.setdefault(declaration.id, declaration)
    return sorted(declarations.values(), key=lambda item: item.id)


def _parameter(declaration: ParameterDecl, program: object) -> dict[str, object]:
    value = program.parameters.get(declaration.id, declaration.default)
    return {
        "bounds": (
            [declaration.minimum, declaration.maximum]
            if declaration.minimum is not None
            and declaration.maximum is not None
            else None
        ),
        "intervention_allowed": declaration.intervention_allowed,
        "source": declaration.source,
        "unit": declaration.unit,
        "value": value,
        "value_type": declaration.kind,
        "version": digest_object(
            {
                "id": declaration.id,
                "value": value,
                "source": declaration.source,
            }
        ),
    }


def _mechanism(mechanism: MechanismDecl, program: object) -> dict[str, object]:
    equation_version = mechanism_digest(mechanism)
    resolved = digest_object(
        {
            "equation": equation_version,
            "parameters": {
                pid: program.parameters.get(pid) for pid in mechanism.params
            },
            "boundary": sorted(
                parent.slot
                for parent in mechanism.parents
                if program.slots[parent.slot].persist == "external"
            ),
        }
    )
    return {
        "boundary_cases": list(mechanism.boundary_cases),
        "equation": mechanism.equation,
        "equation_version": equation_version,
        "function": _function_name(mechanism),
        "output": mechanism.outputs()[0],
        "parameters": [
            {"argument": argument, "parameter": parameter_id}
            for parameter_id, argument in mechanism.param_arguments
        ],
        "parents": [
            _parent_entry(parent, program) for parent in mechanism.parents
        ],
        "random_sources": [],
        "resolved_version": resolved,
        "source_dependencies": list(_deps_for(mechanism)),
        "witnesses": _witness_entries(mechanism, program),
    }


def _function_name(mechanism: MechanismDecl) -> str:
    function = mechanism.impl
    module = getattr(function, "__module__", "")
    qualname = getattr(function, "__qualname__", repr(function))
    return f"{module}.{qualname}"


def _deps_for(mechanism: MechanismDecl) -> tuple[str, ...]:
    return (
        _WORLDGEN_DEPS
        if mechanism.id.startswith("world.")
        else _WEATHER_DEPS
    )


def _parent_entry(parent: Parent, program: object) -> dict[str, object]:
    slot = program.slots[parent.slot]
    if slot.persist == "external":
        source_microstep = _boundary_microstep(slot)
    else:
        source_microstep = _phase_of(slot.writer, program)
    offsets = ((0, 0),)
    if parent.relation != "same":
        offsets = program.relations[parent.relation].offsets
    return {
        "aggregation": parent.aggregation,
        "analysis_role": parent.analysis_role,
        "argument": parent.argument,
        "boundary_operator": "identity_same_chunk",
        "broadcast": "same_instance",
        "entity_relation": "same_chunk",
        "guard": "always",
        "jump_bound": parent.jump_bound,
        "lag": parent.lag,
        "lipschitz": parent.lipschitz,
        "metric": parent.metric,
        "modulus_kind": parent.modulus_kind,
        "parent": parent.slot,
        "source_microstep": source_microstep,
        "spatial_offsets": [list(offset) for offset in offsets],
        "valid_domain": parent.valid_domain,
    }


def _witness_entries(
    mechanism: MechanismDecl,
    program: object,
) -> list[dict[str, object]]:
    """每个父引用取首个"只变该父"的见证对（与旧投影同形）。"""
    slot_of = {parent.argument: parent.slot for parent in mechanism.parents}
    entries: list[dict[str, object]] = []
    for parent in mechanism.parents:
        pair = next(iter(_pairs(mechanism, parent.argument)), None)
        if pair is None:
            continue
        first, second = pair
        inputs_a = {
            slot_of[argument]: value
            for argument, value in first.inputs.items()
            if argument in slot_of
        }
        inputs_b = {
            slot_of[argument]: value
            for argument, value in second.inputs.items()
            if argument in slot_of
        }
        entries.append(
            {
                "equation_version": mechanism_digest(mechanism),
                "expected_outputs": [
                    first.outputs[0], second.outputs[0],
                ],
                "inputs_a": inputs_a,
                "inputs_b": inputs_b,
                "label": f"{first.label}→{second.label}",
                "parent": parent.slot,
                "random_values": {},
            }
        )
    return entries


def _pairs(mechanism: MechanismDecl, argument: str):
    for first in mechanism.witnesses:
        for second in mechanism.witnesses:
            if first is second:
                continue
            if first.inputs.get(argument) == second.inputs.get(argument):
                continue
            keys = set(first.inputs) | set(second.inputs)
            if all(
                key == argument
                or first.inputs.get(key) == second.inputs.get(key)
                for key in keys
            ):
                yield first, second


def _edge(
    mechanism: MechanismDecl,
    parent: Parent,
    program: object,
) -> dict[str, object]:
    entry = _parent_entry(parent, program)
    return {
        "L": parent.lipschitz,
        "analysis_role": parent.analysis_role,
        "boundary_operator": entry["boundary_operator"],
        "child": mechanism.outputs()[0],
        "equation": mechanism.id,
        "jump_bound": parent.jump_bound,
        "lag": parent.lag,
        "modulus_kind": parent.modulus_kind,
        "parent": parent.slot,
        "role": "structural",
        "source_microstep": entry["source_microstep"],
        "spatial_offsets": entry["spatial_offsets"],
    }


if __name__ == "__main__":
    print(to_json())
