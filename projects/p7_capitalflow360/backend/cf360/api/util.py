"""Serialisation, pagination and error translation shared by the routers."""

from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cf360.api.deps import ApiError
from cf360.lifecycle import InvalidTransition
from cf360.services.booking import BookingError
from cf360.services.core import jsonable
from cf360.services.workflow import WorkflowError

MAX_PAGE = 500


def row(obj, exclude: tuple[str, ...] = ()) -> dict:
    if obj is None:
        return None
    return jsonable({c.name: getattr(obj, c.name) for c in obj.__table__.columns if c.name not in exclude})


def rows(objs, exclude: tuple[str, ...] = ()) -> list[dict]:
    return [row(o, exclude) for o in objs]


def page(s: Session, q, page_no: int, size: int, exclude: tuple[str, ...] = ()) -> dict:
    size = max(1, min(size, MAX_PAGE))
    page_no = max(1, page_no)
    total = s.scalar(select(func.count()).select_from(q.order_by(None).subquery()))
    items = s.scalars(q.limit(size).offset((page_no - 1) * size)).all()
    return {"items": rows(items, exclude), "total": total, "page": page_no, "page_size": size,
            "pages": (total + size - 1) // size}


@contextmanager
def translate(s: Session):
    """Map domain exceptions to HTTP errors and roll back the transaction on any failure."""
    try:
        yield
    except WorkflowError as e:
        s.rollback()
        raise ApiError(e.status, e.code, e.message) from e
    except InvalidTransition as e:
        s.rollback()
        raise ApiError(409, "E_INVALID_TRANSITION", str(e), {"rule_id": "R099", "from": e.current, "to": e.target}) from e
    except BookingError as e:
        s.rollback()
        raise ApiError(422, "E_VALIDATION", "Trade failed validation", {"issues": e.issues}) from e
    except ApiError:
        s.rollback()
        raise
    except Exception:
        s.rollback()
        raise
