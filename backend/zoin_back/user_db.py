from sqlalchemy import create_engine, Column,String
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.dialects.postgresql import UUID
import uuid

DATABASE_URL = "postgresql://zoin_om:1325%40MALTO@localhost:5432/zoin"

engine = create_engine(
    DATABASE_URL,
    echo=True
)

SessionLocal = sessionmaker(
    bind = engine,
    autocommit=False,
    autoflush=False
)

Base = declarative_base()

class User(Base):
    __tablename__="users"

    id= Column(
        UUID (as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False

    )
    name = Column( String(255),nullable=False)
    email = Column (String(255), unique=True,nullable=False)
    hashed_password=Column(String, nullable=False)


def get_db():
    db=SessionLocal()
    try:
        yield db
    finally:
        db.close()