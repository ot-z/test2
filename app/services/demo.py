"""演示数据与重置（设置页显式触发，绝不自动执行）。"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import _utcnow
from ..models import DailyPlan, DailyPlanItem, Note, Problem, StudyRecord
from ..seed import seed_all
from .exporter import clear_business_data
from . import plan as plan_service

SAMPLE_NOTE = """## 思路
1. 先把输入读进来，注意边界条件；
2. 用一次遍历维护当前最优值，避免重复计算；
3. 复杂度 O(n)，空间 O(1)。

```cpp
#include <bits/stdc++.h>
using namespace std;
int main() {
    int a, b; cin >> a >> b;
    cout << a + b << endl;
    return 0;
}
```

## 易错点
- 忘记处理 `n = 0`；
- 整数溢出时改用 `long long`。
"""


def reset_all_data(db: Session) -> dict:
    """清空业务数据并重新装配初始状态（用于删除演示数据 / 恢复出厂）。"""
    clear_business_data(db)
    seeded = seed_all(db)
    generated = plan_service.generate(db, days=30)
    return {"seeded": seeded, "generated": generated}


def load_demo_data(db: Session) -> dict:
    """生成近两周的可体验历史：已完成的任务、学习记录、思路笔记。"""
    reset_all_data(db)
    cfg = plan_service.active_config(db)
    plan_service.update_config(db, {"start_date": (date.today() - timedelta(days=14)).isoformat()})
    plan_service.generate(db, days=30, from_date=date.today() - timedelta(days=14))

    today = date.today()
    plans = db.scalars(
        select(DailyPlan).where(DailyPlan.plan_date < today).order_by(DailyPlan.plan_date)
    ).all()
    touched = 0
    for p in plans:
        items = db.scalars(select(DailyPlanItem).where(DailyPlanItem.plan_id == p.id)).all()
        for item in items:
            if item.status == "skipped":
                continue
            target = "reviewed" if item.item_type == "review" else "ac"
            plan_service.update_status(db, item.id, target)
            problem = db.get(Problem, item.problem_id)
            db.add(StudyRecord(
                plan_item_id=item.id, problem_id=item.problem_id,
                result="done", time_spent_min=18 + (item.id % 25),
                language="C++", mastery=3 + (item.id % 3) - 1,
                code_snippet="#include <bits/stdc++.h>\nint main(){return 0;}\n",
                memo="演示记录",
            ))
            if item.item_type == "new" and touched % 2 == 0:
                db.add(Note(
                    problem_id=item.problem_id,
                    title=f"{problem.title if problem else '题目'} · 思路总结",
                    content_md=SAMPLE_NOTE, is_auto_saved=True,
                ))
            touched += 1
    db.commit()
    return {"demo_items": touched, "demo_plans": len(plans), "note": "演示数据已载入，可在设置页一键清空"}
