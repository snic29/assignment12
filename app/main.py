from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID
from typing import List, Optional
from fastapi import Body, FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_active_user
from app.models.calculation import Calculation
from app.models.user import User
from app.schemas.calculation import CalculationBase, CalculationRead, CalculationUpdate
from app.schemas.token import TokenResponse
from app.schemas.user import UserCreate, UserResponse, UserLogin
from app.database import Base, get_db, engine

# Create tables on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Creating tables...")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully!")
    yield

app = FastAPI(
    title="Calculations API",
    description="API for managing calculations",
    version="1.0.0",
    lifespan=lifespan
)

# ------------------------------------------------------------------------------
# Health Endpoint
# ------------------------------------------------------------------------------
@app.get("/health", tags=["health"])
def read_health():
    return {"status": "ok"}

# ------------------------------------------------------------------------------
# User Endpoints
# ------------------------------------------------------------------------------
# The /users/* routes are the public API. The older /auth/* paths are kept as
# hidden aliases so existing clients keep working.

def _authenticate_or_401(db: Session, username_or_email: str, password: str) -> dict:
    """Verify credentials against the stored bcrypt hash, or raise 401."""
    auth_result = User.authenticate(db, username_or_email, password)
    if auth_result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    db.commit()  # persist the last_login update
    return auth_result


@app.post(
    "/users/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["users"],
)
@app.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def register(user_create: UserCreate, db: Session = Depends(get_db)):
    """Create a new user. The password is stored as a bcrypt hash, never in plain text."""
    user_data = user_create.model_dump(exclude={"confirm_password"})
    try:
        user = User.register(db, user_data)
        db.commit()
        db.refresh(user)
        return user
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@app.post("/users/login", response_model=TokenResponse, tags=["users"])
@app.post("/auth/login", response_model=TokenResponse, include_in_schema=False)
def login_json(user_login: UserLogin, db: Session = Depends(get_db)):
    """Log in with a username (or email) and password; returns JWT access and refresh tokens."""
    auth_result = _authenticate_or_401(db, user_login.username, user_login.password)
    user = auth_result["user"]

    expires_at = auth_result["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return TokenResponse(
        access_token=auth_result["access_token"],
        refresh_token=auth_result["refresh_token"],
        token_type="bearer",
        expires_at=expires_at,
        user_id=user.id,
        username=user.username,
        email=user.email,
        first_name=user.first_name,
        last_name=user.last_name,
        is_active=user.is_active,
        is_verified=user.is_verified,
    )


