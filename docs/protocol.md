# Turn Protocol

1. The player must own the spotlight.
2. The runtime records `player_action_received`.
3. The GM plans and may request a check.
4. If requested, the runtime rolls exactly `2d6`.
5. The result band is fixed: 10+ full success, 7-9 success with cost, 6- failure.
6. The GM resolves the already-known roll and proposes patches.
7. The runtime validates and commits patches atomically.
8. If the GM grants actor spotlight, the runtime constructs an `ActorView`.
9. The actor proposes speech and an intended action.
10. The semantic auditor checks for narrative overreach or forbidden knowledge.
11. The runtime emits approved public text and returns spotlight to the player.

## Important distinction

An actor may say, "Mira tries to force the door closed." It may not establish, "Mira closes the door and traps the player," unless the runtime has already committed that consequence.
