from fastapi import APIRouter
from pydantic import BaseModel

from app.services.ai_advisor import build_ai_advice

router = APIRouter(prefix="/ai", tags=["ai"])


class AdviceRequest(BaseModel):
    message: str = ""


@router.post("/advice")
def get_advice(request: AdviceRequest):
    try:
        return build_ai_advice(user_message=request.message)
    except Exception as e:
        return {
            "error": f"AI advisor failed: {str(e)}",
            "reply_json": None,
            "debug": {
                "mode": "route_exception",
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        }
