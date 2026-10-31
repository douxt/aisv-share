# 正例 / 反例（模板）

> 这一节比任何原则都有用。写自己的版本时，按「毛病类型」分组，每组至少一对。

## 1. 负面指令

❌ `She does not turn toward him.`
✅ `She keeps facing the camera, motionless.`

❌ `No extended runway.`
✅ `A small square stage; the audience seats begin one step from its edge.`

## 2. 步数

❌ `He takes three slow steps forward.`
✅ `He walks up until he stands one step before her.`

## 3. 时间轴

❌ `Then she starts singing, and after that the lights change.`
✅ `At 00:00.000 the lights dim. At 00:02.500 she starts singing. At 00:06.000 the lights shift to violet. At 00:09.000 the shot settles.`

## 4. 锁帧复述

❌ `The girl from the previous shot is here.`（没写她是什么姿势、手在哪）
✅ `The girl in <Picture 1>, the final frame of the previous shot: standing at stage center, facing the camera, both hands on the microphone, hair still.`

## 5. 特效

❌ `绚丽的全息光效包裹着他。`
✅ `At 00:02.000 a ring of light appears on the floor under him. At 00:04.000 light rises from the ring, coils around his body. At 00:06.500 the coil tightens into a tower shape and moves with him.`

## 6. 动作去向

❌ `He blocks the shards.`
✅ `He blocks the shards with a forward arc; the shards ricochet outward and clatter onto the floor behind him, away from the child.`

## 7. 空泛形容词

❌ `A cinematic, epic, breathtaking shot.`
✅ `Cool violet key light, teal shadows, shallow depth of field, light haze in the beams.`

---

## 使用方式

1. 每踩一个新坑，追加一对（**用真实原文，不要改写**）；
2. 反例要写明「错在哪」，否则读者只会照抄另一版措辞；
3. 同一类问题超过 3 条，就应该写进 `scripts/validate.py` 变成机械检查。
