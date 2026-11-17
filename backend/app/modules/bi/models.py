from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from app.core.database import Base
from app.core.time import utcnow_naive


class BIVisual(Base):
    """Definición de un visual guardado en un panel BI.

    Se guarda la DEFINICIÓN (tabla, campos, agregación y filtros), no los datos:
    cada vez que se abre el panel se vuelve a consultar la base, así el visual
    muestra los datos actuales y respeta los permisos vigentes del usuario.
    """

    __tablename__ = "bi_visuals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    connection_id = Column(
        Integer,
        ForeignKey("corporate_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    panel_name = Column(String(120), nullable=False, default="Mi panel", index=True)
    title = Column(String(255), nullable=False)
    spec_json = Column(Text, nullable=False)
    position = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=utcnow_naive)
