"""知识点树 / 标签映射 / 大纲 / 候选题单接口（FR-05 ～ FR-08）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFound
from ..models import ProblemKnowledge
from ..services import knowledge

router = APIRouter(prefix="/api", tags=["知识点与大纲"])


@router.get("/knowledge-nodes")
def tree(db: Session = Depends(get_db)):
    return {"items": knowledge.tree(db)}


@router.get("/knowledge-nodes/flat")
def flat(db: Session = Depends(get_db)):
    return {"items": knowledge.flat_nodes(db)}


@router.post("/knowledge-nodes")
def create_node(payload: dict = Body(...), db: Session = Depends(get_db)):
    return knowledge.create_node(db, payload)


@router.patch("/knowledge-nodes/{node_id}")
def update_node(node_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    return knowledge.update_node(db, node_id, payload)


@router.delete("/knowledge-nodes/{node_id}")
def delete_node(node_id: int, force: bool = Query(False), db: Session = Depends(get_db)):
    return knowledge.delete_node(db, node_id, force)


@router.get("/tag-mappings")
def list_mappings(db: Session = Depends(get_db)):
    return {"items": knowledge.list_tag_mappings(db)}


@router.put("/tag-mappings")
def put_mapping(payload: dict = Body(...), db: Session = Depends(get_db)):
    return knowledge.put_tag_mapping(db, payload)


@router.delete("/tag-mappings/{mapping_id}")
def delete_mapping(mapping_id: int, db: Session = Depends(get_db)):
    return knowledge.delete_tag_mapping(db, mapping_id)


@router.post("/problem-knowledge/{link_id}/verify")
def verify_link(link_id: int, db: Session = Depends(get_db)):
    link = db.get(ProblemKnowledge, link_id)
    if link is None:
        raise NotFound("标签关联不存在")
    link.verified = True
    db.commit()
    return {"id": link_id, "verified": True}


@router.get("/syllabi")
def list_syllabi(db: Session = Depends(get_db)):
    return {"items": knowledge.list_syllabi(db)}


@router.post("/syllabi")
def create_syllabus(payload: dict = Body(...), db: Session = Depends(get_db)):
    return knowledge.create_syllabus(db, payload)


@router.get("/syllabi/{syllabus_id}")
def get_syllabus(syllabus_id: int, db: Session = Depends(get_db)):
    from ..models import Syllabus
    row = db.get(Syllabus, syllabus_id)
    if row is None:
        raise NotFound("大纲不存在")
    return knowledge.syllabus_detail(db, row)


@router.post("/syllabi/{syllabus_id}/generate-list")
def generate_list(syllabus_id: int, payload: dict = Body(default={}), db: Session = Depends(get_db)):
    payload = dict(payload)
    payload["syllabus_id"] = syllabus_id
    return knowledge.generate_candidate_list(db, payload)


@router.get("/problem-lists")
def list_lists(db: Session = Depends(get_db)):
    return {"items": knowledge.list_lists(db)}


@router.get("/problem-lists/{list_id}")
def get_list(list_id: int, db: Session = Depends(get_db)):
    return knowledge.list_detail(db, list_id)


@router.delete("/problem-lists/{list_id}/items/{item_id}")
def remove_item(list_id: int, item_id: int, db: Session = Depends(get_db)):
    return knowledge.remove_list_item(db, item_id)


@router.put("/problem-lists/{list_id}/order")
def reorder(list_id: int, payload: dict = Body(...), db: Session = Depends(get_db)):
    return knowledge.reorder_list(db, list_id, payload.get("order") or [])


@router.put("/problem-lists/{list_id}/confirm")
def confirm(list_id: int, db: Session = Depends(get_db)):
    return knowledge.confirm_list(db, list_id)


@router.put("/problem-lists/{list_id}/activate")
def activate(list_id: int, db: Session = Depends(get_db)):
    return knowledge.set_active_list(db, list_id)
