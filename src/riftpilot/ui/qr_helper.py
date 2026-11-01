"""RiftPilot UI — QR Code Pixmap Generation Helper.

Provides in-memory QR code rasterization to PySide6 QPixmap for mobile pairing.
"""

from __future__ import annotations

import io
import qrcode
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap


def generate_qr_pixmap(data: str, size: int = 240) -> QPixmap:
    """Generate a high-contrast QPixmap rendering of a QR code in memory.

    Args:
        data: Payload string to encode in the QR code.
        size: Width and height of the generated pixmap in pixels (default: 240).

    Returns:
        QPixmap containing the rendered QR code scaled to the requested size.

    Raises:
        ValueError: If size is less than or equal to 0.
    """
    if size <= 0:
        raise ValueError(f"QR pixmap size must be positive, got {size}")

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    qimage = QImage.fromData(buffer.getvalue())
    pixmap = QPixmap.fromImage(qimage)

    return pixmap.scaled(
        size,
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


__all__ = ["generate_qr_pixmap"]
