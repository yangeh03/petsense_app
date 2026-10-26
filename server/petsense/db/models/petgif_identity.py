"""PetGIF identities created for and owned by an authenticated PetSense user."""
from sqlalchemy import Column, DateTime, ForeignKey, String

from petsense.db import Base


class PetgifIdentity(Base):
    __tablename__ = 'petgif_identities'

    petgif_id = Column(String(128), primary_key=True)
    user_id = Column(ForeignKey('users.id'), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
