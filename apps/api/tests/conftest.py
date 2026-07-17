import os
import tempfile

os.environ["PDF_EDITOR_DATA"] = tempfile.mkdtemp(prefix="pdf-editor-tests-")