@app.post("/users/token", tags=["users"])
@app.post("/auth/token", include_in_schema=False)
def login_form(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Form-based login used by the Swagger UI "Authorize" button."""
    auth_result = _authenticate_or_401(db, form_data.username, form_data.password)
    return {"access_token": auth_result["access_token"], "token_type": "bearer"}


@app.get("/users/me", response_model=UserResponse, tags=["users"])
def read_current_user(
    current_user=Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """Return the profile of the user who owns the bearer token (requires login)."""
    user = db.query(User).filter(User.id == current_user.id).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user

# ------------------------------------------------------------------------------
# Calculations Endpoints (BREAD)
# ------------------------------------------------------------------------------
# Create (Add) Calculation – using CalculationBase so that 'user_id' from the client is ignored.
@app.post(
    "/calculations",
    response_model=CalculationRead,
    status_code=status.HTTP_201_CREATED,
    tags=["calculations"],
)
def create_calculation(
    calculation_data: CalculationBase,
    current_user = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Compute and persist a calculation.
    
    The endpoint reads the calculation type and inputs from the request (ignoring any extra fields),
    computes the result using the appropriate operation, and assigns the authenticated user's ID.
    """
    try:
        # Create the calculation using the factory method.
        new_calculation = Calculation.create(
            calculation_type=calculation_data.type,
            user_id=current_user.id,
            inputs=calculation_data.inputs,
        )
        new_calculation.result = new_calculation.get_result()

        # Persist the calculation to the database.
        db.add(new_calculation)
        db.commit()
        db.refresh(new_calculation)
        return new_calculation

    except ValueError as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

# Browse / List Calculations (for the current user)
@app.get("/calculations", response_model=List[CalculationRead], tags=["calculations"])
def list_calculations(
    current_user = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """List the current user's calculations, newest first."""
    return (
        db.query(Calculation)
        .filter(Calculation.user_id == current_user.id)
        .order_by(Calculation.created_at.desc())
        .all()
    )

# Read / Retrieve a Specific Calculation by ID
@app.get("/calculations/{calc_id}", response_model=CalculationRead, tags=["calculations"])
def get_calculation(
    calc_id: str,
    current_user = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    try:
        calc_uuid = UUID(calc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid calculation id format.")
    calculation = db.query(Calculation).filter(
        Calculation.id == calc_uuid,
        Calculation.user_id == current_user.id
    ).first()
    if not calculation:
        raise HTTPException(status_code=404, detail="Calculation not found.")
    return calculation

# Edit / Update a Calculation
#   PUT   replaces the calculation: `type` and `inputs` are both required.
#   PATCH changes only the fields sent; the rest keep their current values.
def _update_calculation(
    calc_id: str,
    new_type: Optional[str],
    new_inputs: Optional[List[float]],
    current_user,
    db: Session,
) -> Calculation:
    try:
        calc_uuid = UUID(calc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid calculation id format.")
    calculation = db.query(Calculation).filter(
        Calculation.id == calc_uuid,
        Calculation.user_id == current_user.id
    ).first()
    if not calculation:
        raise HTTPException(status_code=404, detail="Calculation not found.")

    new_type = new_type or calculation.type
    new_inputs = new_inputs if new_inputs is not None else calculation.inputs

    # Recompute with the subclass for the (possibly new) type, without touching
    # the stored row until we know the result is valid (e.g. no division by zero).
    try:
        result = Calculation.create(new_type, calculation.user_id, new_inputs).get_result()
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Write with a direct UPDATE rather than mutating the loaded object: `type` is
    # the polymorphic discriminator, so changing it on an Addition instance (say)
    # would leave an object of the wrong subclass in the session.
    db.query(Calculation).filter(Calculation.id == calc_uuid).update(
        {
            Calculation.type: new_type,
            Calculation.inputs: new_inputs,
            Calculation.result: result,
            Calculation.updated_at: datetime.utcnow(),
        },
        synchronize_session=False,
    )
    db.commit()
    db.expunge(calculation)
    return db.query(Calculation).filter(Calculation.id == calc_uuid).one()


@app.put("/calculations/{calc_id}", response_model=CalculationRead, tags=["calculations"])
def replace_calculation(
    calc_id: str,
    calculation_data: CalculationBase,
    current_user = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Replace a calculation's type and inputs (both required) and recompute its result."""
    return _update_calculation(
        calc_id, calculation_data.type.value, calculation_data.inputs, current_user, db
    )


@app.patch("/calculations/{calc_id}", response_model=CalculationRead, tags=["calculations"])
def update_calculation(
    calc_id: str,
    calculation_update: CalculationUpdate,
    current_user = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Change a calculation's type and/or inputs and recompute its result.
    Fields left out of the body keep their current values.
    """
    new_type = calculation_update.type.value if calculation_update.type else None
    return _update_calculation(calc_id, new_type, calculation_update.inputs, current_user, db)

# Delete a Calculation
@app.delete("/calculations/{calc_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["calculations"])
def delete_calculation(
    calc_id: str,
    current_user = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    try:
        calc_uuid = UUID(calc_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid calculation id format.")
    calculation = db.query(Calculation).filter(
        Calculation.id == calc_uuid,
        Calculation.user_id == current_user.id
    ).first()
    if not calculation:
        raise HTTPException(status_code=404, detail="Calculation not found.")
    db.delete(calculation)
    db.commit()
    return None

# ------------------------------------------------------------------------------
# Main Block to Run the Server
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8001, log_level="info")
