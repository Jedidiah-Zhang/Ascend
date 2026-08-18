"""entity / tp 指令组 — 实体生灭调试与玩家传送。

Mixin，依赖宿主 CommandExecutor 提供的:
  self._entities / self._player / self._i18n / self._clock
  self._default_chunk
"""

import math

from ascend.config import TILE_MAP_SIZE
from ascend.entity import EntityType, split_coords, Controller

from .result import CommandResult


class EntityCommandsMixin:
    """entity / tp 指令组实现。"""

    def _h_entity(self, args: list[str]) -> CommandResult:
        """处理 entity 指令组：list 列表 / birth 诞生 / death 死亡。

        调试用生灭入口（Issue #20 验收）：birth/death 是世界内因果
        事件，经 EntityManager 发布 entity_born/entity_died 到世界树，
        EventBridge 广播后前端应实时渲染/移除。

        Args:
            args: 参数列表。

        Returns:
            执行结果。
        """
        if self._entities is None:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.entity_unavailable"),
            )
        if not args or args[0].lower() == "list":
            return self._h_entity_list()
        sub = args[0].lower()
        if sub == "birth":
            return self._h_entity_birth(args[1:])
        if sub == "death":
            return self._h_entity_death(args[1:])
        return CommandResult(
            success=False, output=self._i18n.t("console.entity_usage"),
        )

    def _h_entity_list(self) -> CommandResult:
        """处理 entity list：列出所有活跃实体。

        Returns:
            执行结果（ID 短前缀、类型、控制者、全局坐标）。
        """
        entities = self._entities.all_entities()
        if not entities:
            return CommandResult(
                success=True, output=self._i18n.t("console.entity_none"),
            )
        lines = [self._i18n.t("console.entity_list_header", count=len(entities))]
        for e in entities:
            x, y = e.global_xy
            lines.append(
                f"  {e.id[:8]}  {e.entity_type.name:<9s}  "
                f"{e.controller.name:<6s}  ({x:.1f}, {y:.1f})  L{e.layer_id}"
            )
        return CommandResult(success=True, output="\n".join(lines))

    def _h_entity_birth(self, args: list[str]) -> CommandResult:
        """处理 entity birth <type> [x y]：实体在指定位置诞生。

        位置缺省为玩家当前位置（无玩家服务时为默认 chunk 原点）。

        Args:
            args: [type, x?, y?]。

        Returns:
            执行结果。
        """
        if not args:
            return CommandResult(
                success=False, output=self._i18n.t("console.entity_usage"),
            )
        type_name = args[0].upper()
        if type_name not in EntityType.__members__:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.entity_type_unknown",
                    name=args[0],
                    types=", ".join(t.name.lower() for t in EntityType),
                ),
            )
        entity_type = EntityType[type_name]

        if len(args) >= 3:
            try:
                x, y = float(args[1]), float(args[2])
            except ValueError:
                return CommandResult(
                    success=False, output=self._i18n.t("console.entity_usage"),
                )
        elif self._player is not None:
            x, y = self._player.position
        else:
            dcx, dcy = self._default_chunk
            x, y = float(dcx * TILE_MAP_SIZE), float(dcy * TILE_MAP_SIZE)

        gx, gy, tx, ty = split_coords(x, y)
        entity = self._entities.birth(
            entity_type, gx, gy, tx, ty,
            data={"fx": x, "fy": y},
            game_time=self._clock.time,
        )
        return CommandResult(
            success=True,
            output=self._i18n.t(
                "console.entity_born",
                id=entity.id[:8], type=entity.entity_type.name,
                x=f"{x:.1f}", y=f"{y:.1f}",
            ),
        )

    def _h_entity_death(self, args: list[str]) -> CommandResult:
        """处理 entity death <id前缀>：实体死亡。

        接受唯一的 ID 前缀（≥4 字符），避免手输完整 UUID。
        拒绝玩家控制的实体——PlayerService 会持有悬垂引用，
        且玩家死亡应走死亡机制而非调试命令。

        Args:
            args: [id_prefix]。

        Returns:
            执行结果。
        """
        if len(args) != 1 or len(args[0]) < 4:
            return CommandResult(
                success=False, output=self._i18n.t("console.entity_usage"),
            )
        prefix = args[0].lower()
        matches = [
            e for e in self._entities.all_entities()
            if e.id.startswith(prefix)
        ]
        if not matches:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.entity_not_found", id=args[0]),
            )
        if len(matches) > 1:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.entity_ambiguous", id=args[0]),
            )
        entity = matches[0]
        if entity.controller == Controller.PLAYER:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.entity_player_protected", id=entity.id[:8],
                ),
            )
        self._entities.death(entity.id, game_time=self._clock.time)
        return CommandResult(
            success=True,
            output=self._i18n.t(
                "console.entity_dead",
                id=entity.id[:8], type=entity.entity_type.name,
            ),
        )

    def _h_tp(self, args: list[str]) -> CommandResult:
        """处理 tp [x y]：传送玩家（权威实体在后端）。

        无参数回出生点。传送通过 player_teleported 事件推送前端吸附。

        Args:
            args: 参数列表（空 或 [x, y]）。

        Returns:
            执行结果。
        """
        if self._player is None:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.player_unavailable"),
            )
        if not args:
            x, y = self._player.teleport_home()
            return CommandResult(
                success=True,
                output=self._i18n.t("console.tp_home", x=f"{x:.0f}", y=f"{y:.0f}"),
            )
        if len(args) != 2:
            return CommandResult(
                success=False, output=self._i18n.t("console.tp_usage"),
            )
        try:
            x = float(args[0])
            y = float(args[1])
        except ValueError:
            return CommandResult(
                success=False, output=self._i18n.t("console.tp_usage"),
            )
        if not math.isfinite(x) or not math.isfinite(y):
            return CommandResult(
                success=False, output=self._i18n.t("console.tp_usage"),
            )
        x, y = self._player.teleport(x, y)
        return CommandResult(
            success=True,
            output=self._i18n.t("console.tp_done", x=f"{x:.0f}", y=f"{y:.0f}"),
        )
