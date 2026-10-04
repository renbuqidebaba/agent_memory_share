"""任务交接 HTTP API。远程部署前必须加真实身份认证和工作区授权。"""
from fastapi import APIRouter, HTTPException

from ..models import HandoffAck, HandoffClaim, HandoffComplete, HandoffCreate
from ..storage.handoff_store import HandoffConflict, HandoffStore

router = APIRouter(prefix="/v1/handoffs", tags=["handoffs"])
store = HandoffStore()


def _run(operation):
    try:
        return operation()
    except KeyError:
        raise HTTPException(status_code=404, detail="handoff not found") from None
    except (HandoffConflict, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("")
def create(body: HandoffCreate):
    return store.create(body)


@router.get("")
def list_handoffs(task_id: str | None = None, status: str | None = None,
                  to_agent: str | None = None, workspace: str | None = None):
    return {"results": store.list(task_id, status, to_agent, workspace)}


@router.get("/{handoff_id}")
def get(handoff_id: str):
    result = store.get(handoff_id)
    if result is None:
        raise HTTPException(status_code=404, detail="handoff not found")
    return result


@router.post("/{handoff_id}/claim")
def claim(handoff_id: str, body: HandoffClaim):
    return _run(lambda: store.claim(handoff_id, body.agent_id, body.lease_seconds))


@router.post("/{handoff_id}/ack")
def ack(handoff_id: str, body: HandoffAck):
    return _run(lambda: store.ack(handoff_id, body.agent_id, body.accepted, body.reason, body.verification_id))


@router.get("/{handoff_id}/verifications")
def verifications(handoff_id: str):
    get(handoff_id)
    return {"results": store.verifications(handoff_id)}


@router.post("/{handoff_id}/complete")
def complete(handoff_id: str, body: HandoffComplete):
    return _run(lambda: store.complete(handoff_id, body.agent_id))
