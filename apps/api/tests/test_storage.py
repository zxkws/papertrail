import errno
import os
from pathlib import Path

from app import storage


def test_publish_export_stages_inside_export_volume(monkeypatch, tmp_path):
    renderer_dir = tmp_path / "renderer-temp"
    renderer_dir.mkdir()
    rendered_pdf = renderer_dir / "rendered.pdf"
    rendered_pdf.write_bytes(b"rendered-pdf")

    real_replace = os.replace

    def reject_cross_device_replace(source, destination):
        if Path(source).parent != Path(destination).parent:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(source, destination)

    monkeypatch.setattr(storage.os, "replace", reject_cross_device_replace)

    version = storage.publish_export("document-id", rendered_pdf, "hash", 1)

    exported_pdf = Path(version["path"])
    assert exported_pdf.read_bytes() == b"rendered-pdf"
    assert exported_pdf.parent == storage.EXPORTS
    assert not rendered_pdf.exists()
